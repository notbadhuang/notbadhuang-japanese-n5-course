#!/usr/bin/env python3
"""Serve active N5 local-player units without exposing answer keys to the browser."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from n5_core_runtime_context import RuntimeContext, load_runtime_context


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "product/n5/course/core-runtime-v1/runtime"
LOCAL_PLAYER_DIR = ROOT / "product/n5/course/local-player-v1"
ACTIVE_CATALOG_PATH = Path(
    os.environ.get(
        "N5_LOCAL_PLAYER_CATALOG_PATH",
        str(LOCAL_PLAYER_DIR / "active-catalog.json"),
    )
).resolve()
MAX_BODY_BYTES = 100_000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
SESSION_MODES = {"new_learning", "delayed_review", "mixed"}
STATIC_FILES = {
    "/": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/index.html": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (RUNTIME_DIR / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (RUNTIME_DIR / "app.js", "text/javascript; charset=utf-8"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def project_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if ROOT not in path.parents or not path.is_file():
        raise ValueError(f"runtime contract path is missing or outside the project: {relative}")
    return path


ACTIVE_CATALOG = json.loads(ACTIVE_CATALOG_PATH.read_text(encoding="utf-8"))
ACTIVE_UNIT_ENTRIES = {
    row["work_unit_id"]: row
    for row in ACTIVE_CATALOG["work_units"]
    if row.get("status") in {"active", "validation_candidate"}
}
FORMALLY_ACTIVE_UNIT_ENTRIES = {
    work_unit_id: row
    for work_unit_id, row in ACTIVE_UNIT_ENTRIES.items()
    if row.get("status") == "active"
}
if ACTIVE_CATALOG.get("active_work_unit_count") != len(FORMALLY_ACTIVE_UNIT_ENTRIES):
    raise ValueError("active local-player catalog count mismatch")
DEFAULT_WORK_UNIT_ID = ACTIVE_CATALOG.get("default_work_unit_id")
if DEFAULT_WORK_UNIT_ID not in ACTIVE_UNIT_ENTRIES:
    raise ValueError("default local-player work unit is not loadable")

UNIT_CONTEXTS: dict[str, RuntimeContext] = {}
for work_unit_id, entry in ACTIVE_UNIT_ENTRIES.items():
    context = load_runtime_context(ROOT, project_path(entry["runtime_definition"]))
    if context.work_unit_id != work_unit_id:
        raise ValueError("catalog entry and runtime definition work_unit_id mismatch")
    UNIT_CONTEXTS[work_unit_id] = context

DEFAULT_CONTEXT = UNIT_CONTEXTS[DEFAULT_WORK_UNIT_ID]
WORK_UNIT_ID = DEFAULT_CONTEXT.work_unit_id
UNIT_CODE = DEFAULT_CONTEXT.unit_code
DISPLAY_TITLE_ZH = DEFAULT_CONTEXT.display_title_zh
SCORING_CONTRACT_ID = DEFAULT_CONTEXT.scoring_contract_id
RUNTIME_DEFINITION = DEFAULT_CONTEXT.definition
UNIT = DEFAULT_CONTEXT.unit
TEACHING_CARDS = DEFAULT_CONTEXT.teaching_cards
GUIDED_ITEMS = DEFAULT_CONTEXT.guided_items
CHECKPOINTS = DEFAULT_CONTEXT.checkpoints
ANSWER_KEYS = DEFAULT_CONTEXT.answer_keys
AUDIO_ASSETS = DEFAULT_CONTEXT.audio_assets
AUDIO_BY_CONSUMER = DEFAULT_CONTEXT.audio_by_consumer
AUDIO_BINDINGS = read_jsonl(
    project_path(DEFAULT_CONTEXT.definition["runtime_overlays"]["audio_bindings"])
)
GRAMMAR_TEACHING_COPY = DEFAULT_CONTEXT.grammar_teaching_copy
GUIDED_PRACTICE_OVERRIDES = DEFAULT_CONTEXT.guided_practice_overrides
INITIAL_CHECKPOINT_OVERRIDES = DEFAULT_CONTEXT.initial_checkpoint_overrides
RECOVERY_ITEMS = DEFAULT_CONTEXT.recovery_items
ROUTE_COPY = DEFAULT_CONTEXT.route_copy
LEARNING_GROUPS = DEFAULT_CONTEXT.learning_groups
TEACHING_BY_ID = DEFAULT_CONTEXT.teaching_by_id
GUIDED_BY_ID = DEFAULT_CONTEXT.guided_by_id
CHECKPOINT_BY_ID = DEFAULT_CONTEXT.checkpoint_by_id
RECOVERY_BY_ID = DEFAULT_CONTEXT.recovery_by_id
RECOVERY_BY_TARGET = DEFAULT_CONTEXT.recovery_by_target
GUIDED_ORDER = DEFAULT_CONTEXT.guided_order
CHECKPOINT_ORDER = DEFAULT_CONTEXT.checkpoint_order
TEACHING_BY_TARGET = DEFAULT_CONTEXT.teaching_by_target
GUIDED_BY_TARGET = DEFAULT_CONTEXT.guided_by_target
CHECKPOINT_BY_TARGET = DEFAULT_CONTEXT.checkpoint_by_target
TARGET_ORDER = DEFAULT_CONTEXT.target_order
TEACHING_ORDER = DEFAULT_CONTEXT.teaching_order
GROUP_START_INDEXES = DEFAULT_CONTEXT.group_start_indexes
GROUP_BY_TARGET = DEFAULT_CONTEXT.group_by_target

TARGET_CONTEXTS: dict[str, RuntimeContext] = {}
AUDIO_ASSET_CONTEXTS: dict[str, RuntimeContext] = {}
for context in UNIT_CONTEXTS.values():
    for target_id in context.target_order:
        if target_id in TARGET_CONTEXTS:
            raise ValueError(f"target appears in more than one loaded work unit: {target_id}")
        TARGET_CONTEXTS[target_id] = context
    for audio_id in context.audio_assets:
        if audio_id in AUDIO_ASSET_CONTEXTS:
            existing = AUDIO_ASSET_CONTEXTS[audio_id].audio_assets[audio_id]
            current = context.audio_assets[audio_id]
            if existing.get("path") != current.get("path") or existing.get("sha256") != current.get("sha256"):
                raise ValueError(f"shared audio asset metadata conflicts across work units: {audio_id}")
            continue
        AUDIO_ASSET_CONTEXTS[audio_id] = context


def learning_context(session: dict) -> RuntimeContext:
    request = session["request"]
    work_unit_id = request.get("new_work_unit_id") or request["work_unit_id"]
    return UNIT_CONTEXTS[work_unit_id]


def review_context(target_id: str) -> RuntimeContext:
    return TARGET_CONTEXTS[target_id]


def public_audio_url(context: RuntimeContext, consumer_id: str) -> str | None:
    audio_id = context.audio_by_consumer.get(consumer_id)
    return f"/api/assets/{audio_id}" if audio_id else None


def sanitize_teaching_card(context: RuntimeContext | dict, card: dict | None = None) -> dict:
    if card is None:
        card = context
        context = DEFAULT_CONTEXT
    public = {
        "teaching_card_id": card["teaching_card_id"],
        "target_kind": card["target_kind"],
        "primary_target_id": card["primary_target_id"],
        "audio_url": public_audio_url(context, card["teaching_card_id"]),
    }
    if card["target_kind"] == "vocabulary":
        public.update(
            {
                "form": card.get("form", card.get("primary_form")),
                "reading": card.get("reading", card.get("primary_reading")),
                "course_meaning_labels_zh": card["course_meaning_labels_zh"],
                "explanation_zh": card["explanation_zh"],
                "teaching_context": card["teaching_context"],
                "word_audio_url": public_audio_url(
                    context, f"{card['teaching_card_id']}:isolated_word"
                ),
            }
        )
    else:
        public.update(
            {
                "title_zh": context.grammar_teaching_copy[card["teaching_card_id"]]["title_zh"],
                "six_slots": card["six_slots"],
                "learner_copy": context.grammar_teaching_copy[card["teaching_card_id"]],
            }
        )
    return public


def sanitize_guided_item(context: RuntimeContext | dict, item: dict | None = None) -> dict:
    if item is None:
        item = context
        context = DEFAULT_CONTEXT
    override = context.guided_practice_overrides.get(item["practice_item_id"], {})
    return {
        "practice_item_id": item["practice_item_id"],
        "primary_target_id": item["primary_target_id"],
        "item_type": item["item_type"],
        "context_ja": item.get("context_ja"),
        "prompt_ja": override.get("prompt_ja", item.get("prompt_ja")),
        "prompt_zh": item.get("prompt_zh"),
        "options": override.get("options", item["options"]),
        "support_markers": item.get("support_markers", []),
        "audio_url": (
            public_audio_url(context, item["practice_item_id"])
            if item.get("context_ja")
            else None
        ),
    }


def sanitize_checkpoint(context: RuntimeContext | dict, item: dict | None = None) -> dict:
    if item is None:
        item = context
        context = DEFAULT_CONTEXT
    override = context.initial_checkpoint_overrides[item["checkpoint_item_id"]]
    return {
        "checkpoint_item_id": item["checkpoint_item_id"],
        "primary_target_id": item["primary_target_id"],
        "item_type": item["item_type"],
        "prompt_zh": override.get("prompt_zh", item.get("prompt_zh")),
        "prompt_ja": override.get("prompt_ja", item.get("prompt_ja")),
        "options": override["options"],
        "support_markers": item.get("support_markers", []),
    }


def sanitize_recovery_item(item: dict) -> dict:
    return {
        "verification_item_id": item["verification_item_id"],
        "primary_target_id": item["primary_target_id"],
        "prompt_zh": item.get("prompt_zh"),
        "prompt_ja": item.get("prompt_ja"),
        "options": item["options"],
    }


def sanitize_meaning_context(context: RuntimeContext, item: dict) -> dict:
    return {
        "meaning_context_id": item["meaning_context_id"],
        "course_meaning_label_zh": item["course_meaning_label_zh"],
        "ja": item["ja"],
        "zh": item["zh"],
        "audio_url": public_audio_url(context, item["meaning_context_id"]),
    }


def sanitize_variant_card(context: RuntimeContext, item: dict) -> dict:
    primary = context.teaching_by_target[item["primary_target_id"]]
    return {
        "variant_card_id": item["variant_card_id"],
        "primary_target_id": item["primary_target_id"],
        "primary_form": primary.get("form", primary.get("primary_form")),
        "primary_reading": primary.get("reading", primary.get("primary_reading")),
        "form": item["form"],
        "reading": item["reading"],
        "example_ja": item["example_ja"],
        "example_zh": item["example_zh"],
        "audio_url": public_audio_url(context, item["variant_card_id"]),
        "example_audio_url": public_audio_url(
            context, f"{item['variant_card_id']}:example_sentence"
        ),
    }


def sanitize_variant_practice(
    context: RuntimeContext, item: dict, *, reveal_answer: bool = False
) -> dict:
    primary = context.teaching_by_target[item["primary_target_id"]]
    payload = {
        "variant_practice_item_id": item["variant_practice_item_id"],
        "variant_card_id": item["variant_card_id"],
        "primary_target_id": item["primary_target_id"],
        "recognition_contract": item.get("recognition_contract", "legacy_reading_recognition"),
        "primary_form": item.get(
            "primary_form", primary.get("form", primary.get("primary_form"))
        ),
        "primary_reading": item.get(
            "primary_reading", primary.get("reading", primary.get("primary_reading"))
        ),
        "prompt_zh": item["prompt_zh"],
        "options": item["options"],
    }
    if reveal_answer:
        payload.update(
            {
                "answer_form": item.get("answer_form"),
                "answer_reading": item.get("answer_reading"),
                "example_ja": item.get("example_ja", item.get("context_ja")),
                "example_zh": item.get("example_zh"),
                "audio_url": public_audio_url(
                    context, item["variant_practice_item_id"]
                ),
                "example_audio_url": public_audio_url(
                    context, f"{item['variant_practice_item_id']}:example_sentence"
                ),
            }
        )
    return payload


def sanitize_embedded_support(item: dict) -> dict:
    return {
        "embedded_support_card_id": item["embedded_support_card_id"],
        "title_zh": item["title_zh"],
        "canonical_form": item["canonical_form"],
        "teaching_explanation_zh": item["teaching_explanation_zh"],
    }


def target_label_zh(context: RuntimeContext | str, target_id: str | None = None) -> str:
    if target_id is None:
        target_id = context
        context = DEFAULT_CONTEXT
    group = context.group_by_target[target_id]
    position = group["primary_target_ids"].index(target_id)
    return group["target_labels_zh"][position]


def current_learning_group(context: RuntimeContext, session: dict) -> dict:
    return context.learning_groups[session["group_index"]]


def learning_group_payload(context: RuntimeContext, session: dict) -> dict:
    group = current_learning_group(context, session)
    group_targets = group["primary_target_ids"]
    payload = {
        "group_id": group["group_id"],
        "title_zh": group["title_zh"],
        "group_number": session["group_index"] + 1,
        "group_count": len(context.learning_groups),
        "target_ids": group_targets,
        "target_labels_zh": [target_label_zh(context, target_id) for target_id in group_targets],
        "target_count": len(group_targets),
        "practice_count": len(session["group_practice_target_ids"]),
    }
    if session["stage"] in {
        "learning_teaching",
        "learning_meaning_contexts",
        "learning_variant",
        "learning_variant_practice",
        "learning_embedded_support",
    }:
        position = session["index"] - context.group_start_indexes[session["group_index"]]
        payload["teaching_position"] = position + 1
        payload["is_last_teaching_card"] = position == len(group_targets) - 1
        payload["primary_action_zh"] = (
            context.route_copy["finish_group_action_zh"]
            if payload["is_last_teaching_card"]
            else context.route_copy["next_action_zh"]
        )
    elif session["stage"] == "group_practice":
        payload["practice_position"] = session["index"] + 1
        payload["is_last_practice_item"] = session["index"] == len(
            session["group_practice_target_ids"]
        ) - 1
    return payload


class SessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.store_path: Path | None = None

    def configure_store(self, store_path: Path | None) -> None:
        self.store_path = None if store_path is None else store_path.resolve()
        self.sessions = {}
        if self.store_path is None or not self.store_path.exists():
            return
        value = json.loads(self.store_path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or (
                value.get("catalog_id") != ACTIVE_CATALOG["catalog_id"]
                and value.get("work_unit_id") != WORK_UNIT_ID
            )
            or not isinstance(value.get("sessions"), dict)
        ):
            raise ValueError("persistent session store contract mismatch")
        for session_id, session in value["sessions"].items():
            if not isinstance(session, dict) or session.get("request", {}).get("session_id") != session_id:
                raise ValueError("persistent session identity mismatch")
            validate_session_request(session["request"])
            if session.get("stage") not in {
                "review_question",
                "review_feedback",
                "review_retry",
                "review_completion",
                "learning_teaching",
                "learning_meaning_contexts",
                "learning_variant",
                "learning_variant_practice",
                "learning_embedded_support",
                "group_practice_intro",
                "group_practice",
                "system_map",
                "checkpoint_intro",
                "unit_checkpoint",
                "recovery_intro",
                "recovery_teaching",
                "recovery_guided",
                "recovery_verification",
                "completed",
            }:
                raise ValueError("persistent session stage is invalid")
            if session.get("status") not in {"in_progress", "completed"}:
                raise ValueError("persistent session status is invalid")
            if not isinstance(session.get("step_revision"), int) or session["step_revision"] < 0:
                raise ValueError("persistent session revision is invalid")
            session.setdefault("extension_index", 0)
            session.setdefault("meaning_context_exposures", [])
            session.setdefault("variant_exposures", [])
            session.setdefault("variant_responses", [])
            session.setdefault("embedded_support_exposures", [])
        self.sessions = value["sessions"]

    def persist(self) -> None:
        if self.store_path is None:
            return
        write_json_atomic(
            self.store_path,
            {
                "schema_version": 1,
                "work_unit_id": WORK_UNIT_ID,
                "catalog_id": ACTIVE_CATALOG["catalog_id"],
                "sessions": self.sessions,
            },
        )

    def create(self, request: dict) -> dict:
        validate_session_request(request)
        session_id = request["session_id"]
        existing = self.sessions.get(session_id)
        if existing:
            if existing["request"] != request:
                raise ValueError("session_id already exists with different request data")
            return existing
        now = utc_now()
        mode = request.get("session_mode", "new_learning")
        review_target_ids = request.get("review_target_ids", [])
        session = {
            "request": request,
            "stage": "learning_teaching" if mode == "new_learning" else "review_question",
            "index": 0,
            "group_index": 0,
            "system_map_index": 0,
            "extension_index": 0,
            "group_practice_target_ids": [],
            "step_revision": 0,
            "guided_feedback": None,
            "teaching_exposures": [],
            "meaning_context_exposures": [],
            "variant_exposures": [],
            "variant_responses": [],
            "embedded_support_exposures": [],
            "learning_routes": [],
            "skipped_learning_target_ids": [],
            "guided_responses": [],
            "checkpoint_responses": [],
            "recovery_target_ids": [],
            "recovery_teaching_exposures": [],
            "recovery_responses": [],
            "review_target_ids": review_target_ids,
            "review_responses": [],
            "target_review_outcomes": [],
            "new_learning_started": mode == "new_learning",
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "result_id": None,
        }
        self.sessions[session_id] = session
        self.persist()
        return session

    def get(self, session_id: str) -> dict:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError("unknown session_id") from exc


SESSIONS = SessionStore()


def validate_session_request(request: dict) -> None:
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        raise ValueError("session request schema_version must be 1")
    session_id = request.get("session_id")
    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid session_id")
    requested_work_unit_id = request.get("work_unit_id")
    if requested_work_unit_id not in ACTIVE_UNIT_ENTRIES:
        raise ValueError("requested work unit is not active or loadable in the local-player catalog")
    requested_context = UNIT_CONTEXTS[requested_work_unit_id]
    asset_ids = request.get("practice_asset_ids")
    if not isinstance(asset_ids, list) or set(asset_ids) != set(requested_context.checkpoint_order):
        raise ValueError("practice_asset_ids must match the active unit checkpoint set requested")
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("practice_asset_ids must be unique")
    mode = request.get("session_mode", "new_learning")
    if mode not in SESSION_MODES:
        raise ValueError("unsupported session_mode")
    review_target_ids = request.get("review_target_ids", [])
    if not isinstance(review_target_ids, list) or len(review_target_ids) != len(
        set(review_target_ids)
    ):
        raise ValueError("review_target_ids must be a unique array")
    if any(target_id not in TARGET_CONTEXTS for target_id in review_target_ids):
        raise ValueError("review_target_ids contain a target outside the active unit catalog")
    if mode in {"delayed_review", "mixed"} and not 1 <= len(review_target_ids) <= 6:
        raise ValueError("review sessions require between one and six review targets")
    if mode == "new_learning" and review_target_ids:
        raise ValueError("new_learning sessions must not include review targets")
    new_work_unit_id = request.get(
        "new_work_unit_id", requested_work_unit_id if mode == "new_learning" else None
    )
    if mode in {"new_learning", "mixed"} and new_work_unit_id != requested_work_unit_id:
        raise ValueError("new learning target must match the requested work unit")
    if mode == "delayed_review" and new_work_unit_id is not None:
        raise ValueError("delayed_review sessions must not include a new work unit")
    if not isinstance(request.get("requested_at"), str) or not request["requested_at"]:
        raise ValueError("requested_at is required")


def stage_position(session: dict) -> tuple[int, int]:
    stage = session["stage"]
    context = learning_context(session)
    if stage in {"review_question", "review_feedback", "review_retry"}:
        return session["index"] + 1, len(session["review_target_ids"])
    if stage == "review_completion":
        total = len(session["review_target_ids"])
        return total, total
    if stage in {
        "learning_teaching",
        "learning_meaning_contexts",
        "learning_variant",
        "learning_variant_practice",
        "learning_embedded_support",
    }:
        group = current_learning_group(context, session)
        return (
            session["index"] - context.group_start_indexes[session["group_index"]] + 1,
            len(group["primary_target_ids"]),
        )
    if stage == "group_practice_intro":
        total = len(session["group_practice_target_ids"])
        return total, total
    if stage == "group_practice":
        return session["index"] + 1, len(session["group_practice_target_ids"])
    if stage == "system_map":
        return session["system_map_index"] + 1, len(context.system_maps)
    if stage == "checkpoint_intro":
        return len(context.target_order), len(context.target_order)
    if stage == "unit_checkpoint":
        return session["index"] + 1, len(context.checkpoint_order)
    if stage == "recovery_intro":
        return 0, len(session["recovery_target_ids"])
    if stage in {"recovery_teaching", "recovery_guided", "recovery_verification"}:
        return session["index"] + 1, len(session["recovery_target_ids"])
    return len(context.checkpoint_order), len(context.checkpoint_order)


def stage_payload(session_id: str, session: dict) -> dict:
    context = learning_context(session)
    current, total = stage_position(session)
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "work_unit": {
            "work_unit_id": context.work_unit_id,
            "unit_code": context.unit_code,
            "title_zh": context.display_title_zh,
            "unit_task_brief": context.unit["unit_task_brief"],
        },
        "status": session["status"],
        "session_mode": session["request"].get("session_mode", "new_learning"),
        "stage": session["stage"],
        "progress": {"current": current, "total": total},
        "updated_at": session["updated_at"],
        "boundary_notice_zh": "这里只记录本次学习和作答证据，不代表已经掌握。",
        "route_copy": context.route_copy,
        "route_summary": {
            "learned_count": len(
                [row for row in session["learning_routes"] if row["route"] == "continue_learning"]
            ),
            "skipped_learning_count": len(session["skipped_learning_target_ids"]),
        },
        "step_token": (
            f"{session['stage']}:{session['group_index']}:{session['index']}:"
            f"{session.get('extension_index', 0)}:"
            f"{1 if session['guided_feedback'] else 0}:{session['step_revision']}"
        ),
    }
    if session["stage"] in {"review_question", "review_feedback", "review_retry", "review_completion"}:
        source_context = (
            review_context(session["review_target_ids"][min(session["index"], len(session["review_target_ids"]) - 1)])
            if session["review_target_ids"]
            else context
        )
        payload["review"] = {
            "position": min(session["index"] + 1, len(session["review_target_ids"])),
            "total": len(session["review_target_ids"]),
            "source_unit_code": source_context.unit_code,
            "source_unit_title_zh": source_context.display_title_zh,
        }
    if session["stage"] == "review_question":
        target_id = session["review_target_ids"][session["index"]]
        source_context = review_context(target_id)
        payload["review_item"] = sanitize_checkpoint(
            source_context, source_context.checkpoint_by_target[target_id]
        )
    elif session["stage"] == "review_feedback":
        target_id = session["review_target_ids"][session["index"]]
        source_context = review_context(target_id)
        response = session["review_responses"][-1]
        card = sanitize_teaching_card(
            source_context, source_context.teaching_by_target[target_id]
        )
        if card["target_kind"] == "vocabulary":
            context = card["teaching_context"]
            teaching = {
                "title_ja": f"{card['form']}（{card['reading']}）",
                "explanation_zh": (
                    f"「{card['form']}」表示“{card['course_meaning_labels_zh'][0]}”。"
                    f"例如：{context['ja']}（{context['zh']}）"
                ),
            }
        else:
            teaching = {
                "title_ja": card["learner_copy"]["learner_pattern_zh"],
                "explanation_zh": card["learner_copy"]["explanation_zh"],
            }
        payload["review_item"] = sanitize_checkpoint(
            source_context, source_context.checkpoint_by_target[target_id]
        )
        payload["review_feedback"] = {
            "selected_option_id": response["option_id"],
            "teaching": teaching,
        }
    elif session["stage"] == "review_retry":
        target_id = session["review_target_ids"][session["index"]]
        source_context = review_context(target_id)
        payload["review_retry_item"] = sanitize_recovery_item(
            source_context.recovery_by_target[target_id]
        )
    elif session["stage"] == "review_completion":
        direct = sum(1 for row in session["target_review_outcomes"] if row["result"] == "correct")
        recovered = sum(
            1 for row in session["target_review_outcomes"] if row["result"] == "incorrect_then_recovered"
        )
        unresolved = sum(1 for row in session["target_review_outcomes"] if row["result"] == "unresolved")
        payload["review_completion"] = {
            "direct_correct_count": direct,
            "recovered_count": recovered,
            "unresolved_count": unresolved,
            "can_continue_new_learning": session["request"].get("session_mode") == "mixed",
            "next_work_unit": (
                {
                    "work_unit_id": context.work_unit_id,
                    "unit_code": context.unit_code,
                    "title_zh": context.display_title_zh,
                }
                if session["request"].get("session_mode") == "mixed"
                else None
            ),
        }
    elif session["stage"] in {
        "learning_teaching",
        "learning_meaning_contexts",
        "learning_variant",
        "learning_variant_practice",
        "learning_embedded_support",
        "group_practice_intro",
        "group_practice",
    }:
        payload["learning_group"] = learning_group_payload(context, session)
    if session["stage"] == "learning_teaching":
        card_id = context.teaching_order[session["index"]]
        payload["teaching_card"] = sanitize_teaching_card(context, context.teaching_by_id[card_id])
    elif session["stage"] == "learning_meaning_contexts":
        target_id = context.target_order[session["index"]]
        card = context.teaching_by_target[target_id]
        payload["meaning_group"] = {
            "primary_target_id": target_id,
            "form": card.get("form", card.get("primary_form")),
            "reading": card.get("reading", card.get("primary_reading")),
            "contexts": [
                sanitize_meaning_context(context, row)
                for row in context.meaning_contexts_by_target[target_id]
            ],
        }
    elif session["stage"] == "learning_variant":
        target_id = context.target_order[session["index"]]
        variant = context.variant_cards_by_target[target_id][session["extension_index"]]
        payload["variant_card"] = sanitize_variant_card(context, variant)
    elif session["stage"] == "learning_variant_practice":
        target_id = context.target_order[session["index"]]
        variant = context.variant_cards_by_target[target_id][session["extension_index"]]
        item = context.variant_practice_by_card[variant["variant_card_id"]]
        payload["variant_practice"] = sanitize_variant_practice(
            context, item, reveal_answer=session["guided_feedback"] is not None
        )
        payload["feedback"] = session["guided_feedback"]
    elif session["stage"] == "learning_embedded_support":
        target_id = context.target_order[session["index"]]
        support = context.embedded_support_by_target[target_id][session["extension_index"]]
        payload["embedded_support"] = sanitize_embedded_support(support)
    elif session["stage"] == "group_practice_intro":
        payload["group_practice_intro"] = {
            "title_zh": "现在练一练这组内容",
            "body_zh": (
                f"下面用{len(session['group_practice_target_ids'])}道混合题，"
                "练习刚才接触的内容。"
            ),
            "item_count": len(session["group_practice_target_ids"]),
            "target_labels_zh": [
                target_label_zh(context, target_id)
                for target_id in session["group_practice_target_ids"]
            ],
        }
    elif session["stage"] == "group_practice":
        target_id = session["group_practice_target_ids"][session["index"]]
        item_id = context.guided_by_target[target_id]["practice_item_id"]
        payload["guided_item"] = sanitize_guided_item(context, context.guided_by_id[item_id])
        payload["feedback"] = session["guided_feedback"]
        payload["practice_phase"] = "group_learning"
    elif session["stage"] == "system_map":
        system_map = context.system_maps[session["system_map_index"]]
        payload["system_map"] = {
            **system_map["presentation"],
            "position": session["system_map_index"] + 1,
            "total": len(context.system_maps),
            "preview_counts_as_taught": system_map["preview_counts_as_taught"],
        }
    elif session["stage"] == "checkpoint_intro":
        payload["checkpoint_intro"] = {
            "title_zh": "开始本单元小测",
            "body_zh": context.route_copy["checkpoint_intro_zh"],
            "item_count": len(context.checkpoint_order),
        }
    elif session["stage"] == "unit_checkpoint":
        item_id = context.checkpoint_order[session["index"]]
        payload["checkpoint"] = sanitize_checkpoint(context, context.checkpoint_by_id[item_id])
    elif session["stage"] == "recovery_intro":
        payload["recovery_intro"] = {
            "title_zh": "只补练需要加强的内容",
            "body_zh": context.route_copy["recovery_intro_zh"],
            "target_count": len(session["recovery_target_ids"]),
        }
    elif session["stage"] == "recovery_teaching":
        target_id = session["recovery_target_ids"][session["index"]]
        payload["teaching_card"] = sanitize_teaching_card(
            context, context.teaching_by_target[target_id]
        )
        payload["recovery_reason_zh"] = "这个知识点在刚才的小测中没有答对，先快速看一遍，再练一题。"
    elif session["stage"] == "recovery_guided":
        target_id = session["recovery_target_ids"][session["index"]]
        item = context.guided_by_target[target_id]
        payload["guided_item"] = sanitize_guided_item(context, item)
        payload["feedback"] = session["guided_feedback"]
        payload["practice_phase"] = "targeted_recovery"
    elif session["stage"] == "recovery_verification":
        target_id = session["recovery_target_ids"][session["index"]]
        payload["recovery_verification"] = sanitize_recovery_item(
            context.recovery_by_target[target_id]
        )
    elif session["stage"] == "completed":
        payload["completion"] = result_payload(session_id, session)
    return payload


def require_step_token(session: dict, payload: dict) -> None:
    expected = (
        f"{session['stage']}:{session['group_index']}:{session['index']}:"
        f"{session.get('extension_index', 0)}:"
        f"{1 if session['guided_feedback'] else 0}:{session['step_revision']}"
    )
    if payload.get("step_token") != expected:
        raise ValueError("stale or invalid step_token")


def finish_session(session: dict) -> None:
    context = learning_context(session)
    session["stage"] = "completed"
    session["status"] = "completed"
    session["completed_at"] = utc_now()
    session["result_id"] = f"n5-core-{context.unit_code.lower()}-result-{secrets.token_hex(8)}"


def finish_review_target(session: dict, result: str, item_id: str) -> None:
    target_id = session["review_target_ids"][session["index"]]
    session["target_review_outcomes"].append(
        {"target_id": target_id, "result": result, "item_id": item_id}
    )
    session["index"] += 1
    if session["index"] >= len(session["review_target_ids"]):
        session["stage"] = "review_completion"
    else:
        session["stage"] = "review_question"


def enter_current_teaching_target(context: RuntimeContext, session: dict) -> None:
    """Enter a primary target, showing any support card before its formal card."""
    target_id = context.target_order[session["index"]]
    pending_support = [
        row
        for row in context.embedded_support_by_target.get(target_id, [])
        if row["embedded_support_card_id"] not in session["embedded_support_exposures"]
    ]
    session["extension_index"] = 0
    session["stage"] = "learning_embedded_support" if pending_support else "learning_teaching"


def advance_after_primary_teaching(
    context: RuntimeContext, session: dict, target_id: str
) -> None:
    if context.meaning_contexts_by_target.get(target_id):
        session["extension_index"] = 0
        session["stage"] = "learning_meaning_contexts"
    elif context.variant_cards_by_target.get(target_id):
        session["extension_index"] = 0
        session["stage"] = "learning_variant"
    else:
        advance_teaching_card(context, session)


def advance_after_meaning_contexts(
    context: RuntimeContext, session: dict, target_id: str
) -> None:
    if context.variant_cards_by_target.get(target_id):
        session["extension_index"] = 0
        session["stage"] = "learning_variant"
    else:
        advance_teaching_card(context, session)


def advance_to_next_group(context: RuntimeContext, session: dict) -> None:
    session["guided_feedback"] = None
    session["group_practice_target_ids"] = []
    session["group_index"] += 1
    if session["group_index"] >= len(context.learning_groups):
        session["stage"] = "system_map" if context.system_maps else "checkpoint_intro"
        session["index"] = 0
        session["system_map_index"] = 0
    else:
        session["index"] = context.group_start_indexes[session["group_index"]]
        enter_current_teaching_target(context, session)


def finish_group_teaching(context: RuntimeContext, session: dict) -> None:
    group = current_learning_group(context, session)
    learned_targets = [
        row["target_id"]
        for row in session["learning_routes"]
        if row["target_id"] in group["primary_target_ids"]
        and row["route"] == "continue_learning"
    ]
    session["group_practice_target_ids"] = learned_targets[
        : group["guided_practice_limit"]
    ]
    session["guided_feedback"] = None
    if session["group_practice_target_ids"]:
        session["stage"] = "group_practice_intro"
    else:
        advance_to_next_group(context, session)


def advance_teaching_card(context: RuntimeContext, session: dict) -> None:
    group = current_learning_group(context, session)
    last_index = context.group_start_indexes[session["group_index"]] + len(
        group["primary_target_ids"]
    ) - 1
    if session["index"] == last_index:
        finish_group_teaching(context, session)
    else:
        session["index"] += 1
        enter_current_teaching_target(context, session)


def apply_action(session_id: str, session: dict, payload: dict) -> dict:
    if session["status"] == "completed":
        return stage_payload(session_id, session)
    require_step_token(session, payload)
    action = payload.get("action")
    stage = session["stage"]
    context = learning_context(session)
    if stage == "review_question":
        target_id = session["review_target_ids"][session["index"]]
        source_context = review_context(target_id)
        item = source_context.checkpoint_by_target[target_id]
        if action != "submit_review" or payload.get("asset_id") != item["checkpoint_item_id"]:
            raise ValueError("invalid review submission")
        option_id = payload.get("option_id")
        if option_id not in {option["option_id"] for option in item["options"]}:
            raise ValueError("invalid review option")
        correct = option_id == source_context.answer_keys[item["checkpoint_item_id"]]["correct_option_id"]
        session["review_responses"].append(
            {
                "phase": "initial",
                "asset_id": item["checkpoint_item_id"],
                "target_id": target_id,
                "option_id": option_id,
                "correct": correct,
            }
        )
        if correct:
            finish_review_target(session, "correct", item["checkpoint_item_id"])
        else:
            session["stage"] = "review_feedback"
    elif stage == "review_feedback":
        if action != "start_review_retry":
            raise ValueError("invalid action after review feedback")
        session["stage"] = "review_retry"
    elif stage == "review_retry":
        target_id = session["review_target_ids"][session["index"]]
        source_context = review_context(target_id)
        item = source_context.recovery_by_target[target_id]
        if action != "submit_review_retry" or payload.get("asset_id") != item["verification_item_id"]:
            raise ValueError("invalid review retry submission")
        option_id = payload.get("option_id")
        if option_id not in {option["option_id"] for option in item["options"]}:
            raise ValueError("invalid review retry option")
        checkpoint_id = item["source_checkpoint_item_id"]
        correct = option_id == source_context.answer_keys[checkpoint_id]["correct_option_id"]
        session["review_responses"].append(
            {
                "phase": "retry",
                "asset_id": item["verification_item_id"],
                "target_id": target_id,
                "option_id": option_id,
                "correct": correct,
            }
        )
        finish_review_target(
            session,
            "incorrect_then_recovered" if correct else "unresolved",
            item["verification_item_id"],
        )
    elif stage == "review_completion":
        mode = session["request"].get("session_mode")
        if action == "finish_review":
            finish_session(session)
        elif action == "continue_new_learning" and mode == "mixed":
            session["index"] = 0
            session["group_index"] = 0
            session["new_learning_started"] = True
            enter_current_teaching_target(context, session)
        else:
            raise ValueError("invalid review completion action")
    elif stage == "learning_teaching":
        if action not in {"continue_learning", "skip_learning"}:
            raise ValueError("invalid action for adaptive teaching stage")
        expected_id = context.teaching_order[session["index"]]
        if payload.get("asset_id") != expected_id:
            raise ValueError("teaching asset does not match current step")
        target_id = context.target_order[session["index"]]
        session["teaching_exposures"].append(expected_id)
        route = "continue_learning" if action == "continue_learning" else "skip_learning"
        session["learning_routes"].append({"target_id": target_id, "route": route})
        if action == "skip_learning":
            session["skipped_learning_target_ids"].append(target_id)
        if action == "continue_learning":
            advance_after_primary_teaching(context, session, target_id)
        else:
            advance_teaching_card(context, session)
    elif stage == "learning_meaning_contexts":
        target_id = context.target_order[session["index"]]
        rows = context.meaning_contexts_by_target[target_id]
        if action != "continue_meaning_contexts" or payload.get("asset_id") != target_id:
            raise ValueError("invalid action for meaning-group contexts")
        session["meaning_context_exposures"].extend(
            row["meaning_context_id"] for row in rows
        )
        advance_after_meaning_contexts(context, session, target_id)
    elif stage == "learning_variant":
        target_id = context.target_order[session["index"]]
        item = context.variant_cards_by_target[target_id][session["extension_index"]]
        if action != "continue_variant" or payload.get("asset_id") != item["variant_card_id"]:
            raise ValueError("invalid action for form-reading variant")
        session["variant_exposures"].append(item["variant_card_id"])
        session["guided_feedback"] = None
        session["stage"] = "learning_variant_practice"
    elif stage == "learning_variant_practice":
        target_id = context.target_order[session["index"]]
        variants = context.variant_cards_by_target[target_id]
        variant = variants[session["extension_index"]]
        item = context.variant_practice_by_card[variant["variant_card_id"]]
        item_id = item["variant_practice_item_id"]
        if session["guided_feedback"] is None:
            if action != "submit_variant_practice" or payload.get("asset_id") != item_id:
                raise ValueError("invalid form-reading variant practice submission")
            option_id = payload.get("option_id")
            if option_id not in {option["option_id"] for option in item["options"]}:
                raise ValueError("invalid form-reading variant practice option")
            correct = option_id == item["correct_option_id"]
            session["variant_responses"].append(
                {
                    "asset_id": item_id,
                    "target_id": target_id,
                    "option_id": option_id,
                    "correct": correct,
                    "counts_as_mastery_evidence": False,
                }
            )
            session["guided_feedback"] = {
                "selected_option_id": option_id,
                "revealed_option_id": item["correct_option_id"],
                "correct": correct,
                "message_zh": item["feedback_zh"],
            }
        else:
            if action != "continue_variant_practice":
                raise ValueError("variant feedback must be acknowledged before continuing")
            session["guided_feedback"] = None
            session["extension_index"] += 1
            if session["extension_index"] < len(variants):
                session["stage"] = "learning_variant"
            else:
                session["extension_index"] = 0
                advance_teaching_card(context, session)
    elif stage == "learning_embedded_support":
        target_id = context.target_order[session["index"]]
        rows = context.embedded_support_by_target[target_id]
        item = rows[session["extension_index"]]
        if action != "continue_embedded_support" or payload.get("asset_id") != item["embedded_support_card_id"]:
            raise ValueError("invalid action for embedded support card")
        session["embedded_support_exposures"].append(item["embedded_support_card_id"])
        session["extension_index"] += 1
        if session["extension_index"] >= len(rows):
            session["extension_index"] = 0
            session["stage"] = "learning_teaching"
    elif stage == "group_practice_intro":
        if action != "start_group_practice":
            raise ValueError("invalid action for group practice introduction")
        session["stage"] = "group_practice"
        session["index"] = 0
    elif stage in {"group_practice", "recovery_guided"}:
        target_id = (
            session["group_practice_target_ids"][session["index"]]
            if stage == "group_practice"
            else session["recovery_target_ids"][session["index"]]
        )
        item_id = context.guided_by_target[target_id]["practice_item_id"]
        item = context.guided_by_id[item_id]
        if session["guided_feedback"] is None:
            if action != "submit_guided" or payload.get("asset_id") != item_id:
                raise ValueError("invalid guided practice submission")
            option_id = payload.get("option_id")
            valid_ids = {option["option_id"] for option in item["options"]}
            if option_id not in valid_ids:
                raise ValueError("invalid guided practice option")
            correct = option_id == item["correct_option_id"]
            session["guided_responses"].append(
                {
                    "asset_id": item_id,
                    "target_id": target_id,
                    "phase": "group_learning" if stage == "group_practice" else "targeted_recovery",
                    "group_id": (
                        current_learning_group(context, session)["group_id"]
                        if stage == "group_practice"
                        else None
                    ),
                    "option_id": option_id,
                    "correct": correct,
                }
            )
            session["guided_feedback"] = {
                "selected_option_id": option_id,
                "revealed_option_id": item["correct_option_id"],
                "correct": correct,
                "message_zh": context.guided_practice_overrides.get(item_id, {}).get(
                    "feedback_zh", item["feedback_zh"]
                ),
            }
        else:
            if action != "continue_guided":
                raise ValueError("guided feedback must be acknowledged before continuing")
            session["guided_feedback"] = None
            if stage == "group_practice":
                session["index"] += 1
                if session["index"] >= len(session["group_practice_target_ids"]):
                    advance_to_next_group(context, session)
            else:
                session["stage"] = "recovery_verification"
    elif stage == "system_map":
        expected_id = context.system_maps[session["system_map_index"]]["system_map_snapshot_id"]
        if action != "continue_system_map" or payload.get("asset_id") != expected_id:
            raise ValueError("invalid action for system map")
        session["system_map_index"] += 1
        if session["system_map_index"] >= len(context.system_maps):
            session["stage"] = "checkpoint_intro"
            session["index"] = 0
    elif stage == "checkpoint_intro":
        if action != "start_checkpoint":
            raise ValueError("invalid action for checkpoint introduction")
        session["stage"] = "unit_checkpoint"
        session["index"] = 0
    elif stage == "unit_checkpoint":
        item_id = context.checkpoint_order[session["index"]]
        item = context.checkpoint_by_id[item_id]
        if action != "submit_checkpoint" or payload.get("asset_id") != item_id:
            raise ValueError("invalid independent checkpoint submission")
        option_id = payload.get("option_id")
        valid_ids = {option["option_id"] for option in item["options"]}
        if option_id not in valid_ids:
            raise ValueError("invalid checkpoint option")
        correct = option_id == context.answer_keys[item_id]["correct_option_id"]
        session["checkpoint_responses"].append(
            {
                "asset_id": item_id,
                "target_id": item["primary_target_id"],
                "option_id": option_id,
                "correct": correct,
            }
        )
        session["index"] += 1
        if session["index"] >= len(context.checkpoint_order):
            session["recovery_target_ids"] = [
                row["target_id"] for row in session["checkpoint_responses"] if not row["correct"]
            ]
            if session["recovery_target_ids"]:
                session["stage"] = "recovery_intro"
                session["index"] = 0
            else:
                finish_session(session)
    elif stage == "recovery_intro":
        if action != "start_recovery":
            raise ValueError("invalid action for recovery introduction")
        session["stage"] = "recovery_teaching"
        session["index"] = 0
    elif stage == "recovery_teaching":
        target_id = session["recovery_target_ids"][session["index"]]
        expected_id = context.teaching_by_target[target_id]["teaching_card_id"]
        if action != "start_recovery_guided" or payload.get("asset_id") != expected_id:
            raise ValueError("invalid action for recovery teaching")
        session["recovery_teaching_exposures"].append(expected_id)
        session["stage"] = "recovery_guided"
    elif stage == "recovery_verification":
        target_id = session["recovery_target_ids"][session["index"]]
        item = context.recovery_by_target[target_id]
        if action != "submit_recovery_verification" or payload.get("asset_id") != item["verification_item_id"]:
            raise ValueError("invalid recovery verification submission")
        option_id = payload.get("option_id")
        valid_ids = {option["option_id"] for option in item["options"]}
        if option_id not in valid_ids:
            raise ValueError("invalid recovery verification option")
        checkpoint_id = item["source_checkpoint_item_id"]
        correct = option_id == context.answer_keys[checkpoint_id]["correct_option_id"]
        session["recovery_responses"].append(
            {
                "asset_id": item["verification_item_id"],
                "source_checkpoint_item_id": checkpoint_id,
                "target_id": target_id,
                "option_id": option_id,
                "correct": correct,
            }
        )
        session["index"] += 1
        if session["index"] >= len(session["recovery_target_ids"]):
            finish_session(session)
        else:
            session["stage"] = "recovery_teaching"
    else:
        raise ValueError("unsupported session stage")
    session["step_revision"] += 1
    session["updated_at"] = utc_now()
    return stage_payload(session_id, session)


def result_payload(session_id: str, session: dict) -> dict:
    context = learning_context(session)
    mode = session["request"].get("session_mode", "new_learning")
    new_learning_completed = bool(session["checkpoint_responses"])
    if mode == "delayed_review" or (mode == "mixed" and not new_learning_completed):
        direct = sum(1 for row in session["target_review_outcomes"] if row["result"] == "correct")
        recovered = sum(
            1 for row in session["target_review_outcomes"] if row["result"] == "incorrect_then_recovered"
        )
        unresolved = sum(1 for row in session["target_review_outcomes"] if row["result"] == "unresolved")
        return {
            "schema_version": 1,
            "result_id": session["result_id"],
            "session_id": session_id,
            "work_unit_id": context.work_unit_id,
            "status": session["status"],
            "completed_at": session["completed_at"],
            "evidence_summary": {
                "answered_count": len(session["target_review_outcomes"]),
                "correct_count": direct + recovered,
                "scoring_contract_id": context.scoring_contract_id,
                "session_mode": mode,
                "new_learning_completed": False,
                "target_review_outcomes": session["target_review_outcomes"],
                "direct_correct_count": direct,
                "recovered_count": recovered,
                "unresolved_count": unresolved,
                "delayed_review_target_ids": [],
                "unresolved_target_ids": [],
                "mastery_claim": "not_inferred_from_single_session",
            },
        }
    correct_count = sum(1 for row in session["checkpoint_responses"] if row["correct"])
    initial_pass_targets = {
        row["target_id"] for row in session["checkpoint_responses"] if row["correct"]
    }
    recovered_targets = {row["target_id"] for row in session["recovery_responses"] if row["correct"]}
    provisional_pass_targets = initial_pass_targets | recovered_targets
    unresolved_targets = set(context.target_order) - provisional_pass_targets
    return {
        "schema_version": 1,
        "result_id": session["result_id"],
        "session_id": session_id,
        "work_unit_id": context.work_unit_id,
        "status": session["status"],
        "completed_at": session["completed_at"],
        "evidence_summary": {
            "session_mode": mode,
            "new_learning_completed": True,
            "target_review_outcomes": session["target_review_outcomes"],
            "answered_count": len(session["checkpoint_responses"]),
            "correct_count": correct_count,
            "scoring_contract_id": context.scoring_contract_id,
            "teaching_exposure_count": len(session["teaching_exposures"]),
            "meaning_context_exposure_count": len(session["meaning_context_exposures"]),
            "variant_exposure_count": len(session["variant_exposures"]),
            "variant_practice_answered_count": len(session["variant_responses"]),
            "embedded_support_exposure_count": len(session["embedded_support_exposures"]),
            "guided_answered_count": len(session["guided_responses"]),
            "learning_group_count": len(context.learning_groups),
            "group_practice_answered_count": len(
                [row for row in session["guided_responses"] if row["phase"] == "group_learning"]
            ),
            "skipped_learning_count": len(session["skipped_learning_target_ids"]),
            "recovery_target_count": len(session["recovery_target_ids"]),
            "recovery_answered_count": len(session["recovery_responses"]),
            "recovered_count": len(recovered_targets),
            "unresolved_count": len(unresolved_targets),
            "provisional_pass_count": len(provisional_pass_targets),
            "delayed_review_pending_count": len(provisional_pass_targets),
            "delayed_review_target_ids": sorted(provisional_pass_targets),
            "unresolved_target_ids": sorted(unresolved_targets),
            "mastery_claim": "not_inferred_from_single_session",
        },
    }


class CoreLearningHandler(BaseHTTPRequestHandler):
    server_version = "N5CoreLearning/1"

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
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/health":
            self.send_json(
                {
                    "status": "ok",
                    "work_unit_id": WORK_UNIT_ID,
                    "bound_audio_asset_count": len(AUDIO_ASSET_CONTEXTS),
                    "active_catalog_id": ACTIVE_CATALOG["catalog_id"],
                    "active_work_unit_count": len(FORMALLY_ACTIVE_UNIT_ENTRIES),
                    "loadable_work_unit_count": len(UNIT_CONTEXTS),
                    "formal_work_unit_active": WORK_UNIT_ID in FORMALLY_ACTIVE_UNIT_ENTRIES,
                    "persistent_session_store": SESSIONS.store_path is not None,
                }
            )
            return
        if path.startswith("/api/assets/"):
            asset_id = path.removeprefix("/api/assets/")
            context = AUDIO_ASSET_CONTEXTS.get(asset_id)
            asset = context.audio_assets.get(asset_id) if context else None
            if not asset:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            asset_path = (ROOT / asset["path"]).resolve()
            if ROOT not in asset_path.parents or not asset_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
            self.send_bytes(asset_path.read_bytes(), content_type)
            return
        session_match = re.fullmatch(r"/api/practice-sessions/([^/]+)", path)
        result_match = re.fullmatch(r"/api/practice-results/([^/]+)", path)
        try:
            if session_match:
                session_id = session_match.group(1)
                self.send_json(stage_payload(session_id, SESSIONS.get(session_id)))
                return
            if result_match:
                session_id = result_match.group(1)
                session = SESSIONS.get(session_id)
                if session["status"] != "completed":
                    self.send_json({"status": "pending", "session_id": session_id}, 409)
                    return
                self.send_json(result_payload(session_id, session))
                return
        except ValueError as error:
            self.send_json({"status": "error", "message": str(error)}, 404)
            return
        static = STATIC_FILES.get(path)
        if static:
            file_path, content_type = static
            self.send_bytes(file_path.read_bytes(), content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            payload = self.read_json_body()
            if path == "/api/practice-sessions":
                session = SESSIONS.create(payload)
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
                session = SESSIONS.get(session_id)
                response = apply_action(session_id, session, payload)
                SESSIONS.persist()
                self.send_json(response)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"status": "error", "message": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--session-store", type=Path)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local learning server only binds to localhost")
    SESSIONS.configure_store(args.session_store)
    server = ThreadingHTTPServer((args.host, args.port), CoreLearningHandler)
    print(f"N5 local player {UNIT_CODE}: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
