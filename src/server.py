#!/usr/bin/env python3
"""本地服务：首页（天气面板）+ 设置页（填写凭据 / 推送通道）。

默认只监听 127.0.0.1（配置 server.host 或 --host 可改为 0.0.0.0 供局域网访问）。
仅用标准库 http.server，无额外依赖。

    /            首页：当前天气、预警、未来趋势、推送状态
    /settings    设置页：和风凭据、推送通道（勾选展开）、播报选项
    /style.css   共享样式
    /api/weather 结构化天气 JSON（和风优先，无凭据时降级 Open-Meteo）
    /api/state   读取配置 + 私钥/公钥状态
    /api/status  首页所需的配置状态汇总
    /api/geoip   按公网 IP 重新定位城市（写入配置）
    /api/city    按名称搜城市（省/市/区县，和风 GeoAPI）
    /api/location 直接指定城市（经纬度）并立即生效
    /api/config  保存配置
    /api/message 生成推送消息预览（不推送）
    /api/test    测试推送：发两条（日常播报走真实天气 + 天气预警走示例预警）
    /api/test-channel 单通道连通性测试（发一条极简消息）
    /api/test-qweather 和风 API 连通性测试（真实调用一次 GeoAPI）
    /api/qweather/key   生成 / 替换和风 Ed25519 密钥对（公钥由私钥推导）
    /api/channel/add    添加推送通道实例（企业微信 / MagicPush，可同类多个）
    /api/channel/remove 删除推送通道实例（按 id，凭据一并丢弃）
    /api/run     立即播报（拉天气 + 推送）
    /api/run-warning 立即检查一次预警（仅在有新预警时推送）
    /api/restart 按新端口重启服务（进程原地 exec，PID 不变）

所有定时任务（日常播报、预警轮询）都跑在本进程内的调度线程里，在设置页配置。
服务在 → 全部功能在；服务停止 → 网页、播报、预警轮询一起停止。
不依赖 1Panel / cron / 任何外部定时器。

用法：
    python3 src/server.py               # 端口取 conf/config.json 的 server.port，缺省 18770
    python3 src/server.py --port 9000   # 临时换端口，不写配置
    python3 src/server.py --no-geoip    # 启动时不按 IP 自动定位
"""
import argparse
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth as web_auth
import config_loader
import formatter
import geoip
import main as core
import pusher
from qweather import QWeatherError

# 静态页面在项目根目录的 web/（代码在 src/，所以用 config_loader 算好的根目录）
BASE_DIR = config_loader.ROOT_DIR
WEB_DIR = os.path.join(BASE_DIR, "web")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("weather-notify.web")

PAGES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/settings": "settings.html",
    "/settings.html": "settings.html",
    "/login": "login.html",
    "/login.html": "login.html",
}

# 需要登录才能访问的路径。
# 首页天气面板保持公开：/api/status、/api/weather 以及首页「更改城市」用到的
# /api/city、/api/location、/api/geoip 都不涉及凭据，也不触发推送。
PROTECTED_GET = frozenset({
    "/settings", "/settings.html", "/api/state", "/api/schedule",
})
PROTECTED_POST = frozenset({
    "/api/config", "/api/message", "/api/test", "/api/test-channel",
    "/api/test-qweather", "/api/qweather/key",
    "/api/channel/add", "/api/channel/remove",
    "/api/run", "/api/run-warning",
    "/api/restart",
    "/api/auth/password", "/api/auth/toggle", "/api/auth/logout",
})

# /api/auth/status、/api/auth/login、/api/auth/setup 天然公开：
# 它们不在 PROTECTED_POST 里，否则就没法登录了。

STATIC = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

TEST_TAG = "🧪 测试"

# 测试推送发这两条：一条日常播报（真实天气）、一条天气预警（示例预警）。
# 第三个元素是正文首行的补充说明，留空表示不插（标题已带「🧪 测试」前缀）。
TEST_MESSAGES = (
    ("broadcast", "日常播报", "", False),
    ("alert", "天气预警", "示例预警，非真实险情", True),
)

# 两条之间留出间隔，保证接收侧顺序与这里一致
TEST_GAP_SEC = 1.2

QW_FIELDS = ("api_host", "project_id", "credential_id")


def tag_test(title: str, body: str, style: str, note: str) -> tuple:
    """给测试消息打标记：「🧪 测试 ·」只加在标题上，正文不再重复。

    note 非空时才在正文首行补一句（预警那条必须说明是示例，避免误当真险情）。
    """
    if note:
        head = note
        body = (f"> {head}\n\n{body}" if style == "md" else f"{head}\n\n{body}")
    return f"{TEST_TAG} · {title}", body

# 预览预警模板用的示例预警：真实预警很少出现，靠它才能调格式
SAMPLE_WARNING = {
    "typeName": "暴雨",
    "level": "橙色",
    "title": "荆州市气象台发布暴雨橙色预警信号",
    "status": "预警中",
    "text": "预计未来 3 小时降雨量将达 50 毫米以上，请注意防范。",
}

# 天气数据缓存（stale-while-revalidate）：
#   10 分钟内打开页面直接命中，不产生任何上游请求；
#   超过 10 分钟也先把旧数据给页面（秒开），同时后台刷新；
#   刷新有 90 秒冷却，多标签页 / 连点刷新只会拉一次。
#   这样一来「打开页面」几乎不增加和风调用量，但用户永远不用等网络。
_WEATHER_CACHE: dict = {"key": None, "ts": 0.0, "data": None}
_CACHE_LOCK = threading.Lock()
CACHE_FRESH_TTL = 600
REFRESH_MIN_GAP = 90
_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE = {"ts": 0.0, "running": False}
# ?fresh=1 强制刷新的全局最小间隔：这个参数不需要登录，不限流能把和风配额刷光
FORCE_MIN_GAP = 15
_FORCE_STATE = {"ts": 0.0}


def _bg_refresh_weather(cfg: dict, key_pem, cache_key) -> bool:
    """后台刷新天气缓存；同一时刻只跑一个，失败后 60 秒内不重试。"""
    now = time.time()
    with _REFRESH_LOCK:
        if _REFRESH_STATE["running"] or now - _REFRESH_STATE["ts"] < REFRESH_MIN_GAP:
            return False
        _REFRESH_STATE["running"] = True
        _REFRESH_STATE["ts"] = now

    def run():
        try:
            data = core.fetch_weather(cfg, key_pem)
        except Exception as e:
            log.warning("后台刷新天气失败: %s", e)
            with _REFRESH_LOCK:
                _REFRESH_STATE["running"] = False
                # 失败后 60 秒内不再重试：页面开着时避免 3 秒一轮地打上游
                _REFRESH_STATE["ts"] = time.time() - REFRESH_MIN_GAP + 60
            return
        with _CACHE_LOCK:
            _WEATHER_CACHE.update({"key": cache_key, "ts": time.time(), "data": data})
        log.info("后台刷新天气完成，下一次页面命中新数据")
        with _REFRESH_LOCK:
            _REFRESH_STATE["running"] = False

    threading.Thread(target=run, name="weather-refresh", daemon=True).start()
    return True

# cookie 签名密钥：启动时从配置读/生成，重启后仍是同一把 → 登录状态不丢
AUTH_SECRET = ""

