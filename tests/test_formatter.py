"""formatter：AQI 色点、星期、模板渲染、标题并入正文。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import formatter  # noqa: E402

WEATHER = {
    "now": {"icon": "☀️", "temp": "16", "feels": "15", "text": "多云",
            "wind_dir": "东北风", "wind_scale": "2", "humidity": 39},
    "air": {"aqi": 106, "category": "轻度污染"},
    "updated": "20:25", "source_label": "和风天气",
}


class AqiMarkTest(unittest.TestCase):
    def test_six_bands(self):
        f = formatter._aqi_mark
        self.assertEqual(f(10, "优"), "🟢 优")
        self.assertEqual(f(50, "优"), "🟢 优")
        self.assertEqual(f(93, "良"), "🟡 良")
        self.assertEqual(f(106, "轻度污染"), "🟠 轻度污染")
        self.assertEqual(f(180, "中度污染"), "🔴 中度污染")
        self.assertEqual(f(280, "重度污染"), "🟣 重度污染")
        self.assertEqual(f(350, "严重污染"), "🟤 严重污染")

    def test_category_only_when_aqi_dirty(self):
        self.assertEqual(formatter._aqi_mark("abc", "良"), "良")
        self.assertEqual(formatter._aqi_mark(None, "优"), "优")

    def test_no_category(self):
        self.assertEqual(formatter._aqi_mark(93), "-")
        self.assertEqual(formatter._aqi_mark(None), "-")


class WeekOfTest(unittest.TestCase):
    def test_known_dates(self):
        self.assertEqual(formatter._week_of("2026-11-02"), "周一")
        self.assertEqual(formatter._week_of("2026-11-08"), "周日")

    def test_invalid(self):
        self.assertEqual(formatter._week_of("bad"), "")
        self.assertEqual(formatter._week_of(""), "")


class InlineTitleTest(unittest.TestCase):
    def test_markdown_gets_h1(self):
        self.assertEqual(formatter.inline_title("T", "B", "md"), "# T\n\nB")

    def test_text_stays_plain(self):
        self.assertEqual(formatter.inline_title("T", "B", "text"), "T\n\nB")

    def test_empty_sides(self):
        self.assertEqual(formatter.inline_title("", "B"), "B")
        self.assertEqual(formatter.inline_title("T", ""), "# T")
        self.assertEqual(formatter.inline_title("", ""), "")


class ToPlainTest(unittest.TestCase):
    def test_strips_markdown(self):
        md = "# 标题\n> **加粗** 正文\n"
        self.assertEqual(formatter.to_plain(md), "标题\n加粗 正文")


class RenderTest(unittest.TestCase):
    def test_broadcast_replaces_known_and_keeps_unknown(self):
        tpl = {"broadcast_title": "{icon} {city} {temp}°C {nope}",
               "broadcast_body": "{city}|{air}|{hourly}|{nope}"}
        title, body = formatter.render(WEATHER, "公安", "broadcast", "md", tpl)
        self.assertEqual(title, "☀️ 公安 16°C {nope}")
        self.assertIn("公安", body)
        self.assertIn("轻度污染", body)
        self.assertIn("{nope}", body)            # 写错的变量原样显示
        self.assertNotIn("{hourly}", body)       # 空区块不留占位符
        self.assertNotIn("{air}", body)

    def test_default_title_has_date_and_weekday(self):
        title, _ = formatter.render(WEATHER, "公安", "broadcast", "md")
        self.assertIn("公安", title)
        self.assertIn("月", title)               # {date}：N月N日
        self.assertIn("周", title)               # {weekday}

    def test_alert_mode_without_weather(self):
        warnings = [{"typeName": "暴雨", "level": "橙色", "title": "暴雨橙色预警",
                     "status": "预警中", "text": "注意防范"}]
        tpl = {"alert_title": "⚠️ {city}{alert_type}预警（{alert_level}）· {clock}",
               "alert_body": "{warnings}"}
        title, body = formatter.render(None, "公安", "alert", "md",
                                       tpl, warnings=warnings)
        self.assertIn("公安暴雨预警（橙色）", title)
        self.assertIn("注意防范", body)

    def test_text_style_has_no_md_marks(self):
        _, body = formatter.render(WEATHER, "公安", "broadcast", "text")
        self.assertNotIn("**", body)
        self.assertNotIn("> ", body)


if __name__ == "__main__":
    unittest.main()
