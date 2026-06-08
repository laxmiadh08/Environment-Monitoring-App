import json
import datetime
import traceback

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import plotly.graph_objects as go

from main import run as etl_run  # full ETL (geocode → fetch → transform → load)
from load import get_latest_weather, get_latest_air, get_hourly_aqi_trend

#App bootstrap
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
}

CARD_STYLE = {
    "background": COLORS["card"],
    "borderRadius": "12px",
    "padding": "8px",
    "marginBottom": "8px",
    "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
}

# ── AQI category
AQI_BANDS = [
    (50,  "Good",                           "#2ecc71"),
    (100, "Moderate",                       "#f1c40f"),
    (150, "Unhealthy for Sensitive Groups", "#f39c12"),
    (200, "Unhealthy",                      "#e74c3c"),
    (300, "Very Unhealthy",                 "#8e44ad"),
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

# Stat card 
def stat_card(label, value, unit=""):
    return html.Div([
        html.Div(label, style={"fontSize": "12px", "color": COLORS["muted"], "marginBottom": "4px"}),
        html.Div([
            html.Span(str(value) if value is not None else "—", style={"fontSize": "20px", "fontWeight": "700"}),
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

#  7-day forecast row
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
        hi = d.get("high_f")
        lo = d.get("low_f")
        emoji = d.get("emoji", "")
        days.append(html.Div([
            html.Div(label, style={"fontWeight": "600", "fontSize": "13px"}),
            html.Div(emoji, style={"fontSize": "24px", "margin": "4px 0"}),
            html.Div(f"{hi:.0f}°" if hi else "—", style={"fontWeight": "700", "fontSize": "14px"}),
            html.Div(f"{lo:.0f}°" if lo else "—", style={"color": COLORS["muted"], "fontSize": "12px"}),
        ], style={"flex": "1", "textAlign": "center", "padding": "8px 4px",
                  "background": COLORS["bg"], "borderRadius": "8px", "margin": "0 4px"}))
    return html.Div(days, style={"display": "flex", "gap": "0"})


# Layout

app.layout = html.Div(style={"minHeight": "100vh", "background": COLORS["bg"],
                              "fontFamily": "'Inter', 'Segoe UI', sans-serif",
                              "color": COLORS["text"]}, children=[

    # Header
    html.Div([
        html.H1("Environmental Health Monitor",
                style={"textAlign": "center", "fontSize": "28px",
                       "fontWeight": "800", "margin": "0 0 4px"}),
       
    ], style={"padding": "16px 0 16px", "background": COLORS["card"],
              "borderBottom": f"1px solid {COLORS['border']}"}),

    # Main container
    html.Div(style={"maxWidth": "980px", "margin": "0 auto", "padding": "24px 16px"}, children=[

        # ── Search bar 
        html.Div([
            html.Label("Location", style={"fontWeight": "600", "marginRight": "12px",
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

        #  Tabs: Today & This Week 
        dcc.Tabs(id="tabs", value="today", children=[
            dcc.Tab(label="Today",     value="today",
                    style={"padding": "10px 24px"}, selected_style={
                        "padding": "10px 24px", "borderBottom": f"3px solid {COLORS['primary']}",
                        "color": COLORS["primary"], "fontWeight": "700"}),
            dcc.Tab(label="This Week", value="week",
                    style={"padding": "10px 24px"}, selected_style={
                        "padding": "10px 24px", "borderBottom": f"3px solid {COLORS['primary']}",
                        "color": COLORS["primary"], "fontWeight": "700"}),
        ], style={"marginBottom": "16px", "background": COLORS["card"],
                  "borderRadius": "12px", "border": f"1px solid {COLORS['border']}",
                  "overflow": "hidden"}),

        # ── Loading + content
        dcc.Loading(id="loading", type="circle", color=COLORS["primary"], children=[
            html.Div(id="main-content"),
        ]),

        # Status / error banner
        html.Div(id="status-bar", style={"color": "#e74c3c", "fontSize": "13px",
                                          "minHeight": "20px", "marginTop": "4px"}),
    ]),

    # Hidden store for data
    dcc.Store(id="data-store"),
    # Auto-load Louisville on page open
    dcc.Interval(id="init-trigger", interval=500, max_intervals=1),
])



# Callbacks



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

    # Initial page load
    if not ctx.triggered:
        try:
            weather = get_latest_weather("Louisville, KY")
            air = get_latest_air("Louisville, KY")

            return {
                "weather": weather,
                "air": air
            }, ""

        except Exception as e:
            return no_update, f"Error loading default data: {e}"

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    location = "Louisville, KY"

    if trigger_id in ("search-btn", "location-input"):
        if location_input and location_input.strip():
            location = location_input.strip()

    try:
        # Run ETL only when user searches
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
    if not data:
        return html.Div("Fetching data…", style={"color": COLORS["muted"], "padding": "40px",
                                                   "textAlign": "center"})

    weather = data.get("weather", {})
    air     = data.get("air", {})

    # ── Timestamp + location header ───────────────────────────────────────
    fetched_raw = weather.get("fetched_at", "")
    try:
        dt = datetime.datetime.fromisoformat(fetched_raw)
        time_str = dt.strftime("%I:%M %p")
        date_str = dt.strftime("%A, %B %d %Y")
    except Exception:
        time_str = fetched_raw
        date_str = ""

    loc_display = weather.get("location", "")
    # shorten to city, state/country (first 2 parts)
    parts = loc_display.split(",")
    loc_short = ", ".join(p.strip() for p in parts[:3]) if parts else loc_display

    header = html.Div([
        html.Div([
            html.Span(f"📍 {loc_short}", style={"fontWeight": "700", "fontSize": "16px"}),
            html.Span(f" · {date_str}", style={"color": COLORS["muted"], "fontSize": "13px",
                                                 "marginLeft": "8px"}),
        ]),
        html.Div(time_str, style={"color": COLORS["muted"], "fontSize": "13px"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "marginBottom": "16px"})

    # ── TODAY view
    if tab == "today":
        # Current weather card
        temp     = weather.get("temperature_f")
        feels    = weather.get("feels_like_f")
        emoji    = weather.get("emoji", "")
        cond     = weather.get("condition", "")

        weather_card = html.Div([
            html.Div("Current Weather", style={"fontWeight": "600", "fontSize": "13px",
                                                "color": COLORS["muted"], "marginBottom": "8px"}),
            html.Div([
                html.Div([
                    html.Span(f"{temp:.0f}" if temp else "—",
                              style={"fontSize": "36px", "fontWeight": "800", "lineHeight": "1"}),
                    html.Span("°F", style={"fontSize": "16px", "verticalAlign": "super",
                                           "color": COLORS["muted"]}),
                ], style={"display": "inline-flex", "alignItems": "flex-start", "marginRight": "24px"}),
                html.Div([
                    html.Div(f"{emoji} {cond}", style={"fontWeight": "700", "fontSize": "20px"}),
                    html.Div(f"Feels like  {feels:.0f} °F" if feels else "",
                             style={"color": COLORS["muted"], "fontSize": "14px", "marginTop": "4px"}),
                ]),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style=CARD_STYLE)

        # Stats row
        stats_row = html.Div([
            stat_card("Air Quality",  air.get("us_aqi"), "AQI"),
            stat_card("Wind",         f'{weather.get("wind_speed_mph", "—"):.0f}' if weather.get("wind_speed_mph") is not None else "—", "mph"),
            stat_card("Humidity",     f'{weather.get("humidity_pct", "—"):.0f}' if weather.get("humidity_pct") is not None else "—", "%"),
            stat_card("Visibility",   weather.get("visibility_mi"), "mi"),
            stat_card("Pressure",     weather.get("pressure_inhg"), "in"),
            stat_card("Dew point",    weather.get("dew_point_f"), "°F"),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", **CARD_STYLE})

        # ── Bottom 2-col: pollutants | health risks + chart
        aqi_val  = air.get("us_aqi")
        category = air.get("aqi_category", "Unknown")
        color    = air.get("aqi_color", "#808080")
        primary_p = air.get("primary_pollutant", "—")
        primary_v = air.get("primary_value")

        # Trend data
        hourly_trend = air.get("hourly_trend", [])
        # take 48 hours centred on now (first 48 entries)
        trend_48 = hourly_trend[:48]

        pollutants_list = [
            ("O₃",  air.get("ozone_ppb"),        "ppb"),
            ("PM2.5", air.get("pm2_5"),           "µg/m³"),
            ("PM10", air.get("pm10"),             "µg/m³"),
            ("NO₂", air.get("no2_ppb"),           "ppb"),
            ("CO",   air.get("co_ppb"),            "ppb"),
            ("SO₂",  air.get("so2_ppb"),          "ppb"),
        ]

        primary_v_str = f"{primary_v:.1f} µg/m³" if primary_v is not None else ""

        pollutant_rows = []
        for name, val, unit in pollutants_list:
            val_str = f"{val:.1f} {unit}" if val is not None else "—"
            pollutant_rows.append(
                html.Div([
                    html.Div(name, style={"color": COLORS["muted"], "fontSize": "12px",
                                         "width": "60px", "flexShrink": "0"}),
                    html.Div(val_str, style={"fontWeight": "600", "fontSize": "13px"}),
                ], style={"display": "flex", "alignItems": "center",
                          "padding": "6px 0", "borderBottom": f"1px solid {COLORS['border']}"})
            )

        left_col = html.Div([
            html.Div("Air Quality Index", style={"fontWeight": "700", "marginBottom": "12px"}),
            html.Div("Current Conditions", style={"color": COLORS["muted"], "fontSize": "13px",
                                                  "marginBottom": "6px"}),
            html.P(
                f"Air quality is {category.lower()}. "
                f"Primary pollutant: {primary_p} {primary_v_str}.",
                style={"fontSize": "13px", "marginBottom": "12px", "lineHeight": "1.5"}
            ),
            html.Div(f"Primary Pollutant: {primary_p} {primary_v_str}",
                     style={"fontWeight": "700", "fontSize": "13px", "marginBottom": "12px"}),
            html.Div(pollutant_rows),
        ], style={"flex": "1", "marginRight": "16px", **CARD_STYLE})

        right_col = html.Div([
            # Health risk banner
            html.Div([
                html.Div("⚠️", style={"fontSize": "28px", "marginRight": "12px"}),
                html.Div([
                    html.Div("Health Risks", style={"fontWeight": "700", "marginBottom": "4px"}),
                    html.Div(
                        f"Air quality is {category.lower()}. "
                        f"Primary pollutant: {primary_p}"
                        + (f" {primary_v:.1f} µg/m³" if primary_v is not None else "") + ".",
                        style={"fontSize": "13px"}
                    ),
                ]),
            ], style={"display": "flex", "alignItems": "center", "padding": "8px",
                      "borderRadius": "10px", "background": color, "color": "white",
                      "marginBottom": "16px", "opacity": "0.92"}),

            # Hourly AQI chart
            html.Div("Hourly Air Quality Index",
                     style={"fontWeight": "700", "marginBottom": "8px"}),
            dcc.Graph(figure=make_aqi_chart(trend_48),
                      config={"displayModeBar": False},
                      style={"height": "220px"}),

            # AQI legend
            html.Div([
                html.Span([
                    html.Span("●", style={"color": c, "marginRight": "4px"}),
                    html.Span(lbl, style={"fontSize": "11px", "marginRight": "12px"}),
                ]) for _, lbl, c in AQI_BANDS + [(400, "Hazardous", "#7f1d1d")]
            ], style={"marginTop": "8px"}),
        ], style={"flex": "1.5", **CARD_STYLE})

        bottom = html.Div([left_col, right_col],
                          style={"display": "flex", "gap": "0", "alignItems": "flex-start"})

        return html.Div([header, weather_card, stats_row, bottom])

    # THIS WEEK view 
    else:
        daily = weather.get("daily_forecast", [])
        forecast_card = html.Div([
            html.Div("7-Day Forecast", style={"fontWeight": "700", "marginBottom": "16px"}),
            make_forecast_row(daily),
        ], style=CARD_STYLE)

        # Weekly AQI summary (repeat today's hourly stretched)
        aqi_val  = air.get("us_aqi")
        category = air.get("aqi_category", "Unknown")
        color    = air.get("aqi_color", "#808080")

        # Build a simple daily-avg AQI bar from hourly trend grouped by date
        hourly = air.get("hourly_trend", [])
        from collections import defaultdict
        daily_aqi: dict = defaultdict(list)
        for h in hourly:
            date = h.get("time", "")[:10]
            if h.get("aqi") is not None:
                daily_aqi[date].append(h["aqi"])
        daily_aqi_avg = {d: sum(v)/len(v) for d, v in daily_aqi.items() if v}

        dates  = sorted(daily_aqi_avg.keys())[:7]
        avgs   = [daily_aqi_avg[d] for d in dates]
        clrs   = [aqi_color(v) for v in avgs]
        labels = []
        for d in dates:
            try:
                labels.append(datetime.date.fromisoformat(d).strftime("%a %b %d"))
            except Exception:
                labels.append(d)

        week_fig = go.Figure(go.Bar(x=labels, y=avgs, marker_color=clrs,
                                    hovertemplate="%{x}<br>Avg AQI: %{y:.0f}<extra></extra>"))
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

        health_card = html.Div([
            html.Div([
                html.Div("⚠️", style={"fontSize": "28px", "marginRight": "12px"}),
                html.Div([
                    html.Div("Overall Health Risk This Week",
                             style={"fontWeight": "700", "marginBottom": "4px"}),
                    html.Div(f"Current AQI: {aqi_val} — {category}. "
                             "Monitor conditions if you have respiratory sensitivities.",
                             style={"fontSize": "13px"}),
                ]),
            ], style={"display": "flex", "alignItems": "center", "padding": "14px 16px",
                      "borderRadius": "10px", "background": color, "color": "white"}),
        ], style=CARD_STYLE)

        return html.Div([header, forecast_card, aqi_chart_card, health_card])



if __name__ == "__main__":
    app.run(debug=True, port=8050)