import os
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
import logging
from dotenv import load_dotenv

# ----------------------------
# SETUP
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DAILY_FILE = DATA_DIR / "ui_daily_view.csv"
CURRENT_FILE = DATA_DIR / "ui_current_weather.csv"

LOG_FILE = BASE_DIR / "etl_error.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)


# ----------------------------
# DB CONNECTION
# ----------------------------
def get_database_url():
    load_dotenv(BASE_DIR / ".env")
    return os.getenv("SUPABASE_DB_URL")


engine = create_engine(get_database_url())


# ----------------------------
# VALIDATION HELPERS
# ----------------------------
def validate_dataframe(df, name):
    logging.info(f"[VALIDATION START] {name}")

    # NULL CHECK
    null_counts = df.isnull().sum()
    if null_counts.any():
        logging.warning(
            f"[NULL CHECK] Missing values detected in {name}:\n{null_counts}"
        )

    # DUPLICATE CHECK
    dup_count = df.duplicated().sum()
    if dup_count > 0:
        logging.warning(f"[DUPLICATES] {name} contains {dup_count} duplicate rows")
    else:
        logging.info(f"[DUPLICATES] No duplicates found in {name}")

    # EMPTY CHECK
    if df.empty:
        raise ValueError(f"[VALIDATION FAILED] {name} is empty")

    logging.info(f"[VALIDATION PASS] {name} rows={len(df)}")


def validate_schema(df, expected_schema, name):
    logging.info(f"[SCHEMA CHECK] {name}")

    missing_cols = set(expected_schema.keys()) - set(df.columns)
    extra_cols = set(df.columns) - set(expected_schema.keys())

    if missing_cols:
        raise ValueError(f"[SCHEMA ERROR] Missing columns in {name}: {missing_cols}")

    if extra_cols:
        logging.warning(f"[SCHEMA WARNING] Extra columns in {name}: {extra_cols}")

    # datatype validation
    for col, dtype in expected_schema.items():
        if not pd.api.types.is_dtype_equal(df[col].dtype, dtype):
            logging.warning(
                f"[TYPE WARNING] {name}.{col} expected {dtype}, got {df[col].dtype}"
            )

    logging.info(f"[SCHEMA PASS] {name}")


def validate_ranges(df, name):
    logging.info(f"[RANGE CHECK] {name}")

    if "temperature" in df.columns:
        if ((df["temperature"] < -60) | (df["temperature"] > 60)).any():
            logging.warning(f"[RANGE ISSUE] Temperature out of bounds in {name}")

    if "humidity" in df.columns:
        if ((df["humidity"] < 0) | (df["humidity"] > 100)).any():
            logging.warning(f"[RANGE ISSUE] Humidity out of bounds in {name}")

    if "wind_speed" in df.columns:
        if (df["wind_speed"] < 0).any():
            logging.warning(f"[RANGE ISSUE] Negative wind speed in {name}")

    logging.info(f"[RANGE CHECK COMPLETE] {name}")


# ----------------------------
# DB VALIDATION
# ----------------------------
def check_row_counts(table, expected_min=1):
    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()

    if count < expected_min:
        raise ValueError(f"[ROW COUNT ERROR] {table} has only {count} rows")

    logging.info(f"[ROW COUNT OK] {table} rows={count}")


def check_referential_integrity():
    with engine.connect() as conn:
        orphan_rows = conn.execute(
            text("""
            SELECT COUNT(*)
            FROM daily_forecast d
            LEFT JOIN location l
            ON d.location_id = l.location_id
            WHERE l.location_id IS NULL
        """)
        ).scalar()

    if orphan_rows > 0:
        raise ValueError(f"[FK ERROR] {orphan_rows} orphan rows in daily_forecast")

    logging.info("[FK CHECK PASS] Referential integrity OK")


