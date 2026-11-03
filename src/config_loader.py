"""配置管理：conf/config.json（私密数据）+ 默认值合并 + 校验。

conf/ 整体 gitignore，不进版本库。
城市字段说明：
    location        送给和风 API 的值：LocationID（101010100）或「经度,纬度」（116.41,39.90）
    location_name   展示用城市名
    location_lat/lon、location_source(ip|manual)、location_updated  定位来源与时间
"""
import copy
import json
import os
import re
import secrets
from datetime import datetime

# 代码放在 src/ 下，而 conf/（含凭据与私钥）、web/、logs/ 都在项目根目录，
# 所以根目录要往上跳一层；直接在根目录跑（不经过 src/）时保持原样。
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = (os.path.dirname(SRC_DIR) if os.path.basename(SRC_DIR) == "src"
            else SRC_DIR)

# 运行期数据目录：默认就是项目根（conf/ 与 logs/ 在根下）。
# 打包成系统应用（fnOS fpk）时程序目录只读，用 WEATHER_NOTIFY_DATA_DIR 把
# 数据指到可写分区（fpk 里设为 TRIM_PKGVAR），conf/ 与 logs/ 在其下自动创建。
# 该变量为空或未设置时行为与原来完全一致。
DATA_DIR = os.environ.get("WEATHER_NOTIFY_DATA_DIR", "").strip() or ROOT_DIR

CONF_DIR = os.path.join(DATA_DIR, "conf")
LOG_DIR = os.path.join(DATA_DIR, "logs")
CONF_FILE = os.path.join(CONF_DIR, "config.json")
KEY_FILE = os.path.join(CONF_DIR, "ed25519-private.pem")
PUB_FILE = os.path.join(CONF_DIR, "ed25519-public.pem")

# 本地服务监听地址与端口（改后需重启服务）
# 默认只监听本机；部署到 NAS 等远程机器时改成 0.0.0.0 才能从局域网访问
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18770
PORT_MIN, PORT_MAX = 1024, 65535

# 设置页访问鉴权（默认开启；首次使用在登录页设置密码）
AUTH_ENABLED = True
AUTH_SESSION_HOURS = 168            # 登录保持 7 天
AUTH_SECRET_MIN_LEN = 32            # HMAC 密钥长度（hex 字符数）

# 内置定时播报（默认关闭；时间点按北京时间 HH:MM）
# 日常播报与预警轮询都由 server.py 进程内的调度线程承担：
# 服务在 → 全部功能在；服务停 → 全部停止。不依赖 1Panel / cron。
SCHEDULE_ENABLED = False
SCHEDULE_TIMES = ["07:30"]
SCHEDULE_MAX_TIMES = 12
SCHEDULE_WARNING_ENABLED = False
SCHEDULE_WARNING_INTERVAL = 10     # 预警轮询间隔（分钟）
SCHEDULE_WARNING_MIN = 1
SCHEDULE_WARNING_MAX = 120

LOCATION_FIELDS = ("location", "location_name", "location_admin", "location_lat",
                   "location_lon", "location_source", "location_updated",
                   "location_ip", "location_note")

# ---- 消息模板默认值 ----
# 区块占位符：{warnings} {current} {air} {hourly} {daily} {indices}
# 标量占位符：{city} {temp} {feels} {text} {wind} {humidity} {aqi} {aqi_category}
#             {time} {source} {alert_type} {alert_level} {alert_title} {alert_status}
DEFAULT_BROADCAST_TITLE = "{icon} {city}天气 {date} {weekday} · {temp}°C {text}"
DEFAULT_BROADCAST_BODY = "\n\n".join((
    "{warnings}", "{current}", "{air}", "{daily}", "{hourly}", "{indices}",
))
DEFAULT_ALERT_TITLE = "⚠️ {city}{alert_type}预警（{alert_level}）· {clock} 发布"
# 城市名统一放在标题里，正文不再重复（正文只有预警内容）
DEFAULT_ALERT_BODY = "{warnings}"

# 设置页「恢复默认」用
MESSAGE_DEFAULTS = {
    "broadcast_title": DEFAULT_BROADCAST_TITLE,
    "broadcast_body": DEFAULT_BROADCAST_BODY,
    "alert_title": DEFAULT_ALERT_TITLE,
    "alert_body": DEFAULT_ALERT_BODY,
}

# 历代默认模板：只有「仍是某一代默认值」的配置才会跟着升级，用户改过的一律不动。
# 按发布顺序排列，新增一代就往列表里追加一项，不用改 migrate_message()。
LEGACY_MESSAGE_DEFAULTS = [
    # v1：标题没有日期 / 发布时刻
    {
        "broadcast_title": "{icon} {city}天气 · {temp}°C {text}",
        "alert_title": "⚠️ {city}{alert_type}预警（{alert_level}）",
    },
    # v2：预警正文首行带 📍 {city}（城市名与「测试」字样已统一移到标题）
    {
        "alert_body": "📍 {city}\n\n{warnings}",
    },
]

