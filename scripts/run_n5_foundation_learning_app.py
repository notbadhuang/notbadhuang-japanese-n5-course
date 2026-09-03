#!/usr/bin/env python3
"""Serve a non-active N5 foundation micro-batch validation unit."""

from __future__ import annotations

import argparse
import hashlib
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
from urllib.parse import unquote, urlparse

from n5_foundation_runtime_context import (
    FoundationRuntimeContext,
    load_foundation_runtime_context,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "product/n5/course/foundation-runtime-v1/runtime"
DEFAULT_CATALOG_PATH = (
    ROOT / "product/n5/course/foundation-runtime-v1/validation-catalog.json"
)
CATALOG_PATH = Path(
    os.environ.get("N5_FOUNDATION_PLAYER_CATALOG_PATH", str(DEFAULT_CATALOG_PATH))
).resolve()
MAX_BODY_BYTES = 100_000
ISOLATED_KANA_PLAYBACK_RATE = 0.8
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
FOUNDATION_STAGES = {
    "foundation_batch_intro",
    "foundation_single_kana_learning",
    "foundation_batch_practice",
    "foundation_checkpoint_intro",
    "foundation_checkpoint",
    "foundation_targeted_repair",
    "foundation_retest",
    "completed",
}
STATIC_FILES = {
    "/": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/index.html": (RUNTIME_DIR / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (RUNTIME_DIR / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (RUNTIME_DIR / "app.js", "text/javascript; charset=utf-8"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
CATALOG_STATUS = CATALOG.get("status")
if CATALOG_STATUS not in {"validation_candidate_not_active", "active"}:
    raise ValueError("unsupported foundation catalog status")
EXPECTED_UNIT_STATUS = "active" if CATALOG_STATUS == "active" else "validation_candidate"
FORMALLY_ACTIVE_WORK_UNIT_COUNT = sum(
    row.get("status") == "active" for row in CATALOG.get("work_units", [])
)
if CATALOG.get("active_work_unit_count") != FORMALLY_ACTIVE_WORK_UNIT_COUNT:
    raise ValueError("foundation catalog active count mismatch")
if CATALOG_STATUS == "validation_candidate_not_active" and FORMALLY_ACTIVE_WORK_UNIT_COUNT:
    raise ValueError("foundation validation catalog must not activate a work unit")
if CATALOG.get("supported_session_modes") != ["new_learning"]:
    raise ValueError("foundation validation catalog only supports new_learning")

UNIT_ENTRIES = {
    row["work_unit_id"]: row
    for row in CATALOG["work_units"]
    if row.get("status") == EXPECTED_UNIT_STATUS
}
DEFAULT_WORK_UNIT_ID = CATALOG["default_work_unit_id"]
if DEFAULT_WORK_UNIT_ID not in UNIT_ENTRIES:
    raise ValueError("foundation validation default unit is not loadable")

UNIT_CONTEXTS: dict[str, FoundationRuntimeContext] = {}
for work_unit_id, entry in UNIT_ENTRIES.items():
    context = load_foundation_runtime_context(
        ROOT, project_path(entry["runtime_definition"])
    )
    if context.work_unit_id != work_unit_id:
        raise ValueError("foundation catalog and runtime definition mismatch")
    UNIT_CONTEXTS[work_unit_id] = context

DEFAULT_CONTEXT = UNIT_CONTEXTS[DEFAULT_WORK_UNIT_ID]
WORK_UNIT_ID = DEFAULT_CONTEXT.work_unit_id
UNIT_CODE = DEFAULT_CONTEXT.unit_code
CHECKPOINT_ORDER = DEFAULT_CONTEXT.checkpoint_order


def merge_context_registry(attribute: str) -> dict:
    registry = {}
    for context in UNIT_CONTEXTS.values():
        for key, value in getattr(context, attribute).items():
            if key in registry:
                existing = registry[key]
                if attribute == "audio_assets":
                    agrees = all(
                        existing.get(field) == value.get(field)
                        for field in ("path", "sha256", "script_ja", "speaking_rate")
                    )
                else:
                    agrees = existing == value
                if not agrees:
                    raise ValueError(f"foundation contexts disagree on shared asset: {key}")
            registry[key] = value
    return registry


ALL_AUDIO_ASSETS = merge_context_registry("audio_assets")

AUDIO_OVERRIDE_MANIFEST_PATH_VALUE = os.environ.get(
    "N5_FOUNDATION_AUDIO_OVERRIDE_MANIFEST_PATH"
)
AUDIO_OVERRIDE_MANIFEST = None
if AUDIO_OVERRIDE_MANIFEST_PATH_VALUE:
    audio_override_path = Path(AUDIO_OVERRIDE_MANIFEST_PATH_VALUE).resolve()
    if ROOT not in audio_override_path.parents or not audio_override_path.is_file():
        raise ValueError("foundation audio override manifest is missing or outside the project")
    AUDIO_OVERRIDE_MANIFEST = json.loads(audio_override_path.read_text(encoding="utf-8"))
    if AUDIO_OVERRIDE_MANIFEST.get("status") != "owner_approved_local_audio_layer_not_for_distribution":
        raise ValueError("foundation audio override is not owner-approved for local course development")
    if AUDIO_OVERRIDE_MANIFEST.get("formal_product_audio_modified") is not False:
        raise ValueError("foundation audio override must not modify formal product audio")
    if AUDIO_OVERRIDE_MANIFEST.get("formal_work_unit_activated") is not False:
        raise ValueError("foundation audio override must not activate a work unit")
    if AUDIO_OVERRIDE_MANIFEST.get("customer_distribution_authorized") is not False:
        raise ValueError("foundation audio override must remain non-distributable")
    override_rows = AUDIO_OVERRIDE_MANIFEST.get("runtime_overrides", [])
    if len(override_rows) != AUDIO_OVERRIDE_MANIFEST.get("covered_target_count"):
        raise ValueError("foundation audio override count mismatch")
    for override in override_rows:
        asset_id = override.get("audio_asset_id")
        existing = ALL_AUDIO_ASSETS.get(asset_id)
        if not existing or existing.get("asset_role") != "target":
            raise ValueError(f"foundation audio override references an unknown target: {asset_id}")
        if existing.get("script_ja") != override.get("script_ja"):
            raise ValueError(f"foundation audio override script mismatch: {asset_id}")
        candidate_path = project_path(override["path"])
        candidate_sha256 = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        if candidate_sha256 != override.get("sha256"):
            raise ValueError(f"foundation audio override hash mismatch: {asset_id}")
        ALL_AUDIO_ASSETS[asset_id] = {
            **existing,
            "path": override["path"],
            "sha256": candidate_sha256,
            "voice_name": override["source_name"],
            "source_url": override["source_url"],
            "candidate_use": override["candidate_use"],
        }
ALL_REPAIR_VISUAL_PATHS = merge_context_registry("repair_visual_paths")
ALL_SINGLE_KANA_MOUTH_PATHS = merge_context_registry("single_kana_mouth_paths")
ALL_SINGLE_KANA_EXAMPLE_AUDIO_PATHS = merge_context_registry("single_kana_example_audio_paths")
ALL_BATCH_ARTICULATION_PATHS = merge_context_registry("batch_articulation_paths")


def public_audio_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}"


def stable_shuffle(values: list[str], seed: str) -> list[str]:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest(),
    )


