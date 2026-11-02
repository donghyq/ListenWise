"""事实核对：确定性规则提取与讲稿保留检查。"""
import json
import unittest

from app import main as app_main

from tests import test_api as fixtures


def make_blocks():
    return [
        {"id": "b1", "kind": "paragraph", "sequence_no": 1, "text": "依据《中华人民共和国数据安全法》第三十二条，自2026年7月28日起实施。", "source_locator": "第 1 段"},
        {"id": "b2", "kind": "paragraph", "sequence_no": 2, "text": "项目预算12.5%，其中设备支出3亿元，另有8000美元预备金。", "source_locator": "第 2 段"},
        {"id": "b3", "kind": "paragraph", "sequence_no": 3, "text": "补充：2026-07-28 与 2026年 两个日期均需复核。", "source_locator": "第 3 段"},
    ]


class ExtractSourceFactsTest(unittest.TestCase):
    def test_extracts_all_kinds_in_position_order(self):
        facts = app_main.extract_source_facts(make_blocks())
        kinds = [(fact["kind"], fact["text"]) for fact in facts]
        self.assertIn(("law", "《中华人民共和国数据安全法》"), kinds)
        self.assertIn(("article", "第三十二条"), kinds)
        self.assertIn(("date", "2026年7月28日"), kinds)
        self.assertIn(("number", "12.5%"), kinds)
        self.assertIn(("number", "3亿元"), kinds)
        self.assertIn(("number", "8000美元"), kinds)
        # 同块内按出现位置排序：law 在 article 之前，date 在 number 之前
        law_index = kinds.index(("law", "《中华人民共和国数据安全法》"))
        article_index = kinds.index(("article", "第三十二条"))
        date_index = kinds.index(("date", "2026年7月28日"))
        percent_index = kinds.index(("number", "12.5%"))
        self.assertLess(law_index, article_index)
        self.assertLess(date_index, percent_index)
        # 块记录来源信息
        first = facts[0]
        self.assertEqual(first["source_block_id"], "b1")
        self.assertEqual(first["source_locator"], "第 1 段")

    def test_longest_date_form_wins_without_duplicate_year(self):
        import app.main as main
        facts = main.extract_source_facts([{"id": "b", "kind": "paragraph", "text": "2026年7月28日生效", "source_locator": ""}])
        self.assertEqual([fact["text"] for fact in facts], ["2026年7月28日"])

    def test_dedupes_same_text_across_blocks(self):
        import app.main as main
        blocks = [
            {"id": "b1", "kind": "paragraph", "text": "预算3亿元。", "source_locator": "1"},
            {"id": "b2", "kind": "paragraph", "text": "追加3亿元。", "source_locator": "2"},
        ]
        facts = main.extract_source_facts(blocks)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["source_block_id"], "b1")

    def test_caps_at_200_items_in_order(self):
        import app.main as main
        blocks = [{"id": f"b{i}", "kind": "paragraph", "text": f"第{i}条", "source_locator": ""} for i in range(210)]
        facts = main.extract_source_facts(blocks)
        self.assertEqual(len(facts), 200)
        self.assertEqual(facts[0]["text"], "第0条")
        self.assertEqual(facts[-1]["text"], "第199条")


class FactCheckApiTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def make_content(self):
        project = self.client.post('/api/projects', json={'name': '事实核对', 'verification_mode': 'source_only'}).json()
        source_text = (
            '依据《数据安全法》第32条，企业应当在2025年3月1日前完成备案，并投入约300万元。\n\n'
            '监管机构在2024年发布了新的指引文件。'
        )
        source = self.client.post(f"/api/projects/{project['id']}/text-sources", json={'title': '材料', 'content': source_text}).json()
        blocks = self.client.get('/api/documents/' + source['id']).json()['blocks']
        paragraph_ids = [b['id'] for b in blocks if b['kind'] == 'paragraph']
        outline_id, content_id = 'o-' + project['id'], 'c-' + project['id']
        markdown = (
            '# 讲稿\n\n'
            '## 背景\n\n'
            '企业应当在２０２５ 年 ３ 月 １ 日前完成备案，并投入约300万元。\n\n'
            '## 依据\n\n'
            '《数据安全法》第32条要求落实数据分级保护。'
        )
        outline = {'title': '讲稿', 'target_total_words': 800, 'sections': []}
        sources = [
            {'section_id': 'sec-a', 'heading': '背景', 'source_block_ids': paragraph_ids[:1]},
            {'section_id': 'sec-b', 'heading': '依据', 'source_block_ids': paragraph_ids[1:]},
        ]
        timestamp = self.main.now_iso()
        with self.main.db() as conn:
            conn.execute(
                "INSERT INTO narrative_outlines VALUES (?, ?, ?, '', '讲稿', '个人学习', 'law_podcast_v4', 'short', ?, ?, 'confirmed', ?, ?)",
                (outline_id, project['id'], source['id'], json.dumps(paragraph_ids), json.dumps(outline, ensure_ascii=False), timestamp, timestamp),
            )
            conn.execute(
                "INSERT INTO narrative_contents VALUES (?, ?, ?, ?, '讲稿', ?, ?, '{}', 'pending_review', '', ?, ?)",
                (content_id, outline_id, project['id'], source['id'], markdown, json.dumps(sources, ensure_ascii=False), timestamp, timestamp),
            )
        return project, content_id

    def test_fact_check_reports_preserved_and_missing(self):
        _, content_id = self.make_content()
        response = self.client.post(f'/api/narrative-contents/{content_id}/fact-check')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['summary'], {'total': 5, 'preserved': 4, 'missing': 1})
        self.assertEqual([fact['text'] for fact in data['facts'] if fact['status'] == 'missing'], ['2024年'])
        # missing 排在结果最前
        self.assertEqual(data['facts'][0]['status'], 'missing')
        preserved = [fact['text'] for fact in data['facts'] if fact['status'] == 'preserved']
        self.assertEqual(preserved, ['《数据安全法》', '第32条', '2025年3月1日', '300万元'])
        by_text = {fact['text']: fact for fact in data['facts']}
        # 全角数字与空白规整后仍能命中：讲稿写的是全角“２０２５ 年 ３ 月 １ 日”
        self.assertEqual(by_text['2025年3月1日']['found_in'], [{'section_id': 'sec-a', 'heading': '背景'}])
        self.assertEqual(by_text['300万元']['found_in'][0]['section_id'], 'sec-a')
        self.assertEqual(by_text['《数据安全法》']['found_in'], [{'section_id': 'sec-b', 'heading': '依据'}])
        self.assertEqual(by_text['第32条']['found_in'][0]['section_id'], 'sec-b')
        self.assertTrue(data['generated_at'])

    def test_fact_check_missing_content_returns_404(self):
        response = self.client.post('/api/narrative-contents/does-not-exist/fact-check')
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
