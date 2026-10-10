"""Integration regression tests for the PA800 Manager Web v0.2 merge."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "data" / "pa800_native_fixtures"

from pa_tool import container  # noqa: E402
from pa_tool.cli import main  # noqa: E402
from pa_tool.native.codec import encode_oc31_literal  # noqa: E402
from pa_tool.native.research import safe_rel  # noqa: E402
from pa_tool.native.web import Handler  # noqa: E402


class NativeIntegrationTests(unittest.TestCase):
    def test_bundled_codec_drives_generic_oc_command_path(self):
        raw = b"bounded built-in codec" * 128
        payload = encode_oc31_literal(raw)
        codec = container.load_codec()
        self.assertIsNotNone(codec)
        result = container.decode_oc_payload(payload, 0, codec)
        self.assertTrue(result.ok)
        self.assertEqual(result.decoded, raw)
        self.assertTrue(result.xor_match)
        self.assertEqual(result.trailer_bytes, 1)

    def test_native_bank_cli(self):
        code = main(["native-bank", str(FIXTURES / "BANK01.PRF"), "--resource", "1"])
        self.assertEqual(code, 0)

    def test_native_sound_cli(self):
        code = main(["native-sounds", "--find", "121.0.0", "--limit", "3"])
        self.assertEqual(code, 0)

    def test_research_path_guard(self):
        self.assertTrue(safe_rel("demo.SET/STYLE/BANK01.STY"))
        self.assertFalse(safe_rel("../escape.STY"))
        self.assertFalse(safe_rel("C:/escape.STY"))
        self.assertFalse(safe_rel("/absolute.STY"))


class NativeWebSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path: str) -> tuple[bytes, str]:
        with urlopen(self.base + path, timeout=10) as response:  # noqa: S310 - local test server
            return response.read(), response.headers.get_content_type()

    def test_health_and_status_routes(self):
        raw, content_type = self.get("/api/health")
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(raw)["mode"], "read-only")
        raw, _ = self.get("/api/status")
        status = json.loads(raw)
        self.assertEqual(status["soundCatalogCount"], 1003)
        self.assertFalse(status["writeSupported"])

    def test_static_ui_is_served(self):
        raw, content_type = self.get("/")
        self.assertEqual(content_type, "text/html")
        self.assertIn(b"PA800 Manager", raw)
        js, js_type = self.get("/app.js")
        self.assertIn(js_type, ("text/javascript", "application/javascript"))
        self.assertIn(b"Style Library", js)


if __name__ == "__main__":
    unittest.main()
