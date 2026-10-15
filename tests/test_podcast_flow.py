"""播客风改写、分章意见反馈与 Windows 兼容的回归测试。"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import main as app_main


WPS_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def build_wps_style_docx(path: Path):
    """构造一份 WPS 导出风格的 docx：styleId 是短 ID（"a"），样式名才是 Title。"""
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{WPS_NAMESPACE}"><w:style w:type="paragraph" w:styleId="a"><w:name w:val="Title"/></w:style></w:styles>'''
    paragraphs = [
        ("a", "综合合规"),
        ("a", "【巴西】巴西总统签署能源转型法令。"),
        (None, "巴西法令的具体义务内容，涉及电力市场与碳市场安排。"),
        (None, "法令还规定了低碳氢能的认证路径。"),
        ("a", "https://example.com/reference-link"),
        ("a", "外商投资"),
        ("a", "【法国】法国强化敏感行业外资审查。"),
        (None, "法国新规将非欧盟投资者审查门槛降低至持股百分之十。"),
        (None, "新规同时覆盖关键基础设施与敏感数据领域。"),
        ("a", "【欧盟】欧盟委员会认定对京东的外国补贴调查构成不当域外管辖。"),
        (None, "中国司法部认定该调查属于不当域外管辖，禁止组织和个人配合执行。"),
        (None, "这一认定对中欧双向投资合规评估有直接影响。"),
    ]
    body = ""
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{WPS_NAMESPACE}"><w:body>{body}</w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


class WpsDocxHeadingTest(unittest.TestCase):
    def test_short_style_id_docx_splits_by_section(self):
        """WPS 导出的 docx styleId 是 "a" 而非 "Title"：必须靠样式名识别标题，
        URL 引用不算标题，【】条目降级为栏目下的二级标题，栏目成为自然章节。"""
        with tempfile.TemporaryDirectory() as temp:
            docx_path = Path(temp) / "brief.docx"
            build_wps_style_docx(docx_path)
            blocks, structure = app_main.parse_docx(docx_path)
        self.assertEqual(structure["heading_count"], 5)  # 2 栏目 + 3 条目标题，URL 不算
        chapters = app_main.create_plan_chapters(blocks, "lexcast")
        titles = [chapter["title"] for chapter in chapters]
        self.assertEqual(len(chapters), 2)
        self.assertIn("综合合规", titles[0])
        self.assertIn("外商投资", titles[1])
        # 条目和引用链接都归属到所在栏目章节内
        self.assertEqual(len(chapters[0]["source_block_ids"]), 5)
        self.assertEqual(len(chapters[1]["source_block_ids"]), 7)


def make_blocks():
    long_a = "人工智能治理背景与具体义务要求。" * 10
    long_b = "数据出境合规的评估路径。" * 10
    return [
        {"id": "h1", "kind": "heading", "sequence_no": 1, "heading_level": 1, "text": "一、背景", "source_locator": "背景"},
        {"id": "p1", "kind": "paragraph", "sequence_no": 2, "text": long_a, "source_locator": "第 1 段"},
        {"id": "h2", "kind": "heading", "sequence_no": 3, "heading_level": 1, "text": "二、数据出境", "source_locator": "数据出境"},
        {"id": "p2", "kind": "paragraph", "sequence_no": 4, "text": long_b, "source_locator": "第 2 段"},
    ]


class AiChapterFeedbackTest(unittest.TestCase):
    def test_feedback_enters_split_prompt(self):
        captured = {}

        def fake_model_chat(messages, **_kwargs):
            captured["prompt"] = messages[1]["content"]
            return json.dumps({"chapters": [
                {"heading_id": "h1", "question": "q1"},
                {"heading_id": "h2", "question": "q2"},
            ]}, ensure_ascii=False)

        with patch.object(app_main, "model_chat", side_effect=fake_model_chat):
            app_main.ai_chapter_proposal(make_blocks(), feedback="分得太细，请合并成一章")
        self.assertIn("用户对上一版章节划分的意见", captured["prompt"])
        self.assertIn("分得太细，请合并成一章", captured["prompt"])

    def test_no_feedback_keeps_prompt_clean(self):
        captured = {}

        def fake_model_chat(messages, **_kwargs):
            captured["prompt"] = messages[1]["content"]
            return json.dumps({"chapters": [{"heading_id": "h1", "question": "q1"}]}, ensure_ascii=False)

        with patch.object(app_main, "model_chat", side_effect=fake_model_chat):
            app_main.ai_chapter_proposal(make_blocks())
        self.assertNotIn("用户对上一版章节划分的意见", captured["prompt"])


class PodcastChapterGenerationTest(unittest.TestCase):
    def test_model_ready_uses_podcast_style_generation(self):
        blocks = [b for b in make_blocks() if b["kind"] == "paragraph"]
        captured = {}

        def fake_model_chat(messages, **_kwargs):
            captured["prompt"] = messages[1]["content"]
            return "这是本章的播客风正文。"

        with patch.object(app_main, "model_generation_ready", return_value=True), \
                patch.object(app_main, "model_chat", side_effect=fake_model_chat), \
                patch.object(app_main, "get_style_profiles", return_value=app_main.STYLE_PROFILES), \
                patch.object(app_main, "get_internal_provider_settings", return_value={}):
            markdown, claims, metadata = app_main.generate_chapter_podcast_markdown(
                "第01章 · 背景", make_blocks(), "个人学习", "adapt", "law_podcast_v4", "这一章讲什么",
            )
        self.assertIn("播客风正文", markdown)
        self.assertTrue(claims)
        self.assertEqual(metadata["generator"], "model")
        self.assertEqual(metadata["transform_mode"], "adapt")
        # 提示词包含播客画像与用户问题，且材料完整进入模型
        self.assertIn("深度播客主播", captured["prompt"])
        self.assertIn("这一章讲什么", captured["prompt"])
        self.assertIn("人工智能治理背景", captured["prompt"])

    def test_template_notice_marks_fallback(self):
        self.assertIn("未接入 AI", app_main.CHAPTER_TEMPLATE_NOTICE)


class WindowsCompatTest(unittest.TestCase):
    def test_quick_link_create_accepts_transform_mode(self):
        payload = app_main.QuickLinkCreate(url="https://example.com/a", scenario="daily_brief", transform_mode="enrich")
        self.assertEqual(payload.transform_mode, "enrich")

    def test_tts_provider_accepts_windows_sapi(self):
        payload = app_main.ProviderSettings(provider_preset="openai", api_key="k", tts_provider="windows_sapi")
        self.assertEqual(payload.tts_provider, "windows_sapi")

    def test_plan_create_accepts_feedback(self):
        payload = app_main.PlanCreate(source_document_id="d1", feedback="太细了，合并前两章")
        self.assertEqual(payload.feedback, "太细了，合并前两章")


class AiParseStructureTest(unittest.TestCase):
    def make_flat_blocks(self):
        return [
            {"id": "b-00001", "kind": "paragraph", "sequence_no": 1, "heading_level": 0, "text": "综合合规", "source_locator": "段落 1"},
            {"id": "b-00002", "kind": "paragraph", "sequence_no": 2, "heading_level": 0, "text": "【巴西】巴西总统签署能源转型法令。", "source_locator": "段落 2"},
            {"id": "b-00003", "kind": "paragraph", "sequence_no": 3, "heading_level": 0, "text": "法令规定电力市场开放的过渡安排。", "source_locator": "段落 3"},
            {"id": "b-00004", "kind": "paragraph", "sequence_no": 4, "heading_level": 0, "text": "https://example.com/source", "source_locator": "段落 4"},
            {"id": "b-00005", "kind": "paragraph", "sequence_no": 5, "heading_level": 0, "text": "外商投资", "source_locator": "段落 5"},
            {"id": "b-00006", "kind": "paragraph", "sequence_no": 6, "heading_level": 0, "text": "【法国】法国强化外资审查。", "source_locator": "段落 6"},
        ]

    def test_ai_parse_rebuilds_heading_structure(self):
        captured = {}

        def fake_model_chat(messages, **_kwargs):
            captured["prompt"] = messages[1]["content"]
            return json.dumps({"headings": [
                {"id": "b-00001", "level": 1},
                {"id": "b-00002", "level": 2},
                {"id": "b-00004", "level": 1},  # URL：必须被忽略
                {"id": "b-00005", "level": 1},
                {"id": "b-00006", "level": 2},
                {"id": "b-99999", "level": 1},  # 不存在的 id：必须被忽略
            ]}, ensure_ascii=False)

        with patch.object(app_main, "model_generation_ready", return_value=True), \
                patch.object(app_main, "model_chat", side_effect=fake_model_chat):
            rebuilt, structure = app_main.ai_parse_structure(self.make_flat_blocks())
        by_id = {block["id"]: block for block in rebuilt}
        self.assertEqual(by_id["b-00001"]["kind"], "heading")
        self.assertEqual(by_id["b-00001"]["heading_level"], 1)
        self.assertEqual(by_id["b-00002"]["heading_level"], 2)
        self.assertEqual(by_id["b-00002"]["heading_path"], "综合合规 / 【巴西】巴西总统签署能源转型法令。")
        self.assertEqual(by_id["b-00003"]["kind"], "paragraph")
        self.assertEqual(by_id["b-00003"]["heading_path"], "综合合规 / 【巴西】巴西总统签署能源转型法令。")
        self.assertEqual(by_id["b-00004"]["kind"], "paragraph")  # URL 不当标题
        self.assertEqual(by_id["b-00005"]["kind"], "heading")
        self.assertTrue(structure["ai_parsed"])
        self.assertEqual(structure["heading_count"], 4)
        # 块 id 与顺序保持稳定，已有章节引用不受影响
        self.assertEqual([b["id"] for b in rebuilt], [f"b-{i:05d}" for i in range(1, 7)])
        self.assertIn("b-00001|综合合规", captured["prompt"])

    def test_ai_parse_feedback_enters_prompt(self):
        captured = {}

        def fake_model_chat(messages, **_kwargs):
            captured["prompt"] = messages[1]["content"]
            return json.dumps({"headings": []}, ensure_ascii=False)

        with patch.object(app_main, "model_generation_ready", return_value=True), \
                patch.object(app_main, "model_chat", side_effect=fake_model_chat):
            app_main.ai_parse_structure(self.make_flat_blocks(), feedback="栏目名才是一级标题")
        self.assertIn("用户对上一版结构解析的意见", captured["prompt"])
        self.assertIn("栏目名才是一级标题", captured["prompt"])

    def test_ai_parse_rejects_model_not_configured(self):
        with patch.object(app_main, "model_generation_ready", return_value=False):
            with self.assertRaises(Exception):
                app_main.ai_parse_structure(self.make_flat_blocks())

    def test_ai_parse_rejects_heading_explosion(self):
        blocks = [{"id": f"b-{i:05d}", "kind": "paragraph", "sequence_no": i + 1, "heading_level": 0,
                   "text": f"正文段落内容{i}，这句话很长，明显不是标题。", "source_locator": f"段落 {i + 1}"}
                  for i in range(20)]

        def fake_model_chat(_messages, **_kwargs):
            return json.dumps({"headings": [{"id": block["id"], "level": 1} for block in blocks]}, ensure_ascii=False)

        with patch.object(app_main, "model_generation_ready", return_value=True), \
                patch.object(app_main, "model_chat", side_effect=fake_model_chat):
            with self.assertRaises(Exception):
                app_main.ai_parse_structure(blocks)


class PodcastTotalWordsTest(unittest.TestCase):
    """压缩比红线（podcast-rewriter：原文每 1 万字 ≥ 3000 字）必须落实到字数目标。"""

    def test_podcast_profile_floor_overrides_duration(self):
        # 10 分钟时长默认只给 2400 字；原文 2 万字时播客风改写应提到 6000 字。
        total = app_main.resolve_narrative_total_words(
            None, "standard", 10, "law_podcast_v4", "adapt", source_chars=20000,
        )
        self.assertEqual(total, 6000)

    def test_podcast_profile_caps_at_absolute_limit(self):
        total = app_main.resolve_narrative_total_words(
            None, "standard", 10, "law_podcast_v4", "adapt", source_chars=60000,
        )
        self.assertEqual(total, app_main.PODCAST_TOTAL_WORDS_CAP)

    def test_condense_stays_duration_bound(self):
        # 提炼模式本来就是要压缩的，不吃压缩比红线。
        total = app_main.resolve_narrative_total_words(
            None, "standard", 5, "law_podcast_v4", "condense", source_chars=20000,
        )
        self.assertEqual(total, 1200)

    def test_other_profiles_unchanged(self):
        total = app_main.resolve_narrative_total_words(
            None, "standard", 10, "professional_blog", "adapt", source_chars=20000,
        )
        self.assertEqual(total, 2400)

    def test_explicit_target_words_win(self):
        total = app_main.resolve_narrative_total_words(
            1500, "deep", 10, "law_podcast_v4", "adapt", source_chars=20000,
        )
        self.assertEqual(total, 1500)


class SupplementalSourcesTest(unittest.TestCase):
    """augment 联网扩充：补充公开资料必须进入 AI 大纲的每一节，不能静默丢失。"""

    def test_supplement_blocks_appended_to_sections(self):
        request = app_main.NarrativeOutlineRequest(
            document_id="doc-primary",
            title="测试大纲",
            source_block_ids=["doc-primary:b-00001", "web-doc:b-00001", "web-doc:b-00002"],
            supplemental_document_ids=["web-doc"],
            style_profile="law_podcast_v4",
            transform_mode="enrich",
        )
        blocks = [
            {"id": "doc-primary:b-00001", "document_id": "doc-primary", "kind": "paragraph", "text": "主文档正文块。"},
            {"id": "web-doc:b-00001", "document_id": "web-doc", "kind": "paragraph", "text": "补充网页正文块一。"},
            {"id": "web-doc:b-00002", "document_id": "web-doc", "kind": "heading", "text": "网页标题（不应补充）"},
        ]
        raw = {"title": "测试大纲", "sections": [{
            "heading": "第一节",
            "purpose": "测试",
            "key_points": ["点"],
            "source_block_ids": ["doc-primary:b-00001"],
        }]}
        outline = app_main.normalize_narrative_outline(raw, request, blocks)
        self.assertEqual(outline["sections"][0]["source_block_ids"],
                         ["doc-primary:b-00001", "web-doc:b-00001"])

    def test_no_supplement_unchanged(self):
        request = app_main.NarrativeOutlineRequest(
            document_id="doc-primary",
            title="测试大纲",
            source_block_ids=["doc-primary:b-00001"],
            style_profile="professional_blog",
        )
        blocks = [{"id": "doc-primary:b-00001", "document_id": "doc-primary", "kind": "paragraph", "text": "主文档正文块。"}]
        raw = {"title": "测试大纲", "sections": [{
            "heading": "第一节",
            "purpose": "测试",
            "key_points": ["点"],
            "source_block_ids": ["doc-primary:b-00001"],
        }]}
        outline = app_main.normalize_narrative_outline(raw, request, blocks)
        self.assertEqual(outline["sections"][0]["source_block_ids"], ["doc-primary:b-00001"])


if __name__ == "__main__":
    unittest.main()
