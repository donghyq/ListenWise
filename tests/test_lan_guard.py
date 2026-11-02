"""手机页 /m/* 守卫：仅本机模式与滑动窗口限流。"""
import time
import unittest
from collections import deque
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from tests import test_api as fixtures


def make_request(host):
    """构造仅带 client 地址的 Request，供仅本机判断函数做单元检查。"""
    scope = {"type": "http", "method": "GET", "path": "/m/some-token", "headers": [], "query_string": b""}
    if host is not None:
        scope["client"] = (host, 50000)
    return Request(scope)


class LanGuardUnitTest(unittest.TestCase):
    def test_enabled_never_blocks(self):
        import app.main as main
        self.assertFalse(main.mobile_lan_guard_blocked(make_request("192.168.1.5"), True))
        self.assertFalse(main.mobile_lan_guard_blocked(make_request("10.0.0.8"), True))

    def test_disabled_blocks_external_only(self):
        import app.main as main
        # 回环地址与测试客户端视为本机，不受限制
        self.assertFalse(main.mobile_lan_guard_blocked(make_request("127.0.0.1"), False))
        self.assertFalse(main.mobile_lan_guard_blocked(make_request("::1"), False))
        self.assertFalse(main.mobile_lan_guard_blocked(make_request("testclient"), False))
        # 外部 IP 一律拦截
        self.assertTrue(main.mobile_lan_guard_blocked(make_request("192.168.1.5"), False))
        self.assertTrue(main.mobile_lan_guard_blocked(make_request("10.0.0.8"), False))
        # 无 client 信息的连接按外部处理
        self.assertTrue(main.mobile_lan_guard_blocked(make_request(None), False))


class RateLimitUnitTest(unittest.TestCase):
    def setUp(self):
        import app.main as main
        main.MOBILE_RATE_HITS.clear()

    def test_window_counts_per_ip(self):
        import app.main as main
        with patch.object(main, "MOBILE_RATE_LIMIT_PER_MINUTE", 2):
            self.assertFalse(main.mobile_rate_limit_exceeded("10.0.0.8"))
            self.assertFalse(main.mobile_rate_limit_exceeded("10.0.0.8"))
            self.assertTrue(main.mobile_rate_limit_exceeded("10.0.0.8"))
            # 其他 IP 不受影响
            self.assertFalse(main.mobile_rate_limit_exceeded("10.0.0.9"))

    def test_loopback_gets_higher_limit(self):
        import app.main as main
        with patch.object(main, "MOBILE_RATE_LIMIT_LOOPBACK_PER_MINUTE", 3), patch.object(main, "MOBILE_RATE_LIMIT_PER_MINUTE", 1):
            self.assertFalse(main.mobile_rate_limit_exceeded("127.0.0.1"))
            self.assertFalse(main.mobile_rate_limit_exceeded("127.0.0.1"))
            self.assertFalse(main.mobile_rate_limit_exceeded("127.0.0.1"))
            self.assertTrue(main.mobile_rate_limit_exceeded("127.0.0.1"))
            # 外部 IP 使用更低的默认阈值
            self.assertFalse(main.mobile_rate_limit_exceeded("10.0.0.8"))
            self.assertTrue(main.mobile_rate_limit_exceeded("10.0.0.8"))

    def test_old_hits_expire_out_of_window(self):
        import app.main as main
        with patch.object(main, "MOBILE_RATE_LIMIT_PER_MINUTE", 1):
            main.MOBILE_RATE_HITS["10.0.0.8"] = deque([time.monotonic() - main.MOBILE_RATE_WINDOW_SECONDS - 1])
            self.assertFalse(main.mobile_rate_limit_exceeded("10.0.0.8"))


class LanGuardApiTest(unittest.TestCase):
    setUpClass = classmethod(fixtures.LawFlowApiTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LawFlowApiTest.tearDownClass.__func__)

    def setUp(self):
        self.main.MOBILE_RATE_HITS.clear()

    def make_share(self):
        project = self.client.post('/api/projects', json={'name': '局域网分享', 'verification_mode': 'source_only'}).json()
        share = self.client.post(f"/api/projects/{project['id']}/mobile-share").json()
        return share['token']

    def set_lan_share(self, enabled):
        response = self.client.put('/api/settings/provider', json={'lan_share_enabled': enabled})
        self.assertEqual(response.status_code, 200, response.text)

    def test_mobile_page_allowed_for_loopback_and_lan_when_enabled(self):
        token = self.make_share()
        response = self.client.get(f'/m/{token}')
        self.assertEqual(response.status_code, 200)
        lan_client = TestClient(self.main.app, client=("192.0.2.55", 1234))
        response = lan_client.get(f'/m/{token}')
        self.assertEqual(response.status_code, 200)

    def test_disabled_lan_share_blocks_external_but_not_loopback(self):
        token = self.make_share()
        try:
            self.set_lan_share(False)
            lan_client = TestClient(self.main.app, client=("192.0.2.55", 1234))
            self.assertEqual(lan_client.get(f'/m/{token}').status_code, 404)
            # 本机回环（含测试客户端）不受限
            self.assertEqual(self.client.get(f'/m/{token}').status_code, 200)
            # 数据接口同样被拦截
            self.assertEqual(lan_client.get(f'/m/{token}/data').status_code, 404)
        finally:
            self.set_lan_share(True)

    def test_rate_limit_returns_429_with_retry_after(self):
        token = self.make_share()
        lan_client = TestClient(self.main.app, client=("198.51.100.7", 1234))
        with patch.object(self.main, "MOBILE_RATE_LIMIT_PER_MINUTE", 2):
            self.assertEqual(lan_client.get(f'/m/{token}').status_code, 200)
            self.assertEqual(lan_client.get(f'/m/{token}').status_code, 200)
            limited = lan_client.get(f'/m/{token}')
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.headers.get('Retry-After'), '60')
        # 回环地址走放宽的阈值
        with patch.object(self.main, "MOBILE_RATE_LIMIT_LOOPBACK_PER_MINUTE", 2):
            self.assertEqual(self.client.get(f'/m/{token}').status_code, 200)
            self.assertEqual(self.client.get(f'/m/{token}').status_code, 200)
            self.assertEqual(self.client.get(f'/m/{token}').status_code, 429)


if __name__ == '__main__':
    unittest.main()