def balanced_slots(count: int, option_count: int, seed: str) -> list[int]:
    tagged = [(index % option_count, index) for index in range(count)]
    tagged.sort(
        key=lambda value: hashlib.sha256(
            f"{seed}:{value[0]}:{value[1]}".encode("utf-8")
        ).hexdigest()
    )
    return [slot for slot, _ in tagged]


def practice_order(context: FoundationRuntimeContext, session: dict, batch_index: int) -> list[str]:
    batch = context.profile["micro_batches"][batch_index]
    if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1":
        return stable_shuffle(
            list(batch["practice_item_ids"]),
            f"{session['request']['session_id']}:{batch['micro_batch_id']}:contextual-order",
        )
    sound_to_shape = stable_shuffle(
        list(batch["practice_item_ids"]),
        f"{session['request']['session_id']}:{batch['micro_batch_id']}:sound-to-shape-order",
    )
    shape_to_sound = stable_shuffle(
        [
            context.shape_to_sound_by_target[target_id]["practice_item_id"]
            for target_id in batch["target_ids"]
        ],
        f"{session['request']['session_id']}:{batch['micro_batch_id']}:shape-to-sound-order",
    )
    return sound_to_shape + shape_to_sound


def checkpoint_order(context: FoundationRuntimeContext, session: dict) -> list[str]:
    return stable_shuffle(
        list(context.checkpoint_order),
        f"{session['request']['session_id']}:checkpoint-order",
    )


def practice_target_id(context: FoundationRuntimeContext, item: dict) -> str:
    if item["practice_item_id"] in context.practice_target_by_id:
        return context.practice_target_by_id[item["practice_item_id"]]
    source = (
        context.sound_to_shape_by_target
        if item.get("tests") == "direct_sound_to_shape"
        else context.shape_to_sound_by_target
    )
    matches = [
        target_id
        for target_id, mapped_item in source.items()
        if mapped_item["practice_item_id"] == item["practice_item_id"]
    ]
    if len(matches) != 1:
        raise ValueError("foundation practice item does not map to exactly one target")
    return matches[0]


def displayed_option_values(
    item: dict,
    *,
    session: dict,
    stage_scope: str,
    position: int,
    item_count: int,
) -> list[str]:
    original = list(item["options_ja"])
    correct = item["correct_answer_ja"]
    distractors = stable_shuffle(
        [value for value in original if value != correct],
        f"{session['request']['session_id']}:{stage_scope}:{item.get('practice_item_id') or item.get('checkpoint_item_id')}:distractors",
    )
    correct_slot = balanced_slots(
        item_count,
        len(original),
        f"{session['request']['session_id']}:{stage_scope}:correct-slots",
    )[position]
    displayed = list(distractors)
    displayed.insert(correct_slot, correct)
    return displayed


def option_rows(values: list[str]) -> list[dict]:
    return [
        {"option_id": chr(ord("A") + index), "text": value}
        for index, value in enumerate(values)
    ]


