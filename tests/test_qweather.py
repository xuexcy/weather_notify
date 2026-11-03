"""qweather：新版预警结构归一化（颜色等级、解除通知、字段兜底）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import qweather  # noqa: E402


def _alert(**kw):
    base = {
        "id": "alert-1",
        "eventType": {"name": "暴雨"},
        "color": {"code": "ORANGE"},
        "severity": "severe",
        "messageType": {"code": "alert"},
        "headline": "暴雨橙色预警",
        "description": "注意防范强降水",
        "instruction": "停课停业",
        "senderName": "气象台",
        "onsetTime": "2026-11-02T08:00+08:00",
    }
    base.update(kw)
    return base


class AlertOutTest(unittest.TestCase):
    def test_full_mapping(self):
        out = qweather._alert_out(_alert())
        self.assertEqual(out["id"], "alert-1")
        self.assertEqual(out["typeName"], "暴雨")
        self.assertEqual(out["level"], "橙色")        # color 优先于 severity
        self.assertEqual(out["title"], "暴雨橙色预警")
        self.assertEqual(out["status"], "预警中")
        self.assertEqual(out["sender"], "气象台")
        self.assertEqual(out["startTime"], "2026-11-02T08:00+08:00")

    def test_instruction_merged_once(self):
        out = qweather._alert_out(_alert())
        self.assertIn("注意防范强降水", out["text"])
        self.assertIn("停课停业", out["text"])
        # instruction 已包含在 description 里时不重复
        out2 = qweather._alert_out(_alert(instruction="注意防范强降水"))
        self.assertEqual(out2["text"], "注意防范强降水")

    def test_cancel_downgrades_to_white(self):
        out = qweather._alert_out(_alert(messageType={"code": "cancel"}))
        self.assertEqual(out["level"], "白色")        # 解除通知不渲染成「红色·已取消」
        self.assertEqual(out["status"], "已取消")

    def test_severity_fallback(self):
        out = qweather._alert_out(_alert(color=None))
        self.assertEqual(out["level"], "橙色")        # 无颜色时用 severity
        out2 = qweather._alert_out(_alert(color=None, severity="extreme"))
        self.assertEqual(out2["level"], "红色")
        out3 = qweather._alert_out(_alert(color=None, severity="unknown"))
        self.assertEqual(out3["level"], "白色")

    def test_missing_fields_defaults(self):
        out = qweather._alert_out({})
        self.assertEqual(out["typeName"], "预警")
        self.assertEqual(out["level"], "白色")
        self.assertEqual(out["title"], "")
        self.assertEqual(out["status"], "预警中")   # messageType 缺省 alert
        self.assertEqual(out["startTime"], "")


if __name__ == "__main__":
    unittest.main()