# ---- 内置调度（随 Web 服务常驻，默认全关）----
# 日常播报按北京时间 HH:MM 触发；预警轮询按间隔（分钟）触发。
# 两类任务都在本进程内的守护线程里，进程退出即消失，不做任何补跑。
SCHEDULE_STATE_FILE = os.path.join(config_loader.CONF_DIR, "schedule_state.json")
SCHEDULE_POLL_SEC = 20
SCHEDULE_GRACE_SEC = 45      # 启动后 45 秒内不触发，避免重启瞬间补播 / 重复推
SCHEDULE_TS_FMT = "%Y-%m-%d %H:%M:%S"
_START_TS = time.monotonic()
_SCHED_LOCK = threading.Lock()

# 本进程实际监听的地址/端口、以及启动时的原始命令行（重启时按它重放）
RUNNING_HOST = config_loader.DEFAULT_HOST
RUNNING_PORT = config_loader.DEFAULT_PORT
START_ARGV: list = []


def sanitize_user_config(user: dict) -> dict:
    """页面提交的配置里，剥掉不该由表单写入的字段。

    - 鉴权密钥材料只能走 /api/auth/* 专用接口，防止把签名密钥覆盖成任意值
    - 时间点统一校验成 HH:MM、预警间隔钳到合法范围，避免脏数据进调度器
    - push.channels 逐项清洗：未知类型丢弃、缺 id 补齐、字段按类型白名单保留，
      否则脚本直接调 /api/config 塞进来的脏通道会一直留在配置里
    """
    if not isinstance(user, dict):
        return {}
    a = user.get("auth")
    if isinstance(a, dict):
        for key in ("password_hash", "salt", "secret"):
            a.pop(key, None)
    s = user.get("schedule")
    if isinstance(s, dict):
        if "times" in s:
            s["times"] = config_loader.normalize_times(s["times"])
        if "warning_interval_min" in s:
            s["warning_interval_min"] = config_loader.clamp_warning_interval(
                s["warning_interval_min"])
    p = user.get("push")
    if isinstance(p, dict) and "channels" in p:
        clean = []
        for ch in p["channels"] if isinstance(p["channels"], list) else []:
            if not isinstance(ch, dict):
                continue
            ctype = ch.get("type")
            meta = config_loader.CHANNEL_TYPES.get(ctype)
            if not meta:
                continue
            fixed = {"id": str(ch.get("id") or "").strip() or secrets.token_hex(4),
                     "type": ctype,
                     "name": str(ch.get("name") or "").strip() or meta["label"],
                     "enabled": bool(ch.get("enabled"))}
            # 老页面/脚本可能既不带 roles 也不带 role：这时不要写入，交给合并逻辑
            # 保留原值，否则「测试」用途会被悄悄改回正式
            if "roles" in ch or "role" in ch:
                fixed["roles"] = config_loader.normalize_roles(
                    ch.get("roles") if "roles" in ch else ch.get("role"))
            for field, default in meta["fields"].items():
                fixed[field] = ch.get(field, default)
            clean.append(fixed)
        p["channels"] = clean
    return user


# 请求体上限：配置 JSON 再大也到不了这个量级，防误操作/恶意请求吃内存
MAX_BODY_BYTES = 1 * 1024 * 1024


def _rotate_secret(raw: dict) -> str:
    """改密码时轮换签名密钥，让其他设备已签发的 cookie 立刻失效。"""
    global AUTH_SECRET
    a = raw.setdefault("auth", {})
    a["secret"] = secrets.token_hex(32)
    config_loader.save_raw(raw)
    AUTH_SECRET = a["secret"]
    return AUTH_SECRET


# ---------------------------------------------------------------- 定时播报

