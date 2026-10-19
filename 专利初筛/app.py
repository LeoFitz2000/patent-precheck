"""Local material intake only. AI execution stays in the active agent conversation."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import secrets
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

APP_ID = "patent-material-intake-v1"
APP_DIR = Path(__file__).resolve().parent
MAX_FILES = 8
MAX_BYTES = 10 * 1024 * 1024
MAX_BODY = 15 * 1024 * 1024
GOALS = {"precheck": "判断是否值得继续", "search": "查找相近公开技术", "review": "核查已有初筛报告"}
EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def latest_case(data_dir):
    """Return transport status only; saved input is immutable, processing uses events."""
    files = sorted(data_dir.glob("案件-*/case.json"), reverse=True)
    if not files:
        return None
    try:
        case = read_json(files[0])
        phase = "ready"
        log = data_dir.parent / "运行记录" / "处理记录.jsonl"
        if log.exists():
            for row in log.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(row)
                    if event.get("case_id") == case["case_id"]:
                        phase = event.get("phase", phase)
                except (ValueError, TypeError):
                    continue
        return {"case_id": case["case_id"], "saved_at": case["saved_at"], "phase": phase}
    except (OSError, ValueError, KeyError):
        return None


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("材料格式不正确，请重新填写。")
    idea = payload.get("idea", "")
    if not isinstance(idea, str) or len(idea) > 30000:
        raise ValueError("技术描述须为文字，最多30000字。")
    idea = idea.strip()
    goal = payload.get("goal", "precheck")
    jurisdiction = payload.get("jurisdiction", "undecided")
    cutoff = payload.get("cutoff_date") or None
    if goal not in GOALS or jurisdiction not in {"undecided", "CN", "US", "other"}:
        raise ValueError("请选择页面中提供的用途和地区。")
    if cutoff:
        if not isinstance(cutoff, str):
            raise ValueError("资料截至日期格式不正确。")
        try:
            date.fromisoformat(cutoff)
        except ValueError as error:
            raise ValueError("资料截至日期格式不正确。") from error
    files = payload.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("每次最多8个附件。")
    prepared = []
    total = 0
    for index, item in enumerate(files, 1):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("data"), str):
            raise ValueError("附件格式不正确。")
        name = re.split(r"[\\/]", item["name"])[-1]
        name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
        suffix = Path(name).suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError("支持PDF、Word、文字和常见图片附件。")
        stem = Path(name).stem[:90].strip(" .") or "材料"
        name = f"{index:02d}-{stem}{suffix}"
        try:
            content = base64.b64decode(item["data"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("附件读取失败，请重新选择。") from error
        total += len(content)
        if not content or total > MAX_BYTES:
            raise ValueError("附件不能是空文件，合计最多10MB。")
        prepared.append((name, content))
    if not idea and not prepared:
        raise ValueError("请描述技术想法，或至少选择一份材料。")
    return idea, goal, jurisdiction, cutoff, prepared


def save_case(data_dir, payload):
    idea, goal, jurisdiction, cutoff, prepared = validate_payload(payload)
    case_id = "案件-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + secrets.token_hex(3)
    destination = data_dir / case_id
    destination.mkdir(parents=True)
    attachments = []
    for name, content in prepared:
        path = destination / name
        path.write_bytes(content)
        attachments.append({"name": name, "path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest().upper()})
    case = {"schema_version": "patent-intake-1", "case_id": case_id, "saved_at": timestamp(), "input_status": "ready", "idea": idea, "goal": goal, "goal_label": GOALS[goal], "jurisdiction": jurisdiction, "cutoff_date": cutoff, "files": attachments, "origin": "user_material_form", "ai_execution_started": False}
    # The manifest is written last; incomplete I/O never appears as a ready case.
    manifest = destination / "case.json"
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(case, stream, ensure_ascii=False, indent=2)
    return {"case_id": case_id, "saved_at": case["saved_at"], "phase": "ready", "message": "材料已保存。回到当前对话说“开始初筛”，AI会读取这份材料。"}


def create_server(port=51919, data_dir=None):
    data_dir = Path(data_dir) if data_dir else APP_DIR / "材料"
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            if isinstance(data, dict):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.local_request():
                return self.send(403, {"error": "请从本机入口打开。"})
            if self.path == "/":
                page = (APP_DIR / "页面.html").read_text(encoding="utf-8").replace("__INTAKE_TOKEN__", token)
                return self.send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self.send(200, {"app_id": APP_ID, "workspace": str(APP_DIR.parent), "latest": latest_case(data_dir)})
            self.send(404, {"error": "页面不存在。"})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.local_request() or self.headers.get("Origin") != origin or self.headers.get("X-Intake-Token") != token:
                return self.send(403, {"error": "请刷新本机材料页后再保存。"})
            if self.path != "/api/cases":
                return self.send(404, {"error": "页面不存在。"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    return self.send(413, {"error": "材料过大，附件合计最多10MB。"})
                payload = json.loads(self.rfile.read(size))
                self.send(201, save_case(data_dir, payload))
            except (ValueError, TypeError, KeyError):
                self.send(400, {"error": "材料格式不正确，或附件超过限制；请检查填写内容和文件。"})
            except OSError:
                self.send(500, {"error": "本地保存未完成，请保留页面内容并重试。"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=51919)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    try:
        with urlopen(url + "api/status", timeout=1) as response:
            existing = json.load(response)
        if existing.get("app_id") == APP_ID and existing.get("workspace") == str(APP_DIR.parent):
            if args.open:
                webbrowser.open(url)
            return
    except (URLError, OSError, ValueError):
        pass
    server = create_server(args.port, args.data_dir)
    if args.open:
        webbrowser.open(url)
    print(f"{APP_ID} {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
