"""config_loader：时间点归一、间隔钳制、坐标解析、配置迁移与脱敏。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import TempConfCase  # noqa: E402

import config_loader  # noqa: E402


class NormalizeTimesTest(TempConfCase):
    def test_list_and_string(self):
        self.assertEqual(config_loader.normalize_times(["07:30", "18:00"]),
                         ["07:30", "18:00"])
        self.assertEqual(config_loader.normalize_times("07:30, 18:00"),
                         ["07:30", "18:00"])
        # 全角逗号与多余空白
        self.assertEqual(config_loader.normalize_times("7:5，12:00  21:00"),
                         ["07:05", "12:00", "21:00"])

    def test_invalid_dropped_and_dedup_sorted(self):
        self.assertEqual(config_loader.normalize_times(["99:99", "7:60",
                                                        "abc", "08:00", "8:0"]),
                         ["08:00"])
        self.assertEqual(config_loader.normalize_times(None), [])
        self.assertEqual(config_loader.normalize_times(42), [])
        self.assertEqual(config_loader.normalize_times(["07:30", "07:30", "7:30"]),
                         ["07:30"])

    def test_max_times(self):
        many = [f"{h:02d}:00" for h in range(1, 30)]
        out = config_loader.normalize_times(many)
        self.assertEqual(len(out), config_loader.SCHEDULE_MAX_TIMES)


class ClampWarningIntervalTest(TempConfCase):
    def test_bounds(self):
        f = config_loader.clamp_warning_interval
        self.assertEqual(f(0), 1)
        self.assertEqual(f(1), 1)
        self.assertEqual(f(10), 10)
        self.assertEqual(f(120), 120)
        self.assertEqual(f(999), 120)

    def test_dirty_values_fall_back_to_default(self):
        f = config_loader.clamp_warning_interval
        for bad in ("abc", None, [], {}):
            self.assertEqual(f(bad), config_loader.SCHEDULE_WARNING_INTERVAL)
        self.assertEqual(f("15"), 15)


class ScheduleFromTest(TempConfCase):
    def test_full_config(self):
        sch = config_loader.schedule_from({
            "schedule": {"enabled": True, "times": ["7:5", "08:00"],
                         "warning_enabled": True, "warning_interval_min": 30}})
        self.assertEqual(sch, {"enabled": True, "times": ["07:05", "08:00"],
                               "warning_enabled": True, "warning_interval_min": 30})

    def test_defaults(self):
        sch = config_loader.schedule_from({})
        self.assertFalse(sch["enabled"])
        self.assertFalse(sch["warning_enabled"])
        self.assertEqual(sch["warning_interval_min"],
                         config_loader.SCHEDULE_WARNING_INTERVAL)


class ParsePairTest(TempConfCase):
    def test_normal(self):
        self.assertEqual(config_loader.parse_pair("112.23,30.06"), (30.06, 112.23))

    def test_fullwidth_comma_and_spaces(self):
        self.assertEqual(config_loader.parse_pair("112.23，30.06"), (30.06, 112.23))
        self.assertEqual(config_loader.parse_pair(" 112.23 , 30.06 "), (30.06, 112.23))

    def test_order_sensitive(self):
        # 纬度写在前会越界（112.23 > 90），必须是「经度,纬度」
        self.assertIsNone(config_loader.parse_pair("30.06,112.23"))

    def test_invalid(self):
        for bad in ("", "公安", "112.23", "abc,def", "112.23,91",
                    "181,30.06", None):
            self.assertIsNone(config_loader.parse_pair(bad), bad)


class OverlaySavedTest(TempConfCase):
    def test_deep_merge_keeps_unmentioned_fields(self):
        self.write_conf({"push": {"channels": [
            {"id": "c1", "type": "wecom", "name": "企微A", "enabled": True,
             "webhook": "http://x", "msg_type": "text"}]}})
        out = config_loader.overlay_saved({"push": {"channels": [
            {"id": "c1", "type": "wecom", "name": "企微A", "enabled": True,
             "webhook": "http://x", "msg_type": "text",
             "note": "新加字段"}]}})
        self.assertEqual(out["push"]["channels"][0]["msg_type"], "text")
        self.assertEqual(out["push"]["channels"][0]["note"], "新加字段")

    def test_scalar_override(self):
        self.write_conf({"location": "112.23,30.06"})
        out = config_loader.overlay_saved({"location": "1,2"})
        self.assertEqual(out["location"], "1,2")


class MergeSavedLocationTest(TempConfCase):
    def test_same_city_keeps_saved_fields(self):
        raw = {"location": "112.23,30.06", "location_lat": 30.06,
               "location_lon": 112.23, "location_source": "manual"}
        user = config_loader.merge_saved_location({"location": "112.23,30.06"}, raw)
        self.assertEqual(user["location_lat"], 30.06)
        self.assertEqual(user["location_source"], "manual")

    def test_changed_coords_get_parsed(self):
        raw = {"location": "1,2"}
        user = config_loader.merge_saved_location({"location": "112.23,30.06"}, raw)
        self.assertEqual(user["location_lat"], 30.06)
        self.assertEqual(user["location_lon"], 112.23)
        self.assertEqual(user["location_source"], "manual")

    def test_changed_name_clears_coords(self):
        user = config_loader.merge_saved_location({"location": "公安"}, {"location": "1,2"})
        self.assertIsNone(user["location_lat"])
        self.assertIsNone(user["location_lon"])


class MigrateSectionsTest(TempConfCase):
    def test_missing_sections_filled(self):
        raw = {}
        self.assertTrue(config_loader.migrate_sections(raw))
        self.assertIn("auth", raw)
        self.assertIn("schedule", raw)
        self.assertIn("warning_enabled", raw["schedule"])
        self.assertIn("warning_interval_min", raw["schedule"])

    def test_old_schedule_gets_warning_fields(self):
        raw = {"auth": {"enabled": True, "secret": "a" * 40},
               "schedule": {"enabled": True, "times": ["08:00"]}}
        self.assertTrue(config_loader.migrate_sections(raw))
        # 已有字段不动，缺的补上
        self.assertEqual(raw["auth"]["secret"], "a" * 40)
        self.assertEqual(raw["schedule"]["times"], ["08:00"])
        self.assertFalse(raw["schedule"]["warning_enabled"])
        self.assertEqual(raw["schedule"]["warning_interval_min"], 10)

    def test_complete_config_untouched(self):
        raw = {"auth": {"enabled": False, "password_hash": "h", "salt": "s",
                        "secret": "x" * 40, "session_hours": 24},
               "schedule": {"enabled": True, "times": ["07:30"],
                            "warning_enabled": True, "warning_interval_min": 20},
               "push": {"channels": [
                   {"id": "a1", "type": "wecom", "name": "企微", "enabled": False,
                    "roles": ["prod"], "webhook": "", "msg_type": "text"}]}}
        self.assertFalse(config_loader.migrate_sections(raw))

    def test_old_push_dict_migrates_to_list(self):
        # 老固定两键结构 → channels 列表；只迁「用户配置过」的通道
        raw = {"push": {"wecom": {"enabled": True, "webhook": "http://x",
                                  "msg_type": "text"},
                        "magicpush": {"enabled": False, "base_url": "",
                                      "token": ""}}}
        self.assertTrue(config_loader.migrate_sections(raw))
        chans = raw["push"]["channels"]
        self.assertEqual([c["type"] for c in chans], ["wecom"])
        self.assertEqual(chans[0]["webhook"], "http://x")
        self.assertTrue(chans[0]["enabled"])
        self.assertTrue(chans[0]["id"])

    def test_magicpush_legacy_default_base_url_not_migrated(self):
        # 老版本把 base_url 默认值硬编码为 NAS 地址，只有默认值、没填 token
        # 不能算「用户配置过」，否则会凭空多出一个未配置的通道
        raw = {"push": {"magicpush": {"enabled": False,
                                      "base_url": "http://192.168.1.100:8083",
                                      "token": ""}}}
        config_loader.migrate_sections(raw)
        self.assertEqual(raw["push"]["channels"], [])

    def test_magicpush_with_token_migrated(self):
        raw = {"push": {"magicpush": {"enabled": False,
                                      "base_url": "http://x", "token": "t"}}}
        config_loader.migrate_sections(raw)
        chans = raw["push"]["channels"]
        self.assertEqual([c["type"] for c in chans], ["magicpush"])
        self.assertEqual(chans[0]["token"], "t")

    def test_dirty_channels_list_cleaned(self):
        raw = {"push": {"channels": [
            {"type": "nope"},                                  # 未知类型 → 丢弃
            {"type": "wecom"},                                 # 缺 id → 补
            "junk",                                            # 非字典 → 丢弃
            {"id": "ok1", "type": "magicpush", "token": "t"},  # 合法 → 保留
        ]}}
        self.assertTrue(config_loader.migrate_sections(raw))
        chans = raw["push"]["channels"]
        self.assertEqual(len(chans), 2)
        self.assertTrue(chans[0]["id"])
        self.assertEqual(chans[0]["webhook"], "")
        self.assertEqual(chans[1]["id"], "ok1")

    def test_channel_junk_keys_dropped_fields_kept(self):
        # 白名单外（脚本/手工塞进来的）键一律清掉，业务字段原样保留
        raw = {"push": {"channels": [
            {"id": "k1", "type": "wecom", "enabled": True,
             "webhook": "http://x", "msg_type": "markdown",
             "hack": "junk", "password_hash": "nope"},
        ]}}
        config_loader.migrate_sections(raw)
        ch = raw["push"]["channels"][0]
        self.assertEqual(ch["webhook"], "http://x")
        self.assertEqual(ch["msg_type"], "markdown")
        self.assertTrue(ch["enabled"])
        self.assertNotIn("hack", ch)
        self.assertNotIn("password_hash", ch)

    def test_channel_rename_preserved(self):
        raw = {"push": {"channels": [
            {"id": "k1", "type": "magicpush", "name": "家里群",
             "enabled": True, "base_url": "http://mp", "token": "t"},
        ]}}
        config_loader.migrate_sections(raw)
        self.assertEqual(raw["push"]["channels"][0]["name"], "家里群")


class MigrateMessageTest(TempConfCase):
    def test_legacy_defaults_upgraded(self):
        v1 = config_loader.LEGACY_MESSAGE_DEFAULTS[0]["broadcast_title"]
        v2_body = config_loader.LEGACY_MESSAGE_DEFAULTS[1]["alert_body"]
        raw = {"message": {"broadcast_title": v1, "alert_body": v2_body}}
        self.assertTrue(config_loader.migrate_message(raw))
        self.assertEqual(raw["message"]["broadcast_title"],
                         config_loader.MESSAGE_DEFAULTS["broadcast_title"])
        self.assertEqual(raw["message"]["alert_body"],
                         config_loader.MESSAGE_DEFAULTS["alert_body"])

    def test_custom_template_untouched(self):
        raw = {"message": {"broadcast_title": "我的模板 {city}"}}
        self.assertFalse(config_loader.migrate_message(raw))
        self.assertEqual(raw["message"]["broadcast_title"], "我的模板 {city}")


class PublicConfigTest(TempConfCase):
    def test_auth_material_stripped(self):
        raw = {"auth": {"enabled": True, "password_hash": "h", "salt": "s",
                        "secret": "x" * 40, "session_hours": 168},
               "location": "112.23,30.06"}
        pub = config_loader.public_config(raw)
        self.assertEqual(set(pub["auth"].keys()),
                         {"enabled", "session_hours", "has_password"})
        self.assertTrue(pub["auth"]["has_password"])
        self.assertEqual(pub["location"], "112.23,30.06")
        # 原始配置不被修改
        self.assertEqual(raw["auth"]["secret"], "x" * 40)

    def test_no_password(self):
        pub = config_loader.public_config({"auth": {"enabled": True,
                                                    "password_hash": ""}})
        self.assertFalse(pub["auth"]["has_password"])


class PortHostTest(TempConfCase):
    def test_port(self):
        self.assertEqual(config_loader.port_from({"server": {"port": 9000}}), 9000)
        self.assertEqual(config_loader.port_from({"server": {"port": 80}}),
                         config_loader.DEFAULT_PORT)
        self.assertEqual(config_loader.port_from({"server": {}}),
                         config_loader.DEFAULT_PORT)
        self.assertEqual(config_loader.port_from({"server": {"port": "abc"}}),
                         config_loader.DEFAULT_PORT)

    def test_host(self):
        self.assertEqual(config_loader.host_from(
            {"server": {"host": "192.168.1.100"}}), "192.168.1.100")
        self.assertEqual(config_loader.host_from({"server": {"host": ""}}),
                         config_loader.DEFAULT_HOST)
        self.assertEqual(config_loader.host_from({}), config_loader.DEFAULT_HOST)


class LoadSaveRawTest(TempConfCase):
    def test_missing_file_returns_template(self):
        raw = config_loader.load_raw()
        self.assertEqual(raw["server"]["port"], config_loader.DEFAULT_PORT)
        self.assertIn("auth", raw)
        self.assertIn("schedule", raw)

    def test_roundtrip_and_permissions(self):
        self.write_conf({"location": "1,2"})
        self.assertEqual(self.read_conf(), {"location": "1,2"})
        mode = os.stat(self.conf_file).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_load_raw_migrates_and_persists(self):
        v1 = config_loader.LEGACY_MESSAGE_DEFAULTS[0]["broadcast_title"]
        self.write_conf({"message": {"broadcast_title": v1}})
        raw = config_loader.load_raw()
        self.assertEqual(raw["message"]["broadcast_title"],
                         config_loader.MESSAGE_DEFAULTS["broadcast_title"])
        # 一次性升级已落盘：直接读文件就是新默认
        self.assertEqual(
            self.read_conf()["message"]["broadcast_title"],
            config_loader.MESSAGE_DEFAULTS["broadcast_title"])


class ChannelAddRemoveTest(TempConfCase):
    def test_template_has_no_channels(self):
        self.assertEqual(config_loader.TEMPLATE["push"]["channels"], [])
        self.assertEqual(config_loader.DEFAULTS["push"]["channels"], [])

    def test_load_raw_persists_migration(self):
        # 老结构（固定两键）读一次即完成迁移并落盘
        self.write_conf({"push": {"wecom": {"enabled": True,
                                            "webhook": "http://x"}}})
        raw = config_loader.load_raw()
        chans = raw["push"]["channels"]
        self.assertEqual([c["type"] for c in chans], ["wecom"])
        self.assertTrue(
            [c["type"] for c in self.read_conf()["push"]["channels"]] == ["wecom"])

    def test_add_then_remove_roundtrip(self):
        raw = config_loader.load_raw()
        ch = config_loader.add_channel(raw, "wecom")
        self.assertEqual(len(raw["push"]["channels"]), 1)
        self.assertTrue(ch["enabled"])
        self.assertEqual(ch["type"], "wecom")
        config_loader.save_raw(raw)

        raw = config_loader.load_raw()
        removed = config_loader.remove_channel(raw, ch["id"])
        self.assertEqual(removed["id"], ch["id"])
        self.assertEqual(raw["push"]["channels"], [])
        config_loader.save_raw(raw)
        self.assertEqual(config_loader.load_raw()["push"]["channels"], [])

    def test_add_same_type_multiple_with_unique_names(self):
        raw = config_loader.load_raw()
        a = config_loader.add_channel(raw, "wecom")
        b = config_loader.add_channel(raw, "wecom")
        c = config_loader.add_channel(raw, "wecom")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(b["id"], c["id"])
        names = [x["name"] for x in (a, b, c)]
        self.assertEqual(len(set(names)), 3)          # 名称互不相同
        self.assertEqual(names[0], "企业微信机器人")
        d = config_loader.add_channel(raw, "magicpush")
        self.assertEqual(d["name"], "MagicPush 魔法推送")

    def test_add_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            config_loader.add_channel({}, "nope")

    def test_remove_missing_id_returns_none(self):
        raw = config_loader.load_raw()
        self.assertIsNone(config_loader.remove_channel(raw, "ghost"))

    def test_find_channel(self):
        raw = config_loader.load_raw()
        ch = config_loader.add_channel(raw, "wecom")
        self.assertEqual(config_loader.find_channel([ch], ch["id"]), ch)
        self.assertIsNone(config_loader.find_channel([ch], "ghost"))

    def test_validate_requires_enabled_channel_with_credentials(self):
        cfg = config_loader.merged(config_loader.load_raw())
        errors = config_loader.validate(cfg, True)
        self.assertTrue(any("至少启用一个正式推送通道" in e for e in errors))

        raw = config_loader.load_raw()
        ch = config_loader.add_channel(raw, "wecom")
        config_loader.save_raw(raw)
        cfg = config_loader.merged(config_loader.load_raw())
        cfg["qweather"] = {"api_host": "a", "project_id": "p", "credential_id": "c"}
        cfg["location"] = "1.0,2.0"
        errors = config_loader.validate(cfg, True)
        self.assertTrue(any("webhook 未填写" in e for e in errors))
        self.assertTrue(any(ch["name"] in e for e in errors))

        ch["webhook"] = "http://x"
        config_loader.save_raw(raw)                            # 带修改落盘
        cfg = config_loader.merged(config_loader.load_raw())
        errors = config_loader.validate(cfg, True)
        self.assertFalse(any("webhook" in e for e in errors))

    def test_channel_max_limit(self):
        raw = config_loader.load_raw()
        for _ in range(config_loader.CHANNEL_MAX):
            config_loader.add_channel(raw, "wecom")
        with self.assertRaises(ValueError):
            config_loader.add_channel(raw, "wecom")


class ChannelRoleTest(TempConfCase):
    """通道用途：正式（收播报/预警）与测试（只收测试推送），可同时具备。"""

    def test_default_roles_is_prod(self):
        raw = config_loader.load_raw()
        ch = config_loader.add_channel(raw, "wecom")
        self.assertEqual(ch["roles"], ["prod"])

    def test_roles_can_be_both(self):
        raw = config_loader.load_raw()
        ch = config_loader.add_channel(raw, "wecom", ["prod", "test"])
        self.assertEqual(ch["roles"], ["prod", "test"])
        self.assertTrue(config_loader.has_role(ch, "prod"))
        self.assertTrue(config_loader.has_role(ch, "test"))
        self.assertEqual(config_loader.roles_label(ch), "正式 + 测试")

    def test_roles_order_and_dedup(self):
        self.assertEqual(config_loader.normalize_roles(["test", "prod", "test"]),
                         ["prod", "test"])
        self.assertEqual(config_loader.normalize_roles(["test"]), ["test"])

    def test_empty_roles_falls_back_to_prod(self):
        # 前端至少保留一个，但接口被脚本直接调用时也要有个兜底
        self.assertEqual(config_loader.normalize_roles([]), ["prod"])

    def test_multiple_same_type_named_without_role_suffix(self):
        raw = config_loader.load_raw()
        a = config_loader.add_channel(raw, "wecom")
        b = config_loader.add_channel(raw, "wecom")
        self.assertEqual(a["name"], "企业微信机器人")
        self.assertEqual(b["name"], "企业微信机器人 2")   # 用途可随时改，名称不绑用途

    def test_unknown_roles_fall_back_to_prod(self):
        raw = config_loader.load_raw()
        self.assertEqual(
            config_loader.add_channel(raw, "wecom", "hack")["roles"], ["prod"])
        self.assertEqual(config_loader.normalize_roles(None), ["prod"])
        self.assertEqual(config_loader.normalize_roles("TEST"), ["test"])

    def test_legacy_single_role_still_readable(self):
        self.assertTrue(config_loader.has_role({"role": "test"}, "test"))
        self.assertFalse(config_loader.has_role({"role": "test"}, "prod"))
        self.assertEqual(config_loader.channel_roles({}), ["prod"])

    def test_migrate_adds_roles_to_legacy_channel(self):
        raw = {"push": {"channels": [
            {"id": "a1", "type": "wecom", "name": "企微", "enabled": True,
             "webhook": "http://x", "msg_type": "text"}]}}
        self.assertTrue(config_loader.migrate_sections(raw))
        ch = raw["push"]["channels"][0]
        self.assertEqual(ch["roles"], ["prod"])
        self.assertNotIn("role", ch)                 # 老字段并入 roles 后清掉

    def test_migrate_keeps_test_role(self):
        raw = {"push": {"channels": [
            {"id": "t1", "type": "wecom", "name": "测试群", "enabled": True,
             "role": "test", "webhook": "http://x", "msg_type": "text"}]}}
        config_loader.migrate_sections(raw)
        self.assertEqual(raw["push"]["channels"][0]["roles"], ["test"])

    def test_migrate_keeps_both_roles(self):
        raw = {"push": {"channels": [
            {"id": "b1", "type": "wecom", "name": "两用", "enabled": True,
             "roles": ["test", "prod"], "webhook": "http://x", "msg_type": "text"}]}}
        config_loader.migrate_sections(raw)
        self.assertEqual(raw["push"]["channels"][0]["roles"], ["prod", "test"])

    def test_migrate_normalizes_bad_role(self):
        raw = {"push": {"channels": [
            {"id": "a1", "type": "wecom", "name": "x", "enabled": True,
             "role": "whatever", "webhook": "", "msg_type": "text"}]}}
        self.assertTrue(config_loader.migrate_sections(raw))
        self.assertEqual(raw["push"]["channels"][0]["roles"], ["prod"])

    def test_validate_ignores_test_channel_credentials(self):
        raw = {"push": {"channels": [
            {"id": "t1", "type": "wecom", "name": "测试群", "enabled": True,
             "role": "test", "webhook": "", "msg_type": "text"}]},
               "qweather": {"api_host": "a", "project_id": "p",
                            "credential_id": "c"},
               "location": "112.23,30.06"}
        cfg = config_loader.merged(raw)
        errors = config_loader.validate(cfg, True)
        # 测试通道不参与正式播报：只提示没有正式通道，不因它缺凭据报错
        self.assertTrue(any("正式推送通道" in e for e in errors))
        self.assertFalse(any("webhook" in e for e in errors))

    def test_validate_counts_prod_channel(self):
        raw = {"push": {"channels": [
            {"id": "p1", "type": "wecom", "name": "正式群", "enabled": True,
             "role": "prod", "webhook": "http://x", "msg_type": "text"}]},
               "qweather": {"api_host": "a", "project_id": "p",
                            "credential_id": "c"},
               "location": "112.23,30.06"}
        errors = config_loader.validate(config_loader.merged(raw), True)
        self.assertEqual(errors, [])

    def test_validate_both_roles_channel_counts_as_prod(self):
        # 两用通道只要缺 webhook，仍算正式通道的阻塞项
        raw = {"push": {"channels": [
            {"id": "b1", "type": "wecom", "name": "两用", "enabled": True,
             "roles": ["prod", "test"], "webhook": "", "msg_type": "text"}]},
               "qweather": {"api_host": "a", "project_id": "p",
                            "credential_id": "c"},
               "location": "112.23,30.06"}
        errors = config_loader.validate(config_loader.merged(raw), True)
        self.assertTrue(any("「两用」已启用，但 webhook 未填写" in e for e in errors))


class MergeChannelsTest(TempConfCase):
    """按 id 合并通道列表：修复「加不了第二个通道」的整表覆盖问题。"""

    def test_keeps_channel_added_after_page_snapshot(self):
        saved = [{"id": "a", "type": "wecom", "name": "正式", "enabled": True,
                  "role": "prod", "webhook": "http://a"},
                 {"id": "b", "type": "wecom", "name": "新加的", "enabled": True,
                  "role": "prod", "webhook": "http://b"}]
        submitted = [{"id": "a", "type": "wecom", "name": "正式", "enabled": True,
                      "role": "prod", "webhook": "http://a2"}]
        out = config_loader.merge_channels(submitted, saved)
        self.assertEqual([c["id"] for c in out], ["a", "b"])
        self.assertEqual(out[0]["webhook"], "http://a2")    # 页面修改生效
        self.assertEqual(out[1]["webhook"], "http://b")     # 新通道没被冲掉

    def test_new_id_appended(self):
        out = config_loader.merge_channels([{"id": "n", "type": "wecom"}], [])
        self.assertEqual([c["id"] for c in out], ["n"])

    def test_empty_submitted_keeps_all(self):
        saved = [{"id": "a", "type": "wecom"}, {"id": "b", "type": "wecom"}]
        self.assertEqual(config_loader.merge_channels([], saved), saved)

    def test_overlay_saved_merges_channels_on_disk(self):
        self.write_conf({"push": {"channels": [
            {"id": "a", "type": "wecom", "name": "正式", "enabled": True,
             "role": "prod", "webhook": "http://a", "msg_type": "text"},
            {"id": "b", "type": "wecom", "name": "测试", "enabled": True,
             "role": "test", "webhook": "http://b", "msg_type": "text"}]}})
        # 页面拿的是只有 a 的旧快照
        user = {"push": {"channels": [
            {"id": "a", "type": "wecom", "name": "正式", "enabled": True,
             "role": "prod", "webhook": "http://a-new", "msg_type": "text"}]}}
        merged_cfg = config_loader.overlay_saved(user)
        ids = [c["id"] for c in merged_cfg["push"]["channels"]]
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(merged_cfg["push"]["channels"][0]["webhook"], "http://a-new")


class ChannelRemovedTest(TempConfCase):
    """删除通道：墓碑机制防止旧快照把删掉的通道写回来。"""

    def _raw(self, *ids):
        return {"push": {"channels": [
            {"id": i, "type": "wecom", "name": f"通道{i}", "enabled": True,
             "roles": ["prod"], "webhook": f"http://{i}", "msg_type": "text"}
            for i in ids]}}

    def test_remove_records_tombstone(self):
        raw = self._raw("a", "b")
        gone = config_loader.remove_channel(raw, "b")
        self.assertEqual(gone["id"], "b")
        self.assertEqual([c["id"] for c in raw["push"]["channels"]], ["a"])
        self.assertEqual(config_loader.removed_ids(raw), ["b"])

    def test_remove_unknown_returns_none_and_no_tombstone(self):
        raw = self._raw("a")
        self.assertIsNone(config_loader.remove_channel(raw, "nope"))
        self.assertEqual(config_loader.removed_ids(raw), [])

    def test_tombstone_capped(self):
        raw = self._raw()
        for i in range(config_loader.REMOVED_MAX + 5):
            raw["push"]["channels"].append(
                {"id": f"x{i}", "type": "wecom", "enabled": True})
            config_loader.remove_channel(raw, f"x{i}")
        self.assertEqual(len(config_loader.removed_ids(raw)),
                         config_loader.REMOVED_MAX)
        self.assertNotIn("x0", config_loader.removed_ids(raw))   # 最早的被挤掉

    def test_stale_snapshot_does_not_resurrect(self):
        # 删除后页面只剩 a，但提交的快照里还带着 b（删除前的 DOM）
        saved = [{"id": "a", "type": "wecom"}]
        submitted = [{"id": "a", "type": "wecom"}, {"id": "b", "type": "wecom"}]
        out = config_loader.merge_channels(submitted, saved, ["b"])
        self.assertEqual([c["id"] for c in out], ["a"])

    def test_new_channel_still_appended_with_tombstones(self):
        # 墓碑只挡「已删除的 id」，新加的通道照旧追加
        out = config_loader.merge_channels(
            [{"id": "c", "type": "wecom"}], [{"id": "a", "type": "wecom"}], ["b"])
        self.assertEqual([c["id"] for c in out], ["c", "a"])

    def test_overlay_saved_after_remove_keeps_it_deleted(self):
        self.write_conf({"push": {
            "channels": self._raw("a", "b")["push"]["channels"]}})
        snapshot = self._raw("a", "b")["push"]["channels"]   # 页面删除前的快照
        raw = config_loader.load_raw()
        config_loader.remove_channel(raw, "b")
        config_loader.save_raw(raw)

        # 前端删除后紧跟 flushSave，提交的正是这份旧快照
        merged_cfg = config_loader.overlay_saved(
            {"push": {"channels": snapshot}})
        self.assertEqual([c["id"] for c in merged_cfg["push"]["channels"]], ["a"])


class KeyPairTest(TempConfCase):
    """设置页生成 / 替换和风 Ed25519 密钥对。"""

    def test_generate_returns_pem_pair(self):
        priv, pub = config_loader.generate_keypair()
        self.assertIn("BEGIN PRIVATE KEY", priv)
        self.assertIn("BEGIN PUBLIC KEY", pub)
        # 公钥确实由这把私钥推出
        priv2, pub2 = config_loader.public_from_private(priv)
        self.assertEqual(pub2.strip(), pub.strip())
        self.assertEqual(priv2.strip(), priv.strip())   # 规范化后往返一致

    def test_public_from_private_accepts_openssh_format(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        ssh_text = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption()).decode()
        priv, pub = config_loader.public_from_private(ssh_text)
        # ssh-keygen 私钥进来，出去的是标准 PKCS#8（和 openssl 产物一致）
        self.assertIn("BEGIN PRIVATE KEY", priv)
        self.assertIn("BEGIN PUBLIC KEY", pub)

    def test_public_from_private_rejects_bad_input(self):
        for bad in ("", "   ", "hello", "-----BEGIN PRIVATE KEY-----\nabc\n"):
            with self.assertRaises(ValueError):
                config_loader.public_from_private(bad)

    def test_public_from_private_rejects_non_ed25519(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        with self.assertRaises(ValueError) as ctx:
            config_loader.public_from_private(pem)
        self.assertIn("Ed25519", str(ctx.exception))

    def test_generated_key_signs_jwt(self):
        # 生成出的密钥对要能走真实的 JWT 签名路径（和风客户端用公钥可验签）
        import base64
        from cryptography.hazmat.primitives import serialization
        from qweather import QWeatherClient
        priv, pub = config_loader.generate_keypair()
        client = QWeatherClient("h.example.com", "proj", "kid", priv)
        h64, p64, s64 = client._make_jwt().split(".")
        pub_key = serialization.load_pem_public_key(pub.encode())
        pub_key.verify(                          # verify(签名, 被签数据)
            base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4)),
            f"{h64}.{p64}".encode())

    def test_save_keypair_writes_with_backup_and_perms(self):
        priv1, pub1 = config_loader.generate_keypair()
        self.assertEqual(config_loader.save_keypair(priv1, pub1), "")   # 第一次无备份
        priv2, pub2 = config_loader.generate_keypair()
        backup = config_loader.save_keypair(priv2, pub2)
        self.assertTrue(backup.startswith(config_loader.KEY_FILE + ".bak_"))
        with open(backup, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), priv1.strip())           # 旧私钥可找回
        self.assertEqual(oct(os.stat(config_loader.KEY_FILE).st_mode & 0o777), "0o600")
        with open(config_loader.KEY_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), priv2.strip())
        self.assertEqual(config_loader.load_public_key(), pub2.strip())

    def test_ensure_public_key_creates_missing_public(self):
        priv, pub = config_loader.generate_keypair()
        config_loader.save_keypair(priv, pub)
        os.remove(config_loader.PUB_FILE)
        note = config_loader.ensure_public_key()
        self.assertIn("自动生成公钥", note)
        self.assertEqual(config_loader.load_public_key(), pub.strip())

    def test_ensure_public_key_fixes_mismatch(self):
        priv, pub = config_loader.generate_keypair()
        other_priv, other_pub = config_loader.generate_keypair()
        config_loader.save_keypair(priv, other_pub)     # 故意配错
        note = config_loader.ensure_public_key()
        self.assertIn("重新生成", note)
        self.assertEqual(config_loader.load_public_key(), pub.strip())

    def test_ensure_public_key_skips_when_consistent(self):
        priv, pub = config_loader.generate_keypair()
        config_loader.save_keypair(priv, pub)
        self.assertEqual(config_loader.ensure_public_key(), "")

    def test_ensure_public_key_without_private(self):
        self.assertEqual(config_loader.ensure_public_key(), "")

    def test_key_info_shape(self):
        info = config_loader.key_info()
        self.assertFalse(info["exists"])
        self.assertEqual(info["public_key"], "")
        priv, pub = config_loader.generate_keypair()
        config_loader.save_keypair(priv, pub)
        info = config_loader.key_info()
        self.assertTrue(info["exists"])
        self.assertEqual(info["public_key"], pub.strip())
        self.assertRegex(info["created_at"], r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


if __name__ == "__main__":
    import unittest
    unittest.main()
