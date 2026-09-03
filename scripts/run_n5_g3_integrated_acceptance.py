#!/usr/bin/env python3
"""Run one isolated owner-acceptance session for the G3 pre-release batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "product/n5/course/g3-acceptance-runtime-v1/runtime"
LISTENING = ROOT / "product/n5/course/listening-v1"
MOCK = ROOT / "product/n5/course/mock-exam-v1"
READING = ROOT / "product/n5/course/reading-v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


LISTENING_PUBLIC = load_json(LISTENING / "public/units.json")
LISTENING_ASSESSMENT = {x["item_id"]: x for x in load_json(LISTENING / "assessment-data/answer-keys-and-audio-scripts.json")["items"]}
MOCK_PUBLIC = load_json(MOCK / "public/exam.json")
MOCK_ASSESSMENT = {x["item_id"]: x for x in load_json(MOCK / "assessment-data/answer-keys-and-audio-scripts.json")["items"]}

SAMPLE_IDS = [
    "n5-g3-listening-l01-practice-departure-v1",
    "n5-g3-listening-l02-practice-place-change-v1",
    "n5-g3-listening-l04-stage_check-luggage-help-v1",
    "n5-g3-listening-l05-stage_check-request-v1",
]
LISTENING_ITEMS = {
    item["item_id"]: item
    for unit in LISTENING_PUBLIC["units"]
    for item in unit["items"]
}


class State:
    def __init__(self, session_id: str, record: Path):
        self.session_id = session_id
        self.record = record
        if record.is_file():
            value = load_json(record)
            if value.get("session_id") != session_id:
                raise ValueError("stored G3 session identity mismatch")
            self.data = value
            return
        self.data = {
            "session_id": session_id,
            "stage": "overview",
            "action_token": secrets.token_urlsafe(16),
            "listening_index": 0,
            "listening_phase": "question",
            "listening_selected": None,
            "listening_results": [],
            "mock_section_index": 0,
            "mock_answers": {},
            "mock_submitted_sections": [],
            "mock_deadline_epoch": None,
            "mock_audio_plays": {},
            "technical_events": [],
            "created_at": int(time.time()),
        }
        self.save()

    def save(self):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.record.name}.", dir=self.record.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, self.record)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def rotate(self):
        self.data["action_token"] = secrets.token_urlsafe(16)
        self.save()

    def expire_if_needed(self):
        if self.data["stage"] != "mock_section":
            return
        deadline = self.data.get("mock_deadline_epoch")
        if deadline and time.time() >= deadline:
            section = MOCK_PUBLIC["sections"][self.data["mock_section_index"]]
            self.data["mock_submitted_sections"].append(section["section_id"])
            self.data["technical_events"].append({"kind": "section_time_expired", "section_id": section["section_id"]})
            self.data["mock_deadline_epoch"] = None
            self.data["stage"] = "mock_between_sections" if self.data["mock_section_index"] < 2 else "results"
            self.rotate()


def clean_public_item(item: dict, audio_owner: str | None = None) -> dict:
    value = dict(item)
    if audio_owner and value.get("audio_path"):
        value["audio_owner"] = audio_owner
        value.pop("audio_path", None)
    return value


def section_results(state: State) -> list[dict]:
    output = []
    for section in MOCK_PUBLIC["sections"]:
        correct = 0
        attempted = 0
        by_type = {}
        for item in section["items"]:
            answer = state.data["mock_answers"].get(item["item_id"])
            key = MOCK_ASSESSMENT[item["item_id"]]
            bucket = by_type.setdefault(item["item_type_id"], {"correct": 0, "attempted": 0, "total": 0})
            bucket["total"] += 1
            if answer:
                attempted += 1
                bucket["attempted"] += 1
                if answer == key["correct_option_id"]:
                    correct += 1
                    bucket["correct"] += 1
        output.append({
            "section_id": section["section_id"], "title_zh": section["title_zh"],
            "correct": correct, "attempted": attempted, "total": len(section["items"]),
            "item_types": by_type,
        })
    return output


def review_priorities(state: State) -> list[str]:
    values = []
    for item_id, key in MOCK_ASSESSMENT.items():
        if state.data["mock_answers"].get(item_id) != key["correct_option_id"]:
            values.extend(key.get("review_l2_object_ids", []))
    return list(dict.fromkeys(values))


def payload(state: State) -> dict:
    state.expire_if_needed()
    data = state.data
    base = {
        "session_id": state.session_id,
        "stage": data["stage"],
        "action_token": data["action_token"],
        "reading": {"unit_count": 4, "status": "owner_approved_non_active_product"},
        "listening": {"unit_count": 5, "item_count": 42, "sample_count": len(SAMPLE_IDS)},
        "mock": {"item_count": 28, "official_item_type_count": 14},
    }
    if data["stage"] == "listening_sample":
        item = LISTENING_ITEMS[SAMPLE_IDS[data["listening_index"]]]
        base.update({
            "sample_index": data["listening_index"], "sample_total": len(SAMPLE_IDS),
            "sample_phase": data["listening_phase"], "item": clean_public_item(item, "listening"),
        })
        if data["listening_phase"] == "feedback":
            key = LISTENING_ASSESSMENT[item["item_id"]]
            base["feedback"] = {
                "selected_option_id": data["listening_selected"],
                "correct_option_id": key["correct_option_id"],
                "correct": data["listening_selected"] == key["correct_option_id"],
                "explanation_zh": key["explanation_zh"],
            }
    elif data["stage"] == "mock_section":
        section = MOCK_PUBLIC["sections"][data["mock_section_index"]]
        base.update({
            "section_index": data["mock_section_index"],
            "section": {**{k:v for k,v in section.items() if k != "items"},
                        "items": [clean_public_item(i, "mock") for i in section["items"]]},
            "answers": {i["item_id"]: data["mock_answers"].get(i["item_id"]) for i in section["items"]},
            "deadline_epoch": data["mock_deadline_epoch"],
            "audio_plays": data["mock_audio_plays"],
        })
    elif data["stage"] == "mock_between_sections":
        next_section = MOCK_PUBLIC["sections"][data["mock_section_index"] + 1]
        base["next_section"] = {k:v for k,v in next_section.items() if k != "items"}
    elif data["stage"] == "results":
        base.update({
            "listening_sample_results": data["listening_results"],
            "section_results": section_results(state),
            "review_priority_l2_object_ids": review_priorities(state),
            "technical_events": data["technical_events"],
            "official_scaled_score": None,
            "jlpt_pass_decision": None,
            "mastery_decision": None,
        })
    return base


def result_payload(state: State) -> dict:
    if state.data["stage"] != "results":
        return {"schema_version": 1, "session_id": state.session_id, "status": "pending"}
    results = section_results(state)
    answered = sum(row["attempted"] for row in results) + len(state.data["listening_results"])
    correct = sum(row["correct"] for row in results) + sum(
        row["correct"] for row in state.data["listening_results"]
    )
    return {
        "schema_version": 1,
        "result_id": f"result:{state.session_id}",
        "session_id": state.session_id,
        "work_unit_id": "n5-g3-listening-and-integrated-mock-v1",
        "status": "completed",
        "completed_at": state.data["completed_at"],
        "evidence_summary": {
            "answered_count": answered,
            "correct_count": correct,
            "scoring_contract_id": "n5-g3-integrated-raw-evidence-v1",
            "session_mode": "assessment",
            "mastery_claim": "not_inferred",
            "review_priority_l2_object_ids": review_priorities(state),
        },
        "official_scaled_score": None,
        "jlpt_pass_decision": None,
        "mastery_decision": None,
    }


def audio_url(owner: str, item_id: str) -> str:
    if owner == "device":
        path = MOCK / "audio/n5-g3-device-check-v1.mp3"
        route = "/media/device/n5-g3-device-check-v1.mp3"
    elif owner == "listening":
        path = LISTENING / "audio" / f"{item_id}.mp3"
        route = f"/media/listening/{item_id}.mp3"
    else:
        path = MOCK / "audio" / f"{item_id}.mp3"
        route = f"/media/mock/{item_id}.mp3"
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"{route}?v={version}"


def apply_action(state: State, body: dict) -> dict:
    if body.get("action_token") != state.data["action_token"]:
        raise ValueError("页面状态已更新，请刷新后重试")
    action = body.get("action")
    d = state.data
    if action == "begin_listening" and d["stage"] == "overview":
        d["stage"] = "listening_sample"
    elif action == "submit_listening" and d["stage"] == "listening_sample" and d["listening_phase"] == "question":
        option = body.get("option_id")
        item = LISTENING_ITEMS[SAMPLE_IDS[d["listening_index"]]]
        if option not in {x["option_id"] for x in item["options"]}:
            raise ValueError("请选择一个答案")
        key = LISTENING_ASSESSMENT[item["item_id"]]
        d["listening_selected"] = option
        d["listening_phase"] = "feedback"
        d["listening_results"].append({"item_id":item["item_id"],"correct":option == key["correct_option_id"]})
    elif action == "next_listening" and d["stage"] == "listening_sample" and d["listening_phase"] == "feedback":
        if d["listening_index"] + 1 < len(SAMPLE_IDS):
            d["listening_index"] += 1
            d["listening_phase"] = "question"
            d["listening_selected"] = None
        else:
            d["stage"] = "mock_device"
    elif action == "request_audio":
        owner, item_id = body.get("owner"), body.get("item_id")
        allowed = False
        if owner == "device" and item_id == "n5-g3-device-check-v1":
            allowed = True
        elif owner == "listening" and item_id in SAMPLE_IDS:
            allowed = True
        elif owner == "mock" and d["stage"] == "mock_section":
            section_ids = {x["item_id"] for x in MOCK_PUBLIC["sections"][d["mock_section_index"]]["items"]}
            allowed = item_id in section_ids and item_id in MOCK_ASSESSMENT and "audio_segments" in MOCK_ASSESSMENT[item_id]
            if allowed and d["mock_audio_plays"].get(item_id, 0) >= 1:
                raise ValueError("模考听力每题只播放一次")
            if allowed:
                d["mock_audio_plays"][item_id] = d["mock_audio_plays"].get(item_id, 0) + 1
        if not allowed:
            raise ValueError("当前页面不能请求这段音频")
        state.rotate()
        return {"audio_url": audio_url(owner, item_id), "action_token": d["action_token"]}
    elif action == "confirm_device" and d["stage"] == "mock_device":
        d["stage"] = "mock_instructions"
    elif action == "start_section" and d["stage"] in {"mock_instructions", "mock_between_sections"}:
        if d["stage"] == "mock_between_sections":
            d["mock_section_index"] += 1
        section = MOCK_PUBLIC["sections"][d["mock_section_index"]]
        d["stage"] = "mock_section"
        d["mock_deadline_epoch"] = int(time.time()) + section["duration_minutes"] * 60
    elif action == "save_answer" and d["stage"] == "mock_section":
        section = MOCK_PUBLIC["sections"][d["mock_section_index"]]
        item = next((x for x in section["items"] if x["item_id"] == body.get("item_id")), None)
        if not item or body.get("option_id") not in {x["option_id"] for x in item["options"]}:
            raise ValueError("答案无效")
        d["mock_answers"][item["item_id"]] = body["option_id"]
    elif action == "submit_section" and d["stage"] == "mock_section":
        section = MOCK_PUBLIC["sections"][d["mock_section_index"]]
        d["mock_submitted_sections"].append(section["section_id"])
        d["mock_deadline_epoch"] = None
        d["stage"] = "mock_between_sections" if d["mock_section_index"] < 2 else "results"
        if d["stage"] == "results":
            d["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        raise ValueError("当前阶段不接受这个操作")
    state.rotate()
    return payload(state)


class Handler(BaseHTTPRequestHandler):
    state: State

    def send_json(self, value: object, status=HTTPStatus.OK):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def serve_file(self, path: Path):
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/health":
            self.send_json({"status": "ok", "session_id": self.state.session_id, "persistent_session_record": True}); return
        if route == "/api/session":
            self.send_json(payload(self.state)); return
        if route == "/api/result":
            self.send_json(result_payload(self.state)); return
        if route.startswith("/media/listening/"):
            self.serve_file(LISTENING / "audio" / unquote(route.rsplit("/",1)[1])); return
        if route.startswith("/media/mock/"):
            self.serve_file(MOCK / "audio" / unquote(route.rsplit("/",1)[1])); return
        if route.startswith("/media/device/"):
            self.serve_file(MOCK / "audio" / unquote(route.rsplit("/",1)[1])); return
        name = "index.html" if route in {"/", "/index.html"} else route.lstrip("/")
        target = (RUNTIME / name).resolve()
        if RUNTIME.resolve() not in target.parents and target != RUNTIME.resolve():
            self.send_error(HTTPStatus.FORBIDDEN); return
        self.serve_file(target)

    def do_POST(self):
        if urlparse(self.path).path != "/api/actions":
            self.send_error(HTTPStatus.NOT_FOUND); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            self.send_json(apply_action(self.state, body))
        except ValueError as error:
            self.send_json({"message":str(error)}, HTTPStatus.CONFLICT)
        except Exception as error:
            self.send_json({"message":f"操作失败：{error}"}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--session-record", type=Path, required=True)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local G3 server only binds to localhost")
    Handler.state = State(args.session_id, args.session_record)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}/?session_id={args.session_id}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
