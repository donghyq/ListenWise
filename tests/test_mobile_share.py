"""「发送到手机」分享链接与移动端收听页测试。"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402


def _create_project_with_audio(client: TestClient, main) -> tuple[str, str]:
    """建一个项目 + 一条已就绪的分章音频，返回 (project_id, audio_id)。"""
    response = client.post("/api/projects", json={"name": "手机收听测试"})
    if response.status_code not in (200, 201):
        raise RuntimeError(response.text)
    project_id = response.json()["id"]
    content_id = "nc-" + project_id[:8]
    audio_id = "ao-" + project_id[:8]
    audio_path = Path(main.DATA_DIR) / f"mobile-test-{audio_id}.mp3"
    audio_path.write_bytes(b"ID3" + b"\x00" * 256)
    with main.db() as conn:
        # 测试只需要 audio_outputs 行，outline/document 外键用占位 id，这里临时关闭外键检查。
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO narrative_contents (id, outline_id, project_id, document_id, title, markdown, section_sources_json, model_json, status, review_note, created_at, updated_at) "
            "VALUES (?, '', ?, '', '手机收听测试讲稿', ?, '[]', '{}', 'confirmed', '', datetime('now'), datetime('now'))",
            (content_id, project_id, "# 手机收听测试讲稿\n\n## 第一章 测试\n\n正文。\n\n## 第二章 结尾\n\n正文。"),
        )
        conn.execute(
            "INSERT INTO audio_outputs (id, project_id, narrative_content_id, title, script, provider, voice, audio_path, duration_seconds, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', 'edge', '', ?, 61, 'ready', datetime('now'), datetime('now'))",
            (audio_id, project_id, content_id, "手机收听测试讲稿 · 第一章 测试", str(audio_path)),
        )
    return project_id, audio_id


class MobileShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import app.main as main

        cls.main = importlib.reload(main)
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="lawflow-mobile-test-"))
        cls.main.DATA_DIR = cls.temp_dir / "data"
        cls.main.PROJECTS_DIR = cls.main.DATA_DIR / "projects"
        cls.main.EXPORTS_DIR = cls.main.DATA_DIR / "exports"
        cls.main.DB_PATH = cls.main.DATA_DIR / "app.db"
        for directory in (cls.main.DATA_DIR, cls.main.PROJECTS_DIR, cls.main.EXPORTS_DIR, cls.main.DATA_DIR / "temp"):
            directory.mkdir(parents=True, exist_ok=True)
        cls.main.init_db()
        cls.client = TestClient(cls.main.app)

    def test_create_and_fetch_share(self) -> None:
        project_id, _ = _create_project_with_audio(self.client, self.main)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        self.assertIn("token", share)
        page = self.client.get(share["path"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("声息", page.text)
        data = self.client.get(share["path"] + "/data").json()
        self.assertEqual(data["title"], "手机收听测试")
        self.assertEqual(len(data["chapters"]), 1)
        chapter = data["chapters"][0]
        self.assertEqual(chapter["title"], "第一章 测试")
        self.assertTrue(chapter["chapter"])
        self.assertFalse(chapter["learned"])
        self.assertEqual(chapter["text"], "正文。")
        self.assertEqual(data["learned_count"], 0)

    def test_recreate_share_revokes_old_link(self) -> None:
        project_id, _ = _create_project_with_audio(self.client, self.main)
        first = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        second = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(self.client.get(first["path"] + "/data").status_code, 404)
        self.assertEqual(self.client.get(second["path"] + "/data").status_code, 200)

    def test_revoke_invalidates_link(self) -> None:
        project_id, _ = _create_project_with_audio(self.client, self.main)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        self.assertEqual(self.client.delete(f"/api/projects/{project_id}/mobile-share").status_code, 200)
        self.assertEqual(self.client.get(share["path"] + "/data").status_code, 404)

    def test_progress_sync_marks_learned(self) -> None:
        project_id, audio_id = _create_project_with_audio(self.client, self.main)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        response = self.client.post(share["path"] + "/progress", json={"audio_id": audio_id, "completed": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        data = self.client.get(share["path"] + "/data").json()
        self.assertTrue(data["chapters"][0]["learned"])
        self.assertEqual(data["learned_count"], 1)

    def test_invalid_page_is_readable_html_not_json(self) -> None:
        response = self.client.get("/m/not-a-real-token")
        self.assertEqual(response.status_code, 404)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("链接已失效", response.text)
        self.assertIn('name="viewport"', response.text)

    def test_audio_duration_and_progress_exclude_whole_book(self) -> None:
        project_id, _ = _create_project_with_audio(self.client, self.main)
        whole_path = Path(self.main.DATA_DIR) / f"mobile-test-whole-{project_id[:8]}.mp3"
        whole_path.write_bytes(b"ID3" + b"\x00" * 256)
        with self.main.db() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO audio_outputs (id, project_id, narrative_content_id, title, script, provider, voice, audio_path, duration_seconds, status, created_at, updated_at) "
                "VALUES (?, ?, NULL, ?, ?, 'edge', '', ?, 120, 'ready', datetime('now'), datetime('now'))",
                ("whole-" + project_id[:8], project_id, "手机收听测试讲稿", "整篇正文。", str(whole_path)),
            )
        detail = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(detail["audio_duration_seconds"], 120)
        listed = next(item for item in self.client.get("/api/projects").json() if item["id"] == project_id)
        self.assertEqual(listed["audio_duration_seconds"], 120)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        book = self.client.get(share["path"] + "/data").json()
        self.assertEqual(book["total"], 1)
        self.assertEqual(book["audio_count"], 2)
        self.assertEqual(len(book["whole_book"]), 1)
        self.assertFalse(book["whole_book"][0]["chapter"])

    def test_audio_stream(self) -> None:
        project_id, audio_id = _create_project_with_audio(self.client, self.main)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        stream = self.client.get(share["path"] + f"/audio/{audio_id}")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("audio/mpeg", stream.headers["content-type"])

    def test_download_bundle_zip(self) -> None:
        import io
        import zipfile as zf

        project_id, audio_id = _create_project_with_audio(self.client, self.main)
        share = self.client.post(f"/api/projects/{project_id}/mobile-share").json()
        response = self.client.get(share["path"] + "/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response.headers["content-type"])
        self.assertIn(".zip", response.headers["content-disposition"])
        with zf.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
        self.assertIn("01 第一章 测试.mp3", names)
        self.assertTrue(any(n.endswith("讲稿.txt") for n in names))
        # 响应完成后临时包应被清理
        self.assertFalse(any(Path(self.main.DATA_DIR / "temp").glob("mobile-bundle-*.zip")))

    def test_download_bundle_invalid_token(self) -> None:
        response = self.client.get("/m/not-a-real-token/download")
        self.assertEqual(response.status_code, 404)

    def test_app_icon_route(self) -> None:
        response = self.client.get("/m/icon-180.png")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/png", response.headers["content-type"])
        self.assertGreater(len(response.content), 1000)

    def test_section_text_extraction_helpers(self) -> None:
        markdown = "# 标题\n\n## 第一章 A\n\n**加粗**内容一。\n\n## 第二章 B\n\n正文二。"
        self.assertEqual(self.main.extract_section_text(markdown, "第一章 A"), "加粗内容一。")
        self.assertEqual(self.main.extract_section_text(markdown, "第二章 B"), "正文二。")
        self.assertEqual(self.main.extract_section_text(markdown, "不存在的章"), self.main.markdown_to_plain(markdown))

    def test_local_lan_ip_returns_string(self) -> None:
        self.assertIsInstance(self.main.local_lan_ip(), str)

    def test_middleware_blocks_remote_api_but_allows_mobile(self) -> None:
        import asyncio

        from fastapi import Response

        async def passthrough(request):
            return Response("ok")

        async def run(scope):
            request = self.main.Request(scope)
            return await self.main.restrict_non_local_requests(request, passthrough)

        api_scope = {"type": "http", "client": ("192.168.0.8", 5000), "method": "GET", "path": "/api/projects", "headers": []}
        self.assertEqual(asyncio.run(run(api_scope)).status_code, 403)
        mobile_scope = {**api_scope, "path": "/m/some-token"}
        self.assertEqual(asyncio.run(run(mobile_scope)).status_code, 200)
        loopback_scope = {**api_scope, "client": ("127.0.0.1", 5000)}
        self.assertEqual(asyncio.run(run(loopback_scope)).status_code, 200)


if __name__ == "__main__":
    unittest.main()
