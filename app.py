import json
import datetime
import traceback

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import plotly.graph_objects as go

from main import run as etl_run  # full ETL (geocode → fetch → transform → load)
from load import get_latest_weather, get_latest_air, get_hourly_aqi_trend

# App bootstrap
app = dash.Dash(
    __name__,
    title="Environmental Health Monitor",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server   # expose Flask server for deployment

COLORS = {
    "bg":       "#f0f2f5",
    "card":     "#ffffff",
    "primary":  "#c0532a",
    "text":     "#1a1a2e",
    "muted":    "#6b7280",
    "border":   "#e5e7eb",
    # Risk level palette
    "risk_moderate":  "#f2b047",
    "risk_high":      "#e67e22",
    "risk_extreme":   "#8e1a1a",
    "risk_good":      "#27ae60",
}
#   "risk_moderate":  "#f2b047",
#     "risk_high":      "#e67e22",
#     "risk_extreme":   "#8e1a1a",
#     "risk_good":      "#27ae60",

CARD_STYLE = {
    "background": COLORS["card"],
    "borderRadius": "12px",
    "padding": "16px",
    "marginBottom": "16px",
    "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
}

# ── AQI category
AQI_BANDS = [
    (50,  "Good",                           "#2ecc71"),
    (100, "Moderate",                           "#f1c40f"),
    (150, "Unhealthy for Sensitive Groups",     "#f39c12"),
    (200, "Unhealthy",                          "#e74c3c"),
    (300, "Very Unhealthy",                     "#8e44ad"),
]

def aqi_color(val):
    if val is None:
        return "#808080"
    for thresh, _, color in AQI_BANDS:
        if val <= thresh:
            return color
    return "#7f1d1d"

def aqi_label(val):
    if val is None:
        return "Unknown"
    for thresh, label, _ in AQI_BANDS:
        if val <= thresh:
            return label
    return "Hazardous"


# Health risk logic

def get_health_risk(temp_f, humidity_pct, aqi_val, primary_pollutant, primary_value):
    temp      = temp_f       if temp_f       is not None else 0
    humidity  = humidity_pct if humidity_pct is not None else 0
    aqi       = aqi_val      if aqi_val      is not None else 0

    # Primary pollutant display string
    pv_str = f"{primary_value:.1f} µg/m³" if primary_value is not None else ""
    pp_display = f"{primary_pollutant} {pv_str}".strip() if primary_pollutant else "—"

    # ── Extreme Danger
    if temp > 95 and (humidity > 60 or aqi > 150) or aqi > 300:
        return {
            "level":      "Extreme Danger",
            "color":      COLORS["risk_extreme"],
            "icon":       "🚨",
            "conditions": f"Temp: {temp:.0f}°F  |  Humidity: {humidity:.0f}%  |  AQI: {aqi:.0f}",
            "pollutant":  f"Primary Pollutant: {pp_display}",
            "advisory":   (
                "Total ban on outdoor labor and exercise. Standard fans are insufficient — "
                "air-conditioned spaces with air purifiers are required. "
                "Prolonged outdoor exposure risks heat stroke and severe respiratory distress."
            ),
            "bullets": [
                "🚫  Do NOT work or exercise outdoors under any circumstances.",
                "❄️  Stay in AC spaces; use air purifiers rated for PM2.5/PM10.",
                "💧  Drink at least 1 litre of water per hour if movement is unavoidable.",
                "🏥  Check on elderly, children, and those with heart or lung conditions every hour.",
                "😷  N95/KN95 masks are mandatory for any unavoidable outdoor exposure.",
            ],
        }

    # ── High Risk
    if (91 <= temp <= 95 and humidity > 60) or (101 <= aqi <= 150):
        return {
            "level":      "High Risk",
            "color":      COLORS["risk_high"],
            "icon":       "⚠️",
            "conditions": f"Temp: {temp:.0f}°F  |  Humidity: {humidity:.0f}%  |  AQI: {aqi:.0f}",
            "pollutant":  f"Primary Pollutant: {pp_display}",
            "advisory":   (
                "Vulnerable groups — elderly, children, pregnant women, and those with "
                "respiratory or cardiovascular conditions — must stay indoors in "
                "air-filtered, cooled spaces."
            ),
            "bullets": [
                "⏰  Limit all outdoor activity to before 8 AM or after sunset.",
                "🏠  Sensitive groups should remain indoors with windows closed.",
                "💨  Run HVAC on recirculation; avoid bringing outdoor air inside.",
                "💧  Stay well-hydrated; avoid caffeine and alcohol.",
                "😷  Wear a mask (N95/KN95) if outdoor exposure is unavoidable.",
            ],
        }

    # ── Moderate Risk
    if (85 <= temp <= 90 and humidity > 70) or (51 <= aqi <= 100):
        return {
            "level":      "Moderate Risk",
            "color":      COLORS["risk_moderate"],
            "icon":       "🔶",
            "conditions": f"Temp: {temp:.0f}°F  |  Humidity: {humidity:.0f}%  |  AQI: {aqi:.0f}",
            "pollutant":  f"Primary Pollutant: {pp_display}",
            "advisory":   (
                "Sensitive groups should limit heavy outdoor exertion to early morning "
                "when temperatures and pollutant levels are typically lower."
            ),
            "bullets": [
                "🌅  Schedule vigorous activity before 9 AM to avoid peak heat and AQI.",
                "💧  Drink water regularly.",
                "🧴  Apply sunscreen; wear light, breathable clothing.",
                "👁️  Monitor air quality updates throughout the day.",
                "🏠  Consider moving prolonged outdoor activities indoors.",
            ],
        }

    # ── No significant risk
    return {
        "level":      "Low Risk",
        "color":      COLORS["risk_good"],
        "icon":       "✅",
        "conditions": f"Temp: {temp:.0f}°F  |  Humidity: {humidity:.0f}%  |  AQI: {aqi:.0f}",
        "pollutant":  f"Primary Pollutant: {pp_display}",
        "advisory":   (
            "Conditions are generally safe for outdoor activities. "
            "Continue to stay hydrated and monitor conditions if planning extended exertion."
        ),
        "bullets": [
            "🌿  Air quality and temperature are within safe ranges.",
            "💧  Maintain normal hydration habits.",
            "🏃  Outdoor exercise is suitable for all groups.",
            "📱  Check back later for any changing conditions.",
        ],
    }


# Stat card
def stat_card(label, value, unit=""):
    return html.Div([
        html.Div(label, style={"fontSize": "12px", "color": COLORS["muted"], "marginBottom": "4px"}),
        html.Div([
            html.Span(str(value) if value is not None else "—",
                      style={"fontSize": "20px", "fontWeight": "700"}),
            html.Span(f" {unit}", style={"fontSize": "13px", "color": COLORS["muted"]}),
        ]),
    ], style={"flex": "1", "minWidth": "80px", "padding": "12px 16px",
              "background": COLORS["bg"], "borderRadius": "8px", "textAlign": "center"})


# Hourly AQI bar chart
def make_aqi_chart(hourly_trend):
    if not hourly_trend:
        return go.Figure()

    times  = [h.get("time", "")[-8:-3] for h in hourly_trend]   # HH:MM
    values = [h.get("aqi") for h in hourly_trend]
    colors = [aqi_color(v) for v in values]

    fig = go.Figure(go.Bar(
        x=times, y=values,
        marker_color=colors,
        hovertemplate="%{x}<br>AQI: %{y}<extra></extra>",
    ))
    fig.update_layout(
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=32, r=12, t=12, b=48),
        xaxis=dict(tickangle=-45, showgrid=False, tickfont=dict(size=10)),
        yaxis=dict(gridcolor="#f0f0f0", title="US AQI"),
        height=220,
    )
    return fig


