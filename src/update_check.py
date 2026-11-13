"""版本号与更新检测：读打包 manifest 的 version，比对 GitHub 最新 release。

- 当前版本：环境变量 WEATHER_NOTIFY_VERSION > 打包目录里的 manifest > 仓库
  fpk/manifest；都读不到时为 None（页面显示 v—）。
- 最新版本：GET https://api.github.com/repos/xuexcy/weather_notify/releases/latest
  成功缓存 24 小时（GitHub 匿名限流 60 次/小时，一天一次足够），失败缓存
  15 分钟（出错了别一直往外打请求）。
- 检查频率由设置页的「检查更新」决定（config 里的 update_check）：
  auto = 打开页面时自动查；manual = 只在点「重新检查」时查；off = 完全不联网。
- 缓存落盘在 conf/version_cache.json：重启服务不用重新联网。
- 有新版本时顺带带上 release 里的 fpk 附件，页面可直接给下载入口。
- 任何异常都不外抛：检测不出来就把 state 置为 unknown，天气功能不受影响。
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time

import config_loader

REPO = "xuexcy/weather_notify"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "weather-notify-version-check (+https://github.com/xuexcy/weather_notify)"

OK_TTL = 24 * 3600         # 成功结果缓存 24 小时（一天查一次就够）
FAIL_TTL = 15 * 60         # 失败结果缓存 15 分钟
HTTP_TIMEOUT = 5.0         # 单次请求超时（秒）
CACHE_NAME = "version_cache.json"

STATE_LATEST = "latest"
STATE_OUTDATED = "outdated"
STATE_UNKNOWN = "unknown"
STATE_OFF = "off"                 # 用户关了检查更新
STATE_UNCHECKED = "unchecked"     # 仅手动模式，还没查过

# fetch_latest 的返回值：版本号、错误说明、release 里的安装包附件
Release = collections.namedtuple("Release", "version error assets")

# 版本号比较：允许 v1 / 1.2 / 1.2.3 / v1.2.3-beta.1，按语义化版本比较
_VERSION_RE = re.compile(
    r"^\s*v?(\d+(?:\.\d+){0,2})(?:[-+][0-9A-Za-z.+\-]*)?\s*$", re.IGNORECASE)


def _strip_v(raw: str) -> str:
    """统一去掉版本号前缀的 v：页面自己会补，存两份容易变成 vv0.1.2。"""
    text = (raw or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:].strip()
    return text

_lock = threading.Lock()
_refreshing = False


# ---------- 版本号 ----------
def parse_version(raw) -> tuple | None:
    """'v0.1.2' → (0, 1, 2, 1)；带预发布标记视为比同号正式版旧。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    m = _VERSION_RE.match(text)
    if not m:
        return None
    nums = [int(part) for part in m.group(1).split(".")]
    nums += [0] * (3 - len(nums))
    pre = 0 if "-" in text.split("+")[0] else 1
    return tuple(nums) + (pre,)


def compare(a, b) -> int:
    """a<b 返回 -1，相等 0，a>b 返回 1；任一侧解析失败按 0（无法比较）处理。"""
    ka, kb = parse_version(a), parse_version(b)
    if ka is None or kb is None:
        return 0
    return (ka > kb) - (ka < kb)


def version_files() -> tuple:
    """按优先级列出可能写着版本号的文件（绝对路径）。"""
    src = os.path.abspath(config_loader.SRC_DIR)
    return (
        os.path.normpath(os.path.join(src, os.pardir, "fpk", "manifest")),
        os.path.normpath(os.path.join(src, os.pardir, os.pardir, "manifest")),
        os.path.normpath(os.path.join(src, os.pardir, "manifest")),
        os.path.normpath(os.path.join(src, os.pardir, "VERSION")),
    )


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _version_from_text(text: str) -> str | None:
    m = re.search(r"^\s*version\s*=\s*(\S+)", text, re.MULTILINE)
    if m:
        cand = m.group(1).strip().strip("\"'")
    else:  # 纯版本号文件
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        cand = lines[0] if lines else ""
    return _strip_v(cand) if parse_version(cand) else None