# ----------------------------
# UPSERT
# ----------------------------
def upsert_dataframe(table_name, df, conflict_cols):
    df = df.where(pd.notnull(df), None)

    cols = list(df.columns)
    col_names = ", ".join(cols)
    placeholders = ", ".join([f":{c}" for c in cols])

    update_cols = [c for c in cols if c not in conflict_cols]
    update_sql = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

    sql = f"""
    INSERT INTO {table_name} ({col_names})
    VALUES ({placeholders})
    ON CONFLICT ({", ".join(conflict_cols)})
    DO UPDATE SET {update_sql};
    """

    with engine.begin() as conn:
        conn.execute(text(sql), df.to_dict(orient="records"))

    logging.info(f"[UPSERT DONE] {table_name} rows={len(df)}")


# ----------------------------
# MAIN PIPELINE
# ----------------------------
def main():
    try:
        logging.info("===== ETL START =====")

        # -------------------------
        # LOAD FILES
        # -------------------------
        daily = pd.read_csv(DAILY_FILE)
        current = pd.read_csv(CURRENT_FILE)

        # -------------------------
        # VALIDATION - RAW INPUT
        # -------------------------
        validate_dataframe(daily, "daily_forecast_raw")
        validate_dataframe(current, "current_weather_raw")

        # -------------------------
        # TRANSFORM
        # -------------------------
        sample = daily.iloc[0]

        loc_df = pd.DataFrame(
            [
                {
                    "formatted_address": sample["target_address"],
                    "latitude": sample["latitude"],
                    "longitude": sample["longitude"],
                    "timezone_id": sample["timezone_id"],
                }
            ]
        )

        upsert_dataframe("location", loc_df, ["formatted_address"])

        with engine.connect() as conn:
            loc_id = conn.execute(
                text("SELECT location_id FROM location WHERE formatted_address=:addr"),
                {"addr": sample["target_address"]},
            ).scalar()

        if not loc_id:
            raise ValueError("[FK ERROR] location_id not found after insert")

        daily["location_id"] = loc_id
        current["location_id"] = loc_id

        # -------------------------
        # CLEAN COLUMNS
        # -------------------------
        daily = daily.drop_duplicates()

        daily = daily[
            [
                c
                for c in daily.columns
                if c
                in {
                    "location_id",
                    "forecast_date",
                    "temperature_max",
                    "temperature_min",
                    "apparent_temperature_max",
                    "apparent_temperature_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "uv_index_max",
                    "wind_speed_max",
                    "weather_code_id",
                    "sunrise",
                    "sunset",
                    "observation_time",
                }
            ]
        ]

        current = current.rename(
            columns={
                "temperature_2m": "temperature",
                "relative_humidity_2m": "humidity",
                "wind_speed_10m": "wind_speed",
            }
        )

        current = current[
            [
                c
                for c in current.columns
                if c
                in {
                    "location_id",
                    "observed_at",
                    "temperature",
                    "weather_code",
                    "humidity",
                    "wind_speed",
                }
            ]
        ]

        # -------------------------
        # VALIDATION - TRANSFORMED DATA
        # -------------------------
        validate_dataframe(daily, "daily_forecast_clean")
        validate_dataframe(current, "current_weather_clean")

        validate_ranges(current, "current_weather")
        validate_ranges(daily, "daily_forecast")

        # -------------------------
        # LOAD
        # -------------------------
        upsert_dataframe("daily_forecast", daily, ["location_id", "forecast_date"])
        upsert_dataframe("current_weather", current, ["location_id", "observed_at"])

        # -------------------------
        # POST LOAD VALIDATION
        # -------------------------
        check_row_counts("location", 1)
        check_row_counts("daily_forecast", 1)
        check_row_counts("current_weather", 1)

        check_referential_integrity()

        logging.info("===== ETL SUCCESS =====")

    except Exception as e:
        logging.error(f"[ETL FAILED] {str(e)}")
        raise


if __name__ == "__main__":
    main()
