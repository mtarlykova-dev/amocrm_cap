import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from amo_proxy import AmoProxyClient, AmoProxyError


class Handler(BaseHTTPRequestHandler):
    calls = []
    rate_limited_once = False

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if status == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).calls.append((self.path, self.headers.get("X-API-Key")))
        base = f"http://127.0.0.1:{self.server.server_port}/amo-api"
        if self.path.startswith("/amo-api/api/v4/leads/404"):
            self._send(404, {"title": "Not found", "detail": "Lead not found"})
        elif self.path.startswith("/amo-api/api/v4/leads/429"):
            if not type(self).rate_limited_once:
                type(self).rate_limited_once = True
                self._send(429, {"detail": "Too many requests"})
            else:
                self._send(200, {"id": 429})
        elif self.path == "/amo-api/api/v4/leads?limit=2":
            self._send(200, {
                "_embedded": {"leads": [{"id": 1}, {"id": 2}]},
                "_links": {"next": {"href": f"{base}/api/v4/leads?limit=2&page=2"}},
            })
        elif self.path == "/amo-api/api/v4/leads?limit=2&page=2":
            self._send(200, {"_embedded": {"leads": [{"id": 3}]}, "_links": {}})
        else:
            self._send(200, {"path": self.path})


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_port}/amo-api/"
        cls.client = AmoProxyClient(api_key="test-key", base_url=base, backoff=0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Handler.calls.clear()

    def test_path_prefix_and_header(self):
        self.assertEqual(self.client.get("leads/1")["path"], "/amo-api/api/v4/leads/1")
        self.assertEqual(self.client.get("/api/v4/account")["path"], "/amo-api/api/v4/account")
        self.assertEqual(Handler.calls[0][1], "test-key")

    def test_whitelist(self):
        with self.assertRaises(ValueError):
            self.client.get("webhooks")

    def test_refuses_foreign_url(self):
        with self.assertRaises(ValueError):
            self.client.get_url("https://example.com/api/v4/leads")

    def test_error_detail(self):
        with self.assertRaises(AmoProxyError) as ctx:
            self.client.get("leads/404")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("Lead not found", str(ctx.exception))

    def test_retries_on_429(self):
        self.assertEqual(self.client.get("leads/429"), {"id": 429})
        self.assertEqual(len(Handler.calls), 2)

    def test_pagination(self):
        ids = [lead["id"] for lead in self.client.iter_items("leads", {"limit": 2})]
        self.assertEqual(ids, [1, 2, 3])

    def test_missing_key(self):
        import os
        old = os.environ.pop("FOURTEENHUB_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                AmoProxyClient()
        finally:
            if old is not None:
                os.environ["FOURTEENHUB_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