def _load_schedule_state() -> dict:
    try:
        with open(SCHEDULE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_schedule_state(state: dict) -> None:
    try:
        os.makedirs(config_loader.CONF_DIR, exist_ok=True)
        with open(SCHEDULE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("写入定时播报状态失败: %s", e)


def _next_run(times: list, now: datetime) -> dict:
    """按北京时间算下一次触发点（今天剩下的，否则明天第一个）。"""
    if not times:
        return {"label": "", "at": "", "in_seconds": 0}
    for item in times:
        hour, minute = (int(x) for x in item.split(":"))
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand > now:
            return {"label": "今天 " + item, "at": cand.strftime("%Y-%m-%d %H:%M"),
                    "in_seconds": int((cand - now).total_seconds())}
    hour, minute = (int(x) for x in times[0].split(":"))
    cand = (now + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return {"label": "明天 " + times[0], "at": cand.strftime("%Y-%m-%d %H:%M"),
            "in_seconds": int((cand - now).total_seconds())}


def _next_warning_at(state: dict, interval_min: int) -> str:
    """按上次检查时间算下一次预警轮询（北京时间），供设置页显示。"""
    last = state.get("warning_last_at", "")
    if not last:
        return ""
    try:
        nxt = (datetime.strptime(last, SCHEDULE_TS_FMT)
               + timedelta(minutes=interval_min))
    except ValueError:
        return ""
    return nxt.strftime(SCHEDULE_TS_FMT)


def schedule_payload() -> dict:
    """设置页「定时播报」卡片的状态数据。"""
    sch = config_loader.schedule_from(
        config_loader.merged(config_loader.load_raw()))
    now = formatter._now()
    state = _load_schedule_state()
    return {
        "ok": True,
        "enabled": sch["enabled"],
        "times": sch["times"],
        "next": _next_run(sch["times"] if sch["enabled"] else [], now),
        "last_fired": state.get("last_fired", ""),
        "last_run": core.load_last_run(),
        "warning_enabled": sch["warning_enabled"],
        "warning_interval_min": sch["warning_interval_min"],
        "warning_next": (_next_warning_at(state, sch["warning_interval_min"])
                         if sch["warning_enabled"] else ""),
        "warning_last_at": state.get("warning_last_at", ""),
        "warning_last_result": state.get("warning_last_result", ""),
        "now": now.strftime(SCHEDULE_TS_FMT),
        "timezone": "Asia/Shanghai",
        "service_running": True,
    }


def _run_scheduled_forecast(cfg: dict) -> None:
    """在独立线程里跑一次日常播报，别挡住调度循环。"""
    try:
        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        if not key_pem:
            log.warning("定时播报跳过：缺少私钥 %s", config_loader.KEY_FILE)
            return
        result = core.run_forecast(cfg, key_pem)
        log.info("定时播报完成 ok=%s error=%s channels=%s",
                 result.get("ok"), result.get("error"),
                 [c.get("channel") for c in result.get("channels", [])])
    except Exception as e:
        log.error("定时播报异常: %s", e, exc_info=True)


def _run_scheduled_warning(cfg: dict, manual: bool = False) -> None:
    """在独立线程里跑一次预警检查；只有新预警才会推送（去重在 core 里）。"""
    try:
        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        if not key_pem:
            log.warning("预警检查跳过：缺少私钥 %s", config_loader.KEY_FILE)
            text = "跳过：缺少私钥"
        else:
            result = core.run_warning(cfg, key_pem)
            if not result.get("ok"):
                text = "失败：" + (result.get("error") or "未知错误")
            elif result.get("sent"):
                text = "已推送新预警"
            else:
                text = "无新增预警"
            log.info("预警检查完成 ok=%s sent=%s",
                     result.get("ok"), result.get("sent"))
        if not manual:
            with _SCHED_LOCK:
                state = _load_schedule_state()
                state["warning_last_result"] = text
                _save_schedule_state(state)
    except Exception as e:
        log.error("预警检查异常: %s", e, exc_info=True)


def _broadcast_tick(sch: dict, now: datetime) -> None:
    """命中北京时间 HH:MM 就触发一次；同一分钟只推一次（状态落盘防重启重复）。"""
    if not sch["enabled"] or not sch["times"]:
        return
    hm = now.strftime("%H:%M")
    if hm not in sch["times"]:
        return
    stamp = now.strftime("%Y-%m-%d %H:%M")
    with _SCHED_LOCK:
        state = _load_schedule_state()
        if state.get("last_fired") == stamp:
            return
        state["last_fired"] = stamp
        state["last_fired_hm"] = hm
        _save_schedule_state(state)
    log.info("定时播报触发：%s（北京时间）", stamp)
    cfg = config_loader.merged(config_loader.load_raw())
    threading.Thread(target=_run_scheduled_forecast, args=(cfg,), daemon=True).start()


def _warning_tick(sch: dict, now: datetime) -> None:
    """到间隔就触发一次预警检查；进程重启后重新起算，不补跑历史缺口。"""
    if not sch["warning_enabled"]:
        return
    last = _load_schedule_state().get("warning_last_at", "")
    if not last:
        return
    try:
        due = ((now.replace(tzinfo=None)
                - datetime.strptime(last, SCHEDULE_TS_FMT)).total_seconds()
               >= sch["warning_interval_min"] * 60)
    except ValueError:
        due = True
    if not due:
        return
    with _SCHED_LOCK:
        state = _load_schedule_state()
        if state.get("warning_last_at") != last:
            return                      # 其他线程已经排过这一轮
        state["warning_last_at"] = now.strftime(SCHEDULE_TS_FMT)
        state["warning_last_result"] = "检查中…"
        _save_schedule_state(state)
    cfg = config_loader.merged(config_loader.load_raw())
    threading.Thread(target=_run_scheduled_warning, args=(cfg,), daemon=True).start()


def _schedule_tick() -> None:
    if time.monotonic() - _START_TS < SCHEDULE_GRACE_SEC:
        return
    sch = config_loader.schedule_from(
        config_loader.merged(config_loader.load_raw()))
    now = formatter._now()
    _broadcast_tick(sch, now)
    _warning_tick(sch, now)


def _schedule_loop() -> None:
    # 预警轮询是间隔制：进程重启后从「现在」重新起算，绝不补跑停机期间的缺口
    with _SCHED_LOCK:
        state = _load_schedule_state()
        state["warning_last_at"] = formatter._now().strftime(SCHEDULE_TS_FMT)
        _save_schedule_state(state)
    _schedule_tick()                       # 启动先查一次（受宽限期保护）
    while True:
        time.sleep(SCHEDULE_POLL_SEC)
        try:
            _schedule_tick()
        except Exception as e:
            log.error("定时调度检查异常: %s", e, exc_info=True)


def _display_host(host: str) -> str:
    """日志与页面里展示用的主机名。

    不去猜「本机出口 IP」：有旁路由 / VPN / Docker 的机器上，路由表选出来的
    往往是 198.18.x、172.x 之类的虚拟地址，拼出来的 URL 根本打不开。
    需要远程访问时，把 server.host 直接写成那块网卡的 IP（如 192.168.1.100）。
    """
    return host or config_loader.DEFAULT_HOST


def _port_in_use(port: int) -> bool:
    """能不能在本机 bind 上这个端口（用通配地址判断，最严格）。

    注意探测 socket 不能设 SO_REUSEADDR：Linux 上它允许「通配 + 通配」的
    重复绑定，会把已占用端口误判成空闲（macOS 不会，属于平台差异坑）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def _restart_argv(port: int) -> list:
    """重放启动参数：去掉旧的 --port / --open，换成新端口。"""
    out, skip = [], False
    for arg in START_ARGV:
        if skip:
            skip = False
        elif arg in ("--port", "-p"):
            skip = True
        elif arg.startswith("--port="):
            continue
        elif arg == "--open":  # 重启不再弹新标签页，由页面自己跳
            continue
        else:
            out.append(arg)
    return out + ["--port", str(port)]


def _restart_soon(port: int) -> None:
    """响应发完之后原地 exec 自身。"""
    time.sleep(0.45)
    argv = _restart_argv(port)
    log.info("重启服务 → %s:%s", _display_host(RUNNING_HOST), port)
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + argv)
    except OSError as e:  # execv 失败进程就留在原地
        log.error("重启失败: %s", e)


def _mask(value: str, head: int = 8, tail: int = 4) -> str:
    value = str(value or "")
    if len(value) <= head + tail + 2:
        return value
    return f"{value[:head]}…{value[-tail:]}"


def _mask_webhook(url: str) -> str:
    url = str(url or "")
    if "key=" in url:
        head, _, key = url.partition("key=")
        return head + "key=" + _mask(key, 4)
    return _mask(url, 24, 4)


def build_status() -> dict:
    """首页状态汇总。"""
    raw = config_loader.load_raw()
    cfg = config_loader.merged(raw)
    key_exists = os.path.exists(config_loader.KEY_FILE)

    qw = cfg.get("qweather") or {}
    missing_qw = [label for field, label in
                  (("api_host", "API Host"), ("project_id", "项目 ID"),
                   ("credential_id", "凭据 ID"))
                  if not str(qw.get(field, "")).strip()]
    loc = config_loader.current_location(cfg)

    channels = []
    for ch in (cfg.get("push") or {}).get("channels") or []:
        if not isinstance(ch, dict):
            continue
        enabled = bool(ch.get("enabled"))
        if ch.get("type") == "wecom":
            target = _mask_webhook(ch.get("webhook", ""))
            filled = bool(str(ch.get("webhook", "")).strip())
        else:
            target = str(ch.get("base_url", "")).strip()
            filled = bool(target and str(ch.get("token", "")).strip())
        meta = config_loader.CHANNEL_TYPES.get(ch.get("type"), {})
        channels.append({"id": ch.get("id"), "type": ch.get("type"),
                         "name": ch.get("name") or meta.get("label", "通道"),
                         "enabled": enabled, "filled": filled, "target": target})

    missing = []
    if missing_qw:
        missing.append("和风天气 API（" + "、".join(missing_qw) + "）")
    if not key_exists:
        missing.append("JWT 私钥")
    if not loc["location"]:
        missing.append("城市位置")
    if not any(c["enabled"] for c in channels):
        missing.append("推送通道")
    else:
        empty = [c["name"] for c in channels if c["enabled"] and not c["filled"]]
        if empty:
            missing.append("推送通道信息（" + "、".join(empty) + " 未填完整）")

    return {
        "ready": not missing,
        "missing": missing,
        "qweather": {"configured": not missing_qw, "missing": missing_qw,
                     "api_host": str(qw.get("api_host", "")).strip()},
        "key": config_loader.key_info(),
        "location": loc,
        "channels": channels,
        # 能否拿到天气：和风齐全，或有坐标可走 Open-Meteo 兜底
        "can_fetch": bool((not missing_qw and key_exists) or core.coords(cfg)),
        "last_run": core.load_last_run(),
        # 机器可能是 UTC（NAS 常见），统一按北京时间展示，别和标题里的日期差一天
        "server_time": formatter._now().strftime("%Y-%m-%d %H:%M:%S"),
        # 设置页「服务」卡片：当前实际监听端口 + 配置里保存的端口
        "server": {"host": RUNNING_HOST,
                   "display_host": _display_host(RUNNING_HOST),
                   "port": RUNNING_PORT,
                   "config_port": config_loader.port_from(cfg),
                   "url": f"http://{_display_host(RUNNING_HOST)}:{RUNNING_PORT}",
                   "port_min": config_loader.PORT_MIN,
                   "port_max": config_loader.PORT_MAX},
        # 设置页渲染模板编辑区用
        "message_defaults": config_loader.MESSAGE_DEFAULTS,
        "placeholders": [{"token": t, "desc": d} for t, d in formatter.PLACEHOLDER_DOC],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "weather-notify"

    def log_message(self, fmt, *args):  # 精简访问日志
        log.info("%s - %s", self.address_string(), fmt % args)

    # ---------- 工具 ----------
    def _extra(self) -> list:
        return getattr(self, "_extra_headers", [])

    def _set_cookie(self, value: str) -> None:
        """本次响应额外下发一个 Set-Cookie（登录 / 改密后立刻续期）。"""
        self._extra_headers = self._extra() + [("Set-Cookie", value)]

    def _json(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        merged = dict(headers or {})
        merged.update(dict(self._extra()))
        for key, value in merged.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # ---------- 鉴权 ----------
    def _client_ip(self) -> str:
        try:
            return self.client_address[0] or "?"
        except (AttributeError, IndexError):
            return "?"

    def _authed(self) -> bool:
        """设置页 / 敏感接口是否已登录。"""
        raw = config_loader.load_raw()
        if not config_loader.auth_enabled(raw):
            return True
        if not config_loader.auth_has_password(raw):
            return False
        token = web_auth.read_cookie(self.headers.get("Cookie", ""))
        return bool(token and web_auth.verify_token(token, AUTH_SECRET))

    def _origin_ok(self) -> bool:
        """同源校验：浏览器发来的 Origin 必须和 Host 一致（curl 无 Origin 放行）。

        cookie 是 SameSite=Strict，再加一道 Origin 校验，挡住跨站诱导改配置 / 触发推送。
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        from urllib.parse import urlsplit
        try:
            netloc = urlsplit(origin).netloc
        except ValueError:
            return False
        host = (self.headers.get("Host") or "").strip()
        return bool(netloc) and netloc.lower() == host.lower()

    def _auth_status(self) -> dict:
        raw = config_loader.load_raw()
        has_pw = config_loader.auth_has_password(raw)
        enabled = config_loader.auth_enabled(raw)
        return {"ok": True, "enabled": enabled, "has_password": has_pw,
                "needs_setup": enabled and not has_pw,
                "logged_in": self._authed(),
                "session_hours": config_loader.auth_session_hours(raw)}

    def _deny(self, path: str) -> None:
        if path.startswith("/api/"):
            self._json({"ok": False, "error": "未登录或登录已过期，请重新登录",
                        "auth": self._auth_status()}, 401)
        else:
            self._redirect("/login")

    def _html(self, name: str, ctype: str = "text/html; charset=utf-8") -> None:
        path = os.path.join(WEB_DIR, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > MAX_BODY_BYTES:
            log.warning("请求体过大（%d 字节），已拒绝解析", length)
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _config_from_body(self, body: dict) -> dict:
        """优先用页面提交的配置；请求体里没提到的字段沿用已保存配置。

        （脚本直接调接口时常常只带少数字段，不能因此把凭据 / 通道当成空）
        """
        user = body.get("config")
        if isinstance(user, dict) and user:
            return config_loader.merged(
                config_loader.overlay_saved(sanitize_user_config(user),
                                            config_loader.load_raw()))
        return config_loader.merged(config_loader.load_raw())

    # ---------- 路由 ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PROTECTED_GET and not self._authed():
            self._deny(path)
            return
        if path in PAGES:
            self._html(PAGES[path])
        elif path in STATIC:
            name, ctype = STATIC[path]
            self._html(name, ctype)
        elif path == "/api/auth/status":
            self._json(self._auth_status())
        elif path == "/api/schedule":
            self._json(schedule_payload())
        elif path == "/api/weather":
            self._weather(force="fresh=1" in self.path)
        elif path == "/api/state":
            # 鉴权密钥材料不下发，只给设置页需要的非敏感字段
            self._json({"config": config_loader.public_config(config_loader.load_raw()),
                        "status": build_status()})
        elif path == "/api/status":
            self._json(build_status())
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        if path in PROTECTED_POST and not self._authed():
            self._deny(path)
            return
        if path in PROTECTED_POST and not self._origin_ok():
            self._json({"ok": False, "error": "请求来源不匹配（Origin 校验失败）"}, 403)
            return

        if path == "/api/auth/status":
            self._json(self._auth_status())

        elif path == "/api/auth/login":
            self._auth_login(body)

        elif path == "/api/auth/setup":
            self._auth_setup(body)

        elif path == "/api/auth/logout":
            self._auth_logout()

        elif path == "/api/auth/password":
            self._auth_password(body)

        elif path == "/api/auth/toggle":
            self._auth_toggle(body)

        elif path == "/api/config":
            self._save_config(body)

        elif path == "/api/geoip":
            self._geoip(body)

        elif path == "/api/city":
            self._city(body)

        elif path == "/api/location":
            self._set_location(body)

        elif path == "/api/weather":
            self._weather(force=True)

        elif path == "/api/test":
            self._test(body)

        elif path == "/api/test-channel":
            self._test_channel(body)

        elif path == "/api/test-qweather":
            self._test_qweather(body)

        elif path == "/api/qweather/key":
            self._qweather_key(body)

        elif path == "/api/channel/add":
            self._channel_add(body)

        elif path == "/api/channel/remove":
            self._channel_remove(body)

        elif path == "/api/message":
            self._message(body)

        elif path == "/api/run":
            self._run(body)

        elif path == "/api/run-warning":
            self._run_warning_now(body)

        elif path == "/api/restart":
            self._restart(body)

        else:
            self.send_error(404)

    # ---------- 天气 ----------
    @staticmethod
    def _cached_weather(cfg: dict, key_pem, force: bool = False):
        """取天气，返回 (weather, cached, error, info)。

        策略（stale-while-revalidate）：
          * 新鲜（<CACHE_FRESH_TTL）：直接返回缓存，0 请求 0 等待；
          * 过期但还有缓存：立刻返回旧数据（info.stale=True），后台刷新；
          * 没有缓存 / force：同步拉一次（并发，约 0.7s）。

        缓存键带上会影响返回内容的配置：地点、逐小时条数、逐日天数、生活指数，
        否则改了这些设置会拿回旧的缓存数据。
        """
        cache_key = (cfg.get("location"), cfg.get("location_name"),
                     int(cfg.get("hourly_hours", 6)),
                     int(cfg.get("forecast_days", 7)),
                     str(cfg.get("indices_types", "")),
                     core.qweather_ready(cfg, key_pem))
        with _CACHE_LOCK:
            have = bool(_WEATHER_CACHE["data"]) and _WEATHER_CACHE["key"] == cache_key
            age = (time.time() - _WEATHER_CACHE["ts"]) if have else -1.0
            cached_data = _WEATHER_CACHE["data"] if have else None

        if not force and have:
            if age < CACHE_FRESH_TTL:
                return cached_data, True, "", {"stale": False, "age": int(age)}
            _bg_refresh_weather(cfg, key_pem, cache_key)
            return cached_data, True, "", {"stale": True, "age": int(age)}

        try:
            data = core.fetch_weather(cfg, key_pem)
        except QWeatherError as e:
            return None, False, str(e), {"stale": False, "age": 0}
        except Exception as e:
            log.error("获取天气异常: %s", e, exc_info=True)
            return None, False, f"执行异常: {e}", {"stale": False, "age": 0}
        with _CACHE_LOCK:
            _WEATHER_CACHE.update({"key": cache_key, "ts": time.time(), "data": data})
        return data, False, "", {"stale": False, "age": 0}

    def _weather(self, force: bool = False) -> None:
        # ?fresh=1 不需要登录，全局限一次/15 秒，避免被匿名刷爆和风配额
        if force:
            now = time.time()
            if now - _FORCE_STATE["ts"] < FORCE_MIN_GAP:
                force = False
            else:
                _FORCE_STATE["ts"] = now
        cfg = config_loader.merged(config_loader.load_raw())
        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        data, cached, error, info = self._cached_weather(cfg, key_pem, force)
        if error:
            self._json({"ok": False, "weather": None, "cached": False,
                        "error": error, "stale": False, "age": 0})
            return
        self._json({"ok": True, "weather": data, "cached": cached, "error": "",
                    "stale": info["stale"], "age": info["age"]})

    def _message(self, body: dict) -> None:
        """生成推送消息预览（不推送）。

        mode=alert 时用示例预警渲染，不依赖天气接口（多数时候本来就没有真预警）。
        """
        cfg = self._config_from_body(body)
        name = cfg.get("location_name") or cfg.get("location") or "当前位置"
        tpl = cfg.get("message")
        mode = "alert" if str(body.get("mode", "")).lower() == "alert" else "broadcast"

        if mode == "alert":
            previews = []
            for key, label, style in core.preview_styles(cfg):
                t, b = formatter.render(None, name, "alert", style, tpl,
                                        warnings=[SAMPLE_WARNING])
                # 企业微信通道没有标题字段，预览里也把标题并进正文，所见即所得
                inline = key.startswith("wecom")
                previews.append({"key": key, "label": label, "style": style,
                                 "title": t, "inline": inline,
                                 "body": formatter.inline_title(t, b, style)
                                         if inline else b})
            self._json({"ok": True, "error": "", "mode": "alert", "sample": True,
                        "previews": previews, "title": previews[0]["title"],
                        "body": previews[0]["body"], "style": previews[0]["style"],
                        "source": "示例预警"})
            return

        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        weather, cached, error, _info = self._cached_weather(cfg, key_pem)
        if error:
            self._json({"ok": False, "error": error, "error_hint": "",
                        "previews": [], "title": "", "body": ""})
            return
        previews = []
        for key, label, style in core.preview_styles(cfg):
            t, b = formatter.render(weather, name, "broadcast", style, tpl)
            inline = key.startswith("wecom")
            previews.append({"key": key, "label": label, "style": style,
                             "title": t, "inline": inline,
                             "body": formatter.inline_title(t, b, style)
                                     if inline else b})
        first = previews[0]
        self._json({"ok": True, "error": "", "mode": "broadcast", "sample": False,
                    "previews": previews, "cached": cached,
                    "title": first["title"], "body": first["body"],
                    "style": first["style"],
                    "source": weather.get("source_label", ""),
                    "fallback_reason": weather.get("fallback_reason", "")})

    def _test(self, body: dict) -> None:
        """测试推送：发两条 —— 日常播报（真实天气）+ 天气预警（示例预警）。

        只发到**测试通道**（role=test），正式通道一条都不碰：
        开发期反复调格式不会把消息刷到正式群；正式群只收定时播报和真预警。
        收到的样子就是将来真推送的样子，都带「🧪 测试」标记。
        """
        cfg = self._config_from_body(body)
        # 测试推送只验证通道，不要求和风凭据（没 key 时自动降级 Open-Meteo）
        errors = self._channel_errors(cfg, config_loader.ROLE_TEST)
        if errors:
            self._json({"ok": False, "channels": [], "errors": errors, "messages": []})
            return

        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        weather, _cached, werr, _info = self._cached_weather(cfg, key_pem)
        if werr:
            self._json({"ok": False, "channels": [], "errors": [f"取不到天气数据：{werr}"],
                        "messages": []})
            return

        name = cfg.get("location_name") or cfg.get("location") or "当前位置"
        tpl = cfg.get("message") or {}
        test_styles = core.preview_styles(cfg, config_loader.ROLE_TEST)
        preview_style = test_styles[0][2]
        # 预览跟随首选测试通道：企微没有标题字段，预览里也把标题并进正文
        inline = test_styles[0][0].startswith("wecom")

        messages = []
        for i, (mode, label, note, is_sample) in enumerate(TEST_MESSAGES):
            if i:
                time.sleep(TEST_GAP_SEC)   # 两条之间留间隔，保证接收顺序

            def renderer(style: str, mode=mode, note=note):
                if mode == "alert":
                    t, b = formatter.render(None, name, "alert", style, tpl,
                                            warnings=[SAMPLE_WARNING])
                else:
                    t, b = formatter.render(weather, name, "broadcast", style, tpl)
                return tag_test(t, b, style, note)

            channels, _ = core.push_all(cfg, renderer, config_loader.ROLE_TEST)
            p_title, p_body = renderer(preview_style)
            p_body = formatter.inline_title(p_title, p_body, preview_style) if inline else p_body
            messages.append({"mode": mode, "label": label, "note": note,
                             "sample": is_sample, "title": p_title, "body": p_body,
                             "inline": inline, "style": preview_style,
                             "channels": channels})

        flat = [{"message": m["label"], **c} for m in messages for c in m["channels"]]
        self._json({"ok": all(c["ok"] for c in flat) if flat else False,
                    "errors": [], "channels": flat, "messages": messages,
                    "source": weather.get("source_label", ""),
                    "fallback_reason": weather.get("fallback_reason", "")})

    def _test_channel(self, body: dict) -> None:
        """单通道连通性测试：只发一条极简消息，不取天气、不渲染模板。

        走该通道自己的地址（正式通道就用正式 webhook，测试通道就用测试 webhook），
        这是正式地址仅有的两种用途之一：连通性验证 + 正式播报。

        body {id, config}：优先在页面提交的配置里按 id 找通道（测试未保存的
        凭据），找不到再回已保存配置。
        """
        ch_id = str(body.get("id") or "")
        cfg = self._config_from_body(body)
        chans = (cfg.get("push") or {}).get("channels") or []
        ch = config_loader.find_channel(chans, ch_id)
        if not ch:
            self._json({"ok": False, "error": "通道不存在或已被删除"})
            return
        name = ch.get("name") or config_loader.CHANNEL_TYPES.get(
            ch.get("type"), {}).get("label", "通道")
        role_label = config_loader.roles_label(ch)
        title = "🧪 连通性测试"
        note = (f"天气提醒服务测试：收到本条说明「{name}」（{role_label}）连接正常。"
                "（在设置页触发，非真实播报）")

        try:
            if ch.get("type") == "wecom":
                if not str(ch.get("webhook", "")).strip():
                    self._json({"ok": False, "error": f"「{name}」还没填写 Webhook 地址"})
                    return
                mt = ("text" if str(ch.get("msg_type", "")).lower() == "text"
                      else "markdown")
                content = (f"**{title}**\n{note}" if mt == "markdown"
                           else f"{title}\n{note}")
                pusher.push_wecom(ch["webhook"], content, mt)
            elif ch.get("type") == "magicpush":
                if not (str(ch.get("base_url", "")).strip()
                        and str(ch.get("token", "")).strip()):
                    self._json({"ok": False,
                                "error": f"「{name}」还没填写服务地址和接口令牌"})
                    return
                pusher.push_magicpush(ch["base_url"], ch["token"], title, note)
            else:
                self._json({"ok": False, "error": f"未知通道类型：{ch.get('type')}"})
                return
        except Exception as e:
            self._json({"ok": False, "error": str(e)})
            return

        log.info("通道连通性测试通过：%s", name)
        self._json({"ok": True, "id": ch_id})

    def _test_qweather(self, body: dict) -> None:
        """和风 API 连通性测试：真实调用一次 GeoAPI。

        body {config}：优先用页面提交的未保存凭据（host/项目ID/凭据ID），
        私钥固定读磁盘上的 conf/ed25519-private.pem。
        """
        cfg = self._config_from_body(body)
        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        try:
            detail = core.test_qweather(cfg, key_pem)
        except QWeatherError as e:
            self._json({"ok": False, "error": str(e)})
            return
        except Exception as e:
            log.error("和风 API 测试异常: %s", e, exc_info=True)
            self._json({"ok": False, "error": f"执行异常: {e}"})
            return
        log.info("和风 API 测试通过")
        self._json({"ok": True, "detail": detail})

    def _qweather_key(self, body: dict) -> None:
        """生成 / 替换和风 Ed25519 密钥对（写 conf/ 下的两个 pem）。

        body {action}：
          generate —— 现场生成一对新密钥（用户在和风控制台创建 JWT 凭据时用）
          replace  —— 用 body.private_key 里粘贴的私钥，公钥由私钥推导

        换密钥会让控制台里的旧公钥失效，所以旧私钥先备份；公钥永远由私钥
        算出来，避免用户分开填两段结果对不上（那是「配好了却一直 401」的
        常见原因）。
        """
        action = str(body.get("action") or "generate").strip().lower()
        try:
            if action == "replace":
                priv, pub = config_loader.public_from_private(body.get("private_key") or "")
            elif action == "generate":
                priv, pub = config_loader.generate_keypair()
            else:
                self._json({"ok": False, "error": f"未知操作：{action}"})
                return
        except ValueError as e:
            self._json({"ok": False, "error": str(e)})
            return
        except Exception as e:      # cryptography 缺失等
            log.error("生成密钥失败: %s", e, exc_info=True)
            self._json({"ok": False, "error": f"生成密钥失败：{e}"})
            return

        try:
            backup = config_loader.save_keypair(priv, pub)
        except OSError as e:
            log.error("写入密钥文件失败: %s", e, exc_info=True)
            self._json({"ok": False, "error": f"写入密钥文件失败：{e}"})
            return

        log.info("和风密钥对已%s%s", "生成" if action == "generate" else "替换",
                 f"（旧私钥备份 {os.path.basename(backup)}）" if backup else "")
        self._json({"ok": True, "action": action,
                    "backup": os.path.basename(backup) if backup else "",
                    "key": config_loader.key_info(),
                    "status": build_status()})

    # ---------- 通道添加 / 删除 ----------
    def _channel_add(self, body: dict) -> None:
        """添加通道实例：往列表追加一个（可同类多个），凭据由用户随后填写。

        body {type, roles?}：只指定通道类型；用途（正式/测试）添加后在卡片上勾选，
        也可以一次传 roles 直接定好。
        """
        ch_type = str(body.get("type") or "")
        if ch_type not in config_loader.CHANNEL_TYPES:
            self._json({"ok": False, "error": f"未知通道类型：{ch_type or '(空)'}"})
            return
        roles = config_loader.normalize_roles(
            body.get("roles") if "roles" in body else body.get("role"))
        raw = config_loader.load_raw()
        try:
            ch = config_loader.add_channel(raw, ch_type, roles)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)})
            return
        config_loader.save_raw(raw)
        log.info("已添加推送通道：%s（%s，用途 %s，默认启用，待填写凭据）",
                 ch["name"], ch["id"], config_loader.roles_label(ch))
        self._json({"ok": True, "id": ch["id"], "roles": ch["roles"],
                    "status": build_status()})

    def _channel_remove(self, body: dict) -> None:
        """删除通道实例：从列表移除，已填写的凭据一并丢弃。"""
        ch_id = str(body.get("id") or "")
        raw = config_loader.load_raw()
        ch = config_loader.remove_channel(raw, ch_id)
        if not ch:
            self._json({"ok": False, "error": "通道不存在或已删除"})
            return
        config_loader.save_raw(raw)
        log.info("已删除推送通道：%s（%s）", ch.get("name"), ch_id)
        self._json({"ok": True, "id": ch_id, "status": build_status()})

    # ---------- 鉴权接口 ----------
    def _issue_session(self, raw: dict) -> None:
        hours = config_loader.auth_session_hours(raw)
        secret = AUTH_SECRET or config_loader.ensure_auth_secret()
        token = web_auth.issue_token(secret, hours)
        self._set_cookie(web_auth.cookie_header(token, hours))

    @staticmethod
    def _check_new_password(value) -> str:
        pw = str(value or "")
        if len(pw) < web_auth.MIN_PASSWORD_LEN:
            return f"密码至少 {web_auth.MIN_PASSWORD_LEN} 位"
        if len(pw) > web_auth.MAX_PASSWORD_LEN:
            return f"密码最多 {web_auth.MAX_PASSWORD_LEN} 位"
        return ""

    def _auth_setup(self, body: dict) -> None:
        """首次使用设置访问密码：只允许在没有密码时调用一次。"""
        raw = config_loader.load_raw()
        if config_loader.auth_has_password(raw):
            self._json({"ok": False, "error": "访问密码已设置，请直接登录"}, 409)
            return
        err = self._check_new_password(body.get("password"))
        if err:
            self._json({"ok": False, "error": err}, 400)
            return
        pw_hash, salt = web_auth.hash_password(str(body["password"]))
        a = raw.setdefault("auth", {})
        a.update({"enabled": True, "password_hash": pw_hash, "salt": salt})
        config_loader.save_raw(raw)
        self._issue_session(raw)
        log.info("已设置访问密码，设置页鉴权生效")
        # 本次请求还没带上新 cookie，_auth_status() 里的 logged_in 会是 False；
        # 响应一旦到达浏览器就已登录，这里显式标成 True 避免语义误导
        self._json({"ok": True, "message": "访问密码已设置",
                    "auth": dict(self._auth_status(), logged_in=True)})

    def _auth_login(self, body: dict) -> None:
        ip = self._client_ip()
        blocked, wait = web_auth.login_blocked(ip)
        if blocked:
            self._json({"ok": False, "error": f"尝试次数过多，请 {wait} 秒后再试"}, 429)
            return
        raw = config_loader.load_raw()
        if not config_loader.auth_enabled(raw):
            self._json({"ok": True, "disabled": True, "message": "鉴权已关闭"})
            return
        a = raw.get("auth") or {}
        if not web_auth.verify_password(str(body.get("password") or ""),
                                        a.get("password_hash", ""), a.get("salt", "")):
            web_auth.note_fail(ip)
            log.warning("登录失败：%s", ip)
            self._json({"ok": False, "error": "密码不对，请重试"}, 401)
            return
        web_auth.clear_fails(ip)
        self._issue_session(raw)
        log.info("登录成功：%s", ip)
        self._json({"ok": True, "message": "登录成功",
                    "auth": dict(self._auth_status(), logged_in=True)})

    def _auth_logout(self) -> None:
        self._set_cookie(web_auth.clear_cookie_header())
        self._json({"ok": True, "message": "已退出登录"})

    def _auth_password(self, body: dict) -> None:
        """改密码：校验原密码，并轮换签名密钥让其他设备的会话立刻失效。"""
        raw = config_loader.load_raw()
        a = raw.get("auth") or {}
        if config_loader.auth_has_password(raw) and not web_auth.verify_password(
                str(body.get("old_password") or ""),
                a.get("password_hash", ""), a.get("salt", "")):
            self._json({"ok": False, "error": "原密码不对"}, 403)
            return
        err = self._check_new_password(body.get("new_password"))
        if err:
            self._json({"ok": False, "error": err}, 400)
            return
        pw_hash, salt = web_auth.hash_password(str(body["new_password"]))
        a = raw.setdefault("auth", {})
        a.update({"password_hash": pw_hash, "salt": salt})
        _rotate_secret(raw)          # 其他设备旧 cookie 全部作废
        self._issue_session(raw)      # 当前页面不下线
        log.info("访问密码已更新")
        self._json({"ok": True, "message": "密码已更新，其他设备的登录已失效",
                    "auth": dict(self._auth_status(), logged_in=True)})

    def _auth_toggle(self, body: dict) -> None:
        """开关鉴权；重新开启要求已经设过密码。"""
        enabled = bool(body.get("enabled"))
        raw = config_loader.load_raw()
        if enabled and not config_loader.auth_has_password(raw):
            self._json({"ok": False, "error": "请先设置访问密码再开启鉴权"}, 400)
            return
        raw.setdefault("auth", {})["enabled"] = enabled
        config_loader.save_raw(raw)
        log.info("设置页鉴权：%s", "开启" if enabled else "关闭")
        self._json({"ok": True,
                    "message": "鉴权已" + ("开启" if enabled else "关闭"),
                    "auth": self._auth_status()})

    # ---------- 具体处理 ----------
    @staticmethod
    def _channel_errors(cfg: dict, role: str = config_loader.ROLE_PROD) -> list:
        """该用途下已启用通道的凭据完整性检查（测试用途不阻塞正式播报）。"""
        errors = []
        chans = [c for c in (cfg.get("push") or {}).get("channels") or []
                 if isinstance(c, dict)]
        picked = [c for c in chans if c.get("enabled")
                  and config_loader.has_role(c, role)]
        if not picked:
            label = "正式" if role == config_loader.ROLE_PROD else "测试"
            errors.append(f"还没有启用的{label}通道")
        for ch in picked:
            name = ch.get("name") or config_loader.CHANNEL_TYPES.get(
                ch.get("type"), {}).get("label", "通道")
            if ch.get("type") == "wecom" and not str(ch.get("webhook", "")).strip():
                errors.append(f"「{name}」已启用，但 webhook 未填写")
            if ch.get("type") == "magicpush" and not (
                    str(ch.get("base_url", "")).strip()
                    and str(ch.get("token", "")).strip()):
                errors.append(f"「{name}」已启用，但地址或令牌未填写")
        return errors

    def _save_config(self, body: dict) -> None:
        user = body.get("config")
        if not isinstance(user, dict) or not user:
            self._json({"ok": False, "errors": ["请求体缺少 config"], "status": build_status()})
            return
        raw = config_loader.load_raw()
        user = sanitize_user_config(user)
        user = config_loader.merge_saved_location(user, raw)
        user = config_loader.overlay_saved(user, raw)
        errors = config_loader.validate(config_loader.merged(user),
                                        os.path.exists(config_loader.KEY_FILE),
                                        require_push=False)
        config_loader.save_raw(user)
        log.info("配置已保存（提示项 %d 条）", len(errors))
        self._json({"ok": not errors, "errors": errors, "status": build_status()})

    def _geoip(self, body: dict) -> None:
        log.info("按公网 IP 定位中…")
        result = geoip.detect()
        if not result.get("ok"):
            self._json({"ok": False, "error": result.get("error", "定位失败"),
                        "status": build_status()})
            return
        # 默认只返回定位结果、不写盘：这个接口首页公开可调用，
        # 只有页面明确传 apply=true 才落配置，避免误调把城市改掉
        if body.get("apply", False):
            cfg = config_loader.apply_geo(result)
            log.info("已应用定位：%s → %s", result["city"], cfg["location"])
            self._json({"ok": True, "geo": result, "status": build_status()})
        else:
            self._json({"ok": True, "geo": result, "status": build_status()})

    def _city(self, body: dict) -> None:
        """按名称搜城市（省/市/区县），供首页与设置页的输入框提示。

        优先用页面提交的凭据（设置页可能还没保存），否则用已保存配置。
        """
        keyword = str(body.get("keyword") or "").strip()
        cfg = self._config_from_body(body)
        key_pem = (config_loader.load_private_key()
                   if os.path.exists(config_loader.KEY_FILE) else None)
        try:
            items = core.search_city(cfg, key_pem, keyword, number=12)
        except QWeatherError as e:
            self._json({"ok": False, "items": [], "error": str(e)})
            return
        except Exception as e:
            log.error("城市搜索异常: %s", e, exc_info=True)
            self._json({"ok": False, "items": [], "error": f"搜索异常: {e}"})
            return
        self._json({"ok": True, "items": items, "error": ""})

    def _set_location(self, body: dict) -> None:
        """用搜索结果里选中的城市替换当前位置（立即生效，不等保存）。"""
        item = body.get("city") if isinstance(body.get("city"), dict) else body
        try:
            cfg = config_loader.apply_city(item)
        except ValueError as e:
            self._json({"ok": False, "error": str(e), "status": build_status()})
            return
        log.info("位置已改为：%s（%s）", cfg.get("location_name"), cfg.get("location"))
        self._json({"ok": True, "error": "",
                    "location": config_loader.current_location(cfg),
                    "status": build_status()})

    def _restart(self, body: dict) -> None:
        """保存端口并原地重启进程。

        先校验端口 → 落盘 → 回响应 → 起线程 exec 自身。
        进程原地替换，PID 不变，配置、密钥、推送记录都不受影响。
        """
        cfg_in = body.get("config")
        port = body.get("port")
        if port is None and isinstance(cfg_in, dict):
            port = (cfg_in.get("server") or {}).get("port")
        if port is None:
            port = RUNNING_PORT
        try:
            port = int(str(port).strip())
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "端口必须是数字"})
            return
        if not (config_loader.PORT_MIN <= port <= config_loader.PORT_MAX):
            self._json({"ok": False, "error":
                        f"端口需在 {config_loader.PORT_MIN}–{config_loader.PORT_MAX} 之间"})
            return
        if port != RUNNING_PORT and _port_in_use(port):
            self._json({"ok": False, "error": f"端口 {port} 已被占用，请换一个"})
            return

        raw = config_loader.load_raw()
        if isinstance(cfg_in, dict) and cfg_in:
            raw = config_loader.overlay_saved(
                config_loader.merge_saved_location(
                    sanitize_user_config(cfg_in), raw), raw)
        if not isinstance(raw.get("server"), dict):
            raw["server"] = {}
        raw["server"]["port"] = port
        config_loader.save_raw(raw)

        changed = port != RUNNING_PORT
        log.info("收到重启请求：端口 %s%s", port, "（已变更）" if changed else "（未变更）")
        self._json({"ok": True, "port": port, "old_port": RUNNING_PORT,
                    "changed": changed,
                    "url": f"http://{_display_host(RUNNING_HOST)}:{port}",
                    "message": (f"端口已改为 {port}，服务正在重启"
                                if changed else "服务正在重启")})
        threading.Thread(target=_restart_soon, args=(port,), daemon=True).start()

    def _run(self, body: dict) -> None:
        cfg = self._config_from_body(body)
        errors = config_loader.validate(cfg, os.path.exists(config_loader.KEY_FILE))
        if errors:
            self._json({"ok": False, "error": "；".join(errors), "channels": [],
                        "title": "", "body": ""})
            return
        raw = body.get("config")
        if isinstance(raw, dict) and raw:
            raw = sanitize_user_config(raw)
            raw = config_loader.merge_saved_location(raw)
            raw = config_loader.overlay_saved(raw)
            config_loader.save_raw(raw)
            cfg = config_loader.merged(raw)
        try:
            result = core.run_forecast(cfg, config_loader.load_private_key())
        except QWeatherError as e:
            result = {"ok": False, "error": str(e), "channels": [], "title": "", "body": ""}
        except Exception as e:
            log.error("播报异常: %s", e, exc_info=True)
            result = {"ok": False, "error": f"执行异常: {e}", "channels": [],
                      "title": "", "body": ""}
        result["status"] = build_status()
        self._json(result)

    def _run_warning_now(self, body: dict) -> None:
        """设置页「立即检查预警」：只在有新预警时推送，与定时轮询共用去重。"""
        cfg = self._config_from_body(body)
        try:
            result = core.run_warning(cfg, config_loader.load_private_key())
        except QWeatherError as e:
            result = {"ok": False, "sent": False, "error": str(e)}
        except Exception as e:
            log.error("预警检查异常: %s", e, exc_info=True)
            result = {"ok": False, "sent": False, "error": f"执行异常: {e}"}
        result["status"] = build_status()
        self._json(result)


