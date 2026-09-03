#!/usr/bin/env python3
"""Serve the approved R01-R04 reading units from the packaged product tree."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
READING = ROOT / "product/n5/course/reading-v1"
RUNTIME = ROOT / "product/n5/course/reading-runtime-v1/runtime"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_BODY_BYTES = 20_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


PUBLIC_BY_WORK_UNIT: dict[str, dict] = {}
ANSWERS_BY_WORK_UNIT: dict[str, dict[str, dict]] = {}
for unit_code in ("r01", "r02", "r03", "r04"):
    public = read_json(READING / "public" / f"{unit_code}.json")
    assessment = read_json(READING / "assessment-data" / f"{unit_code}-answer-keys.json")
    work_unit_id = public["work_unit_id"]
    PUBLIC_BY_WORK_UNIT[work_unit_id] = public
    ANSWERS_BY_WORK_UNIT[work_unit_id] = {
        row["item_id"]: row for row in assessment["answer_keys"]
    }


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        if path.is_file():
            value = read_json(path)
            if value.get("schema_version") != 1 or not isinstance(value.get("sessions"), dict):
                raise ValueError("invalid reading session store")
            self.sessions = value["sessions"]
        else:
            self.sessions: dict[str, dict] = {}
            self.save()

    def save(self) -> None:
        write_json_atomic(self.path, {"schema_version": 1, "sessions": self.sessions})


def public_payload(session_id: str, session: dict) -> dict:
    content = PUBLIC_BY_WORK_UNIT[session["work_unit_id"]]
    answers = ANSWERS_BY_WORK_UNIT[session["work_unit_id"]]
    stage = session["stage"]
    teaching_count = len(content["teaching_blocks"])
    practice_count = len(content["practice_items"])
    total = teaching_count + practice_count
    if stage == "teaching":
        position = session["teaching_index"] + 1
        stage_label = "教学 · 建立阅读方法"
    elif stage in {"practice", "feedback"}:
        position = teaching_count + session["practice_index"] + 1
        stage_label = "练习 · 独立判断" if stage == "practice" else "反馈 · 核对读法"
    else:
        position = total
        stage_label = f"{content['unit_code']}学习完成"
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "work_unit_id": session["work_unit_id"],
        "unit_code": content["unit_code"],
        "status": session["status"],
        "stage": stage,
        "stage_label": stage_label,
        "progress_label": f"{position} / {total}",
        "progress_ratio": round(position / total * 100, 2),
        "teaching_index": session["teaching_index"],
        "teaching_count": teaching_count,
        "practice_index": session["practice_index"],
        "practice_count": practice_count,
        "action_token": session["action_token"],
        "content": {
            "unit_code": content["unit_code"],
            "display_title_zh": content["display_title_zh"],
            "learning_task_zh": content["learning_task_zh"],
            "steps": content["steps"],
        },
    }
    if stage == "teaching":
        payload["content"]["teaching"] = content["teaching_blocks"][session["teaching_index"]]
    elif stage in {"practice", "feedback"}:
        item = content["practice_items"][session["practice_index"]]
        payload["content"]["practice"] = item
        if stage == "feedback":
            answer = answers[item["item_id"]]
            selected = session["responses"][-1]["selected_option_id"]
            correct = selected == answer["correct_option_id"]
            explanation = answer["answer_rationale_zh"]
            if not correct:
                explanation = f"{answer['distractor_rationales_zh'][selected]} {explanation}"
            payload["feedback"] = {
                "selected_option_id": selected,
                "correct_option_id": answer["correct_option_id"],
                "correct": correct,
                "explanation_zh": explanation,
            }
    return payload


class Handler(BaseHTTPRequestHandler):
    store: SessionStore
    server_version = "N5ReadingProduct/1"

    def log_message(self, _fmt: str, *_args: object) -> None:
        return

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value: dict, status: int = 200) -> None:
        self.send_bytes((json.dumps(value, ensure_ascii=False) + "\n").encode(), "application/json; charset=utf-8", status)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("invalid request body length")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/health":
            self.send_json({"status": "ok", "reading_work_unit_count": 4, "persistent_session_store": True})
            return
        if path in {"/", "/index.html"}:
            session_id = parse_qs(parsed.query).get("session_id", [None])[0]
            if not session_id or session_id not in self.store.sessions:
                self.send_json({"status": "error", "message": "unknown session_id"}, 404)
                return
            self.send_bytes((RUNTIME / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self.send_bytes((RUNTIME / "styles.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self.send_bytes((RUNTIME / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        session_match = re.fullmatch(r"/api/practice-sessions/([^/]+)", path)
        if session_match:
            session_id = session_match.group(1)
            session = self.store.sessions.get(session_id)
            if not session:
                self.send_json({"status": "error", "message": "unknown session_id"}, 404)
                return
            self.send_json(public_payload(session_id, session))
            return
        result_match = re.fullmatch(r"/api/practice-results/([^/]+)", path)
        if result_match:
            session_id = result_match.group(1)
            session = self.store.sessions.get(session_id)
            if not session:
                self.send_json({"status": "error", "message": "unknown session_id"}, 404)
                return
            if session["status"] != "completed":
                self.send_json({"status": "pending", "session_id": session_id})
                return
            correct_count = sum(row["correct"] for row in session["responses"])
            self.send_json({
                "schema_version": 1,
                "result_id": f"result:{session_id}",
                "session_id": session_id,
                "work_unit_id": session["work_unit_id"],
                "status": "completed",
                "completed_at": session["completed_at"],
                "evidence_summary": {
                    "answered_count": len(session["responses"]),
                    "correct_count": correct_count,
                    "scoring_contract_id": "n5-g3-reading-raw-evidence-v1",
                    "session_mode": "assessment",
                    "mastery_claim": "not_inferred",
                },
            })
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            request = self.read_body()
            if path == "/api/practice-sessions":
                session_id = request.get("session_id", "")
                work_unit_id = request.get("work_unit_id", "")
                if not SESSION_ID_PATTERN.fullmatch(session_id):
                    raise ValueError("invalid session_id")
                if work_unit_id not in PUBLIC_BY_WORK_UNIT:
                    raise ValueError("unsupported work_unit_id")
                if session_id in self.store.sessions:
                    raise ValueError("session_id already exists")
                self.store.sessions[session_id] = {
                    "work_unit_id": work_unit_id,
                    "status": "in_progress",
                    "stage": "teaching",
                    "teaching_index": 0,
                    "practice_index": 0,
                    "action_token": secrets.token_urlsafe(18),
                    "responses": [],
                    "created_at": utc_now(),
                }
                self.store.save()
                self.send_json({
                    "schema_version": 1,
                    "session_id": session_id,
                    "practice_url": f"{self.origin()}/?session_id={session_id}",
                    "result_url": f"{self.origin()}/api/practice-results/{session_id}",
                    "status": "in_progress",
                }, 201)
                return
            match = re.fullmatch(r"/api/practice-sessions/([^/]+)/actions", path)
            if not match:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            session = self.store.sessions.get(match.group(1))
            if not session:
                raise ValueError("unknown session_id")
            if request.get("action_token") != session["action_token"]:
                raise ValueError("stale action token")
            content = PUBLIC_BY_WORK_UNIT[session["work_unit_id"]]
            answers = ANSWERS_BY_WORK_UNIT[session["work_unit_id"]]
            action = request.get("action")
            if session["stage"] == "teaching" and action == "next_teaching":
                if session["teaching_index"] + 1 >= len(content["teaching_blocks"]):
                    raise ValueError("no next teaching block")
                session["teaching_index"] += 1
            elif session["stage"] == "teaching" and action == "begin_practice":
                if session["teaching_index"] + 1 != len(content["teaching_blocks"]):
                    raise ValueError("teaching sequence is not complete")
                session["stage"] = "practice"
            elif session["stage"] == "practice" and action == "submit_answer":
                item = content["practice_items"][session["practice_index"]]
                option_id = request.get("option_id")
                if option_id not in {row["option_id"] for row in item["options"]}:
                    raise ValueError("invalid option_id")
                session["responses"].append({
                    "item_id": item["item_id"],
                    "selected_option_id": option_id,
                    "correct": option_id == answers[item["item_id"]]["correct_option_id"],
                    "submitted_at": utc_now(),
                })
                session["stage"] = "feedback"
            elif session["stage"] == "feedback" and action == "next_practice":
                if session["practice_index"] + 1 >= len(content["practice_items"]):
                    raise ValueError("no next practice item")
                session["practice_index"] += 1
                session["stage"] = "practice"
            elif session["stage"] == "feedback" and action == "finish":
                if session["practice_index"] + 1 != len(content["practice_items"]):
                    raise ValueError("practice sequence is not complete")
                session["stage"] = "completed"
                session["status"] = "completed"
                session["completed_at"] = utc_now()
            else:
                raise ValueError("action is not valid for current stage")
            session["action_token"] = secrets.token_urlsafe(18)
            self.store.save()
            self.send_json(public_payload(match.group(1), session))
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"status": "error", "message": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--session-store", type=Path, required=True)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local reading server only binds to localhost")
    Handler.store = SessionStore(args.session_store)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
