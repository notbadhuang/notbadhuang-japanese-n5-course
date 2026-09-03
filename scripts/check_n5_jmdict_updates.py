#!/usr/bin/env python3
"""Compare the course's JMdict subset with a current or supplied JMdict snapshot."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINDINGS = ROOT / "product/n5/audio/vocabulary-v2/bindings.jsonl"
DEFAULT_DOWNLOAD_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_bindings(path: Path) -> dict[str, set[tuple[str, str]]]:
    expected: dict[str, set[tuple[str, str]]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        try:
            seq = str(row["jmdict_seq"])
            pair = (str(row["form"]), str(row["reading"]))
        except KeyError as exc:
            raise ValueError(f"binding line {line_number} is missing {exc.args[0]}") from exc
        expected.setdefault(seq, set()).add(pair)
    if not expected:
        raise ValueError("no JMdict bindings found")
    return expected


def entry_pairs(entry: ET.Element) -> set[tuple[str, str]]:
    written_forms = [node.text for node in entry.findall("./k_ele/keb") if node.text]
    pairs: set[tuple[str, str]] = set()
    for reading_element in entry.findall("./r_ele"):
        reading = reading_element.findtext("reb")
        if not reading:
            continue
        pairs.add((reading, reading))
        restrictions = [
            node.text for node in reading_element.findall("re_restr") if node.text
        ]
        if reading_element.find("re_nokanji") is None:
            for form in restrictions or written_forms:
                pairs.add((form, reading))
    return pairs


def read_relevant_entries(
    snapshot: Path, wanted_sequences: set[str]
) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {}
    opener = gzip.open if snapshot.suffix == ".gz" else open
    with opener(snapshot, "rb") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "entry":
                continue
            sequence = element.findtext("ent_seq")
            if sequence in wanted_sequences:
                found[sequence] = entry_pairs(element)
            element.clear()
    return found


def compare(snapshot: Path, bindings: Path, source_url: str | None = None) -> dict:
    expected = load_bindings(bindings)
    current = read_relevant_entries(snapshot, set(expected))
    missing_entries = sorted(set(expected) - set(current), key=int)
    changed_bindings = []
    for sequence in sorted(expected, key=int):
        for form, reading in sorted(expected[sequence]):
            if (form, reading) not in current.get(sequence, set()):
                changed_bindings.append(
                    {
                        "jmdict_seq": sequence,
                        "form": form,
                        "reading": reading,
                        "available_pairs": [
                            {"form": current_form, "reading": current_reading}
                            for current_form, current_reading in sorted(
                                current.get(sequence, set())
                            )
                        ],
                    }
                )
    status = "no_relevant_changes" if not missing_entries and not changed_bindings else "review_required"
    return {
        "schema_version": 1,
        "status": status,
        "source_url": source_url,
        "snapshot_file": snapshot.name,
        "snapshot_sha256": sha256(snapshot),
        "course_entry_sequence_count": len(expected),
        "course_form_reading_binding_count": sum(len(pairs) for pairs in expected.values()),
        "matched_entry_sequence_count": len(current),
        "missing_entry_sequences": missing_entries,
        "changed_form_reading_bindings": changed_bindings,
        "automatic_course_mutation_performed": False,
    }


def download_snapshot(url: str, destination: Path) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": "n5-jmdict-update-check/1"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
        return response.headers.get("Last-Modified")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check only the 701 JMdict entries used by the course. The command reports "
            "changes and never edits course data."
        )
    )
    parser.add_argument("--bindings", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--download-url", default=DEFAULT_DOWNLOAD_URL)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bindings = args.bindings.resolve()
    if args.snapshot:
        snapshot = args.snapshot.resolve()
        report = compare(snapshot, bindings)
    else:
        with tempfile.TemporaryDirectory(prefix="n5-jmdict-update-") as temp_name:
            snapshot = Path(temp_name) / "JMdict_e-current.xml.gz"
            last_modified = download_snapshot(args.download_url, snapshot)
            report = compare(snapshot, bindings, args.download_url)
            report["upstream_last_modified"] = last_modified
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "no_relevant_changes" else 3


if __name__ == "__main__":
    sys.exit(main())
