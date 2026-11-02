"""同项目重复导入检测：正文指纹、来源链接与 force 跳过。"""
import unittest

from tests import test_api as fixtures


TEXT_A = '企业应当确认跨境传输的业务必要性，并完成个人信息保护影响评估，同时保留全部评估记录。'
TEXT_B = '监管机构要求企业在规定期限内提交备案材料，逾期未提交将影响相关业务的正常开展。'


class ImportDedupTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def doc_count(self, project_id):
        with self.main.db() as conn:
            return conn.execute('SELECT COUNT(*) FROM source_documents WHERE project_id = ?', (project_id,)).fetchone()[0]

    def test_text_source_duplicate_returns_existing_record(self):
        project = self.client.post('/api/projects', json={'name': '去重检测'}).json()
        pid = project['id']
        first = self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '原始材料', 'content': TEXT_A})
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '换个标题再贴一次', 'content': TEXT_A})
        # 同项目同指纹：不新建，返回 200 与已有材料信息
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['existing_project_id'], pid)
        self.assertEqual(body['existing_document_id'], first.json()['id'])
        self.assertEqual(body['existing_name'], '原始材料')
        self.assertEqual(self.doc_count(pid), 1)

    def test_text_source_force_skips_detection(self):
        project = self.client.post('/api/projects', json={'name': '强制导入'}).json()
        pid = project['id']
        self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '原始材料', 'content': TEXT_A})
        forced = self.client.post(f'/api/projects/{pid}/text-sources?force=1', json={'title': '再导入一次', 'content': TEXT_A})
        self.assertEqual(forced.status_code, 201, forced.text)
        self.assertNotIn('duplicate', forced.json())
        self.assertEqual(self.doc_count(pid), 2)

    def test_text_source_different_content_creates_new(self):
        project = self.client.post('/api/projects', json={'name': '不同内容'}).json()
        pid = project['id']
        first = self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '材料一', 'content': TEXT_A})
        second = self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '材料二', 'content': TEXT_B})
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()['id'], second.json()['id'])

    def test_whitespace_only_difference_counts_as_duplicate(self):
        project = self.client.post('/api/projects', json={'name': '空白规整'}).json()
        pid = project['id']
        self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '原始材料', 'content': TEXT_A})
        padded = self.client.post(f'/api/projects/{pid}/text-sources', json={'title': '多空白版本', 'content': '  ' + TEXT_A + '  '})
        self.assertEqual(padded.status_code, 200)
        self.assertTrue(padded.json()['duplicate'])

    def test_same_url_in_same_project_is_duplicate(self):
        project = self.client.post('/api/projects', json={'name': '链接去重'}).json()
        pid = project['id']
        first = self.main.save_text_source(pid, '网页文章', TEXT_A, 'https://example.com/article')
        second = self.main.save_text_source(pid, '同一链接不同正文', TEXT_B, ' https://example.com/article ')
        self.assertTrue(second.get('duplicate'))
        self.assertEqual(second['existing_document_id'], first['id'])
        # 不同链接不受影响
        third = self.main.save_text_source(pid, '另一篇', TEXT_B, 'https://example.com/other')
        self.assertNotIn('duplicate', third)

    def test_document_upload_duplicate_detection(self):
        project = self.client.post('/api/projects', json={'name': '文件去重'}).json()
        pid = project['id']
        file_bytes = ('# 会议纪要\n\n' + TEXT_A + '\n\n' + TEXT_B).encode('utf-8')
        first = self.client.post(f'/api/projects/{pid}/documents', files={'file': ('纪要.txt', file_bytes, 'text/plain')})
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(f'/api/projects/{pid}/documents', files={'file': ('纪要副本.txt', file_bytes, 'text/plain')})
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['existing_document_id'], first.json()['id'])
        self.assertEqual(self.doc_count(pid), 1)
        forced = self.client.post(f'/api/projects/{pid}/documents?force=1', files={'file': ('纪要副本.txt', file_bytes, 'text/plain')})
        self.assertEqual(forced.status_code, 201, forced.text)
        self.assertEqual(self.doc_count(pid), 2)

    def test_duplicate_scope_is_same_project_only(self):
        first_project = self.client.post('/api/projects', json={'name': '项目甲'}).json()
        second_project = self.client.post('/api/projects', json={'name': '项目乙'}).json()
        self.client.post(f'/api/projects/{first_project["id"]}/text-sources', json={'title': '原始材料', 'content': TEXT_A})
        other = self.client.post(f'/api/projects/{second_project["id"]}/text-sources', json={'title': '原始材料', 'content': TEXT_A})
        # 指纹范围限同项目：另一个项目同内容正常新建
        self.assertEqual(other.status_code, 201, other.text)


if __name__ == '__main__':
    unittest.main()

