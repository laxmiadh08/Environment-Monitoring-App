# Environmental Health Monitor

A Plotly Dash dashboard that combines real-time weather and air quality data to assess environmental health risk for any city.

## Features

- **Live weather** — temperature, feels-like, humidity, wind, visibility, pressure, dew point
- **Air quality** — US AQI with pollutant breakdown (O₃, PM2.5, PM10, NO₂, CO, SO₂)
- **Health risk assessment** — evaluates the combination of temperature, humidity, and AQI to classify conditions as Low / Moderate / High / Extreme Danger, with tailored advisories and action bullets
- **Hourly AQI chart** — 48-hour bar chart colour-coded by AQI band
- **7-day forecast** — daily high/low with weather emoji and average AQI trend
- **City search** — look up any city; defaults to Louisville, KY on load

## Project Structure
```
data/ CSV weather_codes.xlsx
.
├── app.py        
├── main.py       
├── load.py       
└── README.md
```
## Scripts 
main.py: Production pipeline that fetches the weather and air data and merges them along with weather code descriptions
load.py: DB read helpers (get_latest_weather, get_latest_air, get_hourly_aqi_trend)
app.py:  Dash layout, callbacks, health-risk logic


## Workfllow
(---inprogress)

## Setup

```bash
pip install dash plotly
```

Add any additional dependencies required by `main.py` and `load.py` (e.g. `requests`, a database driver).

## Running

```bash
python app.py
```

Open `http://localhost:8050` in your browser.

## Health Risk Levels

| Level | Conditions |
|---|---|
| ✅ Low Risk | AQI ≤ 50, temp/humidity within normal range |
| 🔶 Moderate Risk | Temp 85–90 °F + Humidity > 70% **or** AQI 51–100 |
| ⚠️ High Risk | Temp 91–95 °F + Humidity > 60% **or** AQI 101–150 |
| 🚨 Extreme Danger | Temp > 95 °F + (Humidity > 60% **or** AQI > 150), or AQI > 300 |

source for Health risk: https://www.who.int/news-room/fact-sheets/detail/climate-change-heat-and-health#:~:text=As%20a%20result%2C%20heat%20extremes,and%20cause%20acute%20kidney%20injury.