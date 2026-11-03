"""server：表单清洗、调度计算与两类定时任务的触发/去重逻辑。

调度线程通过 mock 隔离，测试期间不会真的启动播报线程，也不会读写真实 conf。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR, TempConfCase  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config_loader  # noqa: E402
import server  # noqa: E402


class SanitizeUserConfigTest(unittest.TestCase):
    def test_auth_secrets_stripped(self):
        user = {"auth": {"enabled": True, "password_hash": "HACK",
                         "salt": "HACK", "secret": "HACK"}}
        out = server.sanitize_user_config(user)
        self.assertEqual(out["auth"], {"enabled": True})

    def test_schedule_normalized(self):
        out = server.sanitize_user_config(
            {"schedule": {"times": ["7:5", "99:99", "08:00"],
                          "warning_interval_min": 999}})
        self.assertEqual(out["schedule"]["times"], ["07:05", "08:00"])
        self.assertEqual(out["schedule"]["warning_interval_min"], 120)

    def test_non_dict(self):
        self.assertEqual(server.sanitize_user_config("x"), {})
        self.assertEqual(server.sanitize_user_config(None), {})


class SanitizeChannelsTest(unittest.TestCase):
    """sanitize_user_config 对 push.channels 的逐项清洗。"""

    def test_unknown_type_and_non_dict_dropped(self):
        out = server.sanitize_user_config({"push": {"channels": [
            {"id": "a", "type": "wecom", "enabled": True},
            "junk", 42,
            {"id": "b", "type": "telegram"},
            None,
        ]}})
        chans = out["push"]["channels"]
        self.assertEqual([c["id"] for c in chans], ["a"])

    def test_missing_id_regenerated_and_fields_whitelisted(self):
        out = server.sanitize_user_config({"push": {"channels": [
            {"type": "wecom", "webhook": "http://x", "hack": "junk",
             "secret": "nope"},
        ]}})
        ch = out["push"]["channels"][0]
        self.assertTrue(ch["id"])                       # 自动补 id
        self.assertEqual(ch["webhook"], "http://x")
        self.assertEqual(ch["msg_type"], "text")        # 缺字段补默认
        self.assertNotIn("hack", ch)
        self.assertNotIn("secret", ch)

    def test_name_defaults_and_enabled_coerced(self):
        out = server.sanitize_user_config({"push": {"channels": [
            {"type": "magicpush", "name": "  ", "enabled": 1},
        ]}})
        ch = out["push"]["channels"][0]
        self.assertEqual(ch["name"], "MagicPush 魔法推送")
        self.assertIs(ch["enabled"], True)
        self.assertEqual(ch["base_url"], "")
        self.assertEqual(ch["token"], "")

    def test_channels_not_list_or_missing(self):
        out = server.sanitize_user_config({"push": {"channels": "x"}})
        self.assertEqual(out["push"]["channels"], [])
        out2 = server.sanitize_user_config({"push": {}})
        self.assertNotIn("channels", out2["push"])

    def test_duplicate_ids_not_merged_away(self):
        # 同 id 两项都保留（脚本提交的边缘情况），清洗不改数量
        out = server.sanitize_user_config({"push": {"channels": [
            {"id": "same", "type": "wecom"},
            {"id": "same", "type": "wecom"},
        ]}})
        self.assertEqual(len(out["push"]["channels"]), 2)

    def test_roles_kept_and_normalized(self):
        out = server.sanitize_user_config({"push": {"channels": [
            {"id": "t1", "type": "wecom", "roles": ["test"]},
            {"id": "b1", "type": "wecom", "roles": ["test", "prod"]},
            {"id": "p1", "type": "wecom", "roles": ["HACK"]},
            {"id": "l1", "type": "wecom", "role": "test"},   # 老页面只提交 role
            {"id": "p2", "type": "wecom"},
        ]}})
        roles = {c["id"]: c.get("roles") for c in out["push"]["channels"]}
        self.assertEqual(roles["t1"], ["test"])          # 测试用途不会被改成正式
        self.assertEqual(roles["b1"], ["prod", "test"])  # 两用通道两个都保留
        self.assertEqual(roles["p1"], ["prod"])          # 脏值归一
        self.assertEqual(roles["l1"], ["test"])          # 老单值字段也能读
        self.assertIsNone(roles["p2"])                   # 没提交就不写，交给合并逻辑保留
        self.assertTrue(all("role" not in c for c in out["push"]["channels"]))

    def test_channel_errors_by_role(self):
        cfg = {"push": {"channels": [
            {"id": "p1", "type": "wecom", "name": "正式群", "enabled": True,
             "roles": ["prod"], "webhook": "http://x"},
            {"id": "t1", "type": "wecom", "name": "测试群", "enabled": True,
             "roles": ["test"], "webhook": ""},
        ]}}
        self.assertEqual(server.Handler._channel_errors(cfg, "prod"), [])
        errs = server.Handler._channel_errors(cfg, "test")
        self.assertTrue(any("测试群" in e and "webhook" in e for e in errs))

    def test_both_roles_channel_checked_in_both(self):
        cfg = {"push": {"channels": [
            {"id": "b1", "type": "wecom", "name": "两用群", "enabled": True,
             "roles": ["prod", "test"], "webhook": ""}]}}
        self.assertTrue(any("两用群" in e
                            for e in server.Handler._channel_errors(cfg, "prod")))
        self.assertTrue(any("两用群" in e
                            for e in server.Handler._channel_errors(cfg, "test")))

    def test_channel_errors_without_role_target(self):
        cfg = {"push": {"channels": [
            {"id": "p1", "type": "wecom", "name": "正式群", "enabled": True,
             "roles": ["prod"], "webhook": "http://x"}]}}
        errs = server.Handler._channel_errors(cfg, "test")
        self.assertTrue(any("测试通道" in e for e in errs))


class MaskTest(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(server._mask("a" * 20), "aaaaaaaa…aaaa")
        # 短值原样返回
        self.assertEqual(server._mask("short"), "short")
        self.assertEqual(server._mask(""), "")

    def test_mask_webhook(self):
        url = ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?"
               "key=12345678-abcd-ef01-2345")
        out = server._mask_webhook(url)
        self.assertIn("key=1234…", out)
        self.assertNotIn("abcd-ef01-2345", out)
        # 无 key= 的普通地址按长度打码
        self.assertEqual(server._mask_webhook("short-url"), "short-url")


class RestartArgvTest(unittest.TestCase):
    def test_strips_port_and_open(self):
        server.START_ARGV = ["--host", "0.0.0.0", "--port", "1111",
                             "--open", "--no-geoip"]
        try:
            out = server._restart_argv(2222)
        finally:
            server.START_ARGV = []
        self.assertEqual(out, ["--host", "0.0.0.0", "--no-geoip",
                               "--port", "2222"])

    def test_port_equals_form(self):
        server.START_ARGV = ["--port=1111"]
        try:
            out = server._restart_argv(2222)
        finally:
            server.START_ARGV = []
        self.assertEqual(out, ["--port", "2222"])


class PortInUseTest(unittest.TestCase):
    def test_free_port_then_occupied(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # 端口已释放 → 空闲；自己再占上 → 占用
        self.assertFalse(server._port_in_use(port))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            self.assertTrue(server._port_in_use(port))


class NextRunTest(unittest.TestCase):
    def test_today(self):
        now = datetime(2026, 11, 2, 6, 0)
        nxt = server._next_run(["07:30"], now)
        self.assertEqual(nxt["label"], "今天 07:30")
        self.assertEqual(nxt["at"], "2026-11-02 07:30")

    def test_tomorrow(self):
        now = datetime(2026, 11, 2, 22, 0)
        nxt = server._next_run(["07:30", "21:00"], now)
        self.assertEqual(nxt["label"], "明天 07:30")   # 今天的时间点全过完了

    def test_empty(self):
        self.assertEqual(server._next_run([], datetime(2026, 11, 2, 6, 0)),
                         {"label": "", "at": "", "in_seconds": 0})


class NextWarningAtTest(unittest.TestCase):
    def test_plus_interval(self):
        state = {"warning_last_at": "2026-11-02 10:00:00"}
        self.assertEqual(server._next_warning_at(state, 15), "2026-11-02 10:15:00")

    def test_empty_or_dirty(self):
        self.assertEqual(server._next_warning_at({}, 10), "")
        self.assertEqual(server._next_warning_at(
            {"warning_last_at": "bad"}, 10), "")


class SchedulePayloadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_file = os.path.join(tmp.name, "schedule_state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"warning_last_at": "2026-11-02 10:00:00",
                       "warning_last_result": "无新增预警"}, f)
        p = mock.patch.object(server, "SCHEDULE_STATE_FILE", state_file)
        p.start()
        self.addCleanup(p.stop)
        raw = {"schedule": {"enabled": True, "times": ["07:30", "8:0"],
                            "warning_enabled": True, "warning_interval_min": 15}}
        p2 = mock.patch.object(config_loader, "load_raw", return_value=raw)
        p2.start()
        self.addCleanup(p2.stop)

    def test_payload_shape(self):
        p = server.schedule_payload()
        self.assertTrue(p["ok"])
        self.assertTrue(p["enabled"])
        self.assertEqual(p["times"], ["07:30", "08:00"])
        self.assertTrue(p["warning_enabled"])
        self.assertEqual(p["warning_interval_min"], 15)
        self.assertEqual(p["warning_next"], "2026-11-02 10:15:00")
        self.assertEqual(p["warning_last_result"], "无新增预警")
        self.assertEqual(p["timezone"], "Asia/Shanghai")
        self.assertTrue(p["service_running"])
        self.assertNotIn("cron", p)            # 外部定时方案已移除
        self.assertIn("label", p["next"])      # 下次播报


class ScheduleTickTest(unittest.TestCase):
    """_schedule_tick 的分发与启动宽限期。"""

    def _run(self, within_grace: bool):
        with mock.patch.object(server, "_START_TS",
                               time.monotonic() - (0 if within_grace else 1000)), \
             mock.patch.object(server, "_broadcast_tick") as b, \
             mock.patch.object(server, "_warning_tick") as w, \
             mock.patch.object(config_loader, "load_raw", return_value={}), \
             mock.patch.object(config_loader, "merged", side_effect=lambda c: c):
            server._schedule_tick()
        return b, w

    def test_dispatches_both_ticks(self):
        b, w = self._run(within_grace=False)
        b.assert_called_once()
        w.assert_called_once()

    def test_grace_period_blocks(self):
        b, w = self._run(within_grace=True)
        b.assert_not_called()
        w.assert_not_called()


class BroadcastTickTest(unittest.TestCase):
    """日常播报：命中 HH:MM 触发，同一分钟只推一次。"""

    def _fire(self, now, sch, state0):
        saved = []
        with mock.patch.object(server, "_load_schedule_state",
                               return_value=dict(state0)), \
             mock.patch.object(server, "_save_schedule_state",
                               side_effect=saved.append), \
             mock.patch.object(config_loader, "load_raw", return_value={}), \
             mock.patch.object(config_loader, "merged", side_effect=lambda c: c), \
             mock.patch.object(server.threading, "Thread") as th:
            server._broadcast_tick(sch, now)
        return saved, th

    def test_fires_on_hit(self):
        now = datetime(2026, 11, 2, 7, 30)
        saved, th = self._fire(now, {"enabled": True, "times": ["07:30"]}, {})
        self.assertTrue(th.called)
        self.assertEqual(saved[-1]["last_fired"], "2026-11-02 07:30")

    def test_same_minute_not_refired(self):
        now = datetime(2026, 11, 2, 7, 30)
        state = {"last_fired": "2026-11-02 07:30"}
        saved, th = self._fire(now, {"enabled": True, "times": ["07:30"]}, state)
        self.assertFalse(th.called)
        self.assertEqual(saved, [])

    def test_miss_and_disabled(self):
        now = datetime(2026, 11, 2, 8, 1)
        _, th = self._fire(now, {"enabled": True, "times": ["07:30"]}, {})
        self.assertFalse(th.called)
        _, th = self._fire(now, {"enabled": False, "times": ["07:30"]}, {})
        self.assertFalse(th.called)


class WarningTickTest(unittest.TestCase):
    """预警轮询：间隔到期触发，未到期不触发，重启后从当前时刻重新起算。"""

    def _fire(self, now, sch, state0):
        saved = []
        with mock.patch.object(server, "_load_schedule_state",
                               return_value=dict(state0)), \
             mock.patch.object(server, "_save_schedule_state",
                               side_effect=saved.append), \
             mock.patch.object(config_loader, "load_raw", return_value={}), \
             mock.patch.object(config_loader, "merged", side_effect=lambda c: c), \
             mock.patch.object(server.threading, "Thread") as th:
            server._warning_tick(sch, now)
        return saved, th

    SCH = {"warning_enabled": True, "warning_interval_min": 10}

    def test_due_fires(self):
        now = datetime(2026, 11, 2, 12, 0)
        state = {"warning_last_at": "2026-11-02 11:49:00"}   # 11 分钟前 > 10 分钟
        saved, th = self._fire(now, self.SCH, state)
        self.assertTrue(th.called)
        self.assertEqual(saved[-1]["warning_last_at"], "2026-11-02 12:00:00")
        self.assertEqual(saved[-1]["warning_last_result"], "检查中…")

    def test_not_due_skips(self):
        now = datetime(2026, 11, 2, 12, 0)
        state = {"warning_last_at": "2026-11-02 11:55:00"}   # 5 分钟前
        saved, th = self._fire(now, self.SCH, state)
        self.assertFalse(th.called)
        self.assertEqual(saved, [])

    def test_disabled_or_empty_state(self):
        now = datetime(2026, 11, 2, 12, 0)
        _, th = self._fire(now, {"warning_enabled": False,
                                 "warning_interval_min": 10},
                           {"warning_last_at": "2026-11-02 10:00:00"})
        self.assertFalse(th.called)
        _, th = self._fire(now, self.SCH, {})    # 空状态 = 刚重启，等一个周期
        self.assertFalse(th.called)

    def test_dirty_last_at_triggers(self):
        now = datetime(2026, 11, 2, 12, 0)
        saved, th = self._fire(now, self.SCH, {"warning_last_at": "garbage"})
        self.assertTrue(th.called)
        self.assertEqual(saved[-1]["warning_last_at"], "2026-11-02 12:00:00")


class QWeatherKeyEndpointTest(TempConfCase):
    """POST /api/qweather/key：生成 / 替换和风密钥对。"""

    @staticmethod
    def _handler():
        h = object.__new__(server.Handler)
        out = {}
        h._json = lambda payload: out.update(payload=payload)
        return h, out

    def setUp(self):
        super().setUp()
        # build_status 会拉天气/定位等重对象，这里只关心密钥，stub 掉
        p = mock.patch.object(server, "build_status", lambda: {"stub": True})
        p.start()
        self.addCleanup(p.stop)

    def test_generate(self):
        h, out = self._handler()
        h._qweather_key({"action": "generate"})
        r = out["payload"]
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "generate")
        self.assertIn("BEGIN PUBLIC KEY", r["key"]["public_key"])
        self.assertTrue(os.path.exists(config_loader.KEY_FILE))
        # 私钥永不回显
        self.assertNotIn("PRIVATE KEY", json.dumps(r))

    def test_replace_with_own_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        h, out = self._handler()
        h._qweather_key({"action": "replace", "private_key": pem})
        r = out["payload"]
        self.assertTrue(r["ok"])
        self.assertEqual(r["key"]["public_key"].strip(),
                         key.public_key().public_bytes(
                             serialization.Encoding.PEM,
                             serialization.PublicFormat.SubjectPublicKeyInfo).decode().strip())

    def test_replace_rejects_garbage(self):
        h, out = self._handler()
        h._qweather_key({"action": "replace", "private_key": "nope"})
        self.assertFalse(out["payload"]["ok"])
        self.assertIn("私钥", out["payload"]["error"])

    def test_unknown_action(self):
        h, out = self._handler()
        h._qweather_key({"action": "hack"})
        self.assertFalse(out["payload"]["ok"])
        self.assertIn("未知操作", out["payload"]["error"])

    def test_regenerate_keeps_backup(self):
        h, out = self._handler()
        h._qweather_key({"action": "generate"})
        self.assertEqual(out["payload"]["backup"], "")       # 第一次无旧私钥
        h._qweather_key({"action": "generate"})
        backup = out["payload"]["backup"]
        self.assertIn(".pem.bak_", backup)                   # 旧私钥另存为备份
        self.assertTrue(os.path.exists(os.path.join(config_loader.CONF_DIR,
                                                    backup)))


if __name__ == "__main__":
    unittest.main()
