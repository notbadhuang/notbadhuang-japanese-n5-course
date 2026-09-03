#!/usr/bin/env python3
"""Serve formally active N5 core and foundation contracts through one origin."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "product/n5/course/local-player-v2"
ACTIVE_CATALOG_PATH = Path(
    os.environ.get("N5_MULTI_CONTRACT_CATALOG_PATH", str(PACKAGE / "active-catalog.json"))
).resolve()
CATALOG = json.loads(ACTIVE_CATALOG_PATH.read_text(encoding="utf-8"))
if CATALOG.get("status") != "active":
    raise ValueError("multi-contract launcher requires an active catalog")

FAMILIES = {row["runtime_family_id"]: row for row in CATALOG["runtime_families"]}
os.environ["N5_LOCAL_PLAYER_CATALOG_PATH"] = str(
    ROOT / FAMILIES["core_grouped_learning"]["child_catalog"]
)
os.environ["N5_FOUNDATION_PLAYER_CATALOG_PATH"] = str(
    ROOT / FAMILIES["foundation_learning"]["child_catalog"]
)

import run_n5_core_learning_app as core  # noqa: E402
import run_n5_foundation_learning_app as foundation  # noqa: E402


MAX_BODY_BYTES = 100_000
BACKENDS = {
    "core_grouped_learning": core,
    "foundation_learning": foundation,
}
ACTIVE_ENTRIES = {
    row["work_unit_id"]: row
    for row in CATALOG["work_units"]
    if row.get("status") == "active"
}
if CATALOG.get("active_work_unit_count") != len(ACTIVE_ENTRIES):
    raise ValueError("multi-contract active work unit count mismatch")
if CATALOG.get("default_work_unit_id") not in ACTIVE_ENTRIES:
    raise ValueError("multi-contract default work unit is not active")
for work_unit_id, entry in ACTIVE_ENTRIES.items():
    backend = BACKENDS[entry["runtime_family_id"]]
    if work_unit_id not in backend.UNIT_CONTEXTS:
        raise ValueError(f"active unit is not loadable by its runtime family: {work_unit_id}")

CORE_ASSETS = set(core.AUDIO_ASSET_CONTEXTS)
FOUNDATION_ASSETS = set(foundation.ALL_AUDIO_ASSETS)
SHARED_ASSETS = CORE_ASSETS & FOUNDATION_ASSETS
for asset_id in SHARED_ASSETS:
    core_context = core.AUDIO_ASSET_CONTEXTS[asset_id]
    core_path = core.project_path(core_context.audio_assets[asset_id]["path"])
    foundation_path = foundation.project_path(foundation.ALL_AUDIO_ASSETS[asset_id]["path"])
    if core_path != foundation_path:
        raise ValueError(f"shared audio asset id resolves to different files: {asset_id}")


def backend_for_work_unit(work_unit_id: str):
    entry = ACTIVE_ENTRIES.get(work_unit_id)
    if not entry:
        raise ValueError("requested work unit is not active")
    return BACKENDS[entry["runtime_family_id"]]


def backend_for_session(session_id: str):
    matches = [backend for backend in BACKENDS.values() if session_id in backend.SESSIONS.sessions]
    if len(matches) != 1:
        raise ValueError("unknown or ambiguous session_id")
    return matches[0]


def runtime_family_for_backend(backend) -> str:
    return "core" if backend is core else "foundation"


def runtime_html(backend) -> bytes:
    source = backend.STATIC_FILES["/"][0].read_text(encoding="utf-8")
    family = runtime_family_for_backend(backend)
    source = source.replace('href="/styles.css', f'href="/runtime/{family}/styles.css')
    source = source.replace('src="/app.js', f'src="/runtime/{family}/app.js')
    return source.encode("utf-8")


class MultiContractLearningHandler(BaseHTTPRequestHandler):
    server_version = "N5MultiContractLearning/1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200) -> None:
        self.send_bytes(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("invalid request body length")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def serve_audio(self, asset_id: str) -> bool:
        if asset_id in CORE_ASSETS:
            context = core.AUDIO_ASSET_CONTEXTS[asset_id]
            asset = context.audio_assets[asset_id]
            path = core.project_path(asset["path"])
        elif asset_id in FOUNDATION_ASSETS:
            asset = foundation.ALL_AUDIO_ASSETS[asset_id]
            path = foundation.project_path(asset["path"])
        else:
            return False
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), content_type)
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/health":
            family_counts = Counter(row["runtime_family_id"] for row in ACTIVE_ENTRIES.values())
            self.send_json(
                {
                    "status": "ok",
                    "catalog_id": CATALOG["catalog_id"],
                    "active_work_unit_count": len(ACTIVE_ENTRIES),
                    "active_unit_codes": [row["unit_code"] for row in CATALOG["work_units"]],
                    "runtime_family_counts": dict(sorted(family_counts.items())),
                    "shared_audio_asset_count": len(SHARED_ASSETS),
                    "default_work_unit_id": CATALOG["default_work_unit_id"],
                    "persistent_session_store": any(
                        backend.SESSIONS.store_path is not None for backend in BACKENDS.values()
                    ),
                }
            )
            return
        if path in {"/", "/index.html"}:
            session_id = parse_qs(parsed.query).get("session_id", [None])[0]
            try:
                backend = (
                    backend_for_session(session_id)
                    if session_id
                    else backend_for_work_unit(CATALOG["default_work_unit_id"])
                )
                self.send_bytes(runtime_html(backend), "text/html; charset=utf-8")
            except ValueError as error:
                self.send_json({"status": "error", "message": str(error)}, 404)
            return
        static_match = re.fullmatch(r"/runtime/(core|foundation)/(styles\.css|app\.js)", path)
        if static_match:
            family, filename = static_match.groups()
            backend = core if family == "core" else foundation
            content_type = (
                "text/css; charset=utf-8"
                if filename == "styles.css"
                else "text/javascript; charset=utf-8"
            )
            self.send_bytes((backend.RUNTIME_DIR / filename).read_bytes(), content_type)
            return
        if path.startswith("/api/assets/"):
            if not self.serve_audio(path.removeprefix("/api/assets/")):
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/repair-assets/"):
            asset_path = foundation.ALL_REPAIR_VISUAL_PATHS.get(
                path.removeprefix("/api/repair-assets/")
            )
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self.send_bytes(
                    asset_path.read_bytes(), mimetypes.guess_type(asset_path.name)[0] or "image/png"
                )
            return
        if path.startswith("/api/batch-articulation-assets/"):
            asset_path = foundation.ALL_BATCH_ARTICULATION_PATHS.get(
                path.removeprefix("/api/batch-articulation-assets/")
            )
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self.send_bytes(
                    asset_path.read_bytes(), mimetypes.guess_type(asset_path.name)[0] or "image/png"
                )
            return
        single_match = re.fullmatch(
            r"/api/single-kana-assets/([^/]+)/(mouth|example-audio)", path
        )
        if single_match:
            target_id, asset_kind = single_match.groups()
            registry = (
                foundation.ALL_SINGLE_KANA_MOUTH_PATHS
                if asset_kind == "mouth"
                else foundation.ALL_SINGLE_KANA_EXAMPLE_AUDIO_PATHS
            )
            asset_path = registry.get(target_id)
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self.send_bytes(
                    asset_path.read_bytes(),
                    mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream",
                )
            return
        session_match = re.fullmatch(r"/api/practice-sessions/([^/]+)", path)
        result_match = re.fullmatch(r"/api/practice-results/([^/]+)", path)
        try:
            if session_match:
                session_id = session_match.group(1)
                backend = backend_for_session(session_id)
                self.send_json(backend.stage_payload(session_id, backend.SESSIONS.get(session_id)))
                return
            if result_match:
                session_id = result_match.group(1)
                backend = backend_for_session(session_id)
                session = backend.SESSIONS.get(session_id)
                if session["status"] != "completed":
                    self.send_json({"status": "pending", "session_id": session_id}, 409)
                    return
                self.send_json(backend.result_payload(session_id, session))
                return
        except ValueError as error:
            self.send_json({"status": "error", "message": str(error)}, 404)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            payload = self.read_json_body()
            if path == "/api/practice-sessions":
                backend = backend_for_work_unit(payload.get("work_unit_id"))
                session = backend.SESSIONS.create(payload)
                session_id = payload["session_id"]
                origin = self.origin()
                self.send_json(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "practice_url": f"{origin}/?session_id={session_id}",
                        "result_url": f"{origin}/api/practice-results/{session_id}",
                        "status": session["status"],
                    }
                )
                return
            action_match = re.fullmatch(r"/api/practice-sessions/([^/]+)/actions", path)
            if action_match:
                session_id = action_match.group(1)
                backend = backend_for_session(session_id)
                response = backend.apply_action(
                    session_id, backend.SESSIONS.get(session_id), payload
                )
                backend.SESSIONS.persist()
                self.send_json(response)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"status": "error", "message": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--session-store-dir", type=Path)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local learning server only binds to localhost")
    if args.session_store_dir:
        store_dir = args.session_store_dir.resolve()
        store_dir.mkdir(parents=True, exist_ok=True)
        core.SESSIONS.configure_store(store_dir / "core-sessions.json")
        foundation.SESSIONS.configure_store(store_dir / "foundation-sessions.json")
    else:
        core.SESSIONS.configure_store(None)
        foundation.SESSIONS.configure_store(None)
    server = ThreadingHTTPServer((args.host, args.port), MultiContractLearningHandler)
    print(
        f"N5 multi-contract local player ({len(ACTIVE_ENTRIES)} active units): "
        f"http://{args.host}:{server.server_address[1]}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
