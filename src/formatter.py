"""消息格式化：把结构化天气数据组装成可推送文本。

数据来自 main.fetch_weather()，与网页共用同一份结构，不接触各接口原始字段。

两种风格（style）：
    md    企业微信群 markdown —— 带 `##` / `>` / `<font color>`，企微客户端内渲染好看
    text  纯文本 —— 微信侧接收企业微信消息时不会渲染 markdown，只适合纯文本

两种场景（mode）：
    broadcast  日常播报（实况 + 空气 + 预报 + 指数，有预警时置顶）
    alert      仅预警（--warning 模式）

模板里的占位符由配置 message.* 提供，支持区块与标量两类，见下。
"""
import re
from datetime import date, datetime, timedelta, timezone

import config_loader

LEVEL_EMOJI = {
    "蓝色": "🔵", "黄色": "🟡", "橙色": "🟠", "红色": "🔴",
    "白色": "⚪", "未知": "⚪",
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 日期 / 时刻统一按北京时间算，与天气数据（和风、Open-Meteo 都按 Asia/Shanghai）一致，
# 这样即使跑在 UTC 时区的机器上，标题里的日期也不会差一天。
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except Exception:                      # 系统没有 tzdata 时退回固定 +8
    LOCAL_TZ = timezone(timedelta(hours=8))

# 区块占位符 —— 每个都渲染成一段（可能为空）
BLOCK_KEYS = ("warnings", "current", "air", "hourly", "daily", "indices")

# 标量占位符说明（设置页展示给用户）
PLACEHOLDER_DOC = (
    ("{city}", "城市名"),
    ("{icon}", "天气图标"),
    ("{temp}", "当前气温"),
    ("{feels}", "体感温度"),
    ("{text}", "天气现象"),
    ("{wind}", "风向风力"),
    ("{humidity}", "湿度"),
    ("{aqi}", "空气质量指数"),
    ("{aqi_category}", "空气质量等级"),
    ("{date}", "今天日期（11月2日）"),
    ("{weekday}", "星期（周一）"),
    ("{clock}", "当前时刻（19:25，北京时间）"),
    ("{time}", "数据更新时间（由数据源给出）"),
    ("{source}", "数据源名称"),
    ("{alert_type}", "预警类型"),
    ("{alert_level}", "预警等级"),
    ("{alert_title}", "预警标题"),
    ("{alert_status}", "预警状态"),
    ("{warnings}", "预警列表（区块）"),
    ("{current}", "实况（区块）"),
    ("{air}", "空气质量（区块）"),
    ("{hourly}", "逐小时（区块）"),
    ("{daily}", "未来三天（区块）"),
    ("{indices}", "生活指数（区块）"),
)


# ---------------------------------------------------------------- 排版小工具

def _h(text: str, style: str) -> str:
    """区块小标题。"""
    return f"## {text}" if style == "md" else f"【{text}】"


def _line(label: str, value, style: str, color: str = "") -> str:
    """一行「标签：值」。md 风格加引用前缀与着色。"""
    if style == "md":
        v = f'<font color="{color}">{value}</font>' if color else str(value)
        return f"> {label}：{v}"
    return f"{label}：{value}"


def _dash(value) -> str:
    return "-" if value is None or value == "" else str(value)


def _aqi_mark(aqi, category: str = "") -> str:
    """AQI 数值 → 带色点的等级文字（等级优先用接口返回的，避免两套标准打架）。"""
    if category:
        try:
            v = float(aqi)
        except (TypeError, ValueError):
            return category
        # 六档色点与页面 --aqi-1..6 对应：绿 / 黄 / 橙 / 红 / 紫 / 褐红
        dot = "🟢" if v <= 50 else "🟡" if v <= 100 else "🟠" if v <= 150 \
            else "🔴" if v <= 200 else "🟣" if v <= 300 else "🟤"
        return f"{dot} {category}"
    return "-"


def _week_of(iso_date: str) -> str:
    try:
        return WEEKDAYS[date.fromisoformat(iso_date).weekday()]
    except (ValueError, TypeError):
        return ""


def _now() -> datetime:
    """当前北京时间。"""
    return datetime.now(LOCAL_TZ)


def _fmt_date(d: datetime) -> str:
    """11月2日。"""
    return f"{d.month}月{d.day}日"


def _fmt_clock(d: datetime) -> str:
    """19:25。"""
    return d.strftime("%H:%M")


# ---------------------------------------------------------------- 区块渲染

def format_warning(warnings: list, style: str = "md") -> str:
    """天气预警段落。"""
    if not warnings:
        return ""
    lines = [_h("⚠️ 天气预警", style)]
    for w in warnings:
        level = w.get("level", "未知")
        emoji = LEVEL_EMOJI.get(level, "⚪")
        if style == "md":
            lines.append(f"### {emoji} {w.get('typeName', '预警')}（{level}）")
            if w.get("title"):
                lines.append(f"**{w['title']}**")
        else:
            lines.append(f"{emoji} {w.get('typeName', '预警')}（{level}）")
            if w.get("title"):
                lines.append(w["title"])
        if w.get("status"):
            lines.append(f"状态：{w['status']}")
        text = (w.get("text") or "").strip()
        if text:
            lines.append(text[:300] + "…" if len(text) > 300 else text)
        lines.append("")
    return "\n".join(lines).strip()


def format_current(weather: dict, style: str = "md") -> str:
    """实况段落。

    不带城市名：它已经在标题里了（标题模板的 {city}），正文再写一遍会重复。
    """
    now = (weather or {}).get("now") or {}
    head = f"{now.get('icon', '☁️')} 实况"
    if now.get("obs_time"):
        head += f" {now['obs_time']}"

    temp_text = f"{_dash(now.get('temp'))}°C"
    if now.get("feels") is not None:
        temp_text += f"（体感 {_dash(now.get('feels'))}°C）"   # 缺体感时不写「体感 -°C」

    parts = [
        _line("天气", now.get("text", "-"), style, "warning"),
        _line("气温", temp_text, style, "warning"),
    ]
    # 风向/风力/湿度缺失时不留「- 级」这种残句
    bits = []
    if now.get("wind_dir"):
        bits.append(str(now["wind_dir"]))
    if now.get("wind_scale"):
        bits.append(f"{now['wind_scale']} 级")
    if now.get("humidity") is not None:
        bits.append(f"湿度 {now['humidity']}%")
    if bits:
        parts.append(_line("风况", " · ".join(bits), style, "info"))

    return "\n".join([_h(head, style)] + parts)


def format_air(weather: dict, style: str = "md") -> str:
    air = (weather or {}).get("air")
    if not air or air.get("aqi") is None:
        return ""
    std = "（美标）" if air.get("std") == "US" else ""
    lines = [
        _h("🌬️ 空气质量", style),
        _line("AQI", f"{air['aqi']}{std} {_aqi_mark(air['aqi'], air.get('category', ''))}",
              style, "info"),
    ]
    detail = []
    if air.get("pm2p5") is not None:
        detail.append(f"PM2.5 {air['pm2p5']}")
    if air.get("pm10") is not None:
        detail.append(f"PM10 {air['pm10']}")
    if detail:
        joined = " · ".join(detail) + " μg/m³"
        lines.append(f"> {joined}" if style == "md" else joined)
    return "\n".join(lines)


def format_hourly(weather: dict, style: str = "md") -> str:
    hourly = (weather or {}).get("hourly") or []
    if not hourly:
        return ""
    lines = [_h("⏳ 未来数小时", style)]
    for h in hourly:
        seg = f"{h.get('time', '-')} {h.get('text', '-')} {_dash(h.get('temp'))}°C"
        if h.get("pop") is not None:
            seg += f" · 降水概率 {h['pop']}%"
        lines.append(f"> {seg}" if style == "md" else seg)
    return "\n".join(lines)


def format_daily(weather: dict, style: str = "md", limit: int = 5) -> str:
    """逐日预报段落。数据源可能给到 7~30 天，消息里最多列 limit 天，避免刷屏。"""
    daily = ((weather or {}).get("daily") or [])[:max(int(limit), 1)]
    if not daily:
        return ""
    lines = [_h(f"📅 未来{_cn_num(len(daily))}天", style)]
    for d in daily:
        label = d.get("label") or _week_of(d.get("date", ""))
        hi, lo = d.get("high"), d.get("low")
        span = f"{lo}~{hi}°C" if lo is not None and hi is not None else "-"
        seg = f"{label} {d.get('text', '-')} {span}"
        lines.append(f"> {seg}" if style == "md" else seg)
    return "\n".join(lines)


def _cn_num(n: int) -> str:
    return {1: "一", 2: "两", 3: "三", 4: "四", 5: "五",
            6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}.get(n, str(n))


def format_indices(weather: dict, style: str = "md") -> str:
    indices = (weather or {}).get("indices") or []
    if not indices:
        return ""
    lines = [_h("📋 生活指数", style)]
    for idx in indices:
        if style == "md":
            lines.append(f"> **{idx.get('name', '')}** {idx.get('category', '')}："
                         f"{idx.get('text', '')}")
        else:
            lines.append(f"{idx.get('name', '')} {idx.get('category', '')}："
                         f"{idx.get('text', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 模板拼装

class _SafeDict(dict):
    """未定义的占位符原样保留，方便用户发现自己写错的变量名。"""

    def __missing__(self, key):
        return "{" + key + "}"


def _tidy(text: str) -> str:
    """去掉空区块留下的多余空行与行尾空白。"""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def to_plain(md: str) -> str:
    """把一段 markdown 粗略转成纯文本（测试推送等场景用）。"""
    s = re.sub(r"<font[^>]*>", "", md).replace("</font>", "")
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^>\s?", "", s, flags=re.M)
    s = s.replace("**", "")
    return _tidy(s)


def _scalars(weather: dict, city: str, warnings: list) -> dict:
    weather = weather or {}
    now = weather.get("now") or {}
    air = weather.get("air") or {}
    first = warnings[0] if warnings else {}
    at = _now()                      # 每次渲染现取，预览与真正推送都会跟着走
    wind_dir = now.get("wind_dir") or ""
    wind_scale = now.get("wind_scale") or ""
    return {
        "city": city,
        "icon": now.get("icon") or "☁️",
        "temp": _dash(now.get("temp")),
        "feels": _dash(now.get("feels")),
        "text": now.get("text") or "",
        "wind": (f"{wind_dir} {wind_scale} 级").strip() or "-",
        "humidity": f"{now['humidity']}%" if now.get("humidity") is not None else "-",
        "aqi": _dash(air.get("aqi")),
        "aqi_category": air.get("category") or "",
        "date": _fmt_date(at),
        "weekday": WEEKDAYS[at.weekday()],
        "clock": _fmt_clock(at),
        "time": weather.get("updated") or "",
        "source": weather.get("source_label") or "",
        "alert_type": first.get("typeName") or "",
        "alert_level": first.get("level") or "",
        "alert_title": first.get("title") or "",
        "alert_status": first.get("status") or "",
    }


def render(weather: dict, city: str, mode: str = "broadcast", style: str = "md",
           tpl: dict | None = None, warnings: list | None = None) -> tuple[str, str]:
    """按模板渲染出 (标题, 正文)。

    weather 可为 None（alert 模式只关心 warnings）。
    """
    tpl = tpl or {}
    style = "text" if str(style).lower() in ("text", "plain") else "md"
    if warnings is None:
        warnings = (weather or {}).get("warnings") or []

    if mode == "alert":
        title_tpl = tpl.get("alert_title") or config_loader.DEFAULT_ALERT_TITLE
        body_tpl = tpl.get("alert_body") or config_loader.DEFAULT_ALERT_BODY
        blocks = {"warnings": format_warning(warnings, style)}
    else:
        title_tpl = tpl.get("broadcast_title") or config_loader.DEFAULT_BROADCAST_TITLE
        body_tpl = tpl.get("broadcast_body") or config_loader.DEFAULT_BROADCAST_BODY
        blocks = {
            "warnings": format_warning(warnings, style),
            "current": format_current(weather, style),
            "air": format_air(weather, style),
            "hourly": format_hourly(weather, style),
            "daily": format_daily(weather, style),
            "indices": format_indices(weather, style),
        }

    values = _scalars(weather, city, warnings)
    # 空区块显式置空，避免模板里留下 {hourly} 字样
    values.update({k: (v or "") for k, v in blocks.items()})
    safe = _SafeDict(values)

    title = _tidy(title_tpl.format_map(safe)).replace("\n", " ")
    body = _tidy(body_tpl.format_map(safe))
    return title, body


def inline_title(title: str, body: str, style: str = "md") -> str:
    """把标题并进正文首行。

    企业微信群机器人 webhook 的协议里只有 content，没有标题字段
    （不像 MagicPush 的 {title, content}），标题只能自己拼进正文，
    否则手机上收到就是一段没头的信息。
    """
    title = (title or "").strip()
    if not title:
        return body or ""
    head = f"# {title}" if style == "md" else title
    return f"{head}\n\n{body}" if body else head


# ---------------------------------------------------------------- 兼容入口

def format_message(weather: dict, location_name: str, hours=None,
                   style: str = "md", tpl: dict | None = None) -> tuple[str, str]:
    """日常播报。hours 已在上游截断，这里保留参数只为兼容旧调用。"""
    return render(weather, location_name, mode="broadcast", style=style, tpl=tpl)


def format_warning_only(location_name: str, warnings: list, style: str = "md",
                        tpl: dict | None = None) -> tuple[str, str]:
    """仅预警消息（--warning 模式，不含实况数据）。"""
    return render(None, location_name, mode="alert", style=style,
                  tpl=tpl, warnings=warnings)
