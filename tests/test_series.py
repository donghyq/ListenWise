from __future__ import annotations

import sqlite3
import unittest
from tests import test_api as fixtures


class ProjectSeriesTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def make_confirmed_content(self, name: str):
        project = self.client.post("/api/projects", json={"name": name, "scenario": "topic_learning"}).json()
        source = self.client.post(f"/api/projects/{project['id']}/text-sources", json={
            "title": "系列素材",
            "content": "这是一份用于系列聚合测试的材料。它包含一个明确的章节和两小节内容。",
        }).json()
        document = self.client.get(f"/api/documents/{source['id']}").json()
        block_ids = [block["id"] for block in document["blocks"] if block["kind"] == "paragraph"]
        markdown = "# 系列讲稿\n\n## 第一节\n\n第一小节内容。\n\n## 第二节\n\n第二小节内容。"
        content = self.client.post(f"/api/projects/{project['id']}/skill-host-contents", json={
            "document_id": source["id"],
            "title": "系列讲稿",
            "markdown": markdown,
            "source_block_ids": block_ids,
        }).json()
        confirmed = self.client.put(f"/api/narrative-contents/{content['id']}", json={"markdown": markdown, "status": "confirmed"}).json()
        return project, confirmed

    def add_ready_audio(self, project_id: str, duration: int):
        audio_root = self.main.DATA_DIR / "audio"
        audio_root.mkdir(parents=True, exist_ok=True)
        path = audio_root / f"{project_id}-{duration}.mp3"
        path.write_bytes(b"not-a-real-audio")
        with sqlite3.connect(self.main.DB_PATH) as conn:
            conn.execute(
                """INSERT INTO audio_outputs
                (id, project_id, narrative_content_id, title, script, provider, audio_path, duration_seconds, status, created_at, updated_at)
                VALUES (?, ?, NULL, ?, ?, 'test', ?, ?, 'ready', '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00')""",
                (f"audio-{project_id}-{duration}", project_id, "系列音频", "脚本", str(path), duration),
            )

    def test_series_set_filter_and_aggregation(self):
        first, content = self.make_confirmed_content("系列第一期")
        second = self.client.post("/api/projects", json={"name": "系列第二期"}).json()
        third = self.client.post("/api/projects", json={"name": "另一系列"}).json()

        set_first = self.client.put(f"/api/projects/{first['id']}/series", json={"series": "合规月刊"})
        self.assertEqual(set_first.status_code, 200)
        self.assertEqual(set_first.json()["series"], "合规月刊")
        self.client.put(f"/api/projects/{second['id']}/series", json={"series": "合规月刊"})
        self.client.put(f"/api/projects/{third['id']}/series", json={"series": "AI 观察"})
        progress = self.client.put(f"/api/narrative-contents/{content['id']}/progress", json={"section_id": "section-1", "completed": True})
        self.assertEqual(progress.status_code, 200)
        self.add_ready_audio(first["id"], 60)
        self.add_ready_audio(second["id"], 30)

        projects = self.client.get("/api/projects").json()
        series_values = {item["name"]: item["series"] for item in projects}
        self.assertEqual(series_values[first["name"]], "合规月刊")
        self.assertEqual(series_values[second["name"]], "合规月刊")
        self.assertEqual(series_values[third["name"]], "AI 观察")

        filtered = self.client.get("/api/projects", params={"series": "合规月刊"}).json()
        self.assertEqual({item["id"] for item in filtered}, {first["id"], second["id"]})

        aggregated = self.client.get("/api/series").json()
        compliance = next(item for item in aggregated if item["name"] == "合规月刊")
        self.assertEqual(compliance["count"], 2)
        self.assertEqual(compliance["total_seconds"], 90)
        self.assertEqual(compliance["done"], 1)
        self.assertEqual(compliance["total"], 2)
        self.assertEqual([item["name"] for item in aggregated], sorted((item["name"] for item in aggregated), key=lambda value: value.lower()))

    def test_series_clear_and_empty_default(self):
        project = self.client.post("/api/projects", json={"name": "待清除系列"}).json()
        listed = self.client.get("/api/projects").json()
        self.assertEqual(next(item for item in listed if item["id"] == project["id"])["series"], "")

        self.client.put(f"/api/projects/{project['id']}/series", json={"series": "短期合集"})
        detail = self.client.get(f"/api/projects/{project['id']}").json()
        self.assertEqual(detail["project"]["series"], "短期合集")

        cleared = self.client.put(f"/api/projects/{project['id']}/series", json={"series": ""})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["series"], "")
        detail = self.client.get(f"/api/projects/{project['id']}").json()
        self.assertEqual(detail["project"]["series"], "")
        filtered = self.client.get("/api/projects", params={"series": "短期合集"}).json()
        self.assertEqual(filtered, [])
