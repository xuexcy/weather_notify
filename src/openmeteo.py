"""Open-Meteo 兜底数据源：无 key、无需注册，只按经纬度查询。

用途：首页在还没配置和风凭据时也能显示真实天气，避免第一次打开是空白页。
代价：没有中国官方灾害预警（预警必须走和风），也没有生活指数。
返回结构与和风路径保持同构，便于上层统一渲染。
"""
import logging
from datetime import date

import requests

log = logging.getLogger("weather-notify.openmeteo")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# WMO 天气代码 → (中文描述, 白天图标, 夜间图标)
WMO = {
    0: ("晴", "☀️", "🌙"),
    1: ("晴间多云", "🌤️", "🌙"),
    2: ("多云", "⛅", "☁️"),
    3: ("阴", "☁️", "☁️"),
    45: ("雾", "🌫️", "🌫️"),
    48: ("雾凇", "🌫️", "🌫️"),
    51: ("小毛毛雨", "🌦️", "🌦️"),
    53: ("毛毛雨", "🌦️", "🌦️"),
    55: ("大毛毛雨", "🌧️", "🌧️"),
    56: ("冻毛毛雨", "🌧️", "🌧️"),
    57: ("强冻毛毛雨", "🌧️", "🌧️"),
    61: ("小雨", "🌦️", "🌦️"),
    63: ("中雨", "🌧️", "🌧️"),
    65: ("大雨", "🌧️", "🌧️"),
    66: ("冻雨", "🌧️", "🌧️"),
    67: ("强冻雨", "🌧️", "🌧️"),
    71: ("小雪", "🌨️", "🌨️"),
    73: ("中雪", "🌨️", "🌨️"),
    75: ("大雪", "❄️", "❄️"),
    77: ("米雪", "🌨️", "🌨️"),
    80: ("阵雨", "🌦️", "🌦️"),
    81: ("强阵雨", "🌧️", "🌧️"),
    82: ("暴雨", "⛈️", "⛈️"),
    85: ("阵雪", "🌨️", "🌨️"),
    86: ("强阵雪", "❄️", "❄️"),
    95: ("雷阵雨", "⛈️", "⛈️"),
    96: ("雷阵雨伴冰雹", "⛈️", "⛈️"),
    99: ("强雷阵雨伴冰雹", "⛈️", "⛈️"),
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 蒲福风级上限（km/h）：索引 0 对应 1 级上限 5，依次递增；<1 km/h 为 0 级
_BEAUFORT = (5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117)


def _describe(code) -> tuple:
    return WMO.get(int(code or 0), ("未知", "❓", "❓"))


def _wind_scale(kmh) -> str:
    try:
        v = float(kmh)
    except (TypeError, ValueError):
        return ""
    if v < 1:
        return "0"
    for level, upper in enumerate(_BEAUFORT, start=1):
        if v <= upper:
            return str(level)
    return "12"


def _wind_dir(deg) -> str:
    try:
        d = float(deg) % 360
    except (TypeError, ValueError):
        return ""
    names = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    return names[int((d + 22.5) // 45) % 8] + "风"


def _aqi_category(aqi) -> str:
    """美标 AQI 等级（Open-Meteo 提供的是 US AQI）。"""
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return ""
    for upper, label in ((50, "优"), (100, "良"), (150, "轻度污染"),
                         (200, "中度污染"), (300, "重度污染")):
        if v <= upper:
            return label
    return "严重污染"


def _air(lat: float, lon: float, timeout: float) -> dict | None:
    try:
        resp = requests.get(AIR_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "us_aqi,pm2_5,pm10",
            "timezone": "Asia/Shanghai",
        }, timeout=timeout)
        resp.raise_for_status()
        cur = (resp.json() or {}).get("current") or {}
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo 空气质量获取失败: %s", e)
        return None
    if cur.get("us_aqi") is None:
        return None
    return {
        "aqi": cur.get("us_aqi"),
        "category": _aqi_category(cur.get("us_aqi")),
        "pm2p5": cur.get("pm2_5"),
        "pm10": cur.get("pm10"),
        "std": "US",
    }


def fetch(lat: float, lon: float, hours: int = 6, timeout: float = 8.0,
          days: int = 7) -> dict:
    """按经纬度取天气，返回与和风路径同构的 dict。

    抛 RuntimeError 表示拿不到数据（上层决定提示文案）。
    """
    try:
        forecast_days = min(max(int(days or 7), 1), 16)
    except (TypeError, ValueError):
        forecast_days = 7
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join((
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "is_day", "weather_code", "wind_speed_10m", "wind_direction_10m",
        )),
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max"),
        "timezone": "Asia/Shanghai",
        "forecast_days": forecast_days,
        "wind_speed_unit": "kmh",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(f"Open-Meteo 请求失败: {e}") from e

    cur = data.get("current") or {}
    if cur.get("temperature_2m") is None:
        raise RuntimeError("Open-Meteo 未返回实况数据")

    is_day = bool(cur.get("is_day", 1))
    text, day_icon, night_icon = _describe(cur.get("weather_code"))
    now = {
        "temp": round(float(cur["temperature_2m"])),
        "feels": (round(float(cur["apparent_temperature"]))
                  if cur.get("apparent_temperature") is not None else None),
        "text": text,
        "icon": day_icon if is_day else night_icon,
        "humidity": (round(float(cur["relative_humidity_2m"]))
                     if cur.get("relative_humidity_2m") is not None else None),
        "wind_dir": _wind_dir(cur.get("wind_direction_10m")),
        "wind_scale": _wind_scale(cur.get("wind_speed_10m")),
        "obs_time": str(cur.get("time") or "").replace("T", " "),
    }

    # 逐小时：从当前时刻往后取
    hourly_raw = data.get("hourly") or {}
    times = hourly_raw.get("time") or []
    now_str = str(cur.get("time") or "")
    start = 0
    for i, t in enumerate(times):
        if t >= now_str:
            start = i
            break
    hourly = []
    for i in range(start, min(start + max(int(hours or 0), 0), len(times))):
        code = (hourly_raw.get("weather_code") or [None] * len(times))[i]
        t_text, t_icon, _ = _describe(code)
        temp = (hourly_raw.get("temperature_2m") or [None] * len(times))[i]
        pop = (hourly_raw.get("precipitation_probability") or [None] * len(times))[i]
        hourly.append({
            "time": times[i][-5:],
            "temp": round(float(temp)) if temp is not None else None,
            "text": t_text,
            "icon": t_icon,
            "pop": pop,
        })

    daily_raw = data.get("daily") or {}
    d_times = daily_raw.get("time") or []
    daily = []
    for i, d in enumerate(d_times):
        code = (daily_raw.get("weather_code") or [None] * len(d_times))[i]
        d_text, d_icon, _ = _describe(code)
        hi = (daily_raw.get("temperature_2m_max") or [None] * len(d_times))[i]
        lo = (daily_raw.get("temperature_2m_min") or [None] * len(d_times))[i]
        pop = (daily_raw.get("precipitation_probability_max")
               or [None] * len(d_times))[i]
        try:
            wd = WEEKDAYS[date.fromisoformat(d).weekday()]
        except (ValueError, TypeError):
            wd = ""
        daily.append({
            "date": d,
            "date_label": d[5:].replace("-", "/") if len(str(d)) >= 10 else d,
            "label": ("今天", "明天", "后天")[i] if i < 3 else wd,
            "week": wd,
            "high": round(float(hi)) if hi is not None else None,
            "low": round(float(lo)) if lo is not None else None,
            "text": d_text,
            "icon": d_icon,
            "pop": pop,
            "aqi": None,
            "aqi_cat": "",
        })

    return {
        "source": "openmeteo",
        "source_label": "Open-Meteo",
        "now": now,
        "hourly": hourly,
        "daily": daily,
        "air": _air(lat, lon, timeout),
        "indices": [],
        "warnings": [],
    }
