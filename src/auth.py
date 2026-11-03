#!/usr/bin/env python3
"""设置页访问鉴权：PBKDF2 密码哈希 + HMAC 签名的无状态 cookie。

只用标准库，不引第三方依赖。会话不落服务端内存：cookie 里带过期时间，
用 conf/config.json 里的密钥签名，所以服务重启 / 换端口都不会掉线，
也不需要额外的 session 表。密码本身不存明文，只存 PBKDF2 派生值与随机盐。

    hash/verify   密码落盘与校验
    issue/verify  cookie 令牌签发与校验（HMAC-SHA256）
    cookie_*      Set-Cookie 头与读取
    login_blocked/note_fail/clear_fails   按 IP 的登录失败限流
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from threading import Lock

COOKIE_NAME = "wn_session"
PBKDF2_ROUNDS = 200_000
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 128
DEFAULT_SESSION_HOURS = 168          # 7 天
MAX_FAILS = 5
FAIL_WINDOW_SEC = 300                # 5 分钟内的失败才算数
LOCK_SEC = 60                         # 连续失败后锁定 1 分钟

_fail_lock = Lock()
_fails: dict = {}                     # ip -> [次数, 首次时间, 锁到什么时候]


def hash_password(password: str, salt: bytes | None = None) -> tuple:
    """返回 (哈希 hex, 盐 hex)。"""
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             PBKDF2_ROUNDS)
    return dk.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, salt_hex: str) -> bool:
    """恒定时间比较，密码未设置时永远返回 False。"""
    if not stored_hash or not salt_hex:
        return False
    try:
        salt = bytes.fromhex(str(salt_hex))
        dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                                 salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(dk.hex(), str(stored_hash))
    except (ValueError, TypeError):
        return False


def issue_token(secret_hex: str, hours: int = DEFAULT_SESSION_HOURS) -> str:
    """签一个带过期时间的无状态令牌。"""
    hours = max(1, int(hours or DEFAULT_SESSION_HOURS))
    payload = {"exp": int(time.time()) + hours * 3600,
               "n": secrets.token_hex(8)}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(bytes.fromhex(secret_hex), body.encode("ascii"),
                    hashlib.sha256).hexdigest()
    return body + "." + sig


def verify_token(token: str, secret_hex: str) -> bool:
    """验签 + 检查过期；任何异常都当作未登录。"""
    if not token or "." not in token or not secret_hex:
        return False
    body, _, sig = str(token).partition(".")
    try:
        expect = hmac.new(bytes.fromhex(secret_hex), body.encode("ascii"),
                          hashlib.sha256).hexdigest()
    except (ValueError, TypeError):
        return False
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        padded = body + "=" * (-len(body) % 4)
        exp = int(json.loads(base64.urlsafe_b64decode(padded)).get("exp", 0))
    except Exception:
        return False
    return exp > time.time()


def read_cookie(header_value: str, name: str = COOKIE_NAME) -> str:
    try:
        jar = SimpleCookie()
        jar.load(str(header_value or ""))
        item = jar.get(name)
        return item.value if item else ""
    except Exception:
        return ""


def cookie_header(token: str, hours: int = DEFAULT_SESSION_HOURS) -> str:
    """内网是 http，不能加 Secure；SameSite=Strict 挡跨站带 cookie。"""
    hours = max(1, int(hours or DEFAULT_SESSION_HOURS))
    return (f"{COOKIE_NAME}={token}; Path=/; Max-Age={hours * 3600}; "
            f"HttpOnly; SameSite=Strict")


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"


def login_blocked(ip: str) -> tuple:
    """返回 (是否仍被锁定, 剩余秒数)。"""
    now = time.time()
    with _fail_lock:
        rec = _fails.get(ip)
        if not rec:
            return False, 0
        count, first, until = rec
        if until and now < until:
            return True, int(until - now) + 1
        # 锁定过期或计数窗口过了就清零
        if (until and now >= until) or now - first > FAIL_WINDOW_SEC:
            del _fails[ip]
        return False, 0


def note_fail(ip: str) -> None:
    now = time.time()
    with _fail_lock:
        rec = _fails.get(ip)
        if not rec or now - rec[1] > FAIL_WINDOW_SEC:
            _fails[ip] = [1, now, 0.0]
            return
        rec[0] += 1
        if rec[0] >= MAX_FAILS:
            rec[2] = now + LOCK_SEC


def clear_fails(ip: str) -> None:
    with _fail_lock:
        _fails.pop(ip, None)
