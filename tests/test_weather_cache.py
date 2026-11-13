"""server：天气缓存的 stale-while-revalidate 行为。

不启动 HTTP 服务，直接测 Handler._cached_weather 与 _bg_refresh_weather：
新鲜命中不拉数、过期先返回旧数据并触发后台刷新、无缓存才同步拉、刷新有冷却。
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE_DIR  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import server  # noqa: E402
from qweather import QWeatherError  # noqa: E402

CFG = {"location": "112.23,30.06", "location_name": "荆州",
       "hourly_hours": 6, "forecast_days": 7, "indices_types": "1,2"}


def payload(temp=20):
    return {"source": "qweather", "updated": "23:50", "now": {"temp": temp}}


class InlineThread:
    """把后台线程改成同步执行，便于断言刷新结果。"""

    def __init__(self, target=None, name=None, daemon=None, **kw):
        self._target = target

    def start(self):
        self._target()


class WeatherCacheTest(unittest.TestCase):
    def setUp(self):
        # 这些用例只关心缓存策略，把「和风凭据是否齐全」当成已配置
        p = mock.patch.object(server.core, "qweather_ready", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self._old_cache = dict(server._WEATHER_CACHE)
        self._old_state = dict(server._REFRESH_STATE)
        server._WEATHER_CACHE.update({"key": None, "ts": 0.0, "data": None})
        server._REFRESH_STATE.update({"ts": 0.0, "running": False})
        self.addCleanup(self._restore)

    def _restore(self):
        server._WEATHER_CACHE.update(self._old_cache)
        server._REFRESH_STATE.update(self._old_state)

    def _warm(self, temp=20):
        """先正常拉一次，让缓存里有一份数据。"""
        with mock.patch.object(server.core, "fetch_weather",
                               return_value=payload(temp)) as f:
            data, cached, error, info = server.Handler._cached_weather(CFG, None)
        self.assertTrue(f.called)
        self.assertFalse(cached)
        self.assertEqual(data["now"]["temp"], temp)
        return data

    def test_missing_credentials_returns_need_setup(self):
        with mock.patch.object(server.core, "qweather_ready", return_value=False), \
                mock.patch.object(server.core, "fetch_weather") as f:
            data, cached, error, info = server.Handler._cached_weather(CFG, None)
        self.assertIsNone(data)
        self.assertFalse(f.called, "没配和风不该去打任何上游")
        self.assertTrue(info["need_setup"])
        self.assertIn("还没配置和风天气", error)

    def test_no_cache_fetches_synchronously(self):
        self._warm(18)
        self.assertEqual(server._WEATHER_CACHE["data"]["now"]["temp"], 18)

    def test_fresh_cache_does_not_call_upstream(self):
        self._warm(21)
        with mock.patch.object(server.core, "fetch_weather") as f:
            data, cached, error, info = server.Handler._cached_weather(CFG, None)
        self.assertFalse(f.called, "新鲜期内不应该请求上游")
        self.assertTrue(cached)
        self.assertEqual(data["now"]["temp"], 21)
        self.assertFalse(info["stale"])
        self.assertLess(info["age"], server.CACHE_FRESH_TTL)

    def test_stale_returns_old_data_and_triggers_refresh(self):
        self._warm(19)
        server._WEATHER_CACHE["ts"] = time.time() - (server.CACHE_FRESH_TTL + 5)
        with mock.patch.object(server, "_bg_refresh_weather",
                               return_value=True) as bg, \
             mock.patch.object(server.core, "fetch_weather") as f:
            data, cached, error, info = server.Handler._cached_weather(CFG, None)
        self.assertFalse(f.called, "过期时也应先返回旧数据，不阻塞页面")
        self.assertEqual(data["now"]["temp"], 19)
        self.assertTrue(cached)
        self.assertTrue(info["stale"])
        self.assertTrue(bg.called)

    def test_force_ignores_cache(self):
        self._warm(22)
        with mock.patch.object(server.core, "fetch_weather",
                               return_value=payload(9)) as f:
            data, cached, error, info = server.Handler._cached_weather(
                CFG, None, force=True)
        self.assertTrue(f.called)
        self.assertEqual(data["now"]["temp"], 9)
        self.assertFalse(cached)

    def test_cache_key_changes_with_settings(self):
        self._warm(23)
        other = dict(CFG, hourly_hours=12)
        with mock.patch.object(server.core, "fetch_weather",
                               return_value=payload(7)) as f:
            data, cached, error, info = server.Handler._cached_weather(other, None)
        self.assertTrue(f.called, "改了逐小时条数应该重新取数")
        self.assertEqual(data["now"]["temp"], 7)

    def test_background_refresh_updates_cache(self):
        self._warm(10)
        with mock.patch.object(server.threading, "Thread", InlineThread), \
             mock.patch.object(server.core, "fetch_weather",
                               return_value=payload(11)):
            self.assertTrue(server._bg_refresh_weather(CFG, None, "k"))
        self.assertEqual(server._WEATHER_CACHE["data"]["now"]["temp"], 11)
        self.assertFalse(server._REFRESH_STATE["running"])

    def test_background_refresh_has_cooldown(self):
        with mock.patch.object(server.threading, "Thread", InlineThread), \
             mock.patch.object(server.core, "fetch_weather",
                               return_value=payload(12)):
            self.assertTrue(server._bg_refresh_weather(CFG, None, "k"))
            self.assertFalse(server._bg_refresh_weather(CFG, None, "k"),
                             "冷却期内不应重复刷新")

    def test_failed_refresh_has_retry_gap(self):
        with mock.patch.object(server.threading, "Thread", InlineThread), \
             mock.patch.object(server.core, "fetch_weather",
                               side_effect=QWeatherError("boom")):
            self.assertTrue(server._bg_refresh_weather(CFG, None, "k"))
            self.assertFalse(server._bg_refresh_weather(CFG, None, "k"),
                             "失败后 60 秒内不应立即重试")
        self.assertFalse(server._REFRESH_STATE["running"])


if __name__ == "__main__":
    unittest.main()