def ensure_location() -> None:
    """首次启动：配置里没有城市时，按公网 IP 自动定位并写入。"""
    cfg = config_loader.merged(config_loader.load_raw())
    if str(cfg.get("location", "")).strip():
        log.info("城市已配置：%s（%s）", cfg.get("location_name") or cfg["location"],
                 cfg.get("location_source") or "手动")
        return
    log.info("未配置城市，按公网 IP 自动定位…")
    result = geoip.detect()
    if not result.get("ok"):
        log.warning("自动定位失败：%s（可在设置页手动填写城市）", result.get("error"))
        return
    cfg = config_loader.apply_geo(result)
    log.info("已自动定位：%s → %s（来源 %s）", result["city"], cfg["location"], result["source"])
    if not result.get("inside_china"):
        log.warning("定位结果不在中国大陆，若使用了代理请到设置页手动改城市")


def main() -> None:
    global RUNNING_HOST, RUNNING_PORT, START_ARGV, AUTH_SECRET

    ap = argparse.ArgumentParser(description="weather-notify 本地服务")
    ap.add_argument("--port", type=int, default=None,
                    help="监听端口，缺省读 conf/config.json 的 server.port"
                         f"（默认 {config_loader.DEFAULT_PORT}）")
    ap.add_argument("--host", default=None,
                    help="监听地址，缺省读 conf/config.json 的 server.host"
                         f"（默认 {config_loader.DEFAULT_HOST}；远程部署写局域网 IP 或 0.0.0.0）")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--no-geoip", action="store_true", help="启动时不按 IP 自动定位")
    args = ap.parse_args()

    # 日志同时落一份文件：作为系统应用运行时标准输出被系统收走，
    # 排查问题主要看 <数据目录>/logs/weather-notify.log
    try:
        core.setup_file_log()
    except OSError as e:
        log.warning("无法写入日志文件（%s）：继续用标准输出记录", e)

    if not os.path.exists(config_loader.CONF_FILE):
        config_loader.save_raw(config_loader.TEMPLATE)
        log.info("已生成配置模板 %s", config_loader.CONF_FILE)

    # cookie 签名密钥（配置里没有就生成一次）；顺手把私钥权限收敛到 600
    AUTH_SECRET = config_loader.ensure_auth_secret()
    key_note = config_loader.ensure_key_permissions()
    if key_note:
        log.info(key_note)
    pub_note = config_loader.ensure_public_key()
    if pub_note:
        log.warning(pub_note)

    raw_cfg = config_loader.load_raw()
    if not config_loader.auth_enabled(raw_cfg):
        log.warning("设置页鉴权已关闭（auth.enabled=false）：任何能访问本服务的人都能查看与修改配置")
    elif not config_loader.auth_has_password(raw_cfg):
        log.warning("设置页鉴权已开启但尚未设置访问密码：首次打开 /settings 会先要求设置")
    else:
        log.info("设置页鉴权已开启：/settings 与配置 / 推送接口需要登录")

    if not args.no_geoip:
        try:
            ensure_location()
        except Exception as e:  # 定位失败不能挡住服务启动
            log.warning("启动定位异常: %s", e)

    port = args.port or config_loader.port_from()
    if not (config_loader.PORT_MIN <= port <= config_loader.PORT_MAX):
        log.error("端口 %s 超出范围（%s–%s）", port,
                  config_loader.PORT_MIN, config_loader.PORT_MAX)
        sys.exit(2)
    host = str(args.host or config_loader.host_from()).strip()

    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        log.error("无法监听 %s:%s：%s（换端口/地址，或停掉占用它的进程）", host, port, e)
        sys.exit(1)

    RUNNING_HOST = host
    RUNNING_PORT = port
    START_ARGV = sys.argv[1:]

    # 调度线程是本进程唯一的定时载体：进程在它就在，进程退出全部停止
    threading.Thread(target=_schedule_loop, daemon=True).start()
    sch = config_loader.schedule_from(config_loader.merged(config_loader.load_raw()))
    jobs = []
    if sch["enabled"] and sch["times"]:
        jobs.append("日常播报 每天 " + "、".join(sch["times"]))
    if sch["warning_enabled"]:
        jobs.append("预警轮询 每 %d 分钟" % sch["warning_interval_min"])
    log.info("内置调度：%s（均随本服务进程启停，无外部定时器）",
             "；".join(jobs) if jobs else "全部未开启（在设置页开启）")

    shown = _display_host(host)
    url = f"http://{shown}:{port}"
    log.info("服务已启动: %s:%s（监听 %s）· %s（首页）· %s/settings（设置）",
             shown, port, host, url, url)
    if host in ("0.0.0.0", "::"):
        log.info("监听所有网卡：请用本机局域网 IP 访问；设置页「当前地址」会显示浏览器实际用的地址")
    print(f"\n  监听地址 → {host}:{port}\n  首页 → {url}\n  设置 → {url}/settings\n", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("已退出")
        srv.server_close()


if __name__ == "__main__":
    main()
