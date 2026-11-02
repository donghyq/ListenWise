from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from tests import test_api as fixtures


class FolderWatchImportTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def test_watch_scan_imports_once_and_records_failures(self):
        watch_dir = Path(self.temp_dir) / "watch-inbox"
        watch_dir.mkdir()
        self.assertEqual(self.main.scan_watch_directory(), {"imported": [], "failed": []})
        (watch_dir / "第一份材料.md").write_text("# 第一份材料\n\n这是监听导入的 Markdown 材料，内容足够形成素材块。", encoding="utf-8")
        (watch_dir / "第二份材料.txt").write_text("这是监听导入的纯文本材料。它会和 Markdown 一起进入收件箱。", encoding="utf-8")

        saved = self.client.put("/api/settings/export", json={
            "output_directory": str(Path(self.temp_dir) / "exports"),
            "watch_directory": str(watch_dir),
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["watch_directory"], str(watch_dir))
        settings = self.client.get("/api/settings/export").json()
        self.assertEqual(settings["watch_directory"], str(watch_dir))

        first_scan = self.client.post("/api/settings/watch/run")
        self.assertEqual(first_scan.status_code, 200)
        imported = first_scan.json()["imported"]
        self.assertEqual(len(imported), 2)
        self.assertEqual({item["name"] for item in imported}, {"第一份材料.md", "第二份材料.txt"})
        self.assertEqual(len({item["project_id"] for item in imported}), 1)

        inbox_id = imported[0]["project_id"]
        detail = self.client.get(f"/api/projects/{inbox_id}").json()
        inbox = detail["project"]
        self.assertEqual(inbox["name"], "收件箱")
        self.assertEqual(inbox["scenario"], "topic_learning")
        self.assertTrue(inbox["audio_enabled"])
        self.assertEqual(inbox["web_research_mode"], "off")
        self.assertEqual(len(detail["documents"]), 2)

        with sqlite3.connect(self.main.DB_PATH) as conn:
            statuses = dict(conn.execute("SELECT path, status FROM watch_imports").fetchall())
        self.assertEqual(set(statuses.values()), {"imported"})

        repeat_scan = self.client.post("/api/settings/watch/run").json()
        self.assertEqual(repeat_scan["imported"], [])
        self.assertEqual(repeat_scan["failed"], [])
        detail = self.client.get(f"/api/projects/{inbox_id}").json()
        self.assertEqual(len(detail["documents"]), 2)

        broken = watch_dir / "损坏文件.docx"
        broken.write_bytes(b"not-a-real-docx")
        failure_scan = self.client.post("/api/settings/watch/run").json()
        self.assertEqual(len(failure_scan["failed"]), 1)
        self.assertEqual(failure_scan["failed"][0]["path"], str(broken.resolve()))
        self.assertTrue(failure_scan["failed"][0]["error"])
        detail = self.client.get(f"/api/projects/{inbox_id}").json()
        self.assertEqual(len(detail["documents"]), 2)

        with sqlite3.connect(self.main.DB_PATH) as conn:
            failed_status = conn.execute("SELECT status FROM watch_imports WHERE path = ?", (str(broken.resolve()),)).fetchone()[0]
        self.assertEqual(failed_status, "failed")

        final_scan = self.client.post("/api/settings/watch/run").json()
        self.assertEqual(final_scan["imported"], [])
        self.assertEqual(final_scan["failed"], [])
