"""Load and validate one foundation micro-batch runtime definition."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def project_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError(f"runtime contract path is missing or outside the project: {relative}")
    return path


@dataclass(frozen=True)
class FoundationRuntimeContext:
    definition: dict
    work_unit_id: str
    unit_code: str
    display_title_zh: str
    scoring_contract_id: str
    unit: dict
    profile: dict
    micro_batches: list[dict]
    target_order: list[str]
    teaching_cards: list[dict]
    teaching_by_target: dict[str, dict]
    practice_items: list[dict]
    practice_by_id: dict[str, dict]
    practice_target_by_id: dict[str, str]
    sound_to_shape_by_target: dict[str, dict]
    shape_to_sound_by_target: dict[str, dict]
    checkpoints: list[dict]
    checkpoint_by_id: dict[str, dict]
    checkpoint_order: list[str]
    audio_assets: dict[str, dict]
    repair_visual_paths: dict[str, Path]
    single_kana_learning_by_target: dict[str, dict]
    single_kana_strokes_by_target: dict[str, dict]
    single_kana_mouth_paths: dict[str, Path]
    single_kana_example_audio_paths: dict[str, Path]
    batch_articulation_paths: dict[str, Path]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_foundation_runtime_context(
    root: Path, runtime_definition_path: Path
) -> FoundationRuntimeContext:
    definition = json.loads(runtime_definition_path.read_text(encoding="utf-8"))
    if definition.get("runtime_contract_id") != "n5-local-player-foundation-microbatch-runtime-v1":
        raise ValueError("unsupported foundation runtime contract")
    engine_profile = definition.get("engine_profile")
    if engine_profile not in {
        "foundation_microbatch_audio_checkpoint_v1",
        "foundation_contextual_pattern_checkpoint_v1",
    }:
        raise ValueError("unsupported foundation engine profile")

    work_unit_id = definition["work_unit_id"]
    sources = definition["content_sources"]
    units_value = json.loads(project_path(root, sources["unit_file"]).read_text(encoding="utf-8"))
    units = units_value.get("units", units_value)
    unit = next(row for row in units if row["work_unit_id"] == work_unit_id)
    profile = json.loads(
        project_path(
            root, definition["runtime_overlays"]["foundation_learning_profile"]
        ).read_text(encoding="utf-8")
    )

    def active(rows: list[dict]) -> list[dict]:
        return [row for row in rows if row.get("work_unit_id") == work_unit_id]

    teaching_cards = active(read_jsonl(project_path(root, sources["teaching_cards"])))
    practice_items = active(read_jsonl(project_path(root, sources["practice_items"])))
    checkpoints = active(read_jsonl(project_path(root, sources["checkpoint_items"])))
    all_audio = {
        row["audio_asset_id"]: row
        for row in read_jsonl(project_path(root, sources["audio_assets"]))
    }

    micro_batches = unit["micro_batches"]
    target_order = [
        target_id for batch in micro_batches for target_id in batch["target_ids"]
    ]
    teaching_by_target = {row["target_id"]: row for row in teaching_cards}
    practice_by_id = {row["practice_item_id"]: row for row in practice_items}
    practice_target_by_id: dict[str, str] = {}
    sound_to_shape_by_target: dict[str, dict] = {}
    shape_to_sound_by_target: dict[str, dict] = {}
    if engine_profile == "foundation_microbatch_audio_checkpoint_v1":
        for target_id, teaching in teaching_by_target.items():
            target_suffix = re.sub(r"^n5-fnd-u[0-9]+-target-", "", target_id)
            sound_matches = [
                row
                for row in practice_items
                if row.get("tests") == "direct_sound_to_shape"
                and row.get("practice_item_id", "").endswith(
                    f"sound-to-shape-{target_suffix}"
                )
                and row.get("correct_answer_ja") == teaching["display_ja"]
            ]
            shape_matches = [
                row
                for row in practice_items
                if row.get("tests") == "direct_shape_to_sound"
                and row.get("practice_item_id", "").endswith(
                    f"shape-to-sound-{target_suffix}"
                )
                and row.get("correct_answer_audio_asset_id") == teaching["audio_asset_id"]
            ]
            if len(sound_matches) != 1 or len(shape_matches) != 1:
                raise ValueError("foundation practice must provide both directions per target")
            sound_to_shape_by_target[target_id] = sound_matches[0]
            shape_to_sound_by_target[target_id] = shape_matches[0]
            practice_target_by_id[sound_matches[0]["practice_item_id"]] = target_id
            practice_target_by_id[shape_matches[0]["practice_item_id"]] = target_id
    checkpoint_by_id = {row["checkpoint_item_id"]: row for row in checkpoints}
    profile_batches = profile["micro_batches"]
    checkpoint_order = profile["stage_checkpoint_item_ids"]

    if profile.get("engine_profile") != definition["engine_profile"]:
        raise ValueError("foundation profile engine mismatch")
    if profile.get("work_unit_id") != work_unit_id:
        raise ValueError("foundation profile work unit mismatch")
    expected_modes = (
        ["direct_sound_to_shape", "direct_shape_to_sound"]
        if engine_profile == "foundation_microbatch_audio_checkpoint_v1"
        else ["contextual_pattern_visual_recognition"]
    )
    if profile.get("guided_practice_modes") != expected_modes:
        raise ValueError("foundation profile guided practice mode mismatch")
    if len(target_order) != len(set(target_order)) or set(target_order) != set(teaching_by_target):
        raise ValueError("foundation teaching cards must cover each target exactly once")
    if [row["micro_batch_id"] for row in profile_batches] != [
        row["micro_batch_id"] for row in micro_batches
    ]:
        raise ValueError("foundation profile micro-batch order mismatch")

    practice_ids: list[str] = []
    required_audio_ids: set[str] = set()
    for source_batch, profile_batch in zip(micro_batches, profile_batches, strict=True):
        if profile_batch["target_ids"] != source_batch["target_ids"]:
            raise ValueError("foundation profile target order mismatch")
        source_sequence_id = source_batch.get("sequence_audio_asset_id")
        profile_sequence_id = profile_batch.get("sequence_audio_asset_id")
        if profile_sequence_id != source_sequence_id and profile_batch.get(
            "replaces_sequence_audio_asset_id"
        ) != source_sequence_id:
            raise ValueError("foundation profile sequence audio replacement mismatch")
        if engine_profile == "foundation_microbatch_audio_checkpoint_v1":
            required_audio_ids.add(profile_sequence_id)
        if len(profile_batch["practice_item_ids"]) != len(source_batch["target_ids"]):
            raise ValueError("foundation micro-batch must provide one guided item per target")
        for target_id, item_id in zip(
            source_batch["target_ids"], profile_batch["practice_item_ids"], strict=True
        ):
            item = practice_by_id.get(item_id)
            teaching = teaching_by_target[target_id]
            expected_test = (
                "direct_sound_to_shape"
                if engine_profile == "foundation_microbatch_audio_checkpoint_v1"
                else "contextual_pattern_visual_recognition"
            )
            if not item or item.get("tests") != expected_test or item.get(
                "correct_answer_ja"
            ) != teaching.get("display_ja"):
                raise ValueError("foundation guided item does not match its target")
            practice_target_by_id[item_id] = target_id
            if engine_profile == "foundation_microbatch_audio_checkpoint_v1":
                target_audio = teaching["audio_asset_id"]
                if item.get("prompt_audio_asset_id") != target_audio:
                    raise ValueError("foundation guided item audio does not match its target")
                required_audio_ids.add(target_audio)
                shape_item = shape_to_sound_by_target[target_id]
                required_audio_ids.update(shape_item["option_audio_asset_ids"])
            practice_ids.append(item_id)

    if len(practice_ids) != len(set(practice_ids)):
        raise ValueError("foundation guided practice item ids must be unique")
    if len(checkpoint_order) != len(target_order) or len(checkpoint_order) != len(
        set(checkpoint_order)
    ):
        raise ValueError("foundation stage check must contain one item per target")
    for target_id, item_id in zip(target_order, checkpoint_order, strict=True):
        item = checkpoint_by_id.get(item_id)
        expected_checkpoint_test = (
            "independent_sound_to_shape"
            if engine_profile == "foundation_microbatch_audio_checkpoint_v1"
            else "independent_contextual_pattern_visual_recognition"
        )
        if (
            not item
            or item.get("target_id") != target_id
            or item.get("tests") != expected_checkpoint_test
            or item.get("exposure_policy") != "withheld_until_stage_checkpoint"
        ):
            raise ValueError("foundation stage checkpoint contract mismatch")
        if engine_profile == "foundation_microbatch_audio_checkpoint_v1":
            required_audio_ids.add(item["prompt_audio_asset_id"])

    if engine_profile == "foundation_contextual_pattern_checkpoint_v1":
        context_audio_assets = profile.get("context_audio_assets", [])
        audio_assets = {row["audio_asset_id"]: row for row in context_audio_assets}
        required_audio_ids = {
            audio_id
            for batch in profile_batches
            for audio_id in batch["contextual_lesson"]["audio_asset_ids"]
        }
        if required_audio_ids != set(audio_assets):
            raise ValueError("contextual lesson audio set mismatch")
        if not all(
            row.get("asset_role") == "isolated_vocabulary_pronunciation"
            and row.get("audio_asset_id", "").startswith("n5-vocab-audio-")
            and row.get("path", "").startswith("product/n5/audio/vocabulary-v2/mp3/")
            for row in audio_assets.values()
        ):
            raise ValueError("contextual lessons must use the approved vocabulary audio layer")
    else:
        if not required_audio_ids <= set(all_audio):
            raise ValueError("foundation runtime references unknown audio assets")
        audio_assets = {asset_id: all_audio[asset_id] for asset_id in required_audio_ids}
    for asset_id, asset in audio_assets.items():
        path = project_path(root, asset["path"])
        if _file_sha256(path) != asset["sha256"]:
            raise ValueError(f"foundation audio hash mismatch: {asset_id}")

    repair_visual_paths: dict[str, Path] = {}
    for target_id, guidance in profile.get("repair_guidance_by_target", {}).items():
        if target_id not in teaching_by_target:
            raise ValueError("foundation repair guidance references an unknown target")
        visual_path_value = guidance.get("mouth_visual_path")
        if not visual_path_value:
            continue
        visual_path = project_path(root, visual_path_value)
        if visual_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("foundation repair visual must be a supported image")
        repair_visual_paths[target_id] = visual_path

    batch_articulation_paths: dict[str, Path] = {}
    for batch in profile_batches:
        support = batch.get("articulation_support")
        if not support:
            continue
        if support.get("support_kind") != "shared_consonant_closure":
            raise ValueError("unsupported foundation batch articulation support")
        required_copy = ("row_name_zh", "intro_title_zh", "intro_body_zh", "title_zh", "body_zh", "alt_zh")
        if not all(isinstance(support.get(key), str) and support[key].strip() for key in required_copy):
            raise ValueError("foundation batch articulation copy is incomplete")
        visual_path = project_path(root, support["visual_path"])
        if visual_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("foundation batch articulation visual must be a supported image")
        if _file_sha256(visual_path) != support.get("visual_sha256"):
            raise ValueError("foundation batch articulation visual hash mismatch")
        batch_articulation_paths[batch["micro_batch_id"]] = visual_path

    single_kana_learning_by_target: dict[str, dict] = {}
    single_kana_strokes_by_target: dict[str, dict] = {}
    single_kana_mouth_paths: dict[str, Path] = {}
    single_kana_example_audio_paths: dict[str, Path] = {}
    single_kana_learning_batches = profile.get("single_kana_learning_batches")
    if single_kana_learning_batches is None:
        legacy_single_kana_learning = profile.get("single_kana_learning")
        single_kana_learning_batches = (
            [legacy_single_kana_learning] if legacy_single_kana_learning else []
        )
    detailed_micro_batch_ids: set[str] = set()
    for single_kana_learning in single_kana_learning_batches:
        micro_batch_id = single_kana_learning.get("micro_batch_id")
        if micro_batch_id in detailed_micro_batch_ids:
            raise ValueError("single-kana learning micro-batch must be unique")
        detailed_micro_batch_ids.add(micro_batch_id)
        source_batch = next(
            (
                row
                for row in profile_batches
                if row["micro_batch_id"] == micro_batch_id
            ),
            None,
        )
        if not source_batch:
            raise ValueError("single-kana learning references an unknown micro-batch")
        source = single_kana_learning.get("stroke_source", {})
        if source.get("name") != "KanjiVG" or source.get("license") != "CC BY-SA 3.0":
            raise ValueError("single-kana stroke source attribution is incomplete")
        project_path(root, source["license_path"])
        items = single_kana_learning.get("items", [])
        if [row.get("target_id") for row in items] != source_batch["target_ids"]:
            raise ValueError("single-kana learning must follow its micro-batch target order")
        presentation_kind = single_kana_learning.get(
            "presentation_kind", "vowel_with_mouth"
        )
        if presentation_kind not in {"vowel_with_mouth", "consonant_without_mouth"}:
            raise ValueError("unsupported single-kana presentation kind")
        if (
            presentation_kind == "consonant_without_mouth"
            and not single_kana_learning.get("sound_onset_ipa")
        ):
            raise ValueError("consonant single-kana learning needs a sound onset")
        matrix_pattern = re.compile(
            r"matrix\(\s*1\s+0\s+0\s+1\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s*\)"
        )
        svg_namespace = "{http://www.w3.org/2000/svg}"
        for item in items:
            target_id = item["target_id"]
            if target_id not in teaching_by_target:
                raise ValueError("single-kana learning references an unknown target")
            stroke_path = project_path(root, item["stroke_svg_path"])
            if stroke_path.suffix.lower() != ".svg":
                raise ValueError("single-kana stroke asset must be SVG")
            svg_root = ET.parse(stroke_path).getroot()
            paths = [
                {"d": row.attrib["d"]}
                for row in svg_root.iter(f"{svg_namespace}path")
                if row.attrib.get("d")
            ]
            if len(paths) != item["stroke_count"]:
                raise ValueError("single-kana stroke count does not match KanjiVG")
            labels = []
            for row in svg_root.iter(f"{svg_namespace}text"):
                match = matrix_pattern.fullmatch(row.attrib.get("transform", ""))
                if match and row.text:
                    labels.append(
                        {
                            "text": row.text.strip(),
                            "x": float(match.group(1)),
                            "y": float(match.group(2)),
                        }
                    )
            mouth_visual_path = item.get("mouth_visual_path")
            if presentation_kind == "vowel_with_mouth" and not mouth_visual_path:
                raise ValueError("vowel single-kana learning needs a mouth visual")
            if presentation_kind == "consonant_without_mouth":
                if mouth_visual_path:
                    raise ValueError("consonant single-kana learning must not use a mouth visual")
                if not item.get("vowel_kana") or not item.get("romanization"):
                    raise ValueError("consonant single-kana sound relation is incomplete")
            if mouth_visual_path:
                mouth_path = project_path(root, mouth_visual_path)
                if mouth_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("single-kana mouth asset must be a supported image")
                single_kana_mouth_paths[target_id] = mouth_path
            example_audio = item["example_audio"]
            if example_audio.get("script_ja") != item.get("example_reading_ja"):
                raise ValueError("single-kana example audio must speak the displayed word")
            voice_name = example_audio.get("voice_name")
            if voice_name is not None and (
                not isinstance(voice_name, str) or not voice_name.strip()
            ):
                raise ValueError("single-kana example audio voice name must be a non-empty string")
            if example_audio.get("speaking_rate") != 1.0:
                raise ValueError("single-kana example audio must use the approved speaking rate")
            example_audio_path = project_path(root, example_audio["path"])
            if _file_sha256(example_audio_path) != example_audio["sha256"]:
                raise ValueError("single-kana example audio hash mismatch")
            single_kana_learning_by_target[target_id] = item
            single_kana_strokes_by_target[target_id] = {
                "paths": [
                    {**row, "number": index + 1} for index, row in enumerate(paths)
                ],
                "labels": labels,
            }
            single_kana_example_audio_paths[target_id] = example_audio_path

    if definition["calibration"] != {
        "baseline_minutes": None,
        "numeric_mastery_threshold": None,
    }:
        raise ValueError("foundation runtime calibration must remain unset")

    return FoundationRuntimeContext(
        definition=definition,
        work_unit_id=work_unit_id,
        unit_code=definition["unit_code"],
        display_title_zh=definition["display_title_zh"],
        scoring_contract_id=definition["result_contract"]["scoring_contract_id"],
        unit=unit,
        profile=profile,
        micro_batches=micro_batches,
        target_order=target_order,
        teaching_cards=teaching_cards,
        teaching_by_target=teaching_by_target,
        practice_items=practice_items,
        practice_by_id=practice_by_id,
        practice_target_by_id=practice_target_by_id,
        sound_to_shape_by_target=sound_to_shape_by_target,
        shape_to_sound_by_target=shape_to_sound_by_target,
        checkpoints=checkpoints,
        checkpoint_by_id=checkpoint_by_id,
        checkpoint_order=checkpoint_order,
        audio_assets=audio_assets,
        repair_visual_paths=repair_visual_paths,
        single_kana_learning_by_target=single_kana_learning_by_target,
        single_kana_strokes_by_target=single_kana_strokes_by_target,
        single_kana_mouth_paths=single_kana_mouth_paths,
        single_kana_example_audio_paths=single_kana_example_audio_paths,
        batch_articulation_paths=batch_articulation_paths,
    )
