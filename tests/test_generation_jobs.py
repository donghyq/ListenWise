"""生成任务落库与断点续作、AI 分章理由、大纲复用、用量统计与诊断接口。"""
import json
import unittest
from unittest.mock import patch

import httpx

from tests import test_api as fixtures


def outline_model_output(document_id, section_ids):
    """构造大纲接口使用的模型返回 JSON。"""
    return json.dumps({
        "title": "合规要点讲稿",
        "opening_angle": "从一个备案案例说起",
        "closing_angle": "回到企业落地的三个动作",
        "sections": [
            {"heading": "备案背景", "purpose": "说明备案要求", "key_points": ["时间要求"], "source_block_ids": [section_ids[0]]},
            {"heading": "投入测算", "purpose": "说明资金要求", "key_points": ["预算"], "source_block_ids": [section_ids[-1]]},
        ],
    }, ensure_ascii=False)


class NarrativeFlowFixture(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def make_confirmed_outline(self, style_profile="professional_blog"):
        project = self.client.post('/api/projects', json={'name': '大纲流程', 'verification_mode': 'source_only', 'scenario': 'topic_learning'}).json()
        source_text = (
            '一、备案要求\n\n'
            '第一段说明备案的时限要求、适用主体和材料清单，这些内容会进入提示词供模型切分章节，需要达到足够的字数才会被视为候选标题，因此这里展开说明，备案时限与材料清单是第一部分的要点。同时补充第二句说明，用于满足候选章节的最低字数门槛，避免测试材料过短导致回退到规则切分。\n\n'
            '二、投入分工\n\n'
            '第二段说明预算投入、责任分工和复核机制，同样需要满足候选章节的最低字数要求，因此这里继续展开说明，让第二部分成为独立的候选章节供模型选择。同时补充第二句说明，确保字数统计超过一百二十字，并保留全部数字、金额与分工细节，防止分章候选被过滤掉。'
        )
        source = self.client.post(f"/api/projects/{project['id']}/text-sources", json={'title': '材料', 'content': source_text}).json()
        document = self.client.get('/api/documents/' + source['id']).json()
        section_ids = [b['id'] for b in document['blocks'] if b['kind'] == 'paragraph']
        payload = {
            'document_id': source['id'],
            'title': '合规要点讲稿',
            'source_block_ids': section_ids,
            'style_profile': style_profile,
            'target_length': 'short',
            'target_words': 800,
            'transform_mode': 'adapt',
            'verification_mode': 'source_only',
            'scenario': 'topic_learning',
        }
        return project, source, payload


class ChapterReasonTest(unittest.TestCase):
    def test_ai_chapter_proposal_returns_reasons_and_defaults_empty(self):
        import app.main as app_main
        blocks = [
            {"id": "h1", "kind": "heading", "sequence_no": 1, "heading_level": 1, "text": "一、背景", "source_locator": "背景"},
            {"id": "p1", "kind": "paragraph", "sequence_no": 2, "text": "背景与义务的说明。" * 20, "source_locator": "第 1 段"},
            {"id": "h2", "kind": "heading", "sequence_no": 3, "heading_level": 1, "text": "二、落地", "source_locator": "落地"},
            {"id": "p2", "kind": "paragraph", "sequence_no": 4, "text": "落地与执行方案。" * 20, "source_locator": "第 2 段"},
        ]
        model_output = json.dumps({"chapters": [
            {"heading_id": "h1", "question": "背景是什么", "reason": "先交代制度背景再展开"},
            {"heading_id": "h2", "question": "怎么落地"},
        ]}, ensure_ascii=False)
        with patch.object(app_main, "model_chat", return_value=model_output):
            picked, titles, reasons = app_main.ai_chapter_proposal(blocks)
        self.assertEqual(picked, [("h1", "背景是什么"), ("h2", "怎么落地")])
        self.assertEqual(reasons, {"h1": "先交代制度背景再展开"})


class ReasonPlanApiTest(NarrativeFlowFixture):
    def test_create_plan_writes_reason_into_chapters(self):
        project, source, _ = self.make_confirmed_outline()
        document = self.client.get('/api/documents/' + source['id']).json()
        heading_ids = [b['id'] for b in document['blocks'] if b['kind'] == 'heading' and (b['text'].startswith('一、') or b['text'].startswith('二、'))]
        model_output = json.dumps({"chapters": [
            {"heading_id": heading_ids[0], "question": "背景是什么", "reason": "先讲背景再讲要求"},
            {"heading_id": heading_ids[1], "question": "怎么落地"},
        ]}, ensure_ascii=False)
        with patch.object(self.main, "model_chat", return_value=model_output):
            plan = self.client.post(f"/api/projects/{project['id']}/plans", json={
                "source_document_id": source['id'], "auto_split": True, "split_mode": "ai",
            })
        self.assertEqual(plan.status_code, 201, plan.text)
        chapters = plan.json()['chapters']
        self.assertEqual(chapters[0]['reason'], '先讲背景再讲要求')
        self.assertEqual(chapters[1]['reason'], '')
        with self.main.db() as conn:
            row = conn.execute('SELECT chapters_json FROM content_plans WHERE id = ?', (plan.json()['id'],)).fetchone()
        stored = json.loads(row[0])
        self.assertEqual(stored[0]['reason'], '先讲背景再讲要求')
        self.assertEqual(stored[1]['reason'], '')


class OutlineReuseTest(NarrativeFlowFixture):
    def test_same_params_reuse_existing_outline(self):
        project, source, payload = self.make_confirmed_outline()
        with patch.object(self.main, "model_chat", return_value=outline_model_output(source['id'], payload['source_block_ids'])):
            first = self.client.post(f"/api/projects/{project['id']}/narrative-outlines", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertNotIn('reused', first.json())
        with patch.object(self.main, "model_chat") as model:
            second = self.client.post(f"/api/projects/{project['id']}/narrative-outlines", json=payload)
        # 命中同参数大纲：直接复用，不再调用模型
        self.assertEqual(second.status_code, 200, second.text)
        model.assert_not_called()
        self.assertTrue(second.json()['reused'])
        self.assertEqual(second.json()['id'], first.json()['id'])
        self.assertEqual(second.json()['outline'], first.json()['outline'])
        with self.main.db() as conn:
            count = conn.execute('SELECT COUNT(*) FROM narrative_outlines WHERE project_id = ?', (project['id'],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_allow_reuse_false_creates_new_outline(self):
        project, source, payload = self.make_confirmed_outline()
        with patch.object(self.main, "model_chat", return_value=outline_model_output(source['id'], payload['source_block_ids'])):
            first = self.client.post(f"/api/projects/{project['id']}/narrative-outlines", json=payload)
            second = self.client.post(f"/api/projects/{project['id']}/narrative-outlines", json={**payload, 'allow_reuse': False})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotEqual(first.json()['id'], second.json()['id'])
        with self.main.db() as conn:
            count = conn.execute('SELECT COUNT(*) FROM narrative_outlines WHERE project_id = ?', (project['id'],)).fetchone()[0]
        self.assertEqual(count, 2)


class GenerationJobTest(NarrativeFlowFixture):
    def make_confirmed_outline_via_api(self):
        project, source, payload = self.make_confirmed_outline()
        with patch.object(self.main, "model_chat", return_value=outline_model_output(source['id'], payload['source_block_ids'])):
            outline = self.client.post(f"/api/projects/{project['id']}/narrative-outlines", json=payload)
        self.assertEqual(outline.status_code, 201, outline.text)
        confirmed = self.client.put(f"/api/narrative-outlines/{outline.json()['id']}/confirm", json={'outline': outline.json()['outline']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return project, source, outline.json()['id']

    def job_row(self, outline_id):
        with self.main.db() as conn:
            row = conn.execute('SELECT * FROM generation_jobs WHERE outline_id = ?', (outline_id,)).fetchone()
        return dict(row) if row else None

    def test_interrupted_then_resume_reuses_completed_sections(self):
        _, _, outline_id = self.make_confirmed_outline_via_api()
        texts = ['第一章正文，交代备案背景。', '第二章正文，测算投入金额。']
        # 第一次生成：第 2 节模型调用失败
        with patch.object(self.main, "model_chat", side_effect=[texts[0], __import__('fastapi').HTTPException(502, '模型服务暂时不可用')]):
            response = self.client.post(f'/api/narrative-outlines/{outline_id}/contents')
        self.assertEqual(response.status_code, 502)
        job = self.job_row(outline_id)
        self.assertEqual(job['status'], 'interrupted')
        self.assertIn('模型服务', job['message'])
        self.assertIn('第一章正文', job['sections_json'])
        progress = self.client.get(f'/api/narrative-outlines/{outline_id}/progress').json()
        self.assertEqual(progress['status'], 'interrupted')
        self.assertEqual(progress['total'], 2)
        self.assertEqual(progress['current'], 1)
        self.assertIn('模型服务', progress['message'])
        # 第二次生成：第 1 节不重调模型，只补第 2 节
        with patch.object(self.main, "model_chat", side_effect=[texts[1]]) as model:
            response = self.client.post(f'/api/narrative-outlines/{outline_id}/contents')
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(model.call_count, 1)
        markdown = response.json()['markdown']
        for text in texts:
            self.assertIn(text, markdown)
        self.assertIn('## 备案背景', markdown)
        self.assertIn('## 投入测算', markdown)
        job = self.job_row(outline_id)
        self.assertEqual(job['status'], 'done')
        progress = self.client.get(f'/api/narrative-outlines/{outline_id}/progress').json()
        self.assertEqual(progress['status'], 'done')
        self.assertEqual(progress['current'], 2)

    def test_regenerate_after_done_resets_from_scratch(self):
        _, _, outline_id = self.make_confirmed_outline_via_api()
        with patch.object(self.main, "model_chat", side_effect=['第一版第一章。', '第一版第二章。']):
            self.client.post(f'/api/narrative-outlines/{outline_id}/contents')
        with patch.object(self.main, "model_chat", side_effect=['第二版第一章。', '第二版第二章。']) as model:
            response = self.client.post(f'/api/narrative-outlines/{outline_id}/contents')
        self.assertEqual(response.status_code, 201, response.text)
        # done 后重新生成：从头重置，两节都重新调用模型
        self.assertEqual(model.call_count, 2)
        self.assertIn('第二版第一章。', response.json()['markdown'])
        self.assertNotIn('第一版第一章。', response.json()['markdown'])

    def test_startup_marks_running_jobs_interrupted(self):
        project, source, outline_id = self.make_confirmed_outline_via_api()
        with self.main.db() as conn:
            conn.execute(
                "INSERT INTO generation_jobs (outline_id, status, total, current, heading, message, sections_json, outline_hash, created_at, updated_at) VALUES (?, 'running', 2, 1, '备案背景', '', '{}', 'hash', ?, ?)",
                (outline_id, self.main.now_iso(), self.main.now_iso()),
            )
        with __import__('fastapi.testclient', fromlist=['TestClient']).TestClient(self.main.app):
            pass  # 触发 startup 钩子
        job = self.job_row(outline_id)
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(job['message'], '应用重启，生成中断，可继续')

    def test_progress_idle_for_unknown_outline(self):
        progress = self.client.get('/api/narrative-outlines/unknown/progress').json()
        self.assertEqual(progress, {'status': 'idle', 'total': 0, 'current': 0, 'heading': '', 'message': ''})


class FakeModelResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class UsageSummaryTest(NarrativeFlowFixture):
    def test_usage_accumulates_and_is_reported(self):
        with self.main.db() as conn:
            conn.execute("DELETE FROM settings WHERE setting_key = 'usage_totals'")
        summary = self.client.get('/api/usage/summary').json()
        self.assertEqual(summary, {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'updated_at': ''})
        settings = {'base_url': 'https://api.example.com/v1', 'model_name': 'test-model', 'api_key': 'sk-test', 'allow_source_upload': True}
        responses = [
            FakeModelResponse({"choices": [{"message": {"content": "好的"}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30}}),
            FakeModelResponse({"choices": [{"message": {"content": "继续"}}], "usage": {"prompt_tokens": 80, "completion_tokens": 10}}),
            FakeModelResponse({"choices": [{"message": {"content": "无用量"}}]}),
        ]
        with patch.object(self.main, "get_internal_provider_settings", return_value=settings), \
                patch.object(self.main.httpx, "post", side_effect=responses):
            self.main.model_chat([{"role": "user", "content": "第一次"}])
            self.main.model_chat([{"role": "user", "content": "第二次"}])
            self.main.model_chat([{"role": "user", "content": "第三次"}])
        summary = self.client.get('/api/usage/summary').json()
        self.assertEqual(summary['calls'], 2)
        self.assertEqual(summary['prompt_tokens'], 200)
        self.assertEqual(summary['completion_tokens'], 40)
        self.assertTrue(summary['updated_at'])

    def test_corrupt_stored_value_does_not_break_recording(self):
        with self.main.db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('usage_totals', 'not-json', ?)", (self.main.now_iso(),))
        self.main.record_model_usage({"prompt_tokens": 10, "completion_tokens": 5})
        summary = self.client.get('/api/usage/summary').json()
        self.assertEqual(summary['calls'], 1)
        self.assertEqual(summary['prompt_tokens'], 10)


class DiagnosticsTest(NarrativeFlowFixture):
    def test_diagnostics_shape(self):
        data = self.client.get('/api/diagnostics').json()
        self.assertEqual(data['version'], self.main.__version__)
        self.assertIsInstance(data['data_dir'], str)
        self.assertGreater(data['db_size_bytes'], 0)
        self.assertIsInstance(data['ffmpeg_available'], bool)
        self.assertEqual(set(data['provider']), {'configured', 'provider_name', 'model_name', 'base_url', 'api_key_configured'})
        self.assertEqual(set(data['tts']), {'provider', 'available'})
        self.assertIsInstance(data['recent_errors'], list)
        self.assertIsInstance(data['jobs'], list)

    def test_recent_errors_capped_at_twenty(self):
        before = len(self.main.RECENT_ERRORS)
        for index in range(22):
            self.main.record_recent_error(f'错误 {index}')
        data = self.client.get('/api/diagnostics').json()
        self.assertLessEqual(len(data['recent_errors']), 20)
        self.assertEqual(data['recent_errors'][-1]['message'], '错误 21')
        self.assertGreaterEqual(len(self.main.RECENT_ERRORS), min(before + 22, 50))

    def test_jobs_lists_ten_most_recent(self):
        with self.main.db() as conn:
            for index in range(12):
                from datetime import datetime, timedelta, timezone
                stamp = (datetime.now(timezone.utc).astimezone() - timedelta(minutes=index)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT OR REPLACE INTO generation_jobs (outline_id, status, total, current, heading, message, sections_json, outline_hash, created_at, updated_at) VALUES (?, 'done', 2, 2, '', '', '{}', '', ?, ?)",
                    (f'job-{index}', stamp, stamp),
                )
        data = self.client.get('/api/diagnostics').json()
        self.assertEqual(len(data['jobs']), 10)
        self.assertEqual(data['jobs'][0]['outline_id'], 'job-0')
        for job in data['jobs']:
            self.assertEqual(set(job), {'outline_id', 'status', 'current', 'total', 'updated_at'})

    def test_model_failure_is_recorded(self):
        settings = {'base_url': 'https://api.example.com/v1', 'model_name': 'test-model', 'api_key': 'sk-test', 'allow_source_upload': True}
        before = len(self.main.RECENT_ERRORS)
        with patch.object(self.main, "get_internal_provider_settings", return_value=settings), \
                patch.object(self.main.httpx, "post", side_effect=httpx.ConnectError('network down')):
            with self.assertRaises(__import__('fastapi').HTTPException):
                self.main.model_chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(self.main.RECENT_ERRORS), before + 1)
        self.assertIn('模型调用失败', self.main.RECENT_ERRORS[-1]['message'])


if __name__ == '__main__':
    unittest.main()
