"""update_check：版本号解析 / 比较、当前版本来源、GitHub 检测、缓存与检查频率。"""
import json
import os
import sys
import threading
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import TempConfCase  # noqa: E402

import config_loader  # noqa: E402
import update_check  # noqa: E402


def rel(version=None, error=None, assets=None):
    """fetch_latest 的返回值（Release 具名元组）。"""
    return update_check.Release(version, error, assets or [])


class VersionParseTest(TempConfCase):
    def test_parse(self):
        self.assertEqual(update_check.parse_version("v0.1.2"), (0, 1, 2, 1))
        self.assertEqual(update_check.parse_version("0.1.2"), (0, 1, 2, 1))
        self.assertEqual(update_check.parse_version("1"), (1, 0, 0, 1))
        self.assertEqual(update_check.parse_version("  V2.0 "), (2, 0, 0, 1))
        # 预发布版比同号正式版旧
        self.assertEqual(update_check.parse_version("0.1.2-beta.1"), (0, 1, 2, 0))
        for bad in ("", "abc", None, "v1.2.3.4", 123):
            self.assertIsNone(update_check.parse_version(bad), bad)

    def test_compare(self):
        self.assertEqual(update_check.compare("0.1.2", "v0.1.2"), 0)
        self.assertEqual(update_check.compare("0.1.2", "0.2.0"), -1)
        self.assertEqual(update_check.compare("0.2.0", "0.1.9"), 1)
        # 按数字比大小，不是按字符串
        self.assertEqual(update_check.compare("0.1.10", "0.1.9"), 1)
        self.assertEqual(update_check.compare("1.0.0", "0.9.9"), 1)
        self.assertEqual(update_check.compare("0.1.2", "0.1.2-beta"), 1)
        # 解析失败不炸，按「一样」处理
        self.assertEqual(update_check.compare("abc", "0.1.2"), 0)


