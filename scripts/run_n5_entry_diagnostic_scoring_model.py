#!/usr/bin/env python3
"""Evaluate normalized response events with the approved N5 diagnostic model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "product/n5/diagnostic/scoring-v1/model.json"
ITEM_BANK_MAP_PATH = ROOT / "product/n5/diagnostic/scoring-v1/item-bank-map.json"
VALID_OUTCOMES = {"correct", "incorrect", "invalid"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ability_evidence(
    ability_id: str,
    item_ids: list[str],
    response_by_item: dict[str, dict],
) -> dict:
    events = [response_by_item[item_id] for item_id in item_ids if item_id in response_by_item]
    valid = [event for event in events if event["outcome"] in {"correct", "incorrect"}]
    correct_count = sum(event["outcome"] == "correct" for event in valid)
    incorrect_count = sum(event["outcome"] == "incorrect" for event in valid)
    invalid = [event for event in events if event["outcome"] == "invalid"]
    valid_count = len(valid)

    if valid_count == 0:
        status = "insufficient_evidence" if invalid else "not_measured"
    elif valid_count == 3 and correct_count == 3:
        status = "provisionally_secure"
    elif valid_count >= 2 and incorrect_count >= 1:
        status = "emerging"
    else:
        status = "insufficient_evidence"

    return {
        "ability_point_id": ability_id,
        "status": status,
        "attempted_item_count": valid_count,
        "correct_item_count": correct_count,
        "incorrect_item_count": incorrect_count,
        "invalid_item_count": len(invalid),
        "evidence_item_ids": [event["diagnostic_item_id"] for event in valid],
        "invalid_item_ids": [event["diagnostic_item_id"] for event in invalid],
        "asset_version_ids": sorted(
            {
                event["asset_version_id"]
                for event in events
                if event.get("asset_version_id")
            }
        ),
        "prerequisite_block_ids": [],
    }


def _missing(item_ids: list[str], response_by_item: dict[str, dict]) -> list[str]:
    return [item_id for item_id in item_ids if item_id not in response_by_item]


def _short_route(
    model: dict,
    bank: dict,
    response_by_item: dict[str, dict],
    evidence_by_ability: dict[str, dict],
) -> dict:
    assignments = bank["item_assignments"]
    stages = model["stages"]

    def anchor(ability_id: str) -> str:
        return assignments[ability_id]["anchor_item_id"]

    def probes(ability_id: str) -> list[str]:
        return assignments[ability_id]["probe_item_ids"]

    def outcome(item_id: str) -> str | None:
        event = response_by_item.get(item_id)
        return event["outcome"] if event else None

    script_ids = stages["foundation_script"]
    script_anchors = [anchor(ability_id) for ability_id in script_ids]
    missing = _missing(script_anchors, response_by_item)
    if missing:
        return {
            "status": "in_progress",
            "recommended_start_lane": None,
            "next_item_ids": missing,
            "reason_code": "foundation_script_anchors_incomplete",
        }

    if any(outcome(item_id) != "correct" for item_id in script_anchors):
        script_full = [
            item_id
            for ability_id in script_ids
            for item_id in [anchor(ability_id), *probes(ability_id)]
        ]
        missing = _missing(script_full, response_by_item)
        if missing:
            return {
                "status": "in_progress",
                "recommended_start_lane": None,
                "next_item_ids": missing,
                "reason_code": "foundation_script_boundary_probes_required",
            }
        first, second = (evidence_by_ability[ability_id] for ability_id in script_ids)
        if (
            first["status"] == "emerging"
            and second["status"] == "emerging"
            and first["correct_item_count"] <= 1
            and second["correct_item_count"] <= 1
        ):
            lane = "start_from_zero"
            reason = "both_script_abilities_confirmed_low"
        elif "emerging" in {first["status"], second["status"]}:
            lane = "foundation_repair"
            reason = "at_least_one_script_ability_confirmed_unstable"
        else:
            lane = "insufficient_evidence"
            reason = "script_boundary_not_confirmed"
        return {
            "status": "boundary_confirmed" if lane != "insufficient_evidence" else lane,
            "recommended_start_lane": lane,
            "next_item_ids": [],
            "reason_code": reason,
        }

    mora_id = stages["foundation_mora"][0]
    mora_anchor = anchor(mora_id)
    if mora_anchor not in response_by_item:
        return {
            "status": "in_progress",
            "recommended_start_lane": None,
            "next_item_ids": [mora_anchor],
            "reason_code": "foundation_mora_anchor_required",
        }
    if outcome(mora_anchor) != "correct":
        foundation_full = [
            item_id
            for ability_id in [*script_ids, mora_id]
            for item_id in [anchor(ability_id), *probes(ability_id)]
        ]
        missing = _missing(foundation_full, response_by_item)
        if missing:
            return {
                "status": "in_progress",
                "recommended_start_lane": None,
                "next_item_ids": missing,
                "reason_code": "foundation_full_profile_required",
            }
        mora_evidence = evidence_by_ability[mora_id]
        lane = (
            "foundation_repair"
            if mora_evidence["status"] == "emerging"
            else "insufficient_evidence"
        )
        return {
            "status": "boundary_confirmed" if lane != "insufficient_evidence" else lane,
            "recommended_start_lane": lane,
            "next_item_ids": [],
            "reason_code": "special_mora_confirmed_unstable"
            if lane == "foundation_repair"
            else "special_mora_boundary_not_confirmed",
        }

    for stage_id, lane in [
        ("core_language", "core_language_build"),
        ("receptive", "receptive_integration"),
    ]:
        ability_ids = stages[stage_id]
        anchor_ids = [anchor(ability_id) for ability_id in ability_ids]
        missing = _missing(anchor_ids, response_by_item)
        if missing:
            return {
                "status": "in_progress",
                "recommended_start_lane": None,
                "next_item_ids": missing,
                "reason_code": f"{stage_id}_anchors_incomplete",
            }
        problem_abilities = [
            ability_id
            for ability_id in ability_ids
            if outcome(anchor(ability_id)) != "correct"
        ][:3]
        if problem_abilities:
            required_probes = [
                item_id
                for ability_id in problem_abilities
                for item_id in probes(ability_id)
            ]
            missing = _missing(required_probes, response_by_item)
            if missing:
                return {
                    "status": "in_progress",
                    "recommended_start_lane": None,
                    "next_item_ids": missing,
                    "reason_code": f"{stage_id}_boundary_probes_required",
                }
            if any(
                evidence_by_ability[ability_id]["status"] == "emerging"
                for ability_id in problem_abilities
            ):
                return {
                    "status": "boundary_confirmed",
                    "recommended_start_lane": lane,
                    "next_item_ids": [],
                    "reason_code": f"{stage_id}_boundary_confirmed",
                }
            return {
                "status": "insufficient_evidence",
                "recommended_start_lane": "insufficient_evidence",
                "next_item_ids": [],
                "reason_code": f"{stage_id}_boundary_not_confirmed",
            }

    return {
        "status": "upper_boundary_screen_positive",
        "recommended_start_lane": "mock_readiness_candidate",
        "next_item_ids": [],
        "reason_code": "all_eighteen_anchors_valid_and_correct",
    }


def evaluate_session(
    session: dict,
    model: dict | None = None,
    bank: dict | None = None,
) -> dict:
    model = model or read_json(MODEL_PATH)
    bank = bank or read_json(ITEM_BANK_MAP_PATH)
    if session.get("mode") != "short_placement":
        raise ValueError("The v1 reference engine currently supports short_placement only")

    known_item_ids = set(bank["item_to_ability"])
    response_by_item: dict[str, dict] = {}
    for event in session.get("response_events", []):
        item_id = event.get("diagnostic_item_id")
        if item_id not in known_item_ids:
            raise ValueError(f"Unknown diagnostic_item_id: {item_id}")
        if item_id in response_by_item:
            raise ValueError(f"Duplicate response event for item: {item_id}")
        if event.get("outcome") not in VALID_OUTCOMES:
            raise ValueError(f"Invalid outcome for item: {item_id}")
        if event["outcome"] == "invalid" and not event.get("invalid_reason"):
            raise ValueError(f"invalid_reason is required for invalid item: {item_id}")
        response_by_item[item_id] = event

    evidence = []
    for ability_id in model["language_ability_order"]:
        assignment = bank["item_assignments"][ability_id]
        item_ids = [assignment["anchor_item_id"], *assignment["probe_item_ids"]]
        evidence.append(_ability_evidence(ability_id, item_ids, response_by_item))
    evidence_by_ability = {row["ability_point_id"]: row for row in evidence}
    for row in evidence:
        row["prerequisite_block_ids"] = [
            prerequisite_id
            for prerequisite_id in bank["ability_prerequisite_ids"].get(
                row["ability_point_id"], []
            )
            if evidence_by_ability[prerequisite_id]["status"] == "emerging"
        ]
    route = _short_route(model, bank, response_by_item, evidence_by_ability)

    valid_events = [
        event
        for event in response_by_item.values()
        if event["outcome"] in {"correct", "incorrect"}
    ]
    correct_count = sum(event["outcome"] == "correct" for event in valid_events)
    covered = [
        row
        for row in evidence
        if row["status"] in {"emerging", "provisionally_secure"}
    ]
    invalid_events = [
        event for event in response_by_item.values() if event["outcome"] == "invalid"
    ]
    valid_anchor_ids = [
        assignment["anchor_item_id"]
        for assignment in bank["item_assignments"].values()
        if response_by_item.get(assignment["anchor_item_id"], {}).get("outcome")
        in {"correct", "incorrect"}
    ]

    return {
        "schema_version": 1,
        "model_id": model["model_id"],
        "session_id": session.get("session_id"),
        "mode": session["mode"],
        "recommended_start_lane": route["recommended_start_lane"],
        "short_screen_outcome": {
            "status": route["status"],
            "reason_code": route["reason_code"],
            "next_item_ids": route["next_item_ids"],
        },
        "ability_evidence": evidence,
        "confirmed_strength_ability_ids": [
            row["ability_point_id"]
            for row in evidence
            if row["status"] == "provisionally_secure"
        ],
        "priority_gap_ability_ids": [
            row["ability_point_id"]
            for row in evidence
            if row["status"] == "emerging"
        ],
        "deferred_or_unmeasured_ability_ids": [
            row["ability_point_id"]
            for row in evidence
            if row["status"] in {"not_measured", "insufficient_evidence"}
        ],
        "diagnostic_coverage": {
            "covered_ability_count": len(covered),
            "planned_language_ability_count": len(model["language_ability_order"]),
            "diagnostic_coverage_percent": round(
                len(covered) * 100 / len(model["language_ability_order"])
            ),
            "meaning": "measurement_coverage_not_learning_progress",
        },
        "screening_coverage": {
            "screened_ability_count": len(valid_anchor_ids),
            "planned_language_ability_count": len(model["language_ability_order"]),
            "screening_coverage_percent": round(
                len(valid_anchor_ids) * 100 / len(model["language_ability_order"])
            ),
            "meaning": "abilities_reached_by_valid_anchor_not_conclusion_strength",
        },
        "raw_accuracy": {
            "valid_attempt_count": len(valid_events),
            "correct_count": correct_count,
            "ratio": round(correct_count / len(valid_events), 4)
            if valid_events
            else None,
            "allowed_as_primary_result": False,
        },
        "evidence_quality_flags": [
            {
                "diagnostic_item_id": event["diagnostic_item_id"],
                "flag": event["invalid_reason"],
            }
            for event in invalid_events
        ],
        "pass_confidence": {
            "value": None,
            "status": "not_available_from_entry_diagnostic",
        },
        "planning_outlook": {
            "estimated_days": None,
            "status": "requires_course_work_units_and_time_baselines",
            "first_personalized_reforecast_after_learning_days": 7,
        },
        "next_validation_action": model["route_next_actions"].get(
            route["recommended_start_lane"], "continue_short_diagnostic"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_session(read_json(args.session_path))
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
