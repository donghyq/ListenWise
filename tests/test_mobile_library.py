"""手机书架聚合：分享创建、书目列表、单书数据与音频流、吊销与仅本机模式。"""
import sqlite3
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests import test_api as fixtures


class MobileLibraryTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def add_ready_audio(self, project_id: str, duration: int):
        audio_root = self.main.DATA_DIR / "audio"
        audio_root.mkdir(parents=True, exist_ok=True)
        path = audio_root / f"library-{project_id}-{duration}.mp3"
        path.write_bytes(b"not-a-real-audio")
        with sqlite3.connect(self.main.DB_PATH) as conn:
            conn.execute(
                """INSERT INTO audio_outputs
                (id, project_id, narrative_content_id, title, script, provider, audio_path, duration_seconds, status, created_at, updated_at)
                VALUES (?, ?, NULL, '书架音频', '脚本', 'test', ?, ?, 'ready', '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00')""",
                (f"library-audio-{project_id}-{duration}", project_id, str(path), duration),
            )
        return path

    def make_book(self, name: str, duration: int):
        project = self.client.post("/api/projects", json={"name": name, "verification_mode": "source_only"}).json()
        path = self.add_ready_audio(project["id"], duration)
        return project, path

    def test_library_share_lifecycle_and_book_flow(self):
        first, first_path = self.make_book("书架书一", 60)
        second, _ = self.make_book("书架书二", 30)

        created = self.client.post("/api/mobile-library/share")
        self.assertEqual(created.status_code, 200, created.text)
        data = created.json()
        self.assertTrue(data["active"])
        self.assertTrue(data["path"].startswith("/m/library/"))
        token = data["token"]
        self.assertGreaterEqual(data["book_count"], 2)  # fixture 可能附带示例书

        listing = self.client.get(f"/m/library/{token}/data")
        self.assertEqual(listing.status_code, 200)
        books = listing.json()["books"]
        names = {book["name"] for book in books}
        self.assertIn("书架书一", names)
        self.assertIn("书架书二", names)
        totals = {book["name"]: book["total_seconds"] for book in books}
        self.assertEqual(totals["书架书一"], 60)

        page = self.client.get(f"/m/library/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("SHENGXI", page.text)

        book_page = self.client.get(f"/m/library/{token}/book/{first['id']}")
        self.assertEqual(book_page.status_code, 200)
        book_data = self.client.get(f"/m/library/{token}/book/{first['id']}/data")
        self.assertEqual(book_data.status_code, 200)
        self.assertEqual(book_data.json()["title"], "书架书一")

        audio_id = f"library-audio-{first['id']}-60"
        stream = self.client.get(f"/m/library/{token}/book/{first['id']}/audio/{audio_id}")
        self.assertEqual(stream.status_code, 200)
        download = self.client.get(f"/m/library/{token}/book/{first['id']}/audio/{audio_id}?download=1")
        self.assertEqual(download.status_code, 200)

        # 重新生成会吊销旧链接
        renewed = self.client.post("/api/mobile-library/share").json()
        self.assertNotEqual(renewed["token"], token)
        self.assertEqual(self.client.get(f"/m/library/{token}/data").status_code, 404)

        # 吊销后全部失效
        self.client.delete("/api/mobile-library/share")
        self.assertEqual(self.client.get(f"/m/library/{renewed['token']}/data").status_code, 404)
        status = self.client.get("/api/mobile-library/share").json()
        self.assertFalse(status["active"])

    def test_project_share_library_hint_counts_other_books(self):
        # 其他用例可能已创建书架分享；先吊销，验证「未生成 → 404」分支。
        self.client.delete("/api/mobile-library/share")
        first, _ = self.make_book("提示书一", 20)
        project_share = self.client.post(f"/api/projects/{first['id']}/mobile-share")
        self.assertEqual(project_share.status_code, 200)
        token = project_share.json()["token"]

        # 没有书架分享时 404，前端静默隐藏
        self.assertEqual(self.client.get(f"/m/{token}/library-hint").status_code, 404)

        total = self.client.post("/api/mobile-library/share").json()["book_count"]
        hint = self.client.get(f"/m/{token}/library-hint")
        self.assertEqual(hint.status_code, 200)
        # 只统计其他书：总数里排除当前这一本（同类中其他用例创建的书也会计入总数）。
        self.assertEqual(hint.json()["book_count"], total - 1)

        second, _ = self.make_book("提示书二", 25)
        hint = self.client.get(f"/m/{token}/library-hint")
        self.assertEqual(hint.json()["book_count"], total)  # 加一本后，其他书数与之前总数一致
        self.assertTrue(hint.json()["library_url"].startswith("/m/library/"))
        self.assertTrue(hint.json()["library_url"].startswith("/m/library/"))

    def test_library_share_disabled_for_external_client(self):
        project, _ = self.make_book("外网禁用书", 15)
        self.client.post("/api/mobile-library/share")
        external_client = TestClient(self.main.app, client=("192.0.2.55", 12345))
        # 默认开启：外部客户端可访问
        self.assertEqual(self.client.get("/m/library/invalid/data").status_code, 404)
        settings = self.client.get("/api/settings/provider").json()
        settings["lan_share_enabled"] = False
        response = self.client.put("/api/settings/provider", json=settings)
        self.assertEqual(response.status_code, 200)
        try:
            share = self.client.get("/api/mobile-library/share").json()
            blocked = external_client.get(f"/m/library/{share['token']}/data")
            self.assertEqual(blocked.status_code, 404)
            allowed = self.client.get(f"/m/library/{share['token']}/data")
            self.assertEqual(allowed.status_code, 200)
        finally:
            settings["lan_share_enabled"] = True
            self.client.put("/api/settings/provider", json=settings)


if __name__ == "__main__":
    unittest.main()