def current_version() -> str | None:
    env = (os.environ.get("WEATHER_NOTIFY_VERSION") or "").strip()
    if env:
        return _strip_v(env)
    for path in version_files():
        text = _read_text(path)
        if not text:
            continue
        ver = _version_from_text(text)
        if ver:
            return ver
    return None


# ---------- GitHub ----------
def _short_error(exc: Exception) -> str:
    """把网络异常翻成一句人话；设置页的 tooltip 只放这一句。"""
    name = type(exc).__name__
    if "Timeout" in name:
        return "连接 GitHub 超时（大概网络不通）"
    if "Proxy" in name:
        return "代理连不上（检查系统 / 容器里的 HTTP 代理设置）"
    if "SSL" in name:
        return "TLS 握手失败（可能是代理或证书问题）"
    if "Connection" in name:
        return "连不上 GitHub（大概网络不通）"
    text = f"{name}: {exc}".strip()
    return text[:160]


def _assets(data: dict) -> list:
    """release 里的 fpk 附件，按架构标一下，前端直接列成下载项。

    GitHub 的 release 还会挂源码压缩包（zip / tar.gz），那些不是给 fnOS 装的，
    这里只留 .fpk；取不到就返回空列表，页面退回「去 releases 看看」。
    """
    out = []
    for item in data.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        if not name.lower().endswith(".fpk") or not url.startswith("https://"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append({"name": name, "url": url, "size": size,
                    "arch": "arm" if "_arm" in name.lower() else "x86"})
    return out[:6]


def fetch_latest(timeout: float = HTTP_TIMEOUT) -> Release:
    """请求 GitHub 最新 release，返回 Release(版本号, 错误, 附件)。不抛异常。"""
    try:
        import requests
    except ImportError:  # pragma: no cover - 依赖缺失时
        return Release(None, "缺少 requests 模块", [])
    try:
        resp = requests.get(LATEST_API, timeout=timeout,
                            headers={"User-Agent": USER_AGENT,
                                     "Accept": "application/vnd.github+json"})
    except Exception as exc:  # 网络层各种异常都按检测失败处理
        return Release(None, _short_error(exc), [])

    code = getattr(resp, "status_code", 0)
    if code == 404:
        return Release(None, "仓库还没有发布 release（404）", [])
    if code in (403, 429):
        return Release(None, "GitHub 限流了，稍后再试（HTTP %d）" % code, [])
    if code != 200:
        return Release(None, "GitHub 返回 HTTP %d" % code, [])
    try:
        data = resp.json()
    except ValueError:
        return Release(None, "GitHub 返回的不是 JSON", [])
    if not isinstance(data, dict):
        return Release(None, "GitHub 返回的不是 JSON 对象", [])
    tag = data.get("tag_name") or data.get("name")
    if not isinstance(tag, str) or not parse_version(tag):
        return Release(None, "release 里没有可识别的版本号", [])
    return Release(_strip_v(tag), None, _assets(data))


# ---------- 缓存 ----------
def cache_path() -> str:
    return os.path.join(config_loader.CONF_DIR, CACHE_NAME)


def _load_cache() -> dict | None:
    text = _read_text(cache_path())
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    checked = data.get("checked_at")
    if not isinstance(checked, (int, float)) or checked <= 0:
        return None
    latest = data.get("latest")
    if latest is not None and not isinstance(latest, str):
        return None
    return data


def _save_cache(data: dict) -> None:
    path = cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass  # 缓存写不进去不影响检测结果


def _is_fresh(cache: dict | None, now: float) -> bool:
    if not cache:
        return False
    # 上一次是失败的：按 15 分钟重试，别因为缓存里还留着旧的 latest 就一天不再查
    failed = bool(cache.get("error")) or not cache.get("latest")
    ttl = FAIL_TTL if failed else OK_TTL
    return now - float(cache["checked_at"]) < ttl


def _refresh(now: float | None = None) -> dict:
    """真的去请求一次，写缓存并返回。

    注意：网络请求不能在 _lock 里做。后台刷新时页面请求也会走到
    _kick_refresh()，锁被占着的话它得等 GitHub 回来（最长 5 秒），
    「先给旧结果、后台再刷新」就名存实亡了。
    """
    global _refreshing
    with _lock:
        _refreshing = True
    try:
        release = fetch_latest()
        stamp = time.time()
        with _lock:
            cache = _load_cache() or {}
            if release.version:
                cache["latest"] = release.version
                cache["assets"] = release.assets
                cache["error"] = None
            else:
                cache.setdefault("latest", None)
                cache["error"] = release.error or "未知错误"
            cache["checked_at"] = stamp
            _save_cache(cache)
            return cache
    finally:
        with _lock:
            _refreshing = False


def _kick_refresh() -> None:
    """后台刷新：页面不等网络，下次打开就是新结果。"""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True
    thread = threading.Thread(target=_refresh_safe, name="version-check", daemon=True)
    thread.start()


def _refresh_safe() -> None:
    global _refreshing
    try:
        _refresh()
    except Exception:  # pragma: no cover - 兜底，绝不影响主流程
        with _lock:
            _refreshing = False


# ---------- 对外 ----------
def mode() -> str:
    """设置页选定的检查方式；配置读不出来就按默认的 auto 走。"""
    try:
        # load_raw 不校验完整性：徽标不该因为「还没填天气密钥」而拿不到设置
        return config_loader.update_check_mode(config_loader.load_raw())
    except Exception:  # pragma: no cover - 配置坏了也不该拖累版本徽标
        return "auto"


def _payload(cache: dict | None, stale: bool = False,
             state: str | None = None) -> dict:
    """把缓存整理成页面要的形状。

    state 传了就以它为准（off / unchecked 这类「不是检测结果」的状态），
    否则按版本号比出 latest / outdated / unknown。
    """
    current = current_version()
    latest = _strip_v((cache or {}).get("latest") or "") or None
    checked = float((cache or {}).get("checked_at") or 0)
    error = (cache or {}).get("error")
    assets = [a for a in ((cache or {}).get("assets") or []) if isinstance(a, dict)]

    if state is None:
        if latest and current and parse_version(current):
            state = (STATE_OUTDATED if compare(current, latest) < 0 else STATE_LATEST)
        else:
            state = STATE_UNKNOWN
            if not latest:
                error = error or "还没拿到 GitHub 上的版本信息"
            elif not current:
                error = error or "读不到当前版本号（manifest 缺失？）"
    else:
        error = None            # 关掉 / 没查过不是失败，别挂一句红字

    return {
        "ok": True,
        "repo": REPO,
        "url": RELEASES_URL,
        "mode": mode(),
        "current": current,
        "latest": latest,
        "state": state,
        "stale": bool(stale),
        "error": error,
        "assets": assets if latest else [],
        "release_url": f"{RELEASES_URL}/tag/v{latest}" if latest else "",
        "checked_at": checked,
        "checked_text": (time.strftime("%Y-%m-%d %H:%M", time.localtime(checked))
                         if checked else ""),
    }


def get_state(force: bool = False) -> dict:
    """返回版本状态。force=True 表示用户点了「重新检查」，同步等结果。"""
    how = mode()
    if how == "off":
        return _payload(None, state=STATE_OFF)

    cache = _load_cache()
    now = time.time()
    if how == "manual":
        if force:
            return _payload(_refresh(now))
        if cache is None:
            return _payload(None, state=STATE_UNCHECKED)
        return _payload(cache, stale=not _is_fresh(cache, now))

    if _is_fresh(cache, now) and not force:
        return _payload(cache)
    if force or cache is None:
        return _payload(_refresh(now))
    _kick_refresh()
    return _payload(cache, stale=True)


def reset() -> None:
    """丢掉内存里的「正在刷新」标记（测试与热重载用）。"""
    global _refreshing
    with _lock:
        _refreshing = False