def displayed_audio_asset_ids(item: dict, *, session: dict, stage_scope: str) -> list[str]:
    original = list(item["option_audio_asset_ids"])
    displayed = stable_shuffle(
        original,
        f"{session['request']['session_id']}:{stage_scope}:{item['practice_item_id']}:audio-options",
    )
    if stage_scope == "retest" and displayed == original:
        displayed = displayed[1:] + displayed[:1]
    return displayed


def audio_option_rows(asset_ids: list[str]) -> list[dict]:
    return [
        {
            "option_id": chr(ord("A") + index),
            "audio_url": public_audio_url(asset_id),
            "playback_rate": ISOLATED_KANA_PLAYBACK_RATE,
        }
        for index, asset_id in enumerate(asset_ids)
    ]


def sanitize_sound_to_shape(
    item: dict, *, checkpoint: bool, displayed_options: list[str]
) -> dict:
    return {
        "item_id": item["checkpoint_item_id"] if checkpoint else item["practice_item_id"],
        "target_id": item.get("target_id"),
        "response_mode": item["response_mode"],
        "prompt_zh": item["prompt_zh"],
        "prompt_audio_url": public_audio_url(item["prompt_audio_asset_id"]),
        "prompt_audio_playback_rate": ISOLATED_KANA_PLAYBACK_RATE,
        "options": option_rows(displayed_options),
    }


def sanitize_visual_choice(
    item: dict, *, checkpoint: bool, displayed_options: list[str]
) -> dict:
    return {
        "item_id": item["checkpoint_item_id"] if checkpoint else item["practice_item_id"],
        "target_id": item.get("target_id"),
        "response_mode": "single_choice_visual",
        "prompt_zh": item["prompt_zh"],
        "options": option_rows(displayed_options),
    }


def sanitize_shape_to_sound(item: dict, *, displayed_audio_ids: list[str]) -> dict:
    return {
        "item_id": item["practice_item_id"],
        "response_mode": item["response_mode"],
        "prompt_zh": item["prompt_zh"],
        "display_ja": item["display_ja"],
        "options": audio_option_rows(displayed_audio_ids),
    }


def single_kana_learning_batches(context: FoundationRuntimeContext) -> list[dict]:
    batches = context.profile.get("single_kana_learning_batches")
    if batches is not None:
        return batches
    legacy = context.profile.get("single_kana_learning")
    return [legacy] if legacy else []


def single_kana_learning_for_batch(
    context: FoundationRuntimeContext, batch_index: int
) -> dict | None:
    micro_batch_id = context.profile["micro_batches"][batch_index]["micro_batch_id"]
    return next(
        (
            row
            for row in single_kana_learning_batches(context)
            if row["micro_batch_id"] == micro_batch_id
        ),
        None,
    )


def batch_profile(context: FoundationRuntimeContext, session: dict) -> dict:
    batch = context.profile["micro_batches"][session["batch_index"]]
    if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1":
        lesson = batch["contextual_lesson"]
        return {
            "batch_number": batch["batch_number"],
            "batch_count": len(context.micro_batches),
            "micro_batch_id": batch["micro_batch_id"],
            "title_zh": batch["title_zh"],
            "target_ids": batch["target_ids"],
            "target_displays_ja": batch["target_displays_ja"],
            "practice_count": len(batch["practice_item_ids"]),
            "has_single_kana_learning": False,
            "presentation_kind": "contextual_pattern_lesson",
            "contextual_lesson": {
                **lesson,
                "primary_word": {
                    **lesson["primary_word"],
                    "audio_url": public_audio_url(lesson["primary_word"]["audio_asset_id"]),
                },
                "secondary_word": {
                    **lesson["secondary_word"],
                    "audio_url": public_audio_url(lesson["secondary_word"]["audio_asset_id"]),
                },
            },
        }
    sequence_audio_asset_id = batch["sequence_audio_asset_id"]
    sequence_audio = context.audio_assets[sequence_audio_asset_id]
    if sequence_audio.get("asset_role") != "sequence":
        raise ValueError("foundation batch sequence audio must be one complete sequence asset")
    profile = {
        "batch_number": batch["batch_number"],
        "batch_count": len(context.micro_batches),
        "micro_batch_id": batch["micro_batch_id"],
        "title_zh": batch["title_zh"],
        "target_ids": batch["target_ids"],
        "target_displays_ja": batch["target_displays_ja"],
        "sequence_audio_url": public_audio_url(sequence_audio_asset_id),
        "sequence_audio_playback_rate": 1.0,
        "sequence_audio_authored_speaking_rate": sequence_audio.get("speaking_rate", 1.0),
        "practice_count": len(batch["practice_item_ids"]) * 2,
        "has_single_kana_learning": single_kana_learning_for_batch(
            context, session["batch_index"]
        ) is not None,
    }
    if sequence_audio.get("voice_name"):
        profile["sequence_voice_name"] = sequence_audio["voice_name"]
    support = batch.get("articulation_support")
    if support:
        profile["articulation_support"] = {
            "support_kind": support["support_kind"],
            "row_name_zh": support["row_name_zh"],
            "intro_title_zh": support["intro_title_zh"],
            "intro_body_zh": support["intro_body_zh"],
            "title_zh": support["title_zh"],
            "body_zh": support["body_zh"],
            "alt_zh": support["alt_zh"],
            "visual_url": f"/api/batch-articulation-assets/{batch['micro_batch_id']}",
            "cta_label_zh": support["cta_label_zh"],
        }
    return profile


