"""章节修改的范围、候选稿确认及音频失效回归。"""
import json
import unittest
from unittest.mock import patch

from tests import test_api as fixtures


class ChapterWorkspaceTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def make_book(self):
        project = self.client.post('/api/projects', json={'name': '章节检查', 'verification_mode': 'source_only'}).json()
        source = self.client.post(f"/api/projects/{project['id']}/text-sources", json={'title': '文章', 'content': '第一段记录已确认的背景和日期，需要保留限制条件。\n\n第二段说明原因和影响，不应增加未经证实的结论。'}).json()
        blocks = self.client.get('/api/documents/' + source['id']).json()['blocks']
        ids = [b['id'] for b in blocks if b['kind'] == 'paragraph']
        outline = {'title': '讲稿', 'target_total_words': 800, 'sections': [
            {'id': 'stable-a', 'heading': '背景', 'purpose': '说明背景', 'key_points': ['背景'], 'source_block_ids': [ids[0]], 'target_words': 400},
            {'id': 'stable-b', 'heading': '背景与影响', 'purpose': '解释影响', 'key_points': ['影响'], 'source_block_ids': [ids[-1]], 'target_words': 400},
        ]}
        outline_id, content_id = 'o-' + project['id'], 'c-' + project['id']
        markdown = '# 讲稿\n\n## 背景\n\n当前背景。\n\n## 背景与影响\n\n保留这一章的正文。'
        sources = [{'section_id': s['id'], 'heading': s['heading'], 'source_block_ids': s['source_block_ids']} for s in outline['sections']]
        timestamp = self.main.now_iso()
        with self.main.db() as conn:
            conn.execute("INSERT INTO narrative_outlines VALUES (?, ?, ?, '', '讲稿', '个人学习', 'law_podcast_v4', 'short', ?, ?, 'confirmed', ?, ?)", (outline_id, project['id'], source['id'], json.dumps(ids), json.dumps(outline, ensure_ascii=False), timestamp, timestamp))
            conn.execute("INSERT INTO narrative_contents VALUES (?, ?, ?, ?, '讲稿', ?, ?, '{}', 'confirmed', '', ?, ?)", (content_id, outline_id, project['id'], source['id'], markdown, json.dumps(sources, ensure_ascii=False), timestamp, timestamp))
        audio_ids = []
        for heading in ('背景', '背景与影响', ''):
            audio = self.client.post(f"/api/projects/{project['id']}/audio-scripts", json={'narrative_content_id': content_id, 'section_heading': heading}).json()
            path = self.temp_dir / (audio['id'] + '.mp3')
            path.write_bytes(b'audio-fixture')
            with self.main.db() as conn:
                conn.execute("UPDATE audio_outputs SET status='ready', audio_path=? WHERE id=?", (str(path), audio['id']))
            audio_ids.append(audio['id'])
        return project, source, outline_id, content_id, audio_ids

    def state(self, project):
        return self.client.get('/api/projects/' + project['id']).json()

    def test_candidate_includes_feedback_and_original_without_mutating_book(self):
        project, _, _, content_id, audios = self.make_book()
        content = self.state(project)['narrative_contents'][0]
        chapter = content['chapters'][0]
        self.assertEqual(chapter['id'], 'stable-a')
        self.assertEqual(chapter['reading_id'], 'section-1')
        with patch.object(self.main, 'model_chat', return_value='候选正文，保留日期和限制条件。') as model:
            result = self.client.post(f'/api/narrative-contents/{content_id}/sections/stable-a/revision', json={'instructions': '减少重复，保留日期', 'base_version': chapter['version']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('减少重复，保留日期', model.call_args.args[0][-1]['content'])
        self.assertIn('当前背景。', model.call_args.args[0][-1]['content'])
        self.assertIn('第一段记录', model.call_args.args[0][-1]['content'])
        self.assertNotIn('第二段说明', model.call_args.args[0][-1]['content'])
        after = self.state(project)
        self.assertEqual(after['narrative_contents'][0]['markdown'], content['markdown'])
        self.assertTrue(all(a['audio_available'] for a in after['audio_outputs']))
        self.assertEqual(result.json()['original'], '当前背景。')

    def test_accept_changes_only_target_chapter_and_its_audio(self):
        project, _, _, content_id, audios = self.make_book()
        self.client.put(f'/api/narrative-contents/{content_id}/progress', json={'section_id': 'section-1', 'completed': True})
        content = self.state(project)['narrative_contents'][0]
        result = self.client.put(f'/api/narrative-contents/{content_id}/sections/stable-a', json={'body': '新背景正文。', 'base_version': content['chapters'][0]['version']})
        self.assertEqual(result.status_code, 200, result.text)
        after = self.state(project)
        self.assertIn('## 背景与影响\n\n保留这一章的正文。', after['narrative_contents'][0]['markdown'])
        self.assertEqual(after['narrative_contents'][0]['section_sources'], content['section_sources'])
        self.assertEqual(after['narrative_contents'][0]['status'], 'needs_revision')
        self.assertEqual(after['narrative_contents'][0]['reading_progress']['completed_section_ids'], [])
        audio_map = {a['id']: a for a in after['audio_outputs']}
        self.assertEqual([audio_map[a]['status'] for a in audios], ['source_changed', 'ready', 'source_changed'])
        self.assertEqual(self.client.get('/api/audio-outputs/' + audios[0] + '/stream').status_code, 409)
        self.assertEqual(self.client.post('/api/audio-outputs/' + audios[0] + '/synthesize', json={'preview': True}).status_code, 409)
        self.assertNotIn('audio_path', self.client.post('/api/audio-outputs/' + audios[0] + '/export').json())
        with self.main.db() as conn:
            book = self.main.build_mobile_book(conn, project['id'])
        self.assertEqual([c['title'] for c in book['chapters']], ['背景与影响'])

    def test_stale_candidate_and_structure_changes_cannot_overwrite_current_text(self):
        project, _, _, content_id, _ = self.make_book()
        version = self.state(project)['narrative_contents'][0]['chapters'][0]['version']
        endpoint = f'/api/narrative-contents/{content_id}/sections/stable-a'
        self.assertEqual(self.client.put(endpoint, json={'body': '先保存的新正文。', 'base_version': version}).status_code, 200)
        self.assertEqual(self.client.put(endpoint, json={'body': '过期候选稿。', 'base_version': version}).status_code, 409)
        latest = self.state(project)['narrative_contents'][0]
        self.assertIn('先保存的新正文', latest['markdown'])
        self.assertEqual(self.client.put(endpoint, json={'body': '## 新章\n改变章节结构', 'base_version': latest['chapters'][0]['version']}).status_code, 400)

    def test_editing_one_chapter_does_not_expire_an_unchanged_chapter_draft(self):
        project, _, _, content_id, _ = self.make_book()
        chapters = self.state(project)['narrative_contents'][0]['chapters']
        first = self.client.put(f'/api/narrative-contents/{content_id}/sections/stable-a', json={'body': '第一章先保存。', 'base_version': chapters[0]['version']})
        second = self.client.put(f'/api/narrative-contents/{content_id}/sections/stable-b', json={'body': '第二章继续保存。', 'base_version': chapters[1]['version']})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200, second.text)
        markdown = self.state(project)['narrative_contents'][0]['markdown']
        self.assertIn('第一章先保存', markdown)
        self.assertIn('第二章继续保存', markdown)

    def test_replan_saves_new_draft_with_feedback_and_preserves_used_outline(self):
        project, source, outline_id, content_id, _ = self.make_book()
        before = self.state(project)
        old = before['narrative_outlines'][0]
        proposed = {'title': '讲稿', 'sections': [{'heading': '合并的背景与影响', 'key_points': ['背景', '影响'], 'source_block_ids': old['source_block_ids']}]}
        with patch.object(self.main, 'model_chat', return_value=json.dumps(proposed, ensure_ascii=False)) as model:
            response = self.client.post('/api/projects/' + project['id'] + '/narrative-outlines', json={'document_id': source['id'], 'title': '讲稿', 'source_block_ids': old['source_block_ids'], 'style_profile': 'law_podcast_v4', 'transform_mode': 'condense', 'target_length': 'short', 'target_words': 800, 'revision_of': outline_id, 'revision_instructions': '合并重复的背景章节'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn('合并重复的背景章节', model.call_args.args[0][-1]['content'])
        self.assertIn('背景与影响', model.call_args.args[0][-1]['content'])
        draft = response.json()
        self.assertEqual(draft['status'], 'draft')
        after = self.state(project)
        self.assertEqual(after['narrative_outlines'][0]['id'], draft['id'])
        self.assertEqual(next(o for o in after['narrative_outlines'] if o['id'] == outline_id)['outline'], old['outline'])
        self.assertEqual(after['narrative_contents'][0]['markdown'], before['narrative_contents'][0]['markdown'])
        confirmed = self.client.put('/api/narrative-outlines/' + draft['id'] + '/confirm', json={'outline': draft['outline']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()['outline']['revision_of'], outline_id)

    def test_manual_outline_copy_keeps_old_content_reference(self):
        project, _, outline_id, content_id, _ = self.make_book()
        result = self.client.post('/api/narrative-outlines/' + outline_id + '/copy')
        self.assertEqual(result.status_code, 201)
        self.assertNotEqual(result.json()['id'], outline_id)
        self.assertEqual(self.state(project)['narrative_contents'][0]['outline_id'], outline_id)

    def test_audiobook_rebuilds_changed_audio_and_mobile_keeps_chapter_order(self):
        project, _, _, content_id, audios = self.make_book()
        content = self.state(project)['narrative_contents'][0]
        self.client.put(f'/api/narrative-contents/{content_id}/sections/stable-a', json={'body': '已修改的背景正文。', 'base_version': content['chapters'][0]['version']})
        revised = self.state(project)['narrative_contents'][0]
        self.client.put('/api/narrative-contents/' + content_id, json={'markdown': revised['markdown'], 'status': 'confirmed'})
        new_path = self.temp_dir / 'changed-chapter.mp3'
        new_path.write_bytes(b'new-audio')
        with patch.object(self.main, 'synthesize_audio', return_value=(new_path, 'test', 3)) as synth, patch.object(self.main, 'merge_audiobook') as merge:
            result = self.client.post('/api/projects/' + project['id'] + '/audiobook')
        self.assertEqual(result.status_code, 201, result.text)
        self.assertIn('已修改的背景正文', synth.call_args.args[0])
        self.assertEqual(merge.call_args.args[0][0], ('背景', new_path))
        with self.main.db() as conn:
            book = self.main.build_mobile_book(conn, project['id'])
        self.assertEqual([c['title'] for c in book['chapters']], ['背景', '背景与影响'])
        self.assertIn('已修改的背景正文', book['chapters'][0]['text'])

    def test_section_export_matches_complete_heading_not_a_prefix(self):
        project, _, _, content_id, _ = self.make_book()
        with self.main.db() as conn:
            conn.execute("UPDATE narrative_contents SET markdown=? WHERE id=?", ('# 讲稿\n\n## 背景与影响\n\n长标题正文。\n\n## 背景\n\n短标题正文。', content_id))
        result = self.client.post(f'/api/narrative-contents/{content_id}/sections/stable-a/export')
        self.assertEqual(result.status_code, 201, result.text)
        from pathlib import Path
        exported = Path(result.json()['markdown_path']).read_text()
        self.assertIn('短标题正文', exported)
        self.assertNotIn('长标题正文', exported)


if __name__ == '__main__':
    unittest.main()