# 提醒通道：可添加多个实例（比如多个企业微信群机器人）。
# 配置结构 push.channels = [{id, type, name, enabled, ...凭据字段}, ...]；
# 删除通道即从列表移除。type 只能是 CHANNEL_TYPES 里的键。
CHANNEL_TYPES = {
    "wecom": {"label": "企业微信机器人", "desc": "群机器人 webhook",
              "fields": {"webhook": "", "msg_type": "text"}},
    "magicpush": {"label": "MagicPush 魔法推送", "desc": "统一接口，可再分发到多端",
                  "fields": {"base_url": "", "token": ""}},
}
# 老配置迁移时推断「用户配置过」的字段：只认必须主动填写的凭据。
# magicpush 的 base_url 曾有硬编码默认值（http://192.168.1.100:8083），
# 不能作为「用户配置过」的依据，只看 token。
CHANNEL_INFER_FIELDS = {"wecom": ("webhook",),
                        "magicpush": ("token",)}
CHANNEL_MAX = 16                    # 通道实例上限，防止误操作无限加
# 删除墓碑：记住最近删掉的通道 id。设置页保存的是「页面打开时的快照」，
# 不记墓碑的话，删除后紧跟的一次自动保存（或另一个开着的旧页面）会把
# 已删通道当成「新 id」重新追加，表现为「删了又回来」。
REMOVED_MAX = 32

# 通道用途：正式 = 接定时播报/预警；测试 = 只接设置页的「测试推送」。
# 两者可以同时勾（一个通道既发正式播报，也用来调格式），所以存成列表 roles。
CHANNEL_ROLES = {"prod": "正式", "test": "测试"}
ROLE_PROD, ROLE_TEST = "prod", "test"
ROLE_ORDER = (ROLE_PROD, ROLE_TEST)


def normalize_roles(value) -> list:
    """把 role / roles 的各种写法归一成 ["prod"]、["test"] 或 ["prod", "test"]。

    兼容早期单值 role（老配置、缓存的老页面都可能还在提交它）。
    缺失或全是脏值时按「正式」处理 —— 老配置没写用途也必须照常收播报。
    """
    items = value if isinstance(value, (list, tuple, set)) else [value]
    picked = {str(v or "").strip().lower() for v in items}
    roles = [r for r in ROLE_ORDER if r in picked]
    return roles or [ROLE_PROD]


def channel_roles(ch: dict) -> list:
    """取某个通道的用途列表（兼容只有 role 的老配置）。"""
    if not isinstance(ch, dict):
        return [ROLE_PROD]
    if "roles" in ch:
        return normalize_roles(ch.get("roles"))
    return normalize_roles(ch.get("role"))


def has_role(ch: dict, role: str) -> bool:
    """该通道是否承担某个用途（正式播报 / 测试推送）。"""
    return role in channel_roles(ch)


def roles_label(ch_or_roles) -> str:
    """用途的中文说明，如「正式 + 测试」。"""
    roles = channel_roles(ch_or_roles) if isinstance(ch_or_roles, dict) \
        else normalize_roles(ch_or_roles)
    return " + ".join(CHANNEL_ROLES.get(r, r) for r in roles)


def _new_channel(ch_type: str, src: dict | None = None) -> dict:
    """生成一个通道实例；src 为老结构里的通道配置（迁移用）。"""
    meta = CHANNEL_TYPES.get(ch_type)
    if not meta:
        raise ValueError(f"未知通道类型：{ch_type}")
    ch = {"id": secrets.token_hex(4), "type": ch_type,
          "name": meta["label"], "enabled": bool((src or {}).get("enabled")),
          "roles": channel_roles(src or {})}
    ch.update({k: (src or {}).get(k, v) for k, v in meta["fields"].items()})
    return ch


def _push() -> dict:
    return {"channels": []}

