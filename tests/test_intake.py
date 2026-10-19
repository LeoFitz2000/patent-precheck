"""Real local HTTP/file tests. Fixtures remain under ignored tests/.artifacts/."""
import base64
import hashlib
import importlib.util
import json
import re
import threading
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("patent_intake", ROOT / "专利初筛" / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class IntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        cls.data = ROOT / "tests" / ".artifacts" / run / "材料"
        cls.server = APP.create_server(0, cls.data)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        with urlopen(cls.origin, timeout=5) as response:
            html = response.read().decode("utf-8")
        cls.token = re.search(r'<script nonce="([^"]+)"', html)[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def request(self, path, payload=None, headers=None):
        request_headers = {} if payload is None else {"Content-Type": "application/json", "Origin": self.origin, "X-Intake-Token": self.token}
        request_headers.update(headers or {})
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            with urlopen(Request(self.origin + quote(path, safe="/"), data=body, headers=request_headers), timeout=5) as response:
                return response.status, response.read()
        except HTTPError as error:
            try:
                return error.code, error.read()
            finally:
                error.close()

    def save(self, payload):
        status, body = self.request("/api/cases", payload)
        self.assertEqual(status, 201, body)
        result = json.loads(body)
        path = self.data / result["case_id"]
        return json.loads((path / "case.json").read_text(encoding="utf-8")), path

    def test_empty_material_rejected(self):
        self.assertEqual(self.request("/api/cases", {"idea": "  ", "files": []})[0], 400)

    def test_text_round_trip_preserves_unknowns(self):
        case, _ = self.save({"idea": "构造测试：自动联动未定，不做实际专利检索。"})
        self.assertIn("自动联动未定", case["idea"])
        self.assertEqual(case["jurisdiction"], "undecided")
        self.assertIsNone(case["cutoff_date"])
        self.assertFalse(case["ai_execution_started"])

    def test_attachment_bytes_hash_and_confined_paths(self):
        content = "验证原始附件字节\n不执行模型。".encode("utf-8")
        files = [{"name": "../../材料.txt", "data": base64.b64encode(content).decode()}] * 2
        case, folder = self.save({"files": files})
        self.assertEqual(len({x["path"] for x in case["files"]}), 2)
        for attachment in case["files"]:
            path = folder / attachment["path"]
            self.assertEqual(path.resolve().parent, folder.resolve())
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(attachment["sha256"], hashlib.sha256(content).hexdigest().upper())

    def test_duplicate_submissions_create_new_cases(self):
        a, _ = self.save({"idea": "测试材料"})
        b, _ = self.save({"idea": "测试材料"})
        self.assertNotEqual(a["case_id"], b["case_id"])

    def test_foreign_origin_and_invalid_nonce_rejected(self):
        for headers in [{"Origin": "https://example.invalid"}, {"X-Intake-Token": ""}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/cases", {"idea": "测试"}, headers)[0], 403)

    def test_invalid_payloads_do_not_create_ready_cases(self):
        before = len(list(self.data.glob("案件-*/case.json")))
        payloads = [
            {"idea": "测试", "cutoff_date": "2026-99-99"},
            {"idea": "测试", "goal": "invalid"},
            {"files": [{"name": "x.txt", "data": "not-base64"}]},
            {"files": [{"name": "x.exe", "data": "YWJj"}]},
            {"files": [{"name": "x.txt", "data": ""}]},
            {"idea": "x" * 30001},
        ]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:80]):
                self.assertEqual(self.request("/api/cases", payload)[0], 400)
        self.assertEqual(len(list(self.data.glob("案件-*/case.json"))), before)

    def test_large_request_rejected_before_body_read(self):
        self.assertEqual(self.request("/api/cases", {}, {"Content-Length": str(APP.MAX_BODY + 1)})[0], 413)

    def test_source_and_arbitrary_paths_are_not_served(self):
        for path in ["/app.py", "/../AGENTS.md", "/材料/case.json"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_saved_case_and_processing_event_status(self):
        case, _ = self.save({"idea": "状态测试"})
        status, body = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["latest"]["case_id"], case["case_id"])
        log = self.data.parent / "运行记录" / "处理记录.jsonl"
        log.parent.mkdir(exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"case_id": case["case_id"], "phase": "completed"}) + "\n")
        self.assertEqual(json.loads(self.request("/api/status")[1])["latest"]["phase"], "completed")


if __name__ == "__main__":
    unittest.main()
