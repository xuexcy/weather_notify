"""测试公共设施：把 conf 指到临时目录，任何测试都不会读写真实的 conf/config.json。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
for _p in (SRC_DIR, BASE_DIR):          # 代码在 src/ 下，测试从两层都能导入
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config_loader


class TempConfCase(unittest.TestCase):
    """CONF_DIR / CONF_FILE / KEY_FILE / PUB_FILE 全部指进临时目录。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conf = os.path.join(tmp.name, "conf")
        os.makedirs(conf)
        self.tmp_dir = tmp.name
        self.conf_dir = conf
        for attr in ("CONF_DIR", "CONF_FILE", "KEY_FILE", "PUB_FILE"):
            old = getattr(config_loader, attr)
            if attr == "CONF_DIR":
                new = conf
            else:
                name = os.path.basename(str(old)) or attr.lower()
                new = os.path.join(conf, name)
            p = mock.patch.object(config_loader, attr, new)
            p.start()
            self.addCleanup(p.stop)
        self.conf_file = config_loader.CONF_FILE

    def write_conf(self, raw: dict) -> None:
        config_loader.save_raw(raw)

    def read_conf(self) -> dict:
        with open(self.conf_file, encoding="utf-8") as f:
            import json
            return json.load(f)