# 7-day forecast row
def make_forecast_row(daily_forecast):
    if not daily_forecast:
        return html.Div("No forecast data", style={"color": COLORS["muted"]})

    days = []
    for d in daily_forecast[:7]:
        date_str = d.get("date", "")
        try:
            label = datetime.date.fromisoformat(date_str).strftime("%a")
        except Exception:
            label = date_str
        hi    = d.get("high_f")
        lo    = d.get("low_f")
        emoji = d.get("emoji", "")
        days.append(html.Div([
            html.Div(label, style={"fontWeight": "600", "fontSize": "13px"}),
            html.Div(emoji, style={"fontSize": "24px", "margin": "4px 0"}),
            html.Div(f"{hi:.0f}°" if hi else "—",
                     style={"fontWeight": "700", "fontSize": "14px"}),
            html.Div(f"{lo:.0f}°" if lo else "—",
                     style={"color": COLORS["muted"], "fontSize": "12px"}),
        ], style={"flex": "1", "textAlign": "center", "padding": "8px 4px",
                  "background": COLORS["bg"], "borderRadius": "8px", "margin": "0 4px"}))
    return html.Div(days, style={"display": "flex", "gap": "0"})


# ── Layout
app.layout = html.Div(
    style={"minHeight": "100vh", "background": COLORS["bg"],
           "fontFamily": "'Inter', 'Segoe UI', sans-serif",
           "color": COLORS["text"]},
    children=[

        # Header
        html.Div([
            html.H1("Environmental Health Monitor",
                    style={"textAlign": "center", "fontSize": "28px",
                           "fontWeight": "800", "margin": "0 0 4px"}),
        ], style={"padding": "16px 0 16px", "background": COLORS["card"],
                  "borderBottom": f"1px solid {COLORS['border']}"}),

        # Main container
        html.Div(
            style={"maxWidth": "980px", "margin": "0 auto", "padding": "16px"},
            children=[

                # Search bar
                html.Div([
                    html.Label("Location",
                               style={"fontWeight": "600", "marginRight": "12px",
                                      "fontSize": "14px", "alignSelf": "center"}),
                    dcc.Input(
                        id="location-input",
                        type="text",
                        placeholder="Type a city…",
                        debounce=False,
                        value="",
                        n_submit=0,
                        style={"flex": "1", "padding": "4px 14px", "borderRadius": "8px",
                               "border": f"1.5px solid {COLORS['primary']}",
                               "fontSize": "15px", "outline": "none"},
                    ),
                    html.Button("Search", id="search-btn",
                                style={"padding": "6px 22px", "borderRadius": "8px",
                                       "background": COLORS["primary"], "color": "white",
                                       "border": "none", "fontWeight": "700",
                                       "fontSize": "15px", "cursor": "pointer",
                                       "marginLeft": "8px"}),
                    html.Button("🏠 Louisville", id="home-btn",
                                style={"padding": "6px 16px", "borderRadius": "8px",
                                       "background": "white", "color": "orange",
                                       "border": "none", "fontWeight": "600",
                                       "fontSize": "14px", "cursor": "pointer",
                                       "marginLeft": "8px"}),
                ], style={"display": "flex", "alignItems": "center", **CARD_STYLE}),

                # Tabs
                dcc.Tabs(id="tabs", value="today", children=[
                    dcc.Tab(label="Today", value="today",
                            style={"padding": "10px 24px"},
                            selected_style={"padding": "10px 24px",
                                            "borderBottom": f"3px solid {COLORS['primary']}",
                                            "color": COLORS["primary"], "fontWeight": "700"}),
                    dcc.Tab(label="This Week", value="week",
                            style={"padding": "10px 24px"},
                            selected_style={"padding": "10px 24px",
                                            "borderBottom": f"3px solid {COLORS['primary']}",
                                            "color": COLORS["primary"], "fontWeight": "700"}),
                ], style={"marginBottom": "16px", "background": COLORS["card"],
                          "borderRadius": "12px", "border": f"1px solid {COLORS['border']}",
                          "overflow": "hidden"}),

                # Loading + content
                dcc.Loading(id="loading", type="circle", color=COLORS["primary"],
                            children=[html.Div(id="main-content")]),

                # Status / error banner
                html.Div(id="status-bar",
                         style={"color": "#e74c3c", "fontSize": "13px",
                                "minHeight": "20px", "marginTop": "4px"}),
            ]
        ),

        # Hidden stores
        dcc.Store(id="data-store"),
        dcc.Interval(id="init-trigger", interval=500, max_intervals=1),
    ]
)