class CurrentVersionTest(TempConfCase):
    def setUp(self):
        super().setUp()
        src = os.path.join(self.tmp_dir, "src")
        os.makedirs(src)
        self.src_dir = src
        p = mock.patch.object(config_loader, "SRC_DIR", src)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WEATHER_NOTIFY_VERSION", None)

    def _write(self, rel_path, text):
        path = os.path.join(self.tmp_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_env_override(self):
        self._write("fpk/manifest", "appname = weathernotify\nversion = 0.1.2\n")
        with mock.patch.dict(os.environ, {"WEATHER_NOTIFY_VERSION": "9.9.9"}):
            self.assertEqual(update_check.current_version(), "9.9.9")

    def test_repo_manifest(self):
        self._write("fpk/manifest", "# 注释\nappname = weathernotify\nversion = 0.1.2\n")
        self.assertEqual(update_check.current_version(), "0.1.2")

    def test_packaged_manifest(self):
        # fpk 包：app/src → app/manifest 的上两级
        self._write("manifest", "version               = 0.3.1\n")
        self.assertEqual(update_check.current_version(), "0.3.1")

    def test_version_file(self):
        self._write("VERSION", "v0.4.0\n")
        self.assertEqual(update_check.current_version(), "0.4.0")

    def test_missing(self):
        self.assertIsNone(update_check.current_version())

    def test_garbage_version_ignored(self):
        self._write("fpk/manifest", "version = not-a-version\n")
        self._write("VERSION", "0.5.0\n")
        self.assertEqual(update_check.current_version(), "0.5.0")


class StateTest(TempConfCase):
    """缓存与三种检测结果（默认 auto 模式，打桩 mode 免得读真配置）。"""

    def setUp(self):
        super().setUp()
        update_check.reset()
        self.addCleanup(update_check.reset)
        p = mock.patch.dict(os.environ, {"WEATHER_NOTIFY_VERSION": "0.1.2"})
        p.start()
        self.addCleanup(p.stop)
        m = mock.patch.object(update_check, "mode", return_value="auto")
        m.start()
        self.addCleanup(m.stop)

    def test_latest(self):
        with mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            state = update_check.get_state()
        self.assertEqual(state["state"], "latest")
        self.assertEqual(state["latest"], "0.1.2")
        self.assertIsNone(state["error"])
        self.assertTrue(state["checked_text"])
        self.assertEqual(state["mode"], "auto")
        self.assertEqual(state["assets"], [])

    def test_outdated(self):
        with mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.2.0")):
            state = update_check.get_state()
        self.assertEqual(state["state"], "outdated")

    def test_outdated_carries_download_links(self):
        assets = [{"name": "weathernotify_0.2.0.fpk", "url": "https://e/a.fpk",
                   "size": 6100000, "arch": "x86"}]
        with mock.patch.object(update_check, "fetch_latest",
                               return_value=rel("v0.2.0", None, assets)):
            state = update_check.get_state()
        self.assertEqual(state["state"], "outdated")
        self.assertEqual(state["assets"], assets)
        self.assertTrue(state["release_url"].endswith("/tag/v0.2.0"))

    def test_failure_is_unknown(self):
        with mock.patch.object(update_check, "fetch_latest",
                               return_value=rel(None, "连不上 GitHub（大概网络不通）")):
            state = update_check.get_state()
        self.assertEqual(state["state"], "unknown")
        self.assertIn("连不上 GitHub", state["error"])
        self.assertIsNone(state["latest"])

    def test_cache_hit_skips_network(self):
        fake = mock.Mock(return_value=rel("v0.1.2"))
        with mock.patch.object(update_check, "fetch_latest", fake):
            first = update_check.get_state()
            second = update_check.get_state()
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(first["state"], second["state"])
        self.assertFalse(second["stale"])

    def test_force_bypasses_cache(self):
        fake = mock.Mock(side_effect=[rel("v0.1.2"), rel("v0.3.0")])
        with mock.patch.object(update_check, "fetch_latest", fake):
            update_check.get_state()
            state = update_check.get_state(force=True)
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(state["state"], "outdated")
        self.assertEqual(state["latest"], "0.3.0")

    def test_success_cached_for_24_hours(self):
        with mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            update_check.get_state()
        # 23 小时前的成功结果仍算新鲜：一天只查一次，别每次开页面都打 GitHub
        path = update_check.cache_path()
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        cache["checked_at"] = time.time() - 23 * 3600
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        kicked = mock.Mock()
        with mock.patch.object(update_check, "_kick_refresh", kicked):
            state = update_check.get_state()
        kicked.assert_not_called()
        self.assertFalse(state["stale"])

    def test_stale_cache_kicks_background_refresh(self):
        with mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            update_check.get_state()
        # 把缓存时间改到 25 小时前（超过 24 小时 TTL）
        path = update_check.cache_path()
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        cache["checked_at"] = time.time() - 25 * 3600
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)

        kicked = mock.Mock()
        with mock.patch.object(update_check, "_kick_refresh", kicked):
            state = update_check.get_state()
        kicked.assert_called_once()
        self.assertTrue(state["stale"])
        self.assertEqual(state["state"], "latest")   # 旧结果照用，不因为刷新失败变红

    def test_failed_retry_keeps_known_latest(self):
        with mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            update_check.get_state()
        with mock.patch.object(update_check, "fetch_latest",
                               return_value=rel(None, "GitHub 限流了，稍后再试（HTTP 403）")):
            state = update_check.get_state(force=True)
        # 之前查到的版本号保留，state 不变，错误信息透出
        self.assertEqual(state["latest"], "0.1.2")
        self.assertEqual(state["state"], "latest")
        self.assertIn("限流", state["error"])

    def test_failed_check_retries_after_15_minutes(self):
        # 成功查到过 latest，之后 GitHub 挂了：缓存里还留着旧版本号，
        # 但它不能按 24 小时算「新鲜」，否则一次失败会压住一整天的重试
        path = update_check.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"latest": "0.1.2", "assets": [],
                       "error": "连不上 GitHub",
                       "checked_at": time.time() - 16 * 60}, f)
        kicked = mock.Mock()
        fake = mock.Mock(return_value=rel("v0.1.2"))
        with mock.patch.object(update_check, "_kick_refresh", kicked), \
                mock.patch.object(update_check, "fetch_latest", fake):
            state = update_check.get_state()
        kicked.assert_called_once()
        self.assertTrue(state["stale"])

    def test_refresh_does_not_block_page_requests(self):
        # 后台刷新时请求线程会走到 _kick_refresh()：它不该被网络请求卡住
        started, release = threading.Event(), threading.Event()

        def slow_fetch():
            started.set()
            release.wait(2)
            return rel("v0.1.2")

        with mock.patch.object(update_check, "fetch_latest", side_effect=slow_fetch):
            worker = threading.Thread(target=update_check._refresh, daemon=True)
            worker.start()
            self.assertTrue(started.wait(1))
            began = time.time()
            update_check._kick_refresh()
            elapsed = time.time() - began
            release.set()
            worker.join(3)
        self.assertLess(elapsed, 0.5)

    def test_failure_cached_for_15_minutes(self):
        fake = mock.Mock(return_value=rel(None, "超时"))
        with mock.patch.object(update_check, "fetch_latest", fake):
            update_check.get_state()
            update_check.get_state()
        self.assertEqual(fake.call_count, 1)

    def test_bad_current_version_is_unknown(self):
        os.environ["WEATHER_NOTIFY_VERSION"] = ""
        with mock.patch.object(update_check, "current_version", return_value=None), \
                mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            state = update_check.get_state()
        self.assertEqual(state["state"], "unknown")
        self.assertIn("读不到当前版本号", state["error"])


