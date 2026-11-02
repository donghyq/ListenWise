from __future__ import annotations

import importlib
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


SENTENCE = "监管机构发布了新的数据合规规则，明确了企业的报告义务、整改期限与生效日期。"


class LongDocOutlineTest(unittest.TestCase):
    """超长文分段处理（map-reduce 大纲）的离线验收。"""

    @classmethod
    def setUpClass(cls):
        import app.main as main
        cls.main = importlib.reload(main)
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="lawflow-long-doc-"))
        cls.main.DATA_DIR = cls.temp_dir / "data"
        cls.main.PROJECTS_DIR = cls.main.DATA_DIR / "projects"
        cls.main.EXPORTS_DIR = cls.main.DATA_DIR / "exports"
        cls.main.DB_PATH = cls.main.DATA_DIR / "app.db"
        cls.main.DEFAULT_EXPORT_DIR = cls.temp_dir / "Desktop" / "ai_law"
        for directory in (cls.main.DATA_DIR, cls.main.PROJECTS_DIR, cls.main.EXPORTS_DIR, cls.main.DATA_DIR / "temp", cls.main.DATA_DIR / "logs"):
            directory.mkdir(parents=True, exist_ok=True)
        cls.main.init_db()
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def create_project_with_source(self, name: str, content: str) -> tuple[str, str, list[str]]:
        project = self.client.post("/api/projects", json={"name": name, "verification_mode": "source_only"}).json()
        project_id = project["id"]
        source = self.client.post(f"/api/projects/{project_id}/text-sources", json={"title": name + "素材", "content": content}).json()
        document_id = source["id"]
        document = self.client.get(f"/api/documents/{document_id}").json()
        paragraph_ids = [block["id"] for block in document["blocks"] if block["kind"] == "paragraph"]
        return project_id, document_id, paragraph_ids

    def test_long_doc_outline_uses_map_reduce_digest(self):
        content = SENTENCE * 2200  # 约 8.1 万字，超过 6 万字预算
        project_id, document_id, paragraph_ids = self.create_project_with_source("超长文项目", content)

        calls = []
        original_model_chat = self.main.model_chat

        def fake_model_chat(messages, temperature=0.35, max_tokens=4096):
            prompt = messages[-1]["content"]
            calls.append(prompt)
            if "请只提取影响理解的关键事实" in prompt:
                return json.dumps({"points": ["监管机构发布数据合规新规则。", "规则明确企业报告义务与整改期限。", "规则包含明确生效日期。"]}, ensure_ascii=False)
            return json.dumps({
                "title": "数据合规规则解读",
                "opening_angle": "从规则发布的背景切入。",
                "closing_angle": "落到企业的落地动作。",
                "sections": [
                    {"heading": "规则要点", "purpose": "说明规则内容", "key_points": ["报告义务", "生效日期"], "source_block_ids": [paragraph_ids[0]], "target_words": 800},
                    {"heading": "落地动作", "purpose": "说明企业动作", "key_points": ["整改期限"], "source_block_ids": [paragraph_ids[0]], "target_words": 800},
                ],
            }, ensure_ascii=False)

        self.main.model_chat = fake_model_chat
        try:
            response = self.client.post(f"/api/projects/{project_id}/narrative-outlines", json={
                "document_id": document_id,
                "title": "数据合规规则解读",
                "source_block_ids": paragraph_ids,
                "audience": "企业法务",
                "style_profile": "law_podcast_v4",
                "target_length": "short",
            })
        finally:
            self.main.model_chat = original_model_chat

        self.assertEqual(response.status_code, 201, response.text)
        outline = response.json()["outline"]
        digest = outline["long_doc_digest"]
        self.assertEqual(digest["chunks"], math.ceil(len(content) / self.main.LONG_DOC_CHUNK_CHARS))
        self.assertGreaterEqual(digest["source_chars"], self.main.SOURCE_BUDGET_CHARS)
        extraction_calls = [prompt for prompt in calls if "请只提取影响理解的关键事实" in prompt]
        outline_calls = [prompt for prompt in calls if "请基于下列已选材料规划" in prompt]
        self.assertEqual(len(extraction_calls), digest["chunks"])  # 每个分块恰好一次要点抽取
        self.assertEqual(len(outline_calls), 1)
        self.assertIn("以下为超长材料的分段要点摘要", outline_calls[0])
        self.assertIn("大纲必须覆盖全部编号分块的关键点", outline_calls[0])
        self.assertIn("监管机构发布数据合规新规则。", outline_calls[0])  # 抽取要点进入大纲提示词
        self.assertNotIn(SENTENCE, outline_calls[0])  # 原文不再整段进入提示词
        self.assertTrue(outline["sections"])
        self.assertEqual(len(outline["sections"]), 2)

    def test_below_budget_outline_keeps_single_model_call(self):
        content = SENTENCE * 30  # 约 1100 字，低于预算
        project_id, document_id, paragraph_ids = self.create_project_with_source("短文项目", content)

        calls = []
        original_model_chat = self.main.model_chat

        def fake_model_chat(messages, temperature=0.35, max_tokens=4096):
            prompt = messages[-1]["content"]
            calls.append(prompt)
            return json.dumps({
                "title": "短文大纲",
                "sections": [
                    {"heading": "规则要点", "purpose": "说明规则内容", "key_points": ["报告义务"], "source_block_ids": [paragraph_ids[0]], "target_words": 800},
                ],
            }, ensure_ascii=False)

        self.main.model_chat = fake_model_chat
        try:
            response = self.client.post(f"/api/projects/{project_id}/narrative-outlines", json={
                "document_id": document_id,
                "title": "短文大纲",
                "source_block_ids": paragraph_ids,
                "audience": "企业法务",
                "style_profile": "law_podcast_v4",
                "target_length": "short",
            })
        finally:
            self.main.model_chat = original_model_chat

        self.assertEqual(response.status_code, 201, response.text)
        outline = response.json()["outline"]
        self.assertTrue(outline["sections"])
        self.assertNotIn("long_doc_digest", outline)  # 低于预算时行为完全不变
        self.assertEqual(len(calls), 1)  # 只有大纲生成一次调用，没有分段抽取
        self.assertNotIn("分段要点摘要", calls[0])
        self.assertIn(SENTENCE.strip()[:20], calls[0])  # 原文仍整段进入提示词

    def test_outline_rejects_material_beyond_chunk_limit(self):
        content = SENTENCE * 5500  # 约 20.4 万字，超过 24 块上限
        project_id, document_id, paragraph_ids = self.create_project_with_source("超限项目", content)
        original_model_chat = self.main.model_chat
        self.main.model_chat = lambda messages, temperature=0.35, max_tokens=4096: (_ for _ in ()).throw(AssertionError("超限材料不应调用模型"))
        try:
            response = self.client.post(f"/api/projects/{project_id}/narrative-outlines", json={
                "document_id": document_id,
                "title": "超限大纲",
                "source_block_ids": paragraph_ids,
                "audience": "企业法务",
                "style_profile": "law_podcast_v4",
                "target_length": "short",
            })
        finally:
            self.main.model_chat = original_model_chat
        self.assertEqual(response.status_code, 400)
        self.assertIn("材料超过约 19 万字，请分批导入", response.json()["detail"])