# ── Callbacks

@app.callback(
    Output("data-store", "data"),
    Output("status-bar", "children"),
    Input("search-btn", "n_clicks"),
    Input("home-btn", "n_clicks"),
    Input("location-input", "n_submit"),
    State("location-input", "value"),
    prevent_initial_call=False,
)
def fetch_data(search_clicks, home_clicks, n_submit, location_input):
    ctx = callback_context

    # Initial page load — always serve Louisville
    if not ctx.triggered:
        try:
            weather = get_latest_weather("Louisville, KY")
            air     = get_latest_air("Louisville, KY")
            return {"weather": weather, "air": air}, ""
        except Exception as e:
            return no_update, f"Error loading default data: {e}"

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "home-btn":
        location = "Louisville, KY"
    elif trigger_id in ("search-btn", "location-input"):
        location = location_input.strip() if location_input and location_input.strip() else "Louisville, KY"
    else:
        location = "Louisville, KY"

    try:
        result = etl_run(location)
        return result, ""
    except Exception as e:
        traceback.print_exc()
        return no_update, str(e)


@app.callback(
    Output("main-content", "children"),
    Input("data-store", "data"),
    Input("tabs", "value"),
    prevent_initial_call=True,
)
def render_content(data, tab):
    weather = (data or {}).get("weather") or {}
    air     = (data or {}).get("air") or {}

    if not data or not weather:
        return html.Div("Fetching data…",
                        style={"color": COLORS["muted"], "padding": "40px",
                               "textAlign": "center"})

    # Timestamp + location header
    fetched_raw = weather.get("fetched_at", "")
    try:
        dt       = datetime.datetime.fromisoformat(fetched_raw)
        time_str = dt.strftime("%I:%M %p")
        date_str = dt.strftime("%A, %B %d %Y")
    except Exception:
        time_str = fetched_raw
        date_str = ""

    loc_display = weather.get("location", "")
    parts       = loc_display.split(",")
    loc_short   = ", ".join(p.strip() for p in parts[:3]) if parts else loc_display

    header = html.Div([
        html.Div([
            html.Span(f"📍 {loc_short}",
                      style={"fontWeight": "700", "fontSize": "16px"}),
            html.Span(f" · {date_str}",
                      style={"color": COLORS["muted"], "fontSize": "13px",
                             "marginLeft": "8px"}),
        ]),
        html.Div(time_str, style={"color": COLORS["muted"], "fontSize": "13px"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": "16px"})

    # ── TODAY view
    if tab == "today":

        temp    = weather.get("temperature_f")
        feels   = weather.get("feels_like_f")
        emoji   = weather.get("emoji", "")
        cond    = weather.get("condition", "")
        hum     = weather.get("humidity_pct")

        # Current weather card
        weather_card = html.Div([
            html.Div("Current Weather",
                     style={"fontWeight": "600", "fontSize": "13px",
                            "color": COLORS["muted"], "marginBottom": "8px"}),
            html.Div([
                html.Div([
                    html.Span(f"{temp:.0f}" if temp else "—",
                              style={"fontSize": "36px", "fontWeight": "800",
                                     "lineHeight": "1"}),
                    html.Span("°F", style={"fontSize": "16px", "verticalAlign": "super",
                                          "color": COLORS["muted"]}),
                ], style={"display": "inline-flex", "alignItems": "flex-start",
                          "marginRight": "24px"}),
                html.Div([
                    html.Div(f"{emoji} {cond}",
                             style={"fontWeight": "700", "fontSize": "20px"}),
                    html.Div(f"Feels like  {feels:.0f} °F" if feels else "",
                             style={"color": COLORS["muted"], "fontSize": "14px",
                                    "marginTop": "4px"}),
                ]),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style=CARD_STYLE)

        # Stats row
        stats_row = html.Div([
            stat_card("Air Quality",
                      air.get("us_aqi"), "AQI"),
            stat_card("Wind",
                      f'{weather.get("wind_speed_mph", "—"):.0f}'
                      if weather.get("wind_speed_mph") is not None else "—", "mph"),
            stat_card("Humidity",
                      f'{weather.get("humidity_pct", "—"):.0f}'
                      if weather.get("humidity_pct") is not None else "—", "%"),
            stat_card("Visibility",  weather.get("visibility_mi"),  "mi"),
            stat_card("Pressure",    weather.get("pressure_inhg"),  "in"),
            stat_card("Dew point",   weather.get("dew_point_f"),    "°F"),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", **CARD_STYLE})

        # Air data
        aqi_val       = air.get("us_aqi")
        primary_p     = air.get("primary_pollutant", "—")
        primary_v     = air.get("primary_value")
        hourly_trend  = air.get("hourly_trend", [])
        trend_48      = hourly_trend[:48]

        pollutants_list = [
            ("O₃",    air.get("ozone_ppb"),  "ppb"),
            ("PM2.5", air.get("pm2_5"),      "µg/m³"),
            ("PM10",  air.get("pm10"),       "µg/m³"),
            ("NO₂",   air.get("no2_ppb"),    "ppb"),
            ("CO",    air.get("co_ppb"),     "ppb"),
            ("SO₂",   air.get("so2_ppb"),    "ppb"),
        ]

        # Evaluate combined health risk
        risk = get_health_risk(temp, hum, aqi_val, primary_p, primary_v)

        # ── Health risk banner (top of card)
        risk_banner = html.Div([
            html.Div([
                html.Span(risk["icon"],
                          style={"fontSize": "32px", "marginRight": "14px",
                                 "lineHeight": "1"}),
                html.Div([
                    html.Div(risk["level"],
                             style={"fontWeight": "800", "fontSize": "18px",
                                    "marginBottom": "2px"}),
                    html.Div(risk["conditions"],
                             style={"fontSize": "13px", "opacity": "0.9",
                                    "letterSpacing": "0.3px"}),
                ]),
            ], style={"display": "flex", "alignItems": "center",
                      "marginBottom": "10px"}),

            # Primary pollutant highlight
            html.Div([
                html.Span("🏭  ", style={"fontSize": "15px"}),
                html.Span(risk["pollutant"],
                          style={"fontWeight": "700", "fontSize": "14px"}),
                html.Span(f"   ·   AQI: {aqi_val}" if aqi_val is not None else "",
                          style={"fontWeight": "700", "fontSize": "14px",
                                 "marginLeft": "4px"}),
            ], style={"background": risk["color"] +"44", "border": risk["color"] +"99", "borderRadius": "8px",
                      "padding": "7px 14px", "marginBottom": "12px",
                      "fontSize": "14px"}),

            # Advisory text
            html.Div(risk["advisory"],
                     style={"fontSize": "14px", "lineHeight": "1.6",
                            "marginBottom": "12px", "opacity": "0.95"}),

            # Bullet recommendations
            html.Div([
                html.Div(b, style={"fontSize": "13px", "lineHeight": "1.5",
                                   "padding": "5px 0",
                                   "borderBottom": "1px solid rgba(255,255,255,0.15)"})
                for b in risk["bullets"]
            ], style={"borderTop": "1px solid rgba(255,255,255,0.2)",
                      "paddingTop": "8px"}),

        ], style={"background": risk["color"] +"22", "color": risk["color"],
                  "borderRadius": "10px", "padding": "16px 20px",
                  "marginBottom": "16px"})

        # ── Pollutants compact grid (inside the same card, below banner)
        pollutant_items = []
        for name, val, unit in pollutants_list:
            val_str = f"{val:.1f} {unit}" if val is not None else "—"
            pollutant_items.append(
                html.Div([
                    html.Div(name,
                             style={"color": COLORS["muted"],
                                    "fontSize": "11px", "marginBottom": "2px"}),
                    html.Div(val_str,
                             style={"fontWeight": "700", "fontSize": "13px",
                                    "color": COLORS["text"]}),
                ], style={"flex": "1", "minWidth": "90px",
                          "background": COLORS["bg"],
                          "borderRadius": "8px", "padding": "8px 10px",
                          "textAlign": "center"})
            )

        pollutant_grid = html.Div([
            html.Div("Current Pollutant Levels",
                     style={"fontWeight": "700", "fontSize": "13px",
                            "color": COLORS["muted"], "marginBottom": "10px",
                            "opacity": "1"}),
            html.Div(pollutant_items,
                     style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}),
        ], style={"marginBottom": "16px"})

        # ── Full-width Health Risk + chart card
        full_card = html.Div([
            risk_banner,
            pollutant_grid,

            # Hourly AQI chart section
            html.Div("Hourly Air Quality Index",
                     style={"fontWeight": "700", "marginBottom": "8px",
                            "color": COLORS["text"]}),
            dcc.Graph(
                figure=make_aqi_chart(trend_48),
                config={"displayModeBar": False},
                style={"height": "220px", "borderRadius": "8px",
                       "overflow": "hidden"},
            ),

            # AQI legend
            html.Div([
                html.Span([
                    html.Span("●", style={"color": c, "marginRight": "4px"}),
                    html.Span(lbl, style={"fontSize": "11px", "marginRight": "12px",
                                         "color": COLORS["muted"]}),
                ]) for _, lbl, c in AQI_BANDS + [(400, "Hazardous", "#7f1d1d")]
            ], style={"marginTop": "8px"}),

        ], style={
            "background": COLORS["card"],
            "borderRadius": "12px",
            "padding": "20px",
            "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
            "marginBottom": "8px",
        })

        return html.Div([header, weather_card, stats_row, full_card])

    # ── THIS WEEK view
    else:
        daily = weather.get("daily_forecast", [])
        forecast_card = html.Div([
            html.Div("7-Day Forecast",
                     style={"fontWeight": "700", "marginBottom": "16px"}),
            make_forecast_row(daily),
        ], style=CARD_STYLE)

        aqi_val  = air.get("us_aqi")

        hourly = air.get("hourly_trend", [])
        from collections import defaultdict
        daily_aqi: dict = defaultdict(list)
        for h in hourly:
            date = h.get("time", "")[:10]
            if h.get("aqi") is not None:
                daily_aqi[date].append(h["aqi"])
        daily_aqi_avg = {d: sum(v) / len(v) for d, v in daily_aqi.items() if v}

        dates  = sorted(daily_aqi_avg.keys())[:7]
        avgs   = [daily_aqi_avg[d] for d in dates]
        clrs   = [aqi_color(v) for v in avgs]
        labels = []
        for d in dates:
            try:
                labels.append(datetime.date.fromisoformat(d).strftime("%a %b %d"))
            except Exception:
                labels.append(d)

        week_fig = go.Figure(go.Bar(
            x=labels, y=avgs, marker_color=clrs,
            hovertemplate="%{x}<br>Avg AQI: %{y:.0f}<extra></extra>",
        ))
        week_fig.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=32, r=12, t=12, b=60),
            xaxis=dict(tickangle=-20, showgrid=False),
            yaxis=dict(gridcolor="#f0f0f0", title="Daily Avg AQI"),
            height=260,
        )
        aqi_chart_card = html.Div([
            html.Div("7-Day Average Air Quality Index",
                     style={"fontWeight": "700", "marginBottom": "8px"}),
            dcc.Graph(figure=week_fig, config={"displayModeBar": False}),
        ], style=CARD_STYLE)

        return html.Div([header, forecast_card, aqi_chart_card])


if __name__ == "__main__":
    app.run(debug=True, port=8050)