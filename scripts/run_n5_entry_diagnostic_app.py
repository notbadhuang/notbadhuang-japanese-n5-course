#!/usr/bin/env python3
"""Serve the N5 entry diagnostic without exposing answer keys to the browser."""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from run_n5_entry_diagnostic_scoring_model import evaluate_session


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "product/n5/diagnostic/entry-v1/runtime"
SCORING_DIR = ROOT / "product/n5/diagnostic/scoring-v1"
MODEL = json.loads((SCORING_DIR / "model.json").read_text(encoding="utf-8"))
BANK = json.loads((SCORING_DIR / "item-bank-map.json").read_text(encoding="utf-8"))
MAX_BODY_BYTES = 1_000_000
STATIC_FILES = {
    "/": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/index.html": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (RUNTIME_DIR / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (RUNTIME_DIR / "app.js", "text/javascript; charset=utf-8"),
}
LANE_PRESENTATION = {
    "start_from_zero": {
        "title": "零基础起步",
        "short_title": "零基础",
        "description": "先稳定平假名、片假名和最基本的音拍识别，再进入词汇与句子。",
    },
    "foundation_repair": {
        "title": "假名与音拍修复",
        "short_title": "基础修复",
        "description": "你已经接触过日语，但假名或特殊音拍仍不稳定，先修复这一层会更省力。",
    },
    "core_language_build": {
        "title": "核心语言搭建",
        "short_title": "核心语言",
        "description": "你已经具备部分基础，接下来优先补齐词汇、汉字识别和基础语法连接。",
    },
    "receptive_integration": {
        "title": "阅读听力整合",
        "short_title": "输入整合",
        "description": "基础语言已经能够使用，下一步集中训练阅读线索与听力关键信息。",
    },
    "mock_readiness_candidate": {
        "title": "扩展画像或限时模考",
        "short_title": "模考候选",
        "description": "18个锚点均通过，可以继续做完整画像或独立限时模考；这仍不等于N5合格。",
    },
    "insufficient_evidence": {
        "title": "先解决测试条件",
        "short_title": "证据不足",
        "description": "当前证据不足以确定学习起点，请先解决音频或作答条件后继续。",
    },
}
ABILITY_DOMAIN_LABELS = {
    "FND": "基础文字",
    "VOC": "词汇",
    "KAN": "汉字",
    "GRA": "语法",
    "REA": "阅读",
    "LIS": "听力",
}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_items() -> dict[str, dict]:
    items: dict[str, dict] = {}
    for folder in ["foundation-v1", "text-v1", "listening-v1", "ordering-v1"]:
        path = ROOT / f"product/n5/diagnostic/{folder}/items.jsonl"
        for item in read_jsonl(path):
            item_id = item["diagnostic_item_id"]
            if item_id in items:
                raise ValueError(f"duplicate diagnostic item: {item_id}")
            items[item_id] = item
    if set(items) != set(BANK["item_to_ability"]):
        raise ValueError("formal item set does not match scoring item bank")
    return items


def load_asset_paths() -> dict[str, Path]:
    assets: dict[str, Path] = {}
    manifest_paths = [
        ROOT / "product/n5/diagnostic/foundation-v1/audio-manifest.json",
        ROOT / "product/n5/diagnostic/listening-v1/audio-manifest.json",
        ROOT / "product/n5/diagnostic/listening-v1/visual-manifest.json",
    ]
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("audio_assets", "visual_assets"):
            for asset in manifest.get(key, []):
                asset_id = asset.get("audio_asset_id") or asset.get("visual_asset_id")
                path = (ROOT / asset["path"]).resolve()
                if ROOT not in path.parents or not path.is_file():
                    raise ValueError(f"invalid asset path for {asset_id}")
                assets[asset_id] = path
    return assets


ITEMS = load_items()
ASSET_PATHS = load_asset_paths()
ABILITY_TITLES = {
    ability_id: assignment["title_zh"]
    for ability_id, assignment in BANK["item_assignments"].items()
}


def ability_domain(ability_id: str) -> str:
    code = ability_id.split("-")[1]
    return ABILITY_DOMAIN_LABELS.get(code, "日语能力")


def option_label(option: dict, index: int) -> str:
    for key in ("display_text_ja", "display_text_zh", "text_ja", "text_zh"):
        if option.get(key):
            return option[key]
    return f"{index + 1}番"


def sanitize_item(item: dict) -> dict:
    item_id = item["diagnostic_item_id"]
    ability_id = BANK["item_to_ability"][item_id]
    stimulus = item.get("stimulus_blueprint", {})
    safe_stimulus = {
        key: stimulus[key]
        for key in (
            "displayed_text_ja",
            "sentence_ja",
            "target_ja",
            "passage_ja",
            "material_type",
            "title_ja",
            "rows_ja",
            "preannounced_goal_zh",
            "instruction_ja",
            "fragments",
        )
        if key in stimulus
    }
    audio_asset_id = stimulus.get("audio_asset_id")
    if audio_asset_id:
        safe_stimulus["audio_url"] = f"/api/assets/{audio_asset_id}"
    visual_asset_id = stimulus.get("visual_asset_id")
    if visual_asset_id:
        safe_stimulus["visual_url"] = f"/api/assets/{visual_asset_id}"
    audio_option_ids = stimulus.get("option_audio_asset_ids", {})
    options = []
    for index, option in enumerate(item.get("options", [])):
        public_option = {
            "option_id": option["option_id"],
            "label": option_label(option, index),
        }
        if option["option_id"] in audio_option_ids:
            public_option["audio_url"] = (
                f"/api/assets/{audio_option_ids[option['option_id']]}"
            )
            public_option["label"] = f"音频 {option['option_id']}"
        options.append(public_option)
    return {
        "diagnostic_item_id": item_id,
        "asset_version_id": item["asset_version_id"],
        "ability_point_id": ability_id,
        "ability_title_zh": ABILITY_TITLES[ability_id],
        "domain_label_zh": ability_domain(ability_id),
        "prompt_zh": item["prompt_zh"],
        "response_mode": item["response_mode"],
        "modality": item.get("modality_contract") or stimulus.get("modality"),
        "maximum_plays_per_clip": item.get("maximum_plays_per_clip", 2),
        "stimulus": safe_stimulus,
        "options": options,
    }


def normalize_response(item: dict, response: dict) -> dict:
    item_id = item["diagnostic_item_id"]
    if response.get("invalid_reason"):
        return {
            "diagnostic_item_id": item_id,
            "asset_version_id": item["asset_version_id"],
            "outcome": "invalid",
            "invalid_reason": response["invalid_reason"],
        }
    if item["response_mode"] == "single_choice":
        actual = response.get("option_id")
        valid_ids = {option["option_id"] for option in item.get("options", [])}
        if actual not in valid_ids:
            raise ValueError(f"invalid option for {item_id}")
        expected = item.get("correct_option_id") or item["correct_response"]["option_id"]
        correct = actual == expected
    elif item["response_mode"] == "ordered_fragments":
        actual = response.get("fragment_order")
        fragments = item["stimulus_blueprint"]["fragments"]
        valid_ids = [fragment["fragment_id"] for fragment in fragments]
        if not isinstance(actual, list) or sorted(actual) != sorted(valid_ids):
            raise ValueError(f"invalid fragment order for {item_id}")
        correct = actual == item["correct_response"]["fragment_order"]
    else:
        raise ValueError(f"unsupported response mode for {item_id}")
    return {
        "diagnostic_item_id": item_id,
        "asset_version_id": item["asset_version_id"],
        "outcome": "correct" if correct else "incorrect",
    }


def response_events(payload: dict) -> list[dict]:
    responses = payload.get("responses", [])
    if not isinstance(responses, list) or len(responses) > 54:
        raise ValueError("responses must be a list with no more than 54 items")
    seen: set[str] = set()
    events = []
    for response in responses:
        item_id = response.get("diagnostic_item_id")
        if item_id not in ITEMS or item_id in seen:
            raise ValueError(f"unknown or duplicate diagnostic item: {item_id}")
        seen.add(item_id)
        events.append(normalize_response(ITEMS[item_id], response))
    return events


def result_payload(evaluation: dict, intake: dict, ended_reason: str | None) -> dict:
    lane = evaluation["recommended_start_lane"] or "insufficient_evidence"
    if ended_reason == "persistent_audio_playback_failure":
        lane = "insufficient_evidence"
    lane_info = LANE_PRESENTATION[lane]
    evidence_by_id = {
        row["ability_point_id"]: row for row in evaluation["ability_evidence"]
    }

    def describe(ids: list[str]) -> list[dict]:
        return [
            {
                "ability_point_id": ability_id,
                "title_zh": ABILITY_TITLES[ability_id],
                "domain_zh": ability_domain(ability_id),
                "status": evidence_by_id[ability_id]["status"],
            }
            for ability_id in ids
        ]

    return {
        "recommended_start_lane": lane,
        "recommended_start": lane_info,
        "screening_coverage": evaluation["screening_coverage"],
        "diagnostic_coverage": evaluation["diagnostic_coverage"],
        "confirmed_strengths": describe(evaluation["confirmed_strength_ability_ids"]),
        "priority_gaps": describe(evaluation["priority_gap_ability_ids"]),
        "deferred_or_unmeasured": describe(
            evaluation["deferred_or_unmeasured_ability_ids"]
        ),
        "evidence_quality_flags": evaluation["evidence_quality_flags"],
        "pass_confidence": evaluation["pass_confidence"],
        "planning_outlook": evaluation["planning_outlook"],
        "next_validation_action": evaluation["next_validation_action"],
        "intake": intake,
        "ended_reason": ended_reason,
    }


def advance(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    events = response_events(payload)
    session_id = str(payload.get("session_id") or f"n5-{secrets.token_hex(8)}")
    ended_reason = payload.get("ended_reason")
    if ended_reason not in {None, "persistent_audio_playback_failure"}:
        raise ValueError("unsupported ended_reason")
    session = {
        "session_id": session_id,
        "mode": "short_placement",
        "response_events": events,
    }
    evaluation = evaluate_session(session, model=MODEL, bank=BANK)
    if ended_reason or evaluation["recommended_start_lane"]:
        return {
            "status": "complete",
            "session_id": session_id,
            "answered_item_count": len(events),
            "result": result_payload(evaluation, payload.get("intake", {}), ended_reason),
        }
    next_ids = evaluation["short_screen_outcome"]["next_item_ids"]
    answered_ids = {event["diagnostic_item_id"] for event in events}
    next_item_id = next((item_id for item_id in next_ids if item_id not in answered_ids), None)
    if not next_item_id:
        raise ValueError("routing model returned no next item")
    item = sanitize_item(ITEMS[next_item_id])
    return {
        "status": "in_progress",
        "session_id": session_id,
        "answered_item_count": len(events),
        "progress": {
            "current_number": len(events) + 1,
            "minimum_item_count": MODEL["short_route_limits"]["minimum_valid_scored_items"],
            "normal_fast_path_item_count": MODEL["short_route_limits"]["normal_fast_path_items"],
            "maximum_item_count": MODEL["short_route_limits"]["maximum_scored_items"],
            "progress_percent": min(100, round(len(events) * 100 / 24)),
            "screening_coverage_percent": evaluation["screening_coverage"][
                "screening_coverage_percent"
            ],
        },
        "item": item,
    }


class DiagnosticHandler(BaseHTTPRequestHandler):
    server_version = "N5Diagnostic/1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200) -> None:
        self.send_bytes(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self.send_json({"status": "ok", "formal_item_count": len(ITEMS)})
            return
        if path == "/api/bootstrap":
            self.send_json(
                {
                    "schema_version": 1,
                    "product_name": "日语起点测试",
                    "formal_item_count": len(ITEMS),
                    "language_ability_count": len(MODEL["language_ability_order"]),
                    "short_route_limits": MODEL["short_route_limits"],
                    "device_preflight_audio_url": "/api/assets/n5-fnd-device-preflight-chime-product-v1",
                }
            )
            return
        if path.startswith("/api/assets/"):
            asset_id = path.removeprefix("/api/assets/")
            asset_path = ASSET_PATHS.get(asset_id)
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
            self.send_bytes(asset_path.read_bytes(), content_type)
            return
        static = STATIC_FILES.get(path)
        if static:
            file_path, content_type = static
            self.send_bytes(file_path.read_bytes(), content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/next":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("invalid request body length")
            payload = json.loads(self.rfile.read(length))
            self.send_json(advance(payload))
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"status": "error", "message": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local diagnostic server only binds to localhost")
    server = ThreadingHTTPServer((args.host, args.port), DiagnosticHandler)
    print(f"N5 entry diagnostic: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
