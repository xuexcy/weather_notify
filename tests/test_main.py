"""main：坐标解析、预警过滤去重（等级/类型/升级/复推窗口）、运行记录。"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import main  # noqa: E402


class CoordsTest(unittest.TestCase):
    def test_prefers_latlon_fields(self):
        cfg = {"location_lat": 30.06, "location_lon": 112.23, "location": "1,2"}
        self.assertEqual(main.coords(cfg), (30.06, 112.23))

    def test_parses_location_lon_lat(self):
        self.assertEqual(main.coords({"location": "112.23,30.06"}), (30.06, 112.23))
        self.assertEqual(main.coords({"location": "112.23，30.06"}), (30.06, 112.23))

    def test_invalid(self):
        self.assertIsNone(main.coords({"location": "公安"}))
        self.assertIsNone(main.coords({"location": "abc,def"}))
        self.assertIsNone(main.coords({}))


def _w(level="蓝色", type_name="暴雨", title="t1"):
    return {"typeName": type_name, "level": level, "title": title,
            "startTime": "2026-11-02T08:00+08:00", "text": "x", "status": "预警中"}


def _cfg(**kw):
    base = {"alert_min_level": "蓝色", "alert_types": [], "alert_resend_hours": 6}
    base.update(kw)
    return base


class FilterAlertsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(main, "ALERT_STATE",
                              os.path.join(tmp.name, "alert_state.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_new_alert_passes_and_recorded(self):
        fresh = main.filter_alerts([_w()], _cfg())
        self.assertEqual([w["title"] for w in fresh], ["t1"])
        # 立刻再来同一条 → 去重
        self.assertEqual(main.filter_alerts([_w()], _cfg()), [])

    def test_level_threshold(self):
        # 白色与蓝色同为 rank 1，门槛「黄色」才能滤掉蓝色
        self.assertEqual(main.filter_alerts([_w(level="蓝色", title="low")],
                                            _cfg(alert_min_level="黄色")), [])
        self.assertEqual(len(main.filter_alerts([_w(level="黄色", title="ok")],
                                                _cfg(alert_min_level="黄色"))), 1)

    def test_type_filter(self):
        self.assertEqual(main.filter_alerts([_w(type_name="大风")],
                                            _cfg(alert_types=["暴雨", "台风"])), [])
        self.assertEqual(len(main.filter_alerts([_w(type_name="台风")],
                                                _cfg(alert_types=["暴雨", "台风"]))), 1)

    def test_upgrade_resends(self):
        self.assertEqual(len(main.filter_alerts([_w(level="蓝色")], _cfg())), 1)
        # 等级降/平不重推，升级重推
        self.assertEqual(len(main.filter_alerts([_w(level="蓝色")], _cfg())), 0)
        self.assertEqual(len(main.filter_alerts([_w(level="黄色", title="t1")],
                                                _cfg())), 1)

    def test_resend_window_expiry(self):
        self.assertEqual(len(main.filter_alerts([_w()], _cfg())), 1)
        # 把上次推送时间拨回 7 小时前（窗口 6 小时）→ 重新推送
        with open(main.ALERT_STATE, encoding="utf-8") as f:
            import json
            state = json.load(f)
        old = time.time() - 7 * 3600
        for v in state.values():
            v["ts"] = old
        with open(main.ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        self.assertEqual(len(main.filter_alerts([_w()], _cfg())), 1)

    def test_old_entries_purged(self):
        import json
        main.filter_alerts([_w(title="ancient")], _cfg())
        with open(main.ALERT_STATE, encoding="utf-8") as f:
            state = json.load(f)
        for v in state.values():
            v["ts"] = time.time() - 8 * 86400        # 超过 7 天保留期
        with open(main.ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        main.filter_alerts([_w(title="fresh")], _cfg())
        with open(main.ALERT_STATE, encoding="utf-8") as f:
            state = json.load(f)
        self.assertNotIn("暴雨|ancient|2026-11-02T08:00+08:00", state)
        self.assertIn("暴雨|fresh|2026-11-02T08:00+08:00", state)


class RecordRunTest(unittest.TestCase):
    def test_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(main, "LAST_RUN",
                              os.path.join(tmp.name, "last_run.json"))
        p.start()
        self.addCleanup(p.stop)
        self.assertIsNone(main.load_last_run())
        main.record_run("forecast", {"ok": True, "title": "T", "error": "",
                                     "channels": [{"channel": "企业微信",
                                                   "ok": True, "error": ""}]})
        rec = main.load_last_run()
        self.assertEqual(rec["kind"], "forecast")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["title"], "T")
        self.assertEqual(rec["channels"][0]["channel"], "企业微信")


def _chan(**kw):
    base = {"id": "c1", "type": "wecom", "name": "测试群",
            "enabled": True, "webhook": "http://hook", "msg_type": "text"}
    base.update(kw)
    return base


def _cfg2(channels):
    return {"push": {"channels": channels}}


class PushAllTest(unittest.TestCase):
    """push_all：多通道逐个推送、跳过停用、失败隔离、友好报错。"""

    def run_push(self, channels, role="prod"):
        out = {"wecom": [], "magicpush": []}
        with mock.patch.object(main.pusher, "push_wecom",
                               side_effect=lambda *a, **k: out["wecom"].append(a)), \
             mock.patch.object(main.pusher, "push_magicpush",
                               side_effect=lambda *a, **k: out["magicpush"].append(a)):
            results, title = main.push_all(_cfg2(channels),
                                           lambda style: (f"T-{style}", f"B-{style}"),
                                           role)
        return results, title, out

    def test_all_enabled_channels_pushed(self):
        chans = [_chan(), _chan(id="c2", type="magicpush", name="MP",
                                base_url="http://mp", token="tk")]
        results, title, out = self.run_push(chans)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual([r["channel"] for r in results], ["测试群", "MP"])
        self.assertEqual(title, "T-text")          # 标题取第一个成功通道
        self.assertEqual(len(out["wecom"]), 1)
        # magicpush 收到 (base_url, token, title, body, msg_type)
        self.assertEqual(out["magicpush"][0][0], "http://mp")
        self.assertEqual(out["magicpush"][0][2], "T-md")

    def test_disabled_and_unknown_skipped(self):
        chans = [_chan(enabled=False),
                 _chan(id="c2", type="sms"),
                 {"type": "wecom", "enabled": True, "webhook": "http://h2"}]
        results, _, out = self.run_push(chans)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(out["wecom"]), 1)

    def test_missing_webhook_friendly_error(self):
        chans = [_chan(webhook="   ")]
        results, _, _ = self.run_push(chans)
        self.assertFalse(results[0]["ok"])
        self.assertIn("webhook", results[0]["error"])

    def test_missing_magicpush_credentials(self):
        chans = [_chan(type="magicpush", base_url="", token="t")]
        results, _, _ = self.run_push(chans)
        self.assertFalse(results[0]["ok"])
        self.assertIn("未填写", results[0]["error"])

    def test_failure_isolated(self):
        chans = [_chan(id="c1"), _chan(id="c2", name="二号")]
        with mock.patch.object(main.pusher, "push_wecom",
                               side_effect=[RuntimeError("boom"), None]):
            results, _ = main.push_all(_cfg2(chans),
                                       lambda style: ("T", "B"))
        self.assertEqual([r["ok"] for r in results], [False, True])
        self.assertIn("boom", results[0]["error"])

    def test_no_channels(self):
        results, title = main.push_all(_cfg2([]), lambda s: ("T", "B"))
        self.assertEqual(results, [])
        self.assertEqual(title, "")

    def test_prod_role_only_pushes_prod_channels(self):
        chans = [_chan(id="p1", name="正式群", role="prod"),
                 _chan(id="t1", name="测试群", role="test")]
        results, _, out = self.run_push(chans, role="prod")
        self.assertEqual([r["channel"] for r in results], ["正式群"])
        self.assertEqual(len(out["wecom"]), 1)

    def test_test_role_only_pushes_test_channels(self):
        chans = [_chan(id="p1", name="正式群", role="prod"),
                 _chan(id="t1", name="测试群", role="test"),
                 _chan(id="t2", name="测试群2", role="test")]
        results, _, out = self.run_push(chans, role="test")
        self.assertEqual([r["channel"] for r in results], ["测试群", "测试群2"])
        self.assertEqual(len(out["wecom"]), 2)

    def test_missing_role_treated_as_prod(self):
        # 老配置没有 role 字段：必须仍走正式播报，否则用户会突然收不到
        chans = [{"id": "c1", "type": "wecom", "name": "老通道", "enabled": True,
                  "webhook": "http://hook", "msg_type": "text"}]
        results, _, _ = self.run_push(chans, role="prod")
        self.assertEqual(len(results), 1)
        results, _, _ = self.run_push(chans, role="test")
        self.assertEqual(results, [])

    def test_both_roles_channel_receives_both(self):
        # 一个通道同时是正式 + 测试：两条链路都该发到它
        chans = [_chan(id="b1", name="两用群", roles=["prod", "test"],
                       webhook="http://both"),
                 _chan(id="p1", name="正式群", roles=["prod"],
                       webhook="http://prod")]
        _, _, out = self.run_push(chans, role="prod")
        self.assertEqual([a[0] for a in out["wecom"]], ["http://both", "http://prod"])
        _, _, out = self.run_push(chans, role="test")
        self.assertEqual([a[0] for a in out["wecom"]], ["http://both"])


class PreviewStylesTest(unittest.TestCase):
    def test_enabled_channels_each_style(self):
        chans = [_chan(), _chan(id="c2", type="magicpush")]
        styles = main.preview_styles(_cfg2(chans))
        self.assertEqual([(k, s) for k, _, s in styles],
                         [("wecom_text", "text"), ("magicpush", "md")])

    def test_wecom_markdown_style(self):
        styles = main.preview_styles(_cfg2([_chan(msg_type="markdown")]))
        self.assertEqual(styles[0][0], "wecom_md")
        self.assertEqual(styles[0][2], "md")

    def test_same_style_deduped(self):
        chans = [_chan(), _chan(id="c2", name="二号")]
        styles = main.preview_styles(_cfg2(chans))
        self.assertEqual(len(styles), 1)

    def test_fallback_when_none_enabled(self):
        chans = [_chan(enabled=False, msg_type="text")]
        styles = main.preview_styles(_cfg2(chans))
        self.assertEqual(styles, [("preview", "预览 · 纯文本", "text")])
        styles = main.preview_styles(_cfg2([]))
        self.assertEqual(styles, [("preview", "预览 · Markdown", "md")])

    def test_preview_style_helper(self):
        self.assertEqual(main.preview_style(_cfg2([_chan(msg_type="markdown")])), "md")

    def test_role_filtered_styles(self):
        chans = [_chan(id="p1", role="prod", msg_type="markdown"),
                 _chan(id="t1", role="test", msg_type="text")]
        # 正式预览只看正式通道：md
        self.assertEqual([s for _, _, s in main.preview_styles(_cfg2(chans))], ["md"])
        # 测试推送只看测试通道：text
        self.assertEqual(
            [s for _, _, s in main.preview_styles(_cfg2(chans), role="test")],
            ["text"])

    def test_test_role_without_test_channel_falls_back(self):
        # 没有测试通道时给一份默认预览（实际发送由 _channel_errors 拦下）
        styles = main.preview_styles(_cfg2([_chan(role="prod")]), role="test")
        self.assertEqual(styles[0][0], "preview")

    def test_both_roles_channel_in_both_previews(self):
        chans = [_chan(id="b1", roles=["prod", "test"], msg_type="markdown")]
        self.assertEqual([s for _, _, s in main.preview_styles(_cfg2(chans))], ["md"])
        self.assertEqual(
            [s for _, _, s in main.preview_styles(_cfg2(chans), role="test")], ["md"])


class UtilTest(unittest.TestCase):
    def test_qw_icon(self):
        self.assertEqual(main._qw_icon("100"), "☀️")
        self.assertEqual(main._qw_icon("150"), "🌙")
        self.assertEqual(main._qw_icon("303"), "⛈️")
        self.assertEqual(main._qw_icon("305"), "🌧️")
        self.assertEqual(main._qw_icon("401"), "🌨️")
        self.assertEqual(main._qw_icon("501"), "🌫️")
        self.assertEqual(main._qw_icon("999"), "🌡️")
        self.assertEqual(main._qw_icon(None), "🌡️")

    def test_int_or_none(self):
        self.assertEqual(main._int_or_none("23"), 23)
        self.assertEqual(main._int_or_none(23.0), 23)
        self.assertEqual(main._int_or_none("23.6"), 24)   # round 到整数
        self.assertEqual(main._int_or_none("-1.2"), -1)
        self.assertIsNone(main._int_or_none("abc"))
        self.assertIsNone(main._int_or_none(None))

    def test_week_label(self):
        self.assertEqual(main._week_label("2026-11-02"), "周一")
        self.assertEqual(main._week_label("not-a-date"), "")
        self.assertEqual(main._week_label(""), "")


class TestQweatherTest(unittest.TestCase):
    """和风 API 连通性测试（test_qweather）。"""

    FULL = {"qweather": {"api_host": "h.re.qweatherapi.com",
                         "project_id": "p", "credential_id": "k"}}

    def test_missing_fields_lists_names(self):
        with self.assertRaises(main.QWeatherError) as cm:
            main.test_qweather({"qweather": {"api_host": "h"}}, "PEM")
        msg = str(cm.exception)
        for name in ("项目 ID", "凭据 ID"):
            self.assertIn(name, msg)

    def test_missing_private_key(self):
        with self.assertRaises(main.QWeatherError) as cm:
            main.test_qweather(self.FULL, None)
        self.assertIn("私钥", str(cm.exception))

    def test_success_reports_city(self):
        client = mock.MagicMock()
        client.city_lookup.return_value = [{"name": "北京"}]
        with mock.patch.object(main, "_client", return_value=client) as mk:
            out = main.test_qweather(self.FULL, "PEM")
        self.assertIn("北京", out)
        mk.assert_called_once_with(self.FULL, "PEM")
        client.city_lookup.assert_called_once_with("北京", number=1)

    def test_api_error_wrapped(self):
        client = mock.MagicMock()
        client.city_lookup.side_effect = main.QWeatherError("code=401")
        with mock.patch.object(main, "_client", return_value=client):
            with self.assertRaises(main.QWeatherError) as cm:
                main.test_qweather(self.FULL, "PEM")
        self.assertIn("测试失败", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
