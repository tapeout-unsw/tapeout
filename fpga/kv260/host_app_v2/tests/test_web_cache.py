import io
import unittest
from types import SimpleNamespace

try:
    from tapeout_host.app import create_app
except ModuleNotFoundError:
    create_app = None


class Manager:
    def __init__(self):
        self.calls = []

    def create(self, mode, uploaded, timeout, bypass_mask):
        self.calls.append((mode, uploaded.filename, timeout, bypass_mask))
        return SimpleNamespace(run_id="test-id")


@unittest.skipIf(create_app is None, "Flask and Waitress are not installed")
class WebCacheTests(unittest.TestCase):
    def test_index_has_independent_cache_controls(self):
        client = create_app(Manager()).test_client()
        page = client.get("/").get_data(as_text=True)
        for label in ("Core 0 instruction", "Core 1 instruction", "Core 0 data", "Core 1 data"):
            self.assertIn(label, page)
        self.assertEqual(page.count('class="cache-bypass"'), 4)

    def test_uploaded_mask_reaches_manager(self):
        manager = Manager()
        client = create_app(manager).test_client()
        response = client.post("/api/runs", data={
            "mode": "binary", "timeout": "5", "bypass_mask": "10",
            "program": (io.BytesIO(b"\x13\0\0\0"), "example.bin"),
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(manager.calls, [("binary", "example.bin", 5.0, 10)])


if __name__ == "__main__":
    unittest.main()
