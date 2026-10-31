"""Local OpenAI-format gateway with durable, conservative CNY reservations.

Only the gateway holds the provider key. Unknown/failed request cost is never
refunded, so retries and gateway restarts cannot bypass the session budget.
"""
import argparse
from copy import deepcopy
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.error
import urllib.request

MODEL = "deepseek-flash"
INPUT_CNY_PER_MILLION = 2
OUTPUT_CNY_PER_MILLION = 8
LIMIT_MICRO_CNY = 5_000_000


def prepare_request(body):
    if body.get("model") not in {MODEL, "gpt-4o-2024-11-20"} or body.get("stream") or body.get("n", 1) != 1:
        raise ValueError("model/stream/count not authorized")
    permitted = {"model", "messages", "temperature", "max_tokens", "max_completion_tokens", "tools", "tool_choice", "response_format", "stop", "top_p", "frequency_penalty", "presence_penalty", "seed", "n", "stream"}
    if set(body) - permitted:
        raise ValueError("unsupported request fields")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages required")
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 4096
    if type(max_tokens) is not int or not 1 <= max_tokens <= 16384:
        raise ValueError("output limit must be 1..16384")
    # UTF-8 bytes upper-bound text tokenization; images are <=1024 tokens each
    # per the provider's vision contract, rather than base64 text tokens.
    serialized = json.dumps(body, ensure_ascii=False).encode()
    if len(serialized) > 16_000_000:
        raise ValueError("request exceeds 16 MB boundary")
    billing_view = deepcopy(body)
    image_tokens = 0
    for message in billing_view["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url" and not part.get("image_url", {}).get("url", "").startswith("data:image/"):
                    raise ValueError("only inline image input is authorized")
                if part.get("type") == "image_url":
                    part["image_url"]["url"] = "inline-image"
                    image_tokens += 1024
    reservation = (len(json.dumps(billing_view, ensure_ascii=False).encode()) + image_tokens + 4096 * len(messages) + 8192) * INPUT_CNY_PER_MILLION + max_tokens * OUTPUT_CNY_PER_MILLION
    request = dict(body, model=MODEL, max_tokens=max_tokens, thinking={"type": "disabled"})
    request.pop("max_completion_tokens", None)
    request.pop("seed", None)
    request.pop("n", None)
    request.pop("stream", None)
    return request, reservation


class Ledger:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, reserved INTEGER NOT NULL, status TEXT NOT NULL, usage TEXT, created REAL NOT NULL)")
            if "initial_reserved" not in {r[1] for r in db.execute("PRAGMA table_info(requests)")}:
                db.execute("ALTER TABLE requests ADD COLUMN initial_reserved INTEGER")
                db.execute("UPDATE requests SET initial_reserved=reserved")
            # Settle known completed calls conservatively at peak price. Unknown
            # and failed calls keep their whole reservation across restarts.
            for row, usage in db.execute("SELECT id,usage FROM requests WHERE status='completed'").fetchall():
                data = json.loads(usage) if usage else None
                if data and type(data.get("prompt_tokens")) is int and type(data.get("completion_tokens")) is int:
                    amount = data["prompt_tokens"] * 2 + data["completion_tokens"] * 8
                    db.execute("UPDATE requests SET reserved=? WHERE id=?", (amount, row))

    def reserve(self, amount):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COALESCE(SUM(reserved),0) FROM requests").fetchone()[0]
            if amount <= 0 or used + amount > LIMIT_MICRO_CNY:
                raise ValueError("5 CNY session budget reservation limit reached")
            return db.execute("INSERT INTO requests(reserved,initial_reserved,status,created) VALUES(?,?,?,?)", (amount, amount, "reserved", time.time())).lastrowid

    def finish(self, row, status, usage=None):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET status=?,usage=? WHERE id=?", (status, json.dumps(usage), row))
            if status == "completed" and usage and type(usage.get("prompt_tokens")) is int and type(usage.get("completion_tokens")) is int:
                db.execute("UPDATE requests SET reserved=? WHERE id=?", (usage["prompt_tokens"] * 2 + usage["completion_tokens"] * 8, row))

    def summary(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,reserved,status,usage FROM requests ORDER BY id").fetchall()
        return {"budget_cny": 5, "reserved_upper_bound_cny": sum(r[1] for r in rows) / 1e6, "model": MODEL, "model_requests": len(rows), "requests": [{"id": r[0], "reserved_cny": r[1] / 1e6, "status": r[2], "usage": json.loads(r[3]) if r[3] else None} for r in rows]}


def serve(key_file, runtime, host="127.0.0.1", port=18765, workbench_data=None):
    runtime.mkdir(parents=True, exist_ok=True)
    loader = importlib.util.spec_from_file_location("key_loader", Path(__file__).resolve().parents[3] / "deploy/linux/probe-deepseek.py")
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    key = module.load_key(key_file)
    settings_path = runtime / "gateway.json"
    token = json.loads(settings_path.read_text())["token"] if settings_path.exists() else secrets.token_urlsafe(32)
    ledger = Ledger(runtime / "budget.sqlite")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            if self.path == "/budget":
                return self.reply(200, ledger.summary())
            if self.path in {"/models", "/v1/models"}:
                return self.reply(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
            self.reply(404, {"error": "unsupported path"})

        def do_POST(self):
            if self.path == "/v1/experiment/chat/completions" and workbench_data is not None:
                return self.experiment_request()
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            if self.path not in {"/chat/completions", "/v1/chat/completions"}:
                return self.reply(404, {"error": "unsupported path"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16_000_000:
                    raise ValueError("invalid request size")
                request, amount = prepare_request(json.loads(self.rfile.read(size)))
                row = ledger.reserve(amount)
            except (ValueError, TypeError, KeyError) as error:
                with (runtime / "rejections.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"time": time.time(), "reason": str(error)}) + "\n")
                return self.reply(402, {"error": {"message": "request rejected by schema or 5 CNY budget guard", "type": "budget_guard"}})
            try:
                upstream = urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(request).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(upstream, timeout=180) as response:
                    data = json.load(response)
                ledger.finish(row, "completed", data.get("usage"))
                self.reply(200, data)
            except urllib.error.HTTPError as error:
                ledger.finish(row, "upstream_http_" + str(error.code))
                self.reply(error.code, {"error": {"message": "provider rejected request", "type": "upstream_error"}})
            except (urllib.error.URLError, TimeoutError, ValueError):
                ledger.finish(row, "unknown_cost")
                self.reply(502, {"error": {"message": "provider connection failed; reservation retained", "type": "upstream_error"}})
            finally:
                (runtime / "usage.json").write_text(json.dumps(ledger.summary(), indent=2), encoding="utf-8")

        def experiment_request(self):
            from workbench.backend.services.experiment_budget import ExperimentBudgets
            budgets = ExperimentBudgets(workbench_data)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_048_576:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(size))
                call_id, request = budgets.reserve(self.headers.get("X-Experiment-Grant"), self.headers.get("X-Experiment-Task"),
                    self.headers.get("X-Experiment-Sample"), body, self.headers.get("Authorization", "").removeprefix("Bearer "))
            except (ValueError, TypeError, KeyError):
                return self.reply(402, {"error": "experiment contract or budget rejected"})
            try:
                upstream = urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(request).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(upstream, timeout=180) as response:
                    data = json.load(response)
                budgets.finish(call_id, data)
                self.reply(200, dict(data, workbench_request_id=call_id))
            except (urllib.error.URLError, TimeoutError, ValueError):
                budgets.finish(call_id)
                self.reply(502, {"error": "provider failure; full reservation retained", "workbench_request_id": call_id})

    server = ThreadingHTTPServer((host, port), Handler)
    # This is a limited local proxy credential, never the provider key.
    (runtime / "gateway.json").write_text(json.dumps({"pid": __import__("os").getpid(), "port": server.server_port, "token": token,
        "experiment_data": str(workbench_data.resolve()) if workbench_data is not None else None}), encoding="utf-8")
    print(json.dumps({"status": "ready", "host": host, "port": server.server_port, "budget_cny": 5}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        (runtime / "gateway.json").unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--runtime", type=Path, default=Path("workbench/data/model-session"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--workbench-data", type=Path)
    args = parser.parse_args()
    serve(args.key_file, args.runtime, args.host, args.port, args.workbench_data)
