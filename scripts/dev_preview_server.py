"""Lightweight stdlib preview server for the web UI (development only).

Drives the real filesystem-backed WebRepository (no ASGI server required) so the
built SPA can be exercised against real case/run/doc data during UI work.

    PYTHONPATH=src .runtime-env/python.exe scripts/dev_preview_server.py \
        --data-root <repo-with-results> --dist web/ui/dist --port 18488
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thorium_reactor.web.repository import WebRepository  # noqa: E402
from thorium_reactor.web.schemas import model_to_dict  # noqa: E402

DIST: Path
REPO: WebRepository
DATA_ROOT: Path


def dump(obj):
    if isinstance(obj, list):
        return [model_to_dict(item) if hasattr(item, "model_dump") else item for item in obj]
    return model_to_dict(obj) if hasattr(obj, "model_dump") else obj


def fake_session():
    return {
        "email": "engineer@thorium.lab",
        "is_admin": True,
        "admin_emails": ["engineer@thorium.lab"],
        "daily_run_limit": 5,
        "runs_started_today": 1,
        "runs_remaining_today": 4,
        "rate_limit_date": "2026-07-07",
        "resets_at": "2026-07-08T00:00:00Z",
        "can_start_run": True,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, status=200):
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        parts = [p for p in path.split("/") if p]
        try:
            if len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "validate-draft":
                resp = REPO.validate_draft(parts[2], draft_yaml=body.get("draft_yaml"), patch=body.get("patch", {}))
                return self._send_json(dump(resp))
            if parts == ["api", "runs"]:
                return self._send_json({"detail": "Run launching is disabled in preview mode."}, status=400)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"detail": str(exc)}, status=400)
        self._send_json({"detail": "Not found"}, status=404)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        try:
            if path == "/api/health":
                return self._send_json({"status": "ok", "repo_root": str(DATA_ROOT)})
            if path == "/api/me":
                return self._send_json(fake_session())
            if path == "/api/cases":
                return self._send_json(dump(REPO.list_cases()))
            if len(parts) == 3 and parts[:2] == ["api", "cases"]:
                return self._send_json(dump(REPO.get_case(parts[2])))
            if path == "/api/runs":
                return self._send_json(dump(REPO.list_runs()))
            if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                return self._send_json(dump(REPO.get_run(parts[2], parts[3])))
            if len(parts) >= 6 and parts[:2] == ["api", "runs"] and parts[4] == "artifacts":
                artifact_path = "/".join(parts[5:])
                resolved = REPO.resolve_artifact_path(parts[2], parts[3], artifact_path)
                return self._send_file(resolved)
            if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[4] == "events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                return
            if path == "/api/docs":
                return self._send_json(dump(REPO.list_docs()))
            if len(parts) == 3 and parts[:2] == ["api", "docs"]:
                return self._send_json(dump(REPO.get_doc(parts[2])))
            if path == "/api/admin/rate-limits":
                return self._send_json([])
        except FileNotFoundError as exc:
            return self._send_json({"detail": str(exc)}, status=404)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"detail": str(exc)}, status=400)

        candidate = (DIST / path.lstrip("/")).resolve()
        if candidate.is_file() and str(candidate).startswith(str(DIST.resolve())):
            return self._send_file(candidate)
        index = DIST / "index.html"
        if index.is_file():
            return self._send_file(index)
        self._send_json({"detail": "dist not built"}, status=404)


def main():
    global DIST, REPO, DATA_ROOT
    here = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(here), help="repo root that contains configs/, results/, docs/")
    ap.add_argument("--dist", default=str(here / "web" / "ui" / "dist"))
    ap.add_argument("--port", type=int, default=18488)
    args = ap.parse_args()
    DATA_ROOT = Path(args.data_root).resolve()
    DIST = Path(args.dist).resolve()
    REPO = WebRepository(DATA_ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Preview server on http://127.0.0.1:{args.port} (data_root={DATA_ROOT}, dist={DIST})")
    server.serve_forever()


if __name__ == "__main__":
    main()
