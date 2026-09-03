"""Load and validate one or more N5 local-player work units."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@dataclass(frozen=True)
class RuntimeContext:
    definition: dict
    work_unit_id: str
    unit_code: str
    display_title_zh: str
    scoring_contract_id: str
    unit: dict
    teaching_cards: list[dict]
    guided_items: list[dict]
    checkpoints: list[dict]
    answer_keys: dict[str, dict]
    audio_assets: dict[str, dict]
    audio_by_consumer: dict[str, str]
    grammar_teaching_copy: dict[str, dict]
    guided_practice_overrides: dict[str, dict]
    initial_checkpoint_overrides: dict[str, dict]
    recovery_items: list[dict]
    route_copy: dict
    learning_groups: list[dict]
    system_maps: list[dict]
    kanji_bindings: list[dict]
    meaning_contexts: list[dict]
    meaning_contexts_by_target: dict[str, list[dict]]
    variant_cards: list[dict]
    variant_cards_by_target: dict[str, list[dict]]
    variant_practice_by_card: dict[str, dict]
    embedded_support_cards: list[dict]
    embedded_support_by_target: dict[str, list[dict]]
    teaching_by_id: dict[str, dict]
    guided_by_id: dict[str, dict]
    checkpoint_by_id: dict[str, dict]
    recovery_by_id: dict[str, dict]
    recovery_by_target: dict[str, dict]
    guided_order: list[str]
    checkpoint_order: list[str]
    teaching_by_target: dict[str, dict]
    guided_by_target: dict[str, dict]
    checkpoint_by_target: dict[str, dict]
    target_order: list[str]
    teaching_order: list[str]
    group_start_indexes: list[int]
    group_by_target: dict[str, dict]


def project_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError(f"runtime contract path is missing or outside the project: {relative}")
    return path


def load_runtime_context(root: Path, runtime_definition_path: Path) -> RuntimeContext:
    definition = json.loads(runtime_definition_path.read_text(encoding="utf-8"))
    work_unit_id = definition["work_unit_id"]
    unit_code = definition["unit_code"]
    sources = definition["content_sources"]
    overlays = definition["runtime_overlays"]

    def active(rows: list[dict]) -> list[dict]:
        return [row for row in rows if row.get("unit_code") == unit_code]

    units = json.loads(project_path(root, sources["unit_file"]).read_text(encoding="utf-8"))
    unit = next(row for row in units if row["work_unit_id"] == work_unit_id)
    teaching_cards = active(read_jsonl(project_path(root, sources["teaching_cards"])))
    guided_items = active(read_jsonl(project_path(root, sources["guided_practice_items"])))
    checkpoints = active(read_jsonl(project_path(root, sources["checkpoints"])))
    assessment_data_path = sources.get("assessment_data") or sources.get("private_answer_keys")
    if not assessment_data_path:
        raise ValueError("runtime definition is missing assessment_data")
    answer_keys = {
        row["checkpoint_item_id"]: row
        for row in active(read_jsonl(project_path(root, assessment_data_path)))
    }
    all_audio_assets = {
        row["audio_asset_id"]: row
        for row in active(read_jsonl(project_path(root, sources["audio_assets"])))
    }
    if sources.get("variant_audio_assets"):
        for row in read_jsonl(project_path(root, sources["variant_audio_assets"])):
            all_audio_assets[row["audio_asset_id"]] = row
    if sources.get("variant_example_audio_assets"):
        for row in read_jsonl(project_path(root, sources["variant_example_audio_assets"])):
            all_audio_assets[row["audio_asset_id"]] = row
    audio_bindings = read_jsonl(project_path(root, overlays["audio_bindings"]))
    audio_by_consumer = {
        consumer_id: binding["audio_asset_id"]
        for binding in audio_bindings
        for consumer_id in binding["consumer_asset_ids"]
    }
    bound_audio_ids = {row["audio_asset_id"] for row in audio_bindings}
    if not bound_audio_ids <= set(all_audio_assets):
        raise ValueError(f"{unit_code} runtime audio bindings reference unknown formal audio")
    audio_assets = {
        asset_id: row for asset_id, row in all_audio_assets.items() if asset_id in bound_audio_ids
    }
    grammar_copy = json.loads(
        project_path(root, overlays["grammar_teaching_copy"]).read_text(encoding="utf-8")
    )["items"]
    adaptive = json.loads(
        project_path(root, overlays["adaptive_learning"]).read_text(encoding="utf-8")
    )
    system_maps = []
    if sources.get("system_maps"):
        system_maps = active(read_jsonl(project_path(root, sources["system_maps"])))
    if overlays.get("system_map_presentation"):
        presentation = json.loads(
            project_path(root, overlays["system_map_presentation"]).read_text(encoding="utf-8")
        )
        presentation_by_id = {
            row["system_map_snapshot_id"]: row for row in presentation["items"]
        }
        system_maps = [
            {**row, "presentation": presentation_by_id[row["system_map_snapshot_id"]]}
            for row in system_maps
        ]
    kanji_bindings = []
    if sources.get("kanji_bindings"):
        kanji_bindings = active(read_jsonl(project_path(root, sources["kanji_bindings"])))
    meaning_contexts = []
    if sources.get("meaning_contexts"):
        meaning_contexts = active(read_jsonl(project_path(root, sources["meaning_contexts"])))
    variant_cards = []
    if sources.get("variant_cards"):
        variant_cards = active(read_jsonl(project_path(root, sources["variant_cards"])))
    variant_practice_items = []
    if sources.get("variant_practice_items"):
        variant_practice_items = active(
            read_jsonl(project_path(root, sources["variant_practice_items"]))
        )
    embedded_support_cards = []
    if sources.get("embedded_support_cards"):
        embedded_support_cards = active(
            read_jsonl(project_path(root, sources["embedded_support_cards"]))
        )

    teaching_by_id = {row["teaching_card_id"]: row for row in teaching_cards}
    guided_by_id = {row["practice_item_id"]: row for row in guided_items}
    checkpoint_by_id = {row["checkpoint_item_id"]: row for row in checkpoints}
    recovery_items = adaptive["recovery_verification_items"]
    recovery_by_id = {row["verification_item_id"]: row for row in recovery_items}
    recovery_by_target = {row["primary_target_id"]: row for row in recovery_items}
    teaching_by_target = {row["primary_target_id"]: row for row in teaching_cards}
    guided_by_target = {row["primary_target_id"]: row for row in guided_items}
    checkpoint_by_target = {row["primary_target_id"]: row for row in checkpoints}
    learning_groups = adaptive["learning_groups"]
    target_order = [
        target_id for group in learning_groups for target_id in group["primary_target_ids"]
    ]
    teaching_order = [
        teaching_by_target[target_id]["teaching_card_id"] for target_id in target_order
    ]
    group_start_indexes = []
    cursor = 0
    for group in learning_groups:
        group_start_indexes.append(cursor)
        cursor += len(group["primary_target_ids"])
    group_by_target = {
        target_id: group for group in learning_groups for target_id in group["primary_target_ids"]
    }
    meaning_contexts_by_target: dict[str, list[dict]] = {}
    for row in meaning_contexts:
        meaning_contexts_by_target.setdefault(row["primary_target_id"], []).append(row)
    variant_cards_by_target: dict[str, list[dict]] = {}
    for row in variant_cards:
        variant_cards_by_target.setdefault(row["primary_target_id"], []).append(row)
    variant_practice_by_card = {
        row["variant_card_id"]: row for row in variant_practice_items
    }
    support_placements = adaptive.get("embedded_support_placements", [])
    support_by_id = {
        row["embedded_support_card_id"]: row for row in embedded_support_cards
    }
    embedded_support_by_target: dict[str, list[dict]] = {}
    for placement in support_placements:
        embedded_support_by_target.setdefault(
            placement["before_primary_target_id"], []
        ).append(support_by_id[placement["embedded_support_card_id"]])

    teaching_contract = unit["asset_contract"]["teaching_card_ids"]
    guided_order = unit["asset_contract"]["guided_practice_item_ids"]
    checkpoint_order = unit["asset_contract"]["independent_checkpoint_item_ids"]
    guided_overrides = adaptive["guided_practice_overrides"]
    checkpoint_overrides = adaptive["initial_checkpoint_overrides"]

    if set(teaching_contract) != set(teaching_by_id):
        raise ValueError(f"{unit_code} teaching card contract mismatch")
    if set(guided_order) != set(guided_by_id):
        raise ValueError(f"{unit_code} guided practice contract mismatch")
    if set(checkpoint_order) != set(checkpoint_by_id) or set(checkpoint_order) != set(answer_keys):
        raise ValueError(f"{unit_code} checkpoint or answer-key contract mismatch")
    if not all((root / row["path"]).is_file() for row in audio_assets.values()):
        raise ValueError(f"{unit_code} has missing bound audio files")
    grammar_ids = {
        row["teaching_card_id"] for row in teaching_cards if row["target_kind"] == "grammar"
    }
    if set(grammar_copy) != grammar_ids:
        raise ValueError(f"{unit_code} learner-facing grammar copy contract mismatch")
    grammar_guided_ids = {
        row["practice_item_id"]
        for row in guided_items
        if row["primary_target_id"].startswith("n5-grammar-")
    }
    if set(guided_overrides) != grammar_guided_ids:
        raise ValueError(f"{unit_code} guided grammar override contract mismatch")
    if set(checkpoint_overrides) != set(checkpoint_order):
        raise ValueError(f"{unit_code} initial checkpoint override contract mismatch")
    if set(recovery_by_target) != set(target_order) or len(recovery_by_id) != len(target_order):
        raise ValueError(f"{unit_code} recovery verification contract mismatch")
    if len(target_order) != len(set(target_order)) or set(target_order) != set(teaching_by_target):
        raise ValueError(f"{unit_code} learning groups must cover every teaching target exactly once")
    if any(
        not group.get("group_id")
        or not group.get("title_zh")
        or len(group.get("target_labels_zh", [])) != len(group["primary_target_ids"])
        or not 1 <= group.get("guided_practice_limit", 0) <= len(group["primary_target_ids"])
        for group in learning_groups
    ):
        raise ValueError(f"{unit_code} learning group metadata is invalid")
    for checkpoint_id, override in checkpoint_overrides.items():
        answer_id = answer_keys[checkpoint_id]["correct_option_id"]
        if answer_id not in {option["option_id"] for option in override["options"]}:
            raise ValueError(f"{unit_code} checkpoint override removed the approved answer option")
    for recovery in recovery_items:
        checkpoint_id = recovery["source_checkpoint_item_id"]
        if checkpoint_by_id[checkpoint_id]["primary_target_id"] != recovery["primary_target_id"]:
            raise ValueError(f"{unit_code} recovery verification target mismatch")
        answer_id = answer_keys[checkpoint_id]["correct_option_id"]
        if answer_id not in {option["option_id"] for option in recovery["options"]}:
            raise ValueError(f"{unit_code} recovery verification removed the approved answer option")
    expected_map_ids = set(unit["asset_contract"].get("system_map_snapshot_ids", []))
    if {row["system_map_snapshot_id"] for row in system_maps} != expected_map_ids:
        raise ValueError(f"{unit_code} system-map contract mismatch")
    if system_maps and any("presentation" not in row for row in system_maps):
        raise ValueError(f"{unit_code} system maps do not have a browser presentation contract")
    expected_kanji_ids = set(unit["asset_contract"].get("kanji_binding_ids", []))
    if unit.get("dense_kanji_microblock_required") and not sources.get("kanji_bindings"):
        raise ValueError(f"{unit_code} dense kanji unit is missing its binding source")
    if sources.get("kanji_bindings") and {
        row["kanji_binding_id"] for row in kanji_bindings
    } != expected_kanji_ids:
        raise ValueError(f"{unit_code} kanji-binding contract mismatch")
    if kanji_bindings and any(
        row.get("reading_claim_type") != "whole_word_only"
        or row.get("isolated_character_reading_claimed") is not False
        for row in kanji_bindings
    ):
        raise ValueError(f"{unit_code} kanji bindings must preserve whole-word-only readings")
    expected_meaning_ids = set(unit["asset_contract"].get("meaning_context_ids", []))
    if sources.get("meaning_contexts") and {
        row["meaning_context_id"] for row in meaning_contexts
    } != expected_meaning_ids:
        raise ValueError(f"{unit_code} meaning-context contract mismatch")
    if any(
        row["primary_target_id"] not in teaching_by_target
        or row.get("counts_as_separate_primary_target") is not False
        for row in meaning_contexts
    ):
        raise ValueError(f"{unit_code} meaning contexts exceed their support-only boundary")
    expected_variant_ids = set(unit["asset_contract"].get("form_reading_variant_card_ids", []))
    if sources.get("variant_cards") and {
        row["variant_card_id"] for row in variant_cards
    } != expected_variant_ids:
        raise ValueError(f"{unit_code} form-reading variant contract mismatch")
    if sources.get("variant_practice_items") and set(variant_practice_by_card) != expected_variant_ids:
        raise ValueError(f"{unit_code} form-reading variant practice contract mismatch")
    if sources.get("variant_practice_items") and {
        row["variant_practice_item_id"] for row in variant_practice_items
    } != set(unit["asset_contract"].get("form_reading_variant_practice_item_ids", [])):
        raise ValueError(f"{unit_code} form-reading variant practice asset mismatch")
    if any(
        row["primary_target_id"] not in teaching_by_target
        or row.get("does_not_create_new_primary_target") is not True
        for row in variant_cards
    ) or any(
        row.get("counts_as_independent_mastery_evidence") is not False
        for row in variant_practice_items
    ):
        raise ValueError(f"{unit_code} form-reading variants exceed recognition-only scope")
    expected_support_ids = set(unit["asset_contract"].get("embedded_support_card_ids", []))
    if sources.get("embedded_support_cards") and set(support_by_id) != expected_support_ids:
        raise ValueError(f"{unit_code} embedded-support contract mismatch")
    if sources.get("embedded_support_cards") and {
        row["embedded_support_card_id"] for row in support_placements
    } != expected_support_ids:
        raise ValueError(f"{unit_code} embedded-support placement contract mismatch")
    if any(
        placement["before_primary_target_id"] not in teaching_by_target
        for placement in support_placements
    ) or any(
        row.get("counts_as_primary_target") is not False
        or row.get("counts_as_mastery_evidence") is not False
        for row in embedded_support_cards
    ):
        raise ValueError(f"{unit_code} embedded support exceeds its support-only boundary")

    return RuntimeContext(
        definition=definition,
        work_unit_id=work_unit_id,
        unit_code=unit_code,
        display_title_zh=definition["display_title_zh"],
        scoring_contract_id=definition["result_contract"]["scoring_contract_id"],
        unit=unit,
        teaching_cards=teaching_cards,
        guided_items=guided_items,
        checkpoints=checkpoints,
        answer_keys=answer_keys,
        audio_assets=audio_assets,
        audio_by_consumer=audio_by_consumer,
        grammar_teaching_copy=grammar_copy,
        guided_practice_overrides=guided_overrides,
        initial_checkpoint_overrides=checkpoint_overrides,
        recovery_items=recovery_items,
        route_copy=adaptive["route_copy"],
        learning_groups=learning_groups,
        system_maps=system_maps,
        kanji_bindings=kanji_bindings,
        meaning_contexts=meaning_contexts,
        meaning_contexts_by_target=meaning_contexts_by_target,
        variant_cards=variant_cards,
        variant_cards_by_target=variant_cards_by_target,
        variant_practice_by_card=variant_practice_by_card,
        embedded_support_cards=embedded_support_cards,
        embedded_support_by_target=embedded_support_by_target,
        teaching_by_id=teaching_by_id,
        guided_by_id=guided_by_id,
        checkpoint_by_id=checkpoint_by_id,
        recovery_by_id=recovery_by_id,
        recovery_by_target=recovery_by_target,
        guided_order=guided_order,
        checkpoint_order=checkpoint_order,
        teaching_by_target=teaching_by_target,
        guided_by_target=guided_by_target,
        checkpoint_by_target=checkpoint_by_target,
        target_order=target_order,
        teaching_order=teaching_order,
        group_start_indexes=group_start_indexes,
        group_by_target=group_by_target,
    )
