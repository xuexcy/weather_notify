#!/usr/bin/env python3
"""weather-notify：和风天气 → 企业微信 / MagicPush 天气提醒（含官方预警）。

核心逻辑与 Web 服务共用。定时调度只在 server.py 进程里，本文件的 CLI 仅用于
单次手动调试，不参与定时、不需要配 cron / 1Panel。

用法：
    python3 main.py            # 单次调试：日常播报（实况 + 空气 + 逐小时 + 生活指数 + 预警）
    python3 main.py --warning  # 单次调试：检查一次预警，只有新预警才推送
"""
import json
import logging
import os
import sys
import time
from threading import Lock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_loader
import formatter
import openmeteo
import pusher
from qweather import QWeatherClient, QWeatherError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
ALERT_STATE = os.path.join(config_loader.CONF_DIR, "alert_state.json")
LAST_RUN = os.path.join(config_loader.CONF_DIR, "last_run.json")

LEVEL_RANK = {"白色": 1, "蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}

# alert_state.json 的读写锁：手动「立即检查预警」与定时轮询并发时，
# 没锁会互相覆盖去重状态 → 同一条预警推两次或把已推记录冲掉
_STATE_LOCK = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("weather-notify")


def setup_file_log() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(os.path.join(LOG_DIR, "weather-notify.log"),
                             maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(fh)


def record_run(kind: str, result: dict) -> None:
    """记录最近一次执行结果，供首页展示。"""
    payload = {
        "ts": int(time.time()),
        # 统一北京时间：机器时区可能是 UTC，首页「最近一次推送」不能差 8 小时
        "time": formatter._now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "ok": bool(result.get("ok")),
        "sent": bool(result.get("sent", True)),
        "title": result.get("title", ""),
        "error": result.get("error", ""),
        "channels": [{"channel": c.get("channel"), "ok": bool(c.get("ok")),
                      "error": c.get("error", "")} for c in result.get("channels", [])],
    }
    try:
        os.makedirs(config_loader.CONF_DIR, exist_ok=True)
        with open(LAST_RUN, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("写入运行记录失败: %s", e)


def load_last_run() -> dict | None:
    try:
        with open(LAST_RUN, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def preview_styles(cfg: dict, role: str = config_loader.ROLE_PROD) -> list:
    """预览要展示哪几种风格：该用途下每个已启用通道按自己的 msg_type 各来一份。

    返回 [(key, label, style)]。同类同风格只展示一份；
    都没启用时按第一个企业微信通道（或默认）给一份，这样模板还没配通道也能先调。
    role=test 时只看勾了「测试」的通道（测试推送不会碰正式群）；
    同时勾了两者的通道在两个用途里都会出现。
    """
    chans = (cfg.get("push") or {}).get("channels") or []
    out, seen = [], set()
    for ch in chans:
        if not isinstance(ch, dict) or not ch.get("enabled"):
            continue
        if not config_loader.has_role(ch, role):
            continue
        name = str(ch.get("name") or "").strip()
        if ch.get("type") == "wecom":
            is_text = str(ch.get("msg_type", "")).lower() == "text"
            key = "wecom_text" if is_text else "wecom_md"
            if key in seen:
                continue
            seen.add(key)
            out.append((key,
                        (name or "企业微信") + (" · 纯文本" if is_text else " · Markdown"),
                        "text" if is_text else "md"))
        elif ch.get("type") == "magicpush":
            if "magicpush" in seen:
                continue
            seen.add("magicpush")
            out.append(("magicpush", (name or "MagicPush") + " · Markdown", "md"))
    if not out:
        same_role = [c for c in chans if isinstance(c, dict)
                     and config_loader.has_role(c, role)]
        first_wecom = next((c for c in same_role if c.get("type") == "wecom"), None)
        if first_wecom and str(first_wecom.get("msg_type", "")).lower() == "text":
            out.append(("preview", "预览 · 纯文本", "text"))
        else:
            out.append(("preview", "预览 · Markdown", "md"))
    return out


def preview_style(cfg: dict) -> str:
    """兼容入口：默认展示哪一种风格。"""
    return preview_styles(cfg)[0][2]


def push_all(cfg: dict, renderer, role: str = config_loader.ROLE_PROD) -> tuple[list, str]:
    """推送到该用途下所有启用通道，返回 (逐通道结果, 实际使用的标题)。

    role=prod：定时播报 / 预警（正式）
    role=test：设置页的「测试推送」（随便发不打扰正式群）
    同时勾了两者的通道两种都会收到。

    renderer(style) -> (title, body)：每个通道按自己的风格渲染，
    企业微信可选纯文本（微信侧不渲染 markdown），MagicPush 用 markdown。
    """
    results = []
    used_title = ""

    for ch in (cfg.get("push") or {}).get("channels") or []:
        if not isinstance(ch, dict) or not ch.get("enabled"):
            continue
        if not config_loader.has_role(ch, role):
            continue
        name = str(ch.get("name") or "").strip() or "推送通道"
        try:
            if ch.get("type") == "wecom":
                webhook = str(ch.get("webhook", "")).strip()
                if not webhook:
                    raise RuntimeError("webhook 未填写")
                mt = ("text" if str(ch.get("msg_type", "")).lower() == "text"
                      else "markdown")
                style = "text" if mt == "text" else "md"
                title, body = renderer(style)
                used_title = used_title or title
                # 企微 webhook 没有标题字段，标题只能并入正文首行
                pusher.push_wecom(webhook,
                                  formatter.inline_title(title, body, style), mt)
                results.append({"channel": name, "ok": True, "error": ""})
            elif ch.get("type") == "magicpush":
                base_url = str(ch.get("base_url", "")).strip()
                token = str(ch.get("token", "")).strip()
                if not (base_url and token):
                    raise RuntimeError("服务地址或接口令牌未填写")
                title, body = renderer("md")
                used_title = used_title or title
                pusher.push_magicpush(base_url, token, title, body, "markdown")
                results.append({"channel": name, "ok": True, "error": ""})
        except Exception as e:
            log.error("推送失败（%s）: %s", name, e)
            results.append({"channel": name, "ok": False, "error": str(e)})

    return results, used_title


def _load_state() -> dict:
    try:
        with open(ALERT_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(config_loader.CONF_DIR, exist_ok=True)
    with open(ALERT_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def filter_alerts(warnings: list, cfg: dict) -> list:
    """按等级/类型过滤，并对已推送过的预警去重（同一条 6 小时内不重复，升级则重推）。"""
    min_rank = LEVEL_RANK.get(cfg.get("alert_min_level", "白色"), 1)
    allow_types = cfg.get("alert_types") or []
    resend_hours = float(cfg.get("alert_resend_hours", 6))

    now = time.time()
    fresh = []
    with _STATE_LOCK:
        state = _load_state()
        for w in warnings:
            if LEVEL_RANK.get(w.get("level", ""), 0) < min_rank:
                continue
            if allow_types and w.get("typeName") not in allow_types:
                continue
            key = f"{w.get('typeName', '')}|{w.get('title', '')}|{w.get('startTime', '')}"
            rank = LEVEL_RANK.get(w.get("level", ""), 0)
            prev = state.get(key)
            if (prev and now - prev.get("ts", 0) < resend_hours * 3600
                    and rank <= prev.get("rank", 0)):
                log.info("预警已推送过且未升级，跳过: %s", w.get("title", ""))
                continue
            fresh.append(w)
            state[key] = {"ts": now, "rank": rank}

        state = {k: v for k, v in state.items() if now - v.get("ts", 0) < 7 * 86400}
        _save_state(state)
    return fresh


def _client(cfg: dict, key_pem: str) -> QWeatherClient:
    qw = cfg["qweather"]
    return QWeatherClient(
        api_host=qw["api_host"],
        project_id=qw["project_id"],
        credential_id=qw["credential_id"],
        private_key_pem=key_pem,
    )


def qweather_ready(cfg: dict, key_pem: str | None) -> bool:
    qw = cfg.get("qweather") or {}
    return bool(key_pem) and all(
        str(qw.get(f, "")).strip()
        for f in ("api_host", "project_id", "credential_id"))


def search_city(cfg: dict, key_pem: str | None, keyword: str,
                number: int = 15) -> list:
    """按名称搜城市（顺序：省 → 市 → 区/县），走和风 GeoAPI。

    和风凭据不全时无法搜索（GeoAPI 也要 JWT），抛 QWeatherError 由调用方展示。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        raise QWeatherError("请输入城市名，例如「公安县」或「荆州」")
    if not qweather_ready(cfg, key_pem):
        raise QWeatherError("未配置和风天气凭据，无法按名称搜索；"
                            "可先用「按 IP 自动定位」，或到设置页填写凭据")
    try:
        return _client(cfg, key_pem).city_lookup(keyword, number)
    except QWeatherError as e:
        raise QWeatherError(f"城市搜索失败：{e}")


def test_qweather(cfg: dict, key_pem: str | None) -> str:
    """和风 API 连通性测试：真实调用一次 GeoAPI，成功返回确认文案。

    一次请求同时验证 API Host 可达、项目 ID（JWT sub）、凭据 ID（JWT kid）
    与 Ed25519 私钥签名是否全部有效——任一不对都会被和风拒绝。
    """
    qw = cfg.get("qweather") or {}
    missing = [label for f, label in
               (("api_host", "API Host"), ("project_id", "项目 ID"),
                ("credential_id", "凭据 ID"))
               if not str(qw.get(f, "")).strip()]
    if missing:
        raise QWeatherError("先填写 " + "、".join(missing))
    if not key_pem:
        raise QWeatherError("私钥不存在（conf/ed25519-private.pem），"
                            "先在下方生成密钥对并保存")
    try:
        cities = _client(cfg, key_pem).city_lookup("北京", number=1)
    except QWeatherError as e:
        raise QWeatherError(f"和风 API 测试失败：{e}")
    except Exception as e:                       # 网络层意外错误等
        raise QWeatherError(f"和风 API 测试异常：{e}")
    name = cities[0]["name"] if cities else ""
    return (f"凭据有效，GeoAPI 已返回「{name}」" if name
            else "凭据有效，GeoAPI 请求成功")


def coords(cfg: dict) -> tuple[float, float] | None:
    """取经纬度：优先定位写入的坐标，其次解析「经度,纬度」形式的 location。"""
    lat, lon = cfg.get("location_lat"), cfg.get("location_lon")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    # 全角逗号（中文输入法）与空格一并容忍
    loc = str(cfg.get("location", "")).replace("，", ",").replace(" ", "").strip()
    if "," in loc:
        head, _, tail = loc.partition(",")
        try:
            return float(tail), float(head)  # 和风格式为「经度,纬度」
        except ValueError:
            return None
    return None


def _qw_icon(code, day: bool = True) -> str:
    """和风图标代码 → emoji（粗粒度，够用即可）。"""
    c = str(code or "")
    if c == "100":
        return "☀️" if day else "🌙"
    if c in ("150", "151", "152", "153"):
        return "🌙"
    if c.startswith("1"):
        return "🌤️" if day else "☁️"
    if c.startswith("2"):
        return "☁️"
    if c.startswith("3"):
        n = str(int(c[1:])) if c[1:].isdigit() else "0"   # "302" → "2"，否则雷雨永远匹配不上
        return "⛈️" if n in ("2", "3", "8", "9", "10", "11", "12", "99") else "🌧️"
    if c.startswith("4"):
        return "🌨️"
    if c.startswith("5"):
        return "🌫️"
    return "🌡️"


def _int_or_none(value):
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _week_label(date_str: str) -> str:
    """日期串 → 周X；解析不了返回空串。"""
    try:
        from datetime import date
        return _WEEKDAYS[date.fromisoformat(str(date_str)[:10]).weekday()]
    except (ValueError, TypeError):
        return ""


def _fetch_qweather(cfg: dict, key_pem: str, hours: int) -> dict:
    """和风：实况 + 逐小时 + 逐日 + 空气 + 生活指数 + 官方预警。"""
    client = _client(cfg, key_pem)
    location = cfg["location"]
    # 空气与预警只有新版 /v1/ 接口，必须用经纬度（老 /v7/air|warning 已废弃）
    pair = coords(cfg)

    warnings = []
    if pair:
        try:
            warnings = client.warning_now(pair[0], pair[1])
        except QWeatherError as e:
            log.warning("获取预警失败: %s", e)
    else:
        log.warning("城市不是「经度,纬度」且没有定位坐标，跳过天气预警"
                    "（请在设置页点「按 IP 重新定位」）")

    now = client.weather_now(location)
    if not now:
        raise QWeatherError("获取实况天气失败（检查 API Host / 凭据 / 城市位置）")

    try:
        hourly_raw = client.weather_24h(location)
    except QWeatherError as e:
        log.warning("获取逐小时预报失败: %s", e)
        hourly_raw = []
    try:
        daily_raw, day_count = client.weather_days(location,
                                                   cfg.get("forecast_days", 7))
    except QWeatherError as e:
        log.warning("获取逐日预报失败: %s", e)
        daily_raw, day_count = [], 0
    daily_air = {}
    if pair:
        try:
            daily_air = client.air_daily(pair[0], pair[1])
        except QWeatherError as e:
            log.warning("获取逐日空气质量失败: %s", e)
    air_raw = None
    if pair:
        try:
            air_raw = client.air_now(pair[0], pair[1])
        except QWeatherError as e:
            log.warning("获取空气质量失败: %s", e)
    try:
        indices_raw = client.indices_1d(location, cfg["indices_types"])
    except QWeatherError as e:
        log.warning("获取生活指数失败: %s", e)
        indices_raw = {}

    hourly = []
    for h in hourly_raw[:max(int(hours), 0)]:
        hourly.append({
            "time": str(h.get("fxTime", ""))[11:16],
            "temp": _int_or_none(h.get("temp")),
            "text": h.get("text", ""),
            "icon": _qw_icon(h.get("icon")),
            "pop": _int_or_none(h.get("pop")),
        })

    daily = []
    for i, d in enumerate(daily_raw):
        date = str(d.get("fxDate", ""))
        week = _week_label(date)
        if i == 0:
            label = "今天"
        elif i == 1:
            label = "明天"
        elif i == 2:
            label = "后天"
        else:
            label = week
        air_day = daily_air.get(date) or {}
        daily.append({
            "date": date,
            "date_label": date[5:].replace("-", "/") if len(date) >= 10 else date,
            "label": label,
            "week": week,
            "high": _int_or_none(d.get("tempMax")),
            "low": _int_or_none(d.get("tempMin")),
            "text": d.get("textDay", ""),
            "icon": _qw_icon(d.get("iconDay")),
            "pop": None,
            "aqi": air_day.get("aqi"),
            "aqi_cat": air_day.get("category") or "",
        })

    air = None
    if air_raw:
        air = {
            "aqi": _int_or_none(air_raw.get("aqi")),
            "category": air_raw.get("category", ""),
            "pm2p5": _int_or_none(air_raw.get("pm2p5")),
            "pm10": _int_or_none(air_raw.get("pm10")),
            "std": air_raw.get("std") or "CN",
        }

    indices = []
    for t in str(cfg["indices_types"]).split(","):
        item = indices_raw.get(t.strip())
        if item:
            indices.append({"type": t.strip(), "name": item.get("name", ""),
                            "category": item.get("category", ""),
                            "text": item.get("text", "")})

    return {
        "source": "qweather",
        "source_label": "和风天气",
        "fallback_reason": "",
        "now": {
            "temp": _int_or_none(now.get("temp")),
            "feels": _int_or_none(now.get("feelsLike")),
            "text": now.get("text", ""),
            "icon": _qw_icon(now.get("icon")),
            "humidity": _int_or_none(now.get("humidity")),
            "wind_dir": now.get("windDir", ""),
            "wind_scale": now.get("windScale", ""),
            "obs_time": str(now.get("obsTime", ""))[11:16],
        },
        "hourly": hourly,
        "daily": daily,
        "air": air,
        "indices": indices,
        "warnings": warnings,
    }


def fetch_weather(cfg: dict, key_pem: str | None = None, hours: int | None = None,
                  allow_fallback: bool = True) -> dict:
    """统一天气数据入口。

    和风凭据齐全时走和风（含官方预警与生活指数）；否则、或和风请求失败时，
    降级到 Open-Meteo（无 key、无预警），保证首页总有内容可看。
    """
    hours = int(hours if hours is not None else cfg.get("hourly_hours", 6))
    reason = ""

    if qweather_ready(cfg, key_pem):
        try:
            data = _fetch_qweather(cfg, key_pem, hours)
            data["updated"] = time.strftime("%H:%M")
            return data
        except QWeatherError as e:
            if not allow_fallback:
                raise
            log.warning("和风数据获取失败，降级 Open-Meteo: %s", e)
            reason = str(e)
    else:
        reason = "未配置和风天气凭据"
        if not allow_fallback:
            raise QWeatherError(reason)

    pair = coords(cfg)
    if not pair:
        raise QWeatherError(
            f"{reason}；且没有可用坐标，无法使用备用数据源（请在设置页填写城市或重新定位）")

    data = openmeteo.fetch(pair[0], pair[1], hours=hours,
                           days=cfg.get("forecast_days", 7))
    data["fallback_reason"] = reason
    data["updated"] = time.strftime("%H:%M")
    return data


def run_forecast(cfg: dict, key_pem: str, push: bool = True) -> dict:
    """执行日常播报。push=False 时只拉数据做预览，不推送、不记录。

    返回 {ok, title, body, style, channels, error, location, weather}
    """
    name = cfg.get("location_name") or cfg["location"]
    try:
        weather = fetch_weather(cfg, key_pem, allow_fallback=False)
    except QWeatherError as e:
        return {"ok": False, "error": str(e), "title": "", "body": "",
                "channels": [], "location": name}

    tpl = cfg.get("message")

    def renderer(style: str):
        return formatter.render(weather, name, "broadcast", style, tpl)

    if not push:
        style = preview_style(cfg)
        title, body = renderer(style)
        return {"ok": True, "title": title, "body": body, "style": style,
                "channels": [], "error": "", "location": name, "weather": weather}

    channels, title = push_all(cfg, renderer)
    result = {"ok": all(c["ok"] for c in channels) if channels else False,
              "title": title, "body": renderer(preview_style(cfg))[1],
              "channels": channels, "error": "",
              "location": name, "weather": weather}
    record_run("forecast", result)
    return result


def run_warning(cfg: dict, key_pem: str) -> dict:
    """执行预警检查。返回 {ok, sent, title, body, channels, error}。"""
    client = _client(cfg, key_pem)
    name = cfg.get("location_name") or cfg["location"]
    pair = coords(cfg)
    if not pair:
        result = {"ok": False, "sent": False,
                  "error": "预警接口需要经纬度：城市请填「经度,纬度」，或在设置页点「按 IP 重新定位」",
                  "title": "", "body": "", "channels": []}
        record_run("warning", result)
        return result
    try:
        warnings = client.warning_now(pair[0], pair[1])
    except QWeatherError as e:
        result = {"ok": False, "sent": False, "error": f"获取预警失败: {e}",
                  "title": "", "body": "", "channels": []}
        record_run("warning", result)
        return result

    fresh = filter_alerts(warnings, cfg)
    if not fresh:
        return {"ok": True, "sent": False, "title": "", "body": "", "channels": [],
                "error": ""}

    tpl = cfg.get("message")

    def renderer(style: str):
        return formatter.render(None, name, "alert", style, tpl, warnings=fresh)

    channels, title = push_all(cfg, renderer)
    result = {"ok": all(c["ok"] for c in channels), "sent": True,
              "title": title, "body": renderer(preview_style(cfg))[1],
              "channels": channels, "error": ""}
    record_run("warning", result)
    return result


def main() -> int:
    warning_only = "--warning" in sys.argv
    cfg = config_loader.load_config()
    key_pem = config_loader.load_private_key()

    if warning_only:
        result = run_warning(cfg, key_pem)
        if result["error"]:
            log.error(result["error"])
            return 1
        if not result["sent"]:
            log.info("无新增预警，跳过推送")
            return 0
        log.info("预警推送完成")
        return 0 if result["ok"] else 1

    result = run_forecast(cfg, key_pem)
    if result["error"]:
        log.error(result["error"])
        return 1
    log.info("日常播报推送完成")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    setup_file_log()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        log.error("运行异常: %s", e, exc_info=True)
        sys.exit(1)