DEFAULTS = {
    "server": {"host": DEFAULT_HOST, "port": DEFAULT_PORT},
    "auth": {"enabled": AUTH_ENABLED, "password_hash": "", "salt": "",
             "secret": "", "session_hours": AUTH_SESSION_HOURS},
    "schedule": {"enabled": SCHEDULE_ENABLED, "times": list(SCHEDULE_TIMES),
                 "warning_enabled": SCHEDULE_WARNING_ENABLED,
                 "warning_interval_min": SCHEDULE_WARNING_INTERVAL},
    "location": "",
    "location_name": "",
    "location_admin": "",
    "location_lat": None,
    "location_lon": None,
    "location_source": "",
    "location_updated": "",
    "location_ip": "",
    "location_note": "",
    "hourly_hours": 6,
    "forecast_days": 7,         # 逐日预报天数（和风档位 3/7/10/15/30）
    "indices_types": "3,5,9",  # 穿衣/紫外线/感冒
    "alert_min_level": "蓝色",  # 白色/蓝色/黄色/橙色/红色
    "alert_types": [],          # 空 = 全部类型
    "alert_resend_hours": 6,    # 同一预警重复推送间隔（升级立即重推）
    "push": _push(),
    "message": {
        "broadcast_title": DEFAULT_BROADCAST_TITLE,
        "broadcast_body": DEFAULT_BROADCAST_BODY,
        "alert_title": DEFAULT_ALERT_TITLE,
        "alert_body": DEFAULT_ALERT_BODY,
    },
}

TEMPLATE = {
    "server": {"host": DEFAULT_HOST, "port": DEFAULT_PORT},
    "auth": {"enabled": AUTH_ENABLED, "password_hash": "", "salt": "",
             "secret": "", "session_hours": AUTH_SESSION_HOURS},
    "schedule": {"enabled": SCHEDULE_ENABLED, "times": list(SCHEDULE_TIMES),
                 "warning_enabled": SCHEDULE_WARNING_ENABLED,
                 "warning_interval_min": SCHEDULE_WARNING_INTERVAL},
    "qweather": {
        "api_host": "",
        "project_id": "",
        "credential_id": "",
    },
    "location": "",
    "location_name": "",
    "location_admin": "",
    "location_lat": None,
    "location_lon": None,
    "location_source": "",
    "location_updated": "",
    "location_ip": "",
    "location_note": "",
    "hourly_hours": 6,
    "forecast_days": 7,
    "indices_types": "3,5,9",
    "alert_min_level": "蓝色",
    "alert_types": [],
    "alert_resend_hours": 6,
    "push": _push(),
    "message": dict(MESSAGE_DEFAULTS),
}


def merged(user: dict) -> dict:
    """用户配置与默认值深度合并。"""
    return _deep_merge(DEFAULTS, user or {})


def validate(cfg: dict, key_exists: bool = True, require_push: bool = True) -> list:
    """返回错误列表，空列表代表通过。"""
    errors = []
    qw = cfg.get("qweather") or {}
    missing_qw = [label for field, label in
                  (("api_host", "API Host"), ("project_id", "项目 ID"),
                   ("credential_id", "凭据 ID"))
                  if not str(qw.get(field, "")).strip()]
    if missing_qw:
        errors.append("未配置和风天气 API：" + "、".join(missing_qw))
    if not key_exists:
        errors.append("缺少私钥文件 conf/ed25519-private.pem")
    if not str(cfg.get("location", "")).strip():
        errors.append("未设置城市（可在首页自动定位，或设置页手动填写）")
    if not require_push:
        return errors

    enabled = [c for c in ((cfg.get("push") or {}).get("channels") or [])
               if isinstance(c, dict) and c.get("enabled")]
    # 测试用途不参与正式播报，缺凭据也不算阻塞项
    prod = [c for c in enabled if has_role(c, ROLE_PROD)]
    if not prod:
        errors.append("至少启用一个正式推送通道")
    for ch in prod:
        name = ch.get("name") or CHANNEL_TYPES.get(ch.get("type"), {}).get(
            "label", ch.get("type", "通道"))
        if ch.get("type") == "wecom":
            if not str(ch.get("webhook", "")).strip():
                errors.append(f"「{name}」已启用，但 webhook 未填写")
        elif ch.get("type") == "magicpush":
            if not (str(ch.get("base_url", "")).strip()
                    and str(ch.get("token", "")).strip()):
                errors.append(f"「{name}」已启用，但服务地址或令牌未填写")
    return errors


def migrate_message(raw: dict) -> bool:
    """把仍是旧默认值的模板升级到当前默认，用户改过的不动。

    返回是否发生过改动，方便调用方决定要不要落盘。
    """
    msg = raw.get("message") if isinstance(raw, dict) else None
    if not isinstance(msg, dict):
        return False
    changed = False
    for legacy in LEGACY_MESSAGE_DEFAULTS:          # 逐代检查，命中任一代就升级到当前默认
        for key, old in legacy.items():
            new = MESSAGE_DEFAULTS.get(key)
            if new and str(msg.get(key, "")).strip() == old:
                msg[key] = new
                changed = True
    return changed