def step_token(session: dict) -> str:
    return (
        f"{session['stage']}:{session['batch_index']}:{session['single_kana_index']}:"
        f"{session['practice_index']}:"
        f"{session['checkpoint_index']}:{session['recovery_index']}:"
        f"{1 if session['practice_feedback'] else 0}:"
        f"{session['step_revision']}"
    )


def stage_payload(session_id: str, session: dict) -> dict:
    context = UNIT_CONTEXTS[session["request"]["work_unit_id"]]
    stage = session["stage"]
    if stage == "foundation_single_kana_learning":
        single_kana = single_kana_learning_for_batch(context, session["batch_index"])
        if single_kana is None:
            raise ValueError("single-kana learning stage has no batch contract")
        current = session["single_kana_index"] + 1
        total = len(single_kana["items"])
    elif stage in {"foundation_batch_intro", "foundation_batch_practice"}:
        batch = context.profile["micro_batches"][session["batch_index"]]
        runtime_practice_order = practice_order(context, session, session["batch_index"])
        if (
            stage == "foundation_batch_intro"
            and context.definition["engine_profile"]
            == "foundation_contextual_pattern_checkpoint_v1"
        ):
            current = session["batch_index"] + 1
            total = len(context.micro_batches)
        else:
            current = (
                session["practice_index"] + 1
                if stage == "foundation_batch_practice"
                else 1
            )
            total = len(runtime_practice_order)
    elif stage in {"foundation_checkpoint_intro", "foundation_checkpoint"}:
        runtime_checkpoint_order = checkpoint_order(context, session)
        current = (
            session["checkpoint_index"] + 1
            if stage == "foundation_checkpoint"
            else len(context.target_order)
        )
        total = len(runtime_checkpoint_order)
    elif stage in {"foundation_targeted_repair", "foundation_retest"}:
        current = session["recovery_index"] + 1
        total = len(session["recovery_target_ids"])
    else:
        current = len(context.checkpoint_order)
        total = len(context.checkpoint_order)

    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "work_unit": {
            "work_unit_id": context.work_unit_id,
            "unit_code": context.unit_code,
            "title_zh": context.display_title_zh,
            "unit_task_brief": context.profile["learner_copy"]["unit_task_brief"],
        },
        "status": session["status"],
        "session_mode": "new_learning",
        "engine_profile": context.definition["engine_profile"],
        "stage": stage,
        "progress": {"current": current, "total": total},
        "updated_at": session["updated_at"],
        "boundary_notice_zh": "这里只记录本次学习和作答证据，不代表已经掌握。",
        "step_token": step_token(session),
    }
    if stage == "foundation_batch_intro":
        payload["micro_batch"] = batch_profile(context, session)
        payload["batch_intro"] = {
            "title_zh": context.profile["learner_copy"]["batch_intro_title"],
            "body_zh": context.profile["learner_copy"]["batch_intro_body"],
        }
    elif stage == "foundation_single_kana_learning":
        single_kana = single_kana_learning_for_batch(context, session["batch_index"])
        if single_kana is None:
            raise ValueError("single-kana learning stage has no batch contract")
        item = single_kana["items"][session["single_kana_index"]]
        target_id = item["target_id"]
        teaching = context.teaching_by_target[target_id]
        audio_kind = (
            "word"
            if item["example_audio"]["script_ja"] == item["example_reading_ja"]
            else "sentence"
        )
        payload["micro_batch"] = batch_profile(context, session)
        payload["single_kana"] = {
            "target_id": target_id,
            "display_ja": teaching["display_ja"],
            "position": session["single_kana_index"] + 1,
            "count": len(single_kana["items"]),
            "stroke_count": item["stroke_count"],
            "stroke_paths": context.single_kana_strokes_by_target[target_id]["paths"],
            "stroke_labels": context.single_kana_strokes_by_target[target_id]["labels"],
            "source_kanji": item["source_kanji"],
            "presentation_kind": single_kana.get(
                "presentation_kind", "vowel_with_mouth"
            ),
            "sound_onset_ipa": single_kana.get("sound_onset_ipa"),
            "vowel_kana": item.get("vowel_kana"),
            "romanization": item.get("romanization"),
            "pronunciation_hint_zh": item["pronunciation_hint_zh"],
            "isolated_audio_url": public_audio_url(teaching["audio_asset_id"]),
            "isolated_audio_playback_rate": ISOLATED_KANA_PLAYBACK_RATE,
            "mouth_visual_url": (
                f"/api/single-kana-assets/{target_id}/mouth"
                if target_id in context.single_kana_mouth_paths
                else None
            ),
            "example_reading_ja": item["example_reading_ja"],
            "example_written_ja": item["example_written_ja"],
            "example_meaning_zh": item["example_meaning_zh"],
            "example_audio_url": f"/api/single-kana-assets/{target_id}/example-audio",
            "example_audio_script_ja": item["example_audio"]["script_ja"],
            "example_audio_kind": audio_kind,
            "cta_label_zh": item["cta_label_zh"],
            "stroke_attribution": {
                "name": single_kana["stroke_source"]["name"],
                "homepage": single_kana["stroke_source"]["homepage"],
                "license": single_kana["stroke_source"]["license"],
            },
        }
    elif stage == "foundation_batch_practice":
        batch = context.profile["micro_batches"][session["batch_index"]]
        runtime_order = practice_order(context, session, session["batch_index"])
        item_id = runtime_order[session["practice_index"]]
        item = context.practice_by_id[item_id]
        payload["micro_batch"] = batch_profile(context, session)
        if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1":
            payload["practice_item"] = sanitize_visual_choice(
                item,
                checkpoint=False,
                displayed_options=displayed_option_values(
                    item,
                    session=session,
                    stage_scope=f"practice:{batch['micro_batch_id']}",
                    position=session["practice_index"],
                    item_count=len(runtime_order),
                ),
            )
        elif item["response_mode"] == "single_choice":
            payload["practice_item"] = sanitize_sound_to_shape(
                item,
                checkpoint=False,
                displayed_options=displayed_option_values(
                    item,
                    session=session,
                    stage_scope=f"practice:{batch['micro_batch_id']}",
                    position=session["practice_index"],
                    item_count=len(runtime_order),
                ),
            )
        else:
            payload["practice_item"] = sanitize_shape_to_sound(
                item,
                displayed_audio_ids=displayed_audio_asset_ids(
                    item,
                    session=session,
                    stage_scope=f"practice:{batch['micro_batch_id']}",
                ),
            )
        payload["feedback"] = session["practice_feedback"]
    elif stage == "foundation_checkpoint_intro":
        payload["checkpoint_intro"] = {
            "title_zh": context.profile["learner_copy"]["checkpoint_intro_title"],
            "body_zh": context.profile["learner_copy"]["checkpoint_intro_body"],
            "item_count": len(context.checkpoint_order),
        }
    elif stage == "foundation_checkpoint":
        runtime_order = checkpoint_order(context, session)
        item_id = runtime_order[session["checkpoint_index"]]
        item = context.checkpoint_by_id[item_id]
        displayed = displayed_option_values(
            item,
            session=session,
            stage_scope="checkpoint",
            position=session["checkpoint_index"],
            item_count=len(runtime_order),
        )
        payload["checkpoint"] = (
            sanitize_visual_choice(item, checkpoint=True, displayed_options=displayed)
            if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1"
            else sanitize_sound_to_shape(item, checkpoint=True, displayed_options=displayed)
        )
    elif stage == "foundation_targeted_repair":
        target_id = session["recovery_target_ids"][session["recovery_index"]]
        teaching = context.teaching_by_target[target_id]
        guidance = context.profile.get("repair_guidance_by_target", {}).get(target_id, {})
        payload["repair"] = {
            "target_id": target_id,
            "display_ja": teaching["display_ja"],
            "presentation_kind": (
                "contextual_visual"
                if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1"
                else "direct_audio"
            ),
            "correct_audio_url": (
                None
                if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1"
                else public_audio_url(teaching["audio_asset_id"])
            ),
            "audio_playback_rate": ISOLATED_KANA_PLAYBACK_RATE,
            "source_kanji": guidance.get("source_kanji"),
            "hint_zh": guidance.get(
                "hint_zh", "重新听一遍，再把这个声音和上方写法对应起来。"
            ),
            "mouth_visual_url": (
                f"/api/repair-assets/{target_id}"
                if target_id in context.repair_visual_paths
                else None
            ),
            "title_zh": context.profile["learner_copy"]["repair_title"],
            "body_zh": context.profile["learner_copy"]["repair_body"],
        }
    elif stage == "foundation_retest":
        target_id = session["recovery_target_ids"][session["recovery_index"]]
        if context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1":
            item_id = next(
                item_id
                for item_id, mapped_target in context.practice_target_by_id.items()
                if mapped_target == target_id
            )
            item = context.practice_by_id[item_id]
            displayed = displayed_option_values(
                item,
                session=session,
                stage_scope="retest",
                position=session["recovery_index"],
                item_count=len(session["recovery_target_ids"]),
            )
            payload["retest"] = {
                **sanitize_visual_choice(item, checkpoint=False, displayed_options=displayed),
                "target_id": target_id,
                "title_zh": context.profile["learner_copy"]["retest_title"],
                "body_zh": context.profile["learner_copy"]["retest_body"],
            }
        else:
            item = context.shape_to_sound_by_target[target_id]
            payload["retest"] = {
                **sanitize_shape_to_sound(
                    item,
                    displayed_audio_ids=displayed_audio_asset_ids(
                        item, session=session, stage_scope="retest"
                    ),
                ),
                "target_id": target_id,
                "title_zh": context.profile["learner_copy"]["retest_title"],
                "body_zh": context.profile["learner_copy"]["retest_body"],
            }
    elif stage == "completed":
        payload["completion"] = result_payload(session_id, session)
    return payload


def require_step_token(session: dict, payload: dict) -> None:
    if payload.get("step_token") != step_token(session):
        raise ValueError("stale or invalid step_token")


def selected_text(displayed_options: list[str], option_id: str) -> str:
    if not isinstance(option_id, str) or len(option_id) != 1:
        raise ValueError("invalid option_id")
    index = ord(option_id) - ord("A")
    if index < 0 or index >= len(displayed_options):
        raise ValueError("invalid option_id")
    return displayed_options[index]


def selected_audio_asset(displayed_options: list[str], option_id: str) -> str:
    return selected_text(displayed_options, option_id)


def finish_session(session: dict) -> None:
    session["stage"] = "completed"
    session["status"] = "completed"
    session["completed_at"] = utc_now()
    session["result_id"] = f"n5-foundation-result-{secrets.token_hex(8)}"


def apply_action(session_id: str, session: dict, payload: dict) -> dict:
    if session["status"] == "completed":
        return stage_payload(session_id, session)
    require_step_token(session, payload)
    context = UNIT_CONTEXTS[session["request"]["work_unit_id"]]
    stage = session["stage"]
    action = payload.get("action")

    if stage == "foundation_batch_intro":
        batch = context.profile["micro_batches"][session["batch_index"]]
        if action != "start_micro_batch" or payload.get("asset_id") != batch["micro_batch_id"]:
            raise ValueError("invalid micro-batch start action")
        if single_kana_learning_for_batch(context, session["batch_index"]):
            session["single_kana_index"] = 0
            session["stage"] = "foundation_single_kana_learning"
        else:
            session["teaching_exposures"].extend(batch["target_ids"])
            if (
                context.definition["engine_profile"]
                == "foundation_contextual_pattern_checkpoint_v1"
                and session["batch_index"] + 1 < len(context.micro_batches)
            ):
                session["batch_index"] += 1
                session["stage"] = "foundation_batch_intro"
            else:
                if (
                    context.definition["engine_profile"]
                    == "foundation_contextual_pattern_checkpoint_v1"
                ):
                    session["batch_index"] = 0
                session["practice_index"] = 0
                session["practice_feedback"] = None
                session["stage"] = "foundation_batch_practice"
    elif stage == "foundation_single_kana_learning":
        single_kana = single_kana_learning_for_batch(context, session["batch_index"])
        if single_kana is None:
            raise ValueError("single-kana learning stage has no batch contract")
        items = single_kana["items"]
        item = items[session["single_kana_index"]]
        if action != "continue_single_kana" or payload.get("asset_id") != item["target_id"]:
            raise ValueError("invalid single-kana learning action")
        if item["target_id"] not in session["teaching_exposures"]:
            session["teaching_exposures"].append(item["target_id"])
        session["single_kana_index"] += 1
        if session["single_kana_index"] >= len(items):
            session["practice_index"] = 0
            session["practice_feedback"] = None
            session["stage"] = "foundation_batch_practice"
    elif stage == "foundation_batch_practice":
        batch = context.profile["micro_batches"][session["batch_index"]]
        runtime_order = practice_order(context, session, session["batch_index"])
        item_id = runtime_order[session["practice_index"]]
        item = context.practice_by_id[item_id]
        feedback = session["practice_feedback"]
        if feedback and feedback["correct"]:
            if action != "continue_practice":
                raise ValueError("correct practice feedback must be acknowledged")
            session["practice_feedback"] = None
            session["practice_index"] += 1
            if session["practice_index"] >= len(runtime_order):
                session["batch_index"] += 1
                session["practice_index"] = 0
                if session["batch_index"] >= len(context.micro_batches):
                    session["stage"] = "foundation_checkpoint_intro"
                else:
                    session["stage"] = (
                        "foundation_batch_practice"
                        if context.definition["engine_profile"]
                        == "foundation_contextual_pattern_checkpoint_v1"
                        else "foundation_batch_intro"
                    )
        else:
            if action != "submit_practice" or payload.get("asset_id") != item_id:
                raise ValueError("invalid foundation practice submission")
            option_id = payload.get("option_id")
            if item["response_mode"] == "single_choice":
                displayed_options = displayed_option_values(
                    item,
                    session=session,
                    stage_scope=f"practice:{batch['micro_batch_id']}",
                    position=session["practice_index"],
                    item_count=len(runtime_order),
                )
                selected = selected_text(displayed_options, option_id)
                correct = selected == item["correct_answer_ja"]
            else:
                displayed_options = displayed_audio_asset_ids(
                    item,
                    session=session,
                    stage_scope=f"practice:{batch['micro_batch_id']}",
                )
                selected = selected_audio_asset(displayed_options, option_id)
                correct = selected == item["correct_answer_audio_asset_id"]
            session["guided_responses"].append(
                {
                    "asset_id": item_id,
                    "target_id": practice_target_id(context, item),
                    "option_id": option_id,
                    "correct": correct,
                    "attempt": 1
                    + sum(
                        1
                        for row in session["guided_responses"]
                        if row["asset_id"] == item_id
                    ),
                }
            )
            session["practice_feedback"] = {
                "selected_option_id": option_id,
                "correct": correct,
                "message_zh": (
                    "答对了，可以进入下一题。"
                    if correct
                    else "还不对。再听一遍，重新选择。"
                ),
            }
    elif stage == "foundation_checkpoint_intro":
        if action != "start_checkpoint":
            raise ValueError("invalid foundation checkpoint start action")
        session["checkpoint_index"] = 0
        session["stage"] = "foundation_checkpoint"
    elif stage == "foundation_checkpoint":
        runtime_order = checkpoint_order(context, session)
        item_id = runtime_order[session["checkpoint_index"]]
        item = context.checkpoint_by_id[item_id]
        if action != "submit_checkpoint" or payload.get("asset_id") != item_id:
            raise ValueError("invalid foundation checkpoint submission")
        option_id = payload.get("option_id")
        displayed_options = displayed_option_values(
            item,
            session=session,
            stage_scope="checkpoint",
            position=session["checkpoint_index"],
            item_count=len(runtime_order),
        )
        selected = selected_text(displayed_options, option_id)
        session["checkpoint_responses"].append(
            {
                "asset_id": item_id,
                "target_id": item["target_id"],
                "option_id": option_id,
                "correct": selected == item["correct_answer_ja"],
            }
        )
        session["checkpoint_index"] += 1
        if session["checkpoint_index"] >= len(runtime_order):
            session["recovery_target_ids"] = [
                row["target_id"]
                for row in session["checkpoint_responses"]
                if not row["correct"]
            ]
            if session["recovery_target_ids"]:
                session["recovery_index"] = 0
                session["stage"] = "foundation_targeted_repair"
            else:
                finish_session(session)
    elif stage == "foundation_targeted_repair":
        target_id = session["recovery_target_ids"][session["recovery_index"]]
        if action != "start_retest" or payload.get("asset_id") != target_id:
            raise ValueError("invalid foundation retest start action")
        session["stage"] = "foundation_retest"
    elif stage == "foundation_retest":
        target_id = session["recovery_target_ids"][session["recovery_index"]]
        contextual = context.definition["engine_profile"] == "foundation_contextual_pattern_checkpoint_v1"
        if contextual:
            item_id = next(
                item_id
                for item_id, mapped_target in context.practice_target_by_id.items()
                if mapped_target == target_id
            )
            item = context.practice_by_id[item_id]
        else:
            item = context.shape_to_sound_by_target[target_id]
        if action != "submit_retest" or payload.get("asset_id") != item["practice_item_id"]:
            raise ValueError("invalid foundation retest submission")
        if contextual:
            displayed_options = displayed_option_values(
                item,
                session=session,
                stage_scope="retest",
                position=session["recovery_index"],
                item_count=len(session["recovery_target_ids"]),
            )
            selected = selected_text(displayed_options, payload.get("option_id"))
            correct = selected == item["correct_answer_ja"]
        else:
            displayed_options = displayed_audio_asset_ids(
                item, session=session, stage_scope="retest"
            )
            selected = selected_audio_asset(displayed_options, payload.get("option_id"))
            correct = selected == item["correct_answer_audio_asset_id"]
        session["recovery_responses"].append(
            {
                "asset_id": item["practice_item_id"],
                "target_id": target_id,
                "option_id": payload.get("option_id"),
                "correct": correct,
            }
        )
        session["recovery_index"] += 1
        if session["recovery_index"] >= len(session["recovery_target_ids"]):
            finish_session(session)
        else:
            session["stage"] = "foundation_targeted_repair"
    else:
        raise ValueError("session is not accepting actions")

    session["step_revision"] += 1
    session["updated_at"] = utc_now()
    return stage_payload(session_id, session)


def result_payload(session_id: str, session: dict) -> dict:
    context = UNIT_CONTEXTS[session["request"]["work_unit_id"]]
    checkpoint_correct_targets = {
        row["target_id"] for row in session["checkpoint_responses"] if row["correct"]
    }
    recovered_targets = {
        row["target_id"] for row in session["recovery_responses"] if row["correct"]
    }
    provisional_targets = checkpoint_correct_targets | recovered_targets
    unresolved_targets = set(context.target_order) - provisional_targets
    return {
        "schema_version": 1,
        "result_id": session["result_id"],
        "session_id": session_id,
        "work_unit_id": context.work_unit_id,
        "status": session["status"],
        "completed_at": session["completed_at"],
        "evidence_summary": {
            "session_mode": "new_learning",
            "new_learning_completed": session["status"] == "completed",
            "answered_count": len(session["checkpoint_responses"]),
            "correct_count": len(checkpoint_correct_targets),
            "scoring_contract_id": context.scoring_contract_id,
            "teaching_exposure_count": len(set(session["teaching_exposures"])),
            "guided_answered_count": len(session["guided_responses"]),
            "guided_target_count": len(
                {row["target_id"] for row in session["guided_responses"]}
            ),
            "micro_batch_count": len(context.micro_batches),
            "stage_checkpoint_answered_count": len(session["checkpoint_responses"]),
            "recovery_target_count": len(session["recovery_target_ids"]),
            "recovery_retest_answered_count": len(session["recovery_responses"]),
            "recovered_count": len(recovered_targets),
            "recovered_target_ids": sorted(recovered_targets),
            "provisional_pass_count": len(provisional_targets),
            "delayed_review_pending_count": len(provisional_targets),
            "delayed_review_target_ids": sorted(provisional_targets),
            "unresolved_count": len(unresolved_targets),
            "unresolved_target_ids": sorted(unresolved_targets),
            "mastery_claim": "not_inferred_from_single_session",
        },
    }


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
            or value.get("catalog_id") != CATALOG["catalog_id"]
            or not isinstance(value.get("sessions"), dict)
        ):
            raise ValueError("foundation persistent session store contract mismatch")
        for session_id, session in value["sessions"].items():
            if (
                not isinstance(session, dict)
                or session.get("request", {}).get("session_id") != session_id
                or session.get("stage") not in FOUNDATION_STAGES
                or session.get("status") not in {"in_progress", "completed"}
            ):
                raise ValueError("foundation persistent session is invalid")
            validate_session_request(session["request"])
            session.setdefault("recovery_index", 0)
            session.setdefault("single_kana_index", 0)
            session.setdefault("recovery_target_ids", [])
            session.setdefault("recovery_responses", [])
        self.sessions = value["sessions"]

    def persist(self) -> None:
        if self.store_path is not None:
            write_json_atomic(
                self.store_path,
                {
                    "schema_version": 1,
                    "catalog_id": CATALOG["catalog_id"],
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
        session = {
            "request": request,
            "stage": "foundation_batch_intro",
            "batch_index": 0,
            "single_kana_index": 0,
            "practice_index": 0,
            "checkpoint_index": 0,
            "recovery_index": 0,
            "practice_feedback": None,
            "step_revision": 0,
            "teaching_exposures": [],
            "guided_responses": [],
            "checkpoint_responses": [],
            "recovery_target_ids": [],
            "recovery_responses": [],
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
    work_unit_id = request.get("work_unit_id")
    if work_unit_id not in UNIT_CONTEXTS:
        raise ValueError("requested foundation unit is not loadable in the current catalog")
    if request.get("session_mode", "new_learning") != "new_learning":
        raise ValueError("foundation validation only supports new_learning")
    context = UNIT_CONTEXTS[work_unit_id]
    asset_ids = request.get("practice_asset_ids")
    if (
        not isinstance(asset_ids, list)
        or len(asset_ids) != len(set(asset_ids))
        or set(asset_ids) != set(context.checkpoint_order)
    ):
        raise ValueError("practice_asset_ids must match the foundation stage checkpoint set")
    if not isinstance(request.get("requested_at"), str) or not request["requested_at"]:
        raise ValueError("requested_at is required")


class FoundationLearningHandler(BaseHTTPRequestHandler):
    server_version = "N5FoundationLearning/1"

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

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self.send_json(
                {
                    "status": "ok",
                    "catalog_id": CATALOG["catalog_id"],
                    "work_unit_id": WORK_UNIT_ID,
                    "engine_profile": DEFAULT_CONTEXT.definition["engine_profile"],
                    "loadable_work_unit_count": len(UNIT_CONTEXTS),
                    "loadable_engine_profiles": sorted(
                        {
                            context.definition["engine_profile"]
                            for context in UNIT_CONTEXTS.values()
                        }
                    ),
                    "active_work_unit_count": FORMALLY_ACTIVE_WORK_UNIT_COUNT,
                    "formal_work_unit_active": WORK_UNIT_ID
                    in {
                        row["work_unit_id"]
                        for row in CATALOG["work_units"]
                        if row.get("status") == "active"
                    },
                    "bound_audio_asset_count": len(ALL_AUDIO_ASSETS),
                    "local_audio_override_id": (
                        AUDIO_OVERRIDE_MANIFEST.get("candidate_id")
                        if AUDIO_OVERRIDE_MANIFEST
                        else None
                    ),
                    "local_audio_override_count": (
                        AUDIO_OVERRIDE_MANIFEST.get("covered_target_count", 0)
                        if AUDIO_OVERRIDE_MANIFEST
                        else 0
                    ),
                    "persistent_session_store": SESSIONS.store_path is not None,
                }
            )
            return
        if path.startswith("/api/assets/"):
            asset_id = path.removeprefix("/api/assets/")
            asset = ALL_AUDIO_ASSETS.get(asset_id)
            if not asset:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            asset_path = project_path(asset["path"])
            content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
            self.send_bytes(asset_path.read_bytes(), content_type)
            return
        if path.startswith("/api/repair-assets/"):
            target_id = path.removeprefix("/api/repair-assets/")
            asset_path = ALL_REPAIR_VISUAL_PATHS.get(target_id)
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(asset_path.name)[0] or "image/png"
            self.send_bytes(asset_path.read_bytes(), content_type)
            return
        if path.startswith("/api/batch-articulation-assets/"):
            micro_batch_id = path.removeprefix("/api/batch-articulation-assets/")
            asset_path = ALL_BATCH_ARTICULATION_PATHS.get(micro_batch_id)
            if not asset_path:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(asset_path.name)[0] or "image/png"
            self.send_bytes(asset_path.read_bytes(), content_type)
            return
        single_kana_asset_match = re.fullmatch(
            r"/api/single-kana-assets/([^/]+)/(mouth|example-audio)", path
        )
        if single_kana_asset_match:
            target_id, asset_kind = single_kana_asset_match.groups()
            if asset_kind == "mouth":
                asset_path = ALL_SINGLE_KANA_MOUTH_PATHS.get(target_id)
            else:
                asset_path = ALL_SINGLE_KANA_EXAMPLE_AUDIO_PATHS.get(target_id)
            if not asset_path:
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
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--session-store", type=Path)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This local learning server only binds to localhost")
    SESSIONS.configure_store(args.session_store)
    server = ThreadingHTTPServer((args.host, args.port), FoundationLearningHandler)
    print(f"N5 foundation local player {UNIT_CODE}: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