class ModeTest(TempConfCase):
    """设置页的「检查更新」：auto / manual / off。"""

    def setUp(self):
        super().setUp()
        update_check.reset()
        self.addCleanup(update_check.reset)
        p = mock.patch.dict(os.environ, {"WEATHER_NOTIFY_VERSION": "0.1.2"})
        p.start()
        self.addCleanup(p.stop)

    def test_off_never_touches_github(self):
        fake = mock.Mock()
        with mock.patch.object(update_check, "mode", return_value="off"), \
                mock.patch.object(update_check, "fetch_latest", fake):
            state = update_check.get_state(force=True)
        fake.assert_not_called()
        self.assertEqual(state["state"], "off")
        self.assertEqual(state["current"], "0.1.2")
        self.assertIsNone(state["error"])
        self.assertFalse(os.path.exists(update_check.cache_path()))

    def test_manual_without_cache_is_unchecked(self):
        fake = mock.Mock()
        with mock.patch.object(update_check, "mode", return_value="manual"), \
                mock.patch.object(update_check, "fetch_latest", fake):
            state = update_check.get_state()
        fake.assert_not_called()
        self.assertEqual(state["state"], "unchecked")

    def test_manual_uses_cache_without_background_refresh(self):
        with mock.patch.object(update_check, "mode", return_value="manual"), \
                mock.patch.object(update_check, "fetch_latest", return_value=rel("v0.1.2")):
            first = update_check.get_state(force=True)
        self.assertEqual(first["state"], "latest")
        path = update_check.cache_path()
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        cache["checked_at"] = time.time() - 30 * 24 * 3600
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        kicked = mock.Mock()
        with mock.patch.object(update_check, "mode", return_value="manual"), \
                mock.patch.object(update_check, "_kick_refresh", kicked):
            again = update_check.get_state()
        kicked.assert_not_called()
        self.assertTrue(again["stale"])
        self.assertEqual(again["state"], "latest")

    def test_manual_force_checks(self):
        fake = mock.Mock(return_value=rel("v0.1.2"))
        with mock.patch.object(update_check, "mode", return_value="manual"), \
                mock.patch.object(update_check, "fetch_latest", fake):
            state = update_check.get_state(force=True)
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(state["state"], "latest")

    def test_mode_reads_saved_config(self):
        config_loader.save_raw({"update_check": "off"})
        self.assertEqual(update_check.mode(), "off")
        config_loader.save_raw({"update_check": "manual"})
        self.assertEqual(update_check.mode(), "manual")
        config_loader.save_raw({})
        self.assertEqual(update_check.mode(), "auto")


class FetchLatestTest(TempConfCase):
    """fetch_latest 的 HTTP 分支（requests 打桩，不联网）。"""

    def setUp(self):
        super().setUp()
        import requests
        self.requests = requests

    def _resp(self, code, payload=None):
        resp = mock.Mock()
        resp.status_code = code
        resp.json.return_value = payload if payload is not None else {}
        return resp

    def test_ok(self):
        with mock.patch.object(self.requests, "get",
                               return_value=self._resp(200, {"tag_name": "v0.2.0"})):
            release = update_check.fetch_latest()
        self.assertEqual((release.version, release.error), ("0.2.0", None))
        self.assertEqual(release.assets, [])

    def test_fpk_assets(self):
        payload = {"tag_name": "v0.2.0", "assets": [
            {"name": "weathernotify_0.2.0.fpk", "size": 6100000,
             "browser_download_url": "https://example.com/a.fpk"},
            {"name": "weathernotify_0.2.0_arm.fpk", "size": 6000000,
             "browser_download_url": "https://example.com/b.fpk"},
            {"name": "source.zip", "size": 10,
             "browser_download_url": "https://example.com/s.zip"},
            {"name": "broken.fpk", "size": 1, "browser_download_url": ""},
        ]}
        with mock.patch.object(self.requests, "get",
                               return_value=self._resp(200, payload)):
            release = update_check.fetch_latest()
        self.assertEqual([a["name"] for a in release.assets],
                         ["weathernotify_0.2.0.fpk", "weathernotify_0.2.0_arm.fpk"])
        self.assertEqual([a["arch"] for a in release.assets], ["x86", "arm"])
        self.assertEqual(release.assets[0]["size"], 6100000)

    def test_404(self):
        with mock.patch.object(self.requests, "get", return_value=self._resp(404)):
            release = update_check.fetch_latest()
        self.assertIsNone(release.version)
        self.assertIn("404", release.error)

    def test_rate_limited(self):
        with mock.patch.object(self.requests, "get", return_value=self._resp(403)):
            release = update_check.fetch_latest()
        self.assertIsNone(release.version)
        self.assertIn("限流", release.error)

    def test_network_error(self):
        with mock.patch.object(self.requests, "get",
                               side_effect=self.requests.exceptions.Timeout("timed out")):
            release = update_check.fetch_latest()
        self.assertIsNone(release.version)
        self.assertIn("超时", release.error)

    def test_no_tag(self):
        with mock.patch.object(self.requests, "get",
                               return_value=self._resp(200, {"name": "随便"})):
            release = update_check.fetch_latest()
        self.assertIsNone(release.version)
        self.assertIn("版本号", release.error)