def migrate_sections(raw: dict) -> bool:
    """老配置补齐新增的 auth / schedule 段，并落盘一次。

    新增配置段时，已有的 conf/config.json 里没有这些键；只在 merged() 里补默认值
    的话，/api/state 返回给设置页的仍是「缺字段」，时间点卡片会显示成空的。
    """
    if not isinstance(raw, dict):
        return False
    changed = False
    a = raw.get("auth")
    if not isinstance(a, dict):
        raw["auth"] = {"enabled": AUTH_ENABLED, "password_hash": "", "salt": "",
                       "secret": "", "session_hours": AUTH_SESSION_HOURS}
        changed = True
    else:
        for key, val in (("enabled", AUTH_ENABLED),
                         ("session_hours", AUTH_SESSION_HOURS),
                         ("password_hash", ""), ("salt", ""), ("secret", "")):
            if key not in a:
                a[key] = val
                changed = True
    s = raw.get("schedule")
    if not isinstance(s, dict):
        raw["schedule"] = {"enabled": SCHEDULE_ENABLED,
                            "times": list(SCHEDULE_TIMES),
                            "warning_enabled": SCHEDULE_WARNING_ENABLED,
                            "warning_interval_min": SCHEDULE_WARNING_INTERVAL}
        changed = True
    else:
        for key, val in (("enabled", SCHEDULE_ENABLED),
                         ("times", list(SCHEDULE_TIMES)),
                         ("warning_enabled", SCHEDULE_WARNING_ENABLED),
                         ("warning_interval_min", SCHEDULE_WARNING_INTERVAL)):
            if key not in s:
                s[key] = val
                changed = True
    # 通道结构迁移：老配置是 {wecom: {...}, magicpush: {...}} 固定两键，
    # 现在是 channels 列表（可多实例）。启用过或填了关键凭据的才迁进来。
    push = raw.get("push")
    if isinstance(push, dict) and "channels" not in push:
        chans = []
        for key in CHANNEL_INFER_FIELDS:
            ch = push.get(key)
            if not isinstance(ch, dict):
                continue
            if ch.get("enabled") or any(str(ch.get(f, "")).strip()
                                        for f in CHANNEL_INFER_FIELDS[key]):
                chans.append(_new_channel(key, ch))
        raw["push"] = {"channels": chans}
        changed = True
    elif not isinstance(push, dict):
        raw["push"] = _push()
        changed = True
    elif not isinstance(push.get("channels"), list):
        push["channels"] = []
        changed = True
    else:
        # 列表里的脏项：类型未知或缺 id 的丢弃，字段按类型白名单保留
        clean = []
        mutated = False
        for ch in push["channels"]:
            if not isinstance(ch, dict) or ch.get("type") not in CHANNEL_TYPES:
                changed = True
                mutated = True
                continue
            meta = CHANNEL_TYPES[ch["type"]]
            keep = {"id", "name", "enabled", "roles", "role"} | set(meta["fields"])
            fixed = _new_channel(ch["type"], ch)
            fixed.update({k: v for k, v in ch.items() if k in keep})
            fixed["roles"] = channel_roles(fixed)   # 脏用途归一（空的补正式）
            fixed.pop("role", None)                 # 老单值字段并入 roles 后就不再留
            if not str(fixed.get("id") or "").strip():
                fixed["id"] = secrets.token_hex(4)
                changed = True
            if fixed != ch:
                mutated = True
            clean.append(fixed)
        if mutated:
            push["channels"] = clean
            changed = True
    return changed


def add_channel(raw: dict, ch_type: str, roles=None) -> dict:
    """往通道列表追加一个新实例（默认启用，凭据由用户随后填写），返回新通道。

    新通道默认只勾「正式」；用途随后在卡片上勾选，一个通道可以同时是正式和测试。
    同类通道可以反复添加（不同群、不同推送地址），名称自动去重。
    """
    chans = raw.setdefault("push", {}).setdefault("channels", [])
    if len(chans) >= CHANNEL_MAX:
        raise ValueError(f"最多添加 {CHANNEL_MAX} 个通道")
    ch = _new_channel(ch_type)
    ch["enabled"] = True             # 新加的通道默认启用，凭据随后填写
    if ch["id"] in removed_ids(raw):  # 概率极低：别让新通道一出生就躺在墓碑里
        ch["id"] = secrets.token_hex(4)
    if roles is not None:
        ch["roles"] = normalize_roles(roles)
    meta = CHANNEL_TYPES[ch_type]
    # 名称去重：企业微信机器人、企业微信机器人 2、…（名称不带用途，用途可随时改）
    base = meta["label"]
    ch["name"] = base
    names = {str(c.get("name") or "").strip() for c in chans}
    if ch["name"] in names:
        n = 2
        while f"{base} {n}" in names:
            n += 1
        ch["name"] = f"{base} {n}"
    chans.append(ch)
    return ch


def removed_ids(raw: dict) -> list:
    """配置里记录的删除墓碑（最近删掉的通道 id）。"""
    p = raw.get("push")
    v = p.get("removed") if isinstance(p, dict) else None
    return [str(x) for x in v] if isinstance(v, list) else []


