"""auth：密码哈希、HMAC cookie 令牌、登录失败限流。"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import auth  # noqa: E402

SECRET = "ab" * 32


class PasswordTest(unittest.TestCase):
    def test_hash_verify_roundtrip(self):
        h, s = auth.hash_password("abc12345")
        self.assertTrue(auth.verify_password("abc12345", h, s))
        self.assertFalse(auth.verify_password("wrong!", h, s))

    def test_salt_randomized(self):
        h1, s1 = auth.hash_password("same")
        h2, s2 = auth.hash_password("same")
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(h1, h2)

    def test_verify_never_true_when_unset(self):
        self.assertFalse(auth.verify_password("x", "", ""))
        self.assertFalse(auth.verify_password("x", "h", ""))
        self.assertFalse(auth.verify_password("x", "", "s"))

    def test_verify_tolerates_dirty_input(self):
        h, s = auth.hash_password("abc12345")
        self.assertFalse(auth.verify_password("abc12345", h, "zz"))   # 非法 hex
        self.assertFalse(auth.verify_password(None, h, s))            # None 密码


def _make_token(secret: str, payload: dict) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(bytes.fromhex(secret), body.encode("ascii"),
                   hashlib.sha256).hexdigest()
    return body + "." + sig


class TokenTest(unittest.TestCase):
    def test_issue_verify_roundtrip(self):
        t = auth.issue_token(SECRET, 2)
        self.assertTrue(auth.verify_token(t, SECRET))
        self.assertFalse(auth.verify_token(t, "cd" * 32))   # 换密钥
        self.assertFalse(auth.verify_token(t[:-2] + "ff", SECRET))  # 篡改签名
        self.assertFalse(auth.verify_token("", SECRET))
        self.assertFalse(auth.verify_token("no-dot", SECRET))

    def test_expired_token_rejected(self):
        t = _make_token(SECRET, {"exp": int(time.time()) - 10, "n": "x"})
        self.assertFalse(auth.verify_token(t, SECRET))

    def test_future_token_accepted(self):
        t = _make_token(SECRET, {"exp": int(time.time()) + 3600, "n": "x"})
        self.assertTrue(auth.verify_token(t, SECRET))

    def test_garbage_payload_rejected(self):
        body = base64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
        sig = hmac.new(bytes.fromhex(SECRET), body.encode("ascii"),
                       hashlib.sha256).hexdigest()
        self.assertFalse(auth.verify_token(body + "." + sig, SECRET))

    def test_session_hours_floor(self):
        # 非法小时数回落到至少 1 小时
        t = auth.issue_token(SECRET, 0)
        self.assertTrue(auth.verify_token(t, SECRET))


class CookieTest(unittest.TestCase):
    def test_read_cookie(self):
        header = auth.cookie_header("tok123", 2)
        self.assertEqual(auth.read_cookie("a=1; wn_session=tok123"), "tok123")
        self.assertEqual(auth.read_cookie(header), "tok123")
        self.assertEqual(auth.read_cookie("a=1"), "")
        self.assertEqual(auth.read_cookie(""), "")
        self.assertEqual(auth.read_cookie(None), "")

    def test_cookie_attributes(self):
        h = auth.cookie_header("t", 2)
        self.assertIn("wn_session=t", h)
        self.assertIn("Max-Age=7200", h)
        self.assertIn("HttpOnly", h)
        self.assertIn("SameSite=Strict", h)
        self.assertNotIn("Secure", h)          # 内网 http，不能加 Secure
        self.assertIn("Max-Age=0", auth.clear_cookie_header())


class LoginRateLimitTest(unittest.TestCase):
    def setUp(self):
        auth._fails.clear()
        self.addCleanup(auth._fails.clear)

    def test_locks_after_max_fails(self):
        ip = "10.0.0.1"
        for _ in range(auth.MAX_FAILS - 1):
            auth.note_fail(ip)
        blocked, _ = auth.login_blocked(ip)
        self.assertFalse(blocked)
        auth.note_fail(ip)                     # 第 MAX_FAILS 次
        blocked, remain = auth.login_blocked(ip)
        self.assertTrue(blocked)
        self.assertGreater(remain, 0)

    def test_clear_fails_unlocks(self):
        ip = "10.0.0.2"
        for _ in range(auth.MAX_FAILS):
            auth.note_fail(ip)
        self.assertTrue(auth.login_blocked(ip)[0])
        auth.clear_fails(ip)
        self.assertFalse(auth.login_blocked(ip)[0])

    def test_ips_are_isolated(self):
        for _ in range(auth.MAX_FAILS):
            auth.note_fail("10.0.0.3")
        self.assertTrue(auth.login_blocked("10.0.0.3")[0])
        self.assertFalse(auth.login_blocked("10.0.0.4")[0])


if __name__ == "__main__":
    unittest.main()
