from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.models import ManagedMod
from src.nexus import build_manual_download_url
from src.storage import deserialize_mod, serialize_mod
from src.writers import write_manifest_update_keys


class UpdateKeysPersistenceTests(unittest.TestCase):
    """验证 UpdateKeys 的写回与持久化行为。"""

    def test_serialize_mod_persists_update_keys(self) -> None:
        """state.json 序列化应保留 UpdateKeys，方便重启后直接恢复显示。"""
        record = ManagedMod(source_path=Path("/mods/demo"), update_keys=["nexus:1", "nexus:2"])
        payload = serialize_mod(record)

        self.assertEqual(payload["update_keys"], ["nexus:1", "nexus:2"])

    def test_deserialize_mod_roundtrip_update_keys(self) -> None:
        """旧状态文件里的 UpdateKeys 仍可被兼容读取。"""
        payload = {
            "source_path": "/mods/demo",
            "update_keys": ["nexus:1", "nexus:2"],
        }

        record = deserialize_mod(payload)

        self.assertEqual(record.update_keys, ["nexus:1", "nexus:2"])

    def test_manual_download_url_is_built_and_backfilled(self) -> None:
        """Nexus 手动下载页应可由已知 ID 生成并从旧状态回填。"""
        expected = "https://www.nexusmods.com/stardewvalley/mods/30319?tab=files&file_id=143962"

        self.assertEqual(build_manual_download_url(30319, 143962), expected)

        record = ManagedMod(source_path=Path("/mods/demo"), nexus_mod_id=30319, nexus_file_id=143962)
        payload = serialize_mod(record)
        self.assertEqual(payload["nexus_manual_download_url"], expected)

        restored = deserialize_mod({"source_path": "/mods/demo", "nexus_mod_id": 30319, "nexus_file_id": 143962})
        self.assertEqual(restored.nexus_manual_download_url, expected)

    def test_write_manifest_update_keys_overwrites_and_clears(self) -> None:
        """manifest 写回应覆盖旧值，空列表应删除字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({"Name": "Demo", "UpdateKeys": ["nexus:1"]}, ensure_ascii=False), encoding="utf-8")

            write_manifest_update_keys(manifest_path, ["nexus:2", "nexus:3"], expected_root=root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["UpdateKeys"], ["nexus:2", "nexus:3"])

            write_manifest_update_keys(manifest_path, [], expected_root=root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("UpdateKeys", payload)

    def test_write_manifest_update_keys_creates_missing_field(self) -> None:
        """manifest 没有 UpdateKeys 时应自动创建。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({"Name": "Demo"}, ensure_ascii=False), encoding="utf-8")

            write_manifest_update_keys(manifest_path, ["nexus:99"], expected_root=root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["UpdateKeys"], ["nexus:99"])

    def test_write_manifest_update_keys_rejects_outside_root(self) -> None:
        """manifest 写回应拒绝越界路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mods"
            root.mkdir()
            manifest_path = Path(tmp) / "other" / "manifest.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps({"Name": "Demo"}, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(ValueError):
                write_manifest_update_keys(manifest_path, ["nexus:1"], expected_root=root)


if __name__ == "__main__":
    unittest.main()