def _remember_removed(push: dict, ch_id: str) -> None:
    """把删除的 id 记进墓碑，只保留最近 REMOVED_MAX 个。"""
    tomb = [x for x in (push.get("removed") or []) if x != ch_id]
    tomb.append(ch_id)
    push["removed"] = tomb[-REMOVED_MAX:]


def remove_channel(raw: dict, ch_id: str) -> dict | None:
    """按 id 从通道列表删除，返回被删的通道；找不到返回 None。"""
    push = raw.setdefault("push", {})
    chans = push.setdefault("channels", [])
    for i, ch in enumerate(chans):
        if ch.get("id") == ch_id:
            gone = chans.pop(i)
            _remember_removed(push, ch_id)     # 记墓碑，防止旧快照把它写回来
            return gone
    return None


def find_channel(channels, ch_id: str) -> dict | None:
    """按 id 在通道列表里找实例。"""
    for ch in channels or []:
        if isinstance(ch, dict) and ch.get("id") == ch_id:
            return ch
    return None


def load_raw() -> dict:
    """读取原始配置（不存在返回模板）。"""
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return json.loads(json.dumps(TEMPLATE))
    if isinstance(raw, dict):
        changed = migrate_message(raw)
        changed = migrate_sections(raw) or changed
        if changed:
            save_raw(raw)             # 一次性升级，下次读就不再匹配
    return raw


def save_raw(user: dict) -> None:
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(CONF_FILE, "w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False, indent=2)
    os.chmod(CONF_FILE, 0o600)


def merge_channels(submitted: list, saved: list, removed=None) -> list:
    """按 id 合并页面提交的通道列表与已保存的通道列表。

    设置页提交的是页面打开时的 DOM 快照。期间若通过 /api/channel/add
    新加了通道（页面还没重渲染），整表替换会把新通道冲掉 —— 之前「加不了
    第二个企业微信通道」就是这个原因。这里规则：
      · 提交了的通道按 id 更新，字段以页面为准
      · 已保存但没提交的通道原样保留（删除只走 /api/channel/remove）
      · 提交里带的新 id 直接追加，**但在删除墓碑里的 id 除外** ——
        否则删除后紧随的一次自动保存会把刚删的通道原样加回来
    """
    submitted = [c for c in (submitted or []) if isinstance(c, dict)]
    saved_map = {c.get("id"): c for c in (saved or [])
                 if isinstance(c, dict) and c.get("id")}
    tomb = {str(x) for x in (removed or [])}
    out, seen = [], set()
    for ch in submitted:
        cid = ch.get("id")
        if cid and cid in tomb and cid not in saved_map:
            continue                      # 已删除的通道：旧快照还在提交，忽略
        if cid and cid in saved_map:
            merged = dict(saved_map[cid])
            merged.update(ch)
            out.append(merged)
        else:
            out.append(ch)
        if cid:
            seen.add(cid)
    for ch in saved or []:
        if isinstance(ch, dict) and ch.get("id") and ch["id"] not in seen:
            out.append(ch)
    return out


def overlay_saved(user: dict, raw: dict | None = None) -> dict:
    """把页面提交的配置叠到已保存配置上。

    页面通常提交完整表单，但接口被脚本直接调用时可能只带几个字段，
    直接覆盖会把没提到的字段（凭据、通道）清空 —— 这里先合并再落盘。
    通道列表额外按 id 合并，避免旧快照冲掉刚添加的通道。
    """
    raw = load_raw() if raw is None else raw
    merged_cfg = _deep_merge(raw, user or {})
    p = (user or {}).get("push")
    if isinstance(p, dict) and isinstance(p.get("channels"), list):
        merged_cfg.setdefault("push", {})["channels"] = merge_channels(
            p["channels"], (raw.get("push") or {}).get("channels") or [],
            removed_ids(raw))
    return merged_cfg


def port_from(cfg: dict | None = None) -> int:
    """配置里的监听端口；缺失或越界时回落到 DEFAULT_PORT。"""
    cfg = cfg or merged(load_raw())
    try:
        port = int((cfg.get("server") or {}).get("port"))
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if PORT_MIN <= port <= PORT_MAX else DEFAULT_PORT


def host_from(cfg: dict | None = None) -> str:
    """配置里的监听地址；缺失或非法时回落到 DEFAULT_HOST。

    NAS 部署时把它设为 0.0.0.0（或某一块网卡的 IP），否则只能从 NAS 本机访问。
    """
    cfg = cfg or merged(load_raw())
    host = str((cfg.get("server") or {}).get("host") or "").strip()
    return host or DEFAULT_HOST


def normalize_times(value) -> list:
    """把时间点归一成 ['07:30', '18:00']：接受数组或逗号/空格分隔字符串。

    非法项直接丢掉，去重排序，最多 SCHEDULE_MAX_TIMES 个。
    """
    if isinstance(value, str):
        parts = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    out = []
    for part in parts:
        m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", str(part).strip())
        if not m:
            continue
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            item = f"{hour:02d}:{minute:02d}"
            if item not in out:
                out.append(item)
    return sorted(out)[:SCHEDULE_MAX_TIMES]


def clamp_warning_interval(value) -> int:
    """预警轮询间隔钳到 1–120 分钟，脏值回落到默认 10 分钟。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return SCHEDULE_WARNING_INTERVAL
    return min(SCHEDULE_WARNING_MAX, max(SCHEDULE_WARNING_MIN, n))


def schedule_from(cfg: dict | None = None) -> dict:
    """合并后的定时配置：时间点干净、预警间隔合法。

    两类定时任务都由 Web 服务进程内的调度线程执行，服务停止即全部停止。
    """
    cfg = cfg or merged(load_raw())
    sch = cfg.get("schedule") or {}
    return {"enabled": bool(sch.get("enabled", SCHEDULE_ENABLED)),
            "times": normalize_times(sch.get("times")),
            "warning_enabled": bool(sch.get("warning_enabled",
                                            SCHEDULE_WARNING_ENABLED)),
            "warning_interval_min": clamp_warning_interval(
                sch.get("warning_interval_min", SCHEDULE_WARNING_INTERVAL))}


def auth_enabled(raw: dict | None = None) -> bool:
    """是否开启鉴权。配置里没写 auth 段时按默认「开启」处理。"""
    raw = load_raw() if raw is None else raw
    a = raw.get("auth")
    return bool(a.get("enabled", AUTH_ENABLED)) if isinstance(a, dict) else AUTH_ENABLED


def auth_has_password(raw: dict | None = None) -> bool:
    raw = load_raw() if raw is None else raw
    a = raw.get("auth") or {}
    return bool(str((a.get("password_hash") if isinstance(a, dict) else "") or "").strip())


def auth_session_hours(raw: dict | None = None) -> int:
    raw = load_raw() if raw is None else raw
    a = raw.get("auth") or {}
    try:
        hours = int((a.get("session_hours") if isinstance(a, dict) else 0)
                    or AUTH_SESSION_HOURS)
    except (TypeError, ValueError):
        hours = AUTH_SESSION_HOURS
    return max(1, min(hours, 24 * 90))


def ensure_auth_secret() -> str:
    """返回 cookie 签名密钥，配置里没有就生成并落盘。

    只做一次：密钥存在配置文件里，服务 exec 重启后读到的还是同一把，
    所以登录状态不会因为换端口 / 重启而丢。
    """
    raw = load_raw()
    a = raw.get("auth")
    if not isinstance(a, dict):
        a = {}
        raw["auth"] = a
    secret = str(a.get("secret") or "").strip()
    if len(secret) >= AUTH_SECRET_MIN_LEN:
        return secret
    secret = secrets.token_hex(32)
    a["secret"] = secret
    save_raw(raw)
    return secret


def public_config(raw: dict) -> dict:
    """给设置页 /api/state 用的视图：抹掉鉴权密钥材料。

    密码哈希、盐、签名密钥一旦下发，等于把「造 cookie」的能力交给浏览器，
    所以只回开关、会话时长和「是否已设密码」三个非敏感字段。
    """
    out = copy.deepcopy(raw)
    a = out.get("auth")
    if isinstance(a, dict):
        out["auth"] = {
            "enabled": bool(a.get("enabled", AUTH_ENABLED)),
            "session_hours": auth_session_hours(raw),
            "has_password": bool(str(a.get("password_hash") or "").strip()),
        }
    return out


def ensure_key_permissions() -> str:
    """私钥必须是 600；返回一句日志文案，无需改动时返回空串。

    macOS 解压 / scp 常把私钥变成 644 甚至 755，同机其他用户就能读到
    和风 JWT 私钥。这里启动时收敛一次，避免靠人记。
    """
    if not os.path.exists(KEY_FILE) or not hasattr(os, "chmod"):
        return ""
    mode = 0
    try:
        mode = os.stat(KEY_FILE).st_mode & 0o777
        if not (mode & 0o077):
            return ""
        os.chmod(KEY_FILE, 0o600)
        return f"已把私钥权限从 {mode:o} 收敛到 600"
    except OSError as e:
        return f"私钥权限为 {mode:o}，自动收敛失败：{e}（建议手动 chmod 600）"


def current_location(cfg: dict | None = None) -> dict:
    """当前位置信息（供页面展示）。"""
    cfg = cfg or merged(load_raw())
    return {
        "location": str(cfg.get("location", "")).strip(),
        "name": cfg.get("location_name") or "",
        "admin": cfg.get("location_admin") or "",
        "lat": cfg.get("location_lat"),
        "lon": cfg.get("location_lon"),
        "source": cfg.get("location_source") or "",
        "updated": cfg.get("location_updated") or "",
        "ip": cfg.get("location_ip") or "",
        "note": cfg.get("location_note") or "",
    }


def parse_pair(location: str) -> tuple[float, float] | None:
    """「经度,纬度」→ (lat, lon)，不是这个格式或越界返回 None。

    中文输入法常打出全角逗号，这里一并兼容，空格也忽略。
    """
    text = str(location or "").replace("，", ",").replace(" ", "").strip()
    if "," not in text:
        return None
    head, _, tail = text.partition(",")
    try:
        lon, lat = float(head), float(tail)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def apply_geo(geo: dict) -> dict:
    """把 IP 定位结果写入配置文件，返回合并后的配置。"""
    raw = load_raw()
    raw["location"] = f"{float(geo['lon']):.2f},{float(geo['lat']):.2f}"
    raw["location_name"] = geo.get("city") or raw.get("location_name", "")
    raw["location_admin"] = geo.get("region") or ""
    raw["location_lat"] = geo.get("lat")
    raw["location_lon"] = geo.get("lon")
    raw["location_source"] = "ip"
    raw["location_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    raw["location_ip"] = geo.get("ip", "")
    raw["location_note"] = geo.get("warn") or ""
    save_raw(raw)
    return merged(raw)


def apply_city(item: dict) -> dict:
    """把用户选定的城市（GeoAPI 搜索结果）写入配置。

    存「经度,纬度」：和风实况/预报用它，空气质量和预警也直接有坐标可用；
    这样区县级的定位不会退化成整市。
    """
    name = str(item.get("name") or "").strip()
    try:
        lat, lon = float(item.get("lat")), float(item.get("lon"))
    except (TypeError, ValueError):
        raise ValueError("城市坐标无效")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("城市坐标超出范围")
    if not name:
        raise ValueError("城市名称为空")

    admin = " ".join(x for x in (str(item.get("adm1") or "").strip(),
                                 str(item.get("adm2") or "").strip())
                     if x and x != name)
    country = str(item.get("country") or "").strip()

    raw = load_raw()
    raw["location"] = f"{lon:.2f},{lat:.2f}"
    raw["location_name"] = name
    raw["location_admin"] = admin if country in ("", "中国") else f"{admin} {country}".strip()
    raw["location_lat"] = round(lat, 5)
    raw["location_lon"] = round(lon, 5)
    raw["location_source"] = "manual"
    raw["location_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    raw["location_ip"] = ""
    raw["location_note"] = ""
    save_raw(raw)
    return merged(raw)


def merge_saved_location(user: dict, raw: dict | None = None) -> dict:
    """页面只提交表单字段时，沿用已保存的定位来源信息。

    - 城市值未变 → 保留 location_lat/lon/source/updated
    - 城市值被改 → 标记为手动设置；是「经度,纬度」就顺手解析出坐标，
      这样手填坐标也能拿到空气质量与预警（这两个接口只收经纬度）
    """
    raw = load_raw() if raw is None else raw
    prev = str(raw.get("location", "")).strip()
    new = str(user.get("location", "")).strip()
    if new and new == prev:
        for key in ("location_admin", "location_lat", "location_lon",
                    "location_source", "location_updated", "location_ip",
                    "location_note"):
            user.setdefault(key, raw.get(key))
    elif new:
        user["location_source"] = "manual"
        pair = parse_pair(new)
        if pair:
            user["location_lat"], user["location_lon"] = pair
        else:
            user["location_lat"] = None
            user["location_lon"] = None
        user.setdefault("location_admin", "")
        user["location_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user["location_ip"] = ""
        user["location_note"] = ""
    return user


def load_config() -> dict:
    """CLI 用：读配置 → 合并 → 校验，失败直接退出。"""
    import logging
    import sys
    log = logging.getLogger("weather-notify")
    if not os.path.exists(CONF_FILE):
        save_raw(TEMPLATE)
        log.error("未找到 %s，已生成模板，请填写后重试", CONF_FILE)
        sys.exit(2)
    cfg = merged(load_raw())
    errors = validate(cfg, os.path.exists(KEY_FILE))
    if errors:
        log.error("配置不完整: %s", "; ".join(errors))
        sys.exit(2)
    return cfg


def load_private_key() -> str:
    with open(KEY_FILE, encoding="utf-8") as f:
        return f.read()


def load_public_key() -> str:
    try:
        with open(PUB_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# ---- Ed25519 密钥对：设置页里生成 / 替换，不再要求用户敲 openssl ----

def _crypto():
    """按需导入 cryptography。

    放在函数里而不是模块顶层：没装这个库时（或旧环境）读配置、走
    Open-Meteo 兜底都不受影响，只有真正要生成/解析密钥时才报错。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return serialization, Ed25519PrivateKey


def _as_pem(serialization, key, private: bool) -> str:
    """统一序列化成 PEM：私钥 PKCS#8、公钥 SubjectPublicKeyInfo。

    与 `openssl genpkey -algorithm ED25519` / `openssl pkey -pubout` 的输出
    格式一致，这样用户拿去和风控制台粘贴、或换回命令行工具都通用。
    """
    if private:
        return key.private_bytes(serialization.Encoding.PEM,
                                 serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode()
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def load_any_private_key(text: str):
    """把用户粘贴的私钥文本解析成 Ed25519 私钥对象。

    认两种常见格式：
      · PEM（-----BEGIN PRIVATE KEY-----，和风控制台 / openssl 产出的）
      · OpenSSH（-----BEGIN OPENSSH PRIVATE KEY-----，ssh-keygen -t ed25519 产出的）
    其余类型（RSA / EC）明确报错 —— 和风 JWT 只接受 Ed25519。
    """
    serialization, Ed25519PrivateKey = _crypto()
    text = str(text or "").strip()
    if not text:
        raise ValueError("私钥内容为空")
    data = (text + "\n").encode()

    loaders = [serialization.load_pem_private_key]
    if hasattr(serialization, "load_ssh_private_key"):
        loaders.append(serialization.load_ssh_private_key)

    key = None
    for loader in loaders:
        try:
            key = loader(data, password=None)
            break
        except Exception:
            continue
    if key is None:
        raise ValueError("无法识别这段私钥：请粘贴「-----BEGIN PRIVATE KEY-----」"
                         "开头的 PEM 全文，或 ssh-keygen 生成的私钥全文")
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("这不是 Ed25519 私钥：和风 JWT 凭据只支持 Ed25519 密钥对")
    return key


def generate_keypair() -> tuple:
    """现场生成一对 Ed25519 密钥，返回 (私钥 PEM, 公钥 PEM)，不落盘。"""
    serialization, Ed25519PrivateKey = _crypto()
    key = Ed25519PrivateKey.generate()
    return _as_pem(serialization, key, True), _as_pem(serialization, key, False)


def public_from_private(priv_text: str) -> tuple:
    """校验私钥并算出对应公钥，返回 (规范化私钥 PEM, 公钥 PEM)。

    公钥一律由私钥推导，而不是让用户分开填两段 —— 两份对不上是最常见的
    「配好了却一直 401」原因。
    """
    serialization, _ = _crypto()
    key = load_any_private_key(priv_text)
    return _as_pem(serialization, key, True), _as_pem(serialization, key, False)


def _write_text(path: str, text: str, mode: int) -> None:
    """先写临时文件再改名：写到一半断电也不会留下半截密钥。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def save_keypair(priv_pem: str, pub_pem: str) -> str:
    """写入密钥对，返回旧私钥的备份路径（原本没有私钥时返回空串）。

    换密钥意味着和风控制台里的旧公钥立刻作废，所以先把旧私钥另存一份，
    万一传错还能换回来。
    """
    os.makedirs(CONF_DIR, exist_ok=True)
    backup = ""
    if os.path.exists(KEY_FILE):
        backup = f"{KEY_FILE}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.replace(KEY_FILE, backup)
    _write_text(KEY_FILE, priv_pem, 0o600)
    _write_text(PUB_FILE, pub_pem, 0o644)
    return backup


def ensure_public_key() -> str:
    """启动兜底：私钥在、公钥缺失或与私钥不匹配时，按私钥重算公钥。

    返回一句日志文案，无需处理时返回空串。这样只拷了私钥过来的用户
    （或者手动改过私钥的）也能在设置页直接看到可粘贴的公钥。
    """
    if not os.path.exists(KEY_FILE):
        return ""
    try:
        _priv, pub = public_from_private(load_private_key())
    except (ValueError, OSError) as e:
        return f"私钥无法解析（{e}），请在设置页「天气数据」重新生成或替换"
    current = load_public_key()
    if current == pub.strip():
        return ""
    try:
        _write_text(PUB_FILE, pub, 0o644)
    except OSError as e:
        return f"公钥写入失败：{e}"
    return ("已按私钥自动生成公钥 ed25519-public.pem" if not current
            else "公钥与私钥不匹配，已按私钥重新生成")


def key_info() -> dict:
    """密钥区展示用：有没有私钥、公钥文本、生成时间。"""
    exists = os.path.exists(KEY_FILE)
    info = {"exists": exists, "public_key": load_public_key(), "created_at": ""}
    if exists:
        try:
            info["created_at"] = datetime.fromtimestamp(
                os.path.getmtime(KEY_FILE)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            pass
    return info


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
