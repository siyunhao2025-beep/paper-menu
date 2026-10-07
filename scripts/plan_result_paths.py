#!/usr/bin/env python3
"""Allocate shared result numbers for code, figure, and table artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


KIND_DIRECTORIES = {
    "code": "2_代码",
    "figure": "3_图片",
    "table": "4_表格",
}

RESULT_PATTERN = re.compile(r"^(\d+)_得到(.+?)结果(\.[^.]+)$")


def normalize_label(raw_label: str) -> str:
    label = unicodedata.normalize("NFC", raw_label.strip())
    label = re.sub(r"^\d+_", "", label)
    if label.startswith("得到"):
        label = label[2:]
    if label.endswith("结果"):
        label = label[:-2]
    label = re.sub(r"\s+", "_", label.strip())
    if not label:
        raise ValueError("result label is empty after normalization")
    if any(character in label for character in '<>:"/\\|?*') or any(ord(character) < 32 for character in label):
        raise ValueError("result label contains a path separator, reserved character, or control character")
    if label.endswith((" ", ".")) or label in {".", ".."}:
        raise ValueError("result label is not a safe file-name component")
    if len(label) > 80:
        raise ValueError("result label is longer than 80 characters")
    return label


def normalize_extension(raw_extension: str) -> str:
    extension = raw_extension.strip().lower()
    if not extension.startswith("."):
        extension = "." + extension
    if not re.fullmatch(r"\.[a-z0-9][a-z0-9._-]{0,15}", extension):
        raise ValueError(f"invalid extension: {raw_extension}")
    return extension


def load_state(project: Path) -> tuple[Path, dict[str, object]]:
    state_path = project / ".paper-menu.json"
    if not state_path.is_file():
        raise ValueError(f"missing Paper Menu state file: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid state JSON: {exc}") from exc
    if state.get("schema_version") != 1 or not isinstance(state.get("result_units"), list):
        raise ValueError("unsupported or malformed .paper-menu.json")
    return state_path, state


def scan_existing(project: Path) -> tuple[dict[int, set[str]], set[int]]:
    labels: dict[int, set[str]] = {}
    indices: set[int] = set()
    for directory in KIND_DIRECTORIES.values():
        category = project / directory
        if not category.is_dir():
            raise ValueError(f"missing fixed directory: {category}")
        for child in category.iterdir():
            if child.name.startswith("."):
                continue
            match = RESULT_PATTERN.fullmatch(child.name)
            if not match:
                continue
            index = int(match.group(1))
            indices.add(index)
            labels.setdefault(index, set()).add(match.group(2))
    return labels, indices


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Paper Menu project root")
    parser.add_argument("--result", required=True, help="Neutral description of the result object")
    parser.add_argument("--kinds", nargs="+", choices=tuple(KIND_DIRECTORIES), required=True)
    parser.add_argument("--index", type=int, help="Reuse an already allocated index with the same label")
    parser.add_argument("--code-ext", default=".py")
    parser.add_argument("--figure-ext", default=".png")
    parser.add_argument("--table-ext", default=".xlsx")
    parser.add_argument("--reserve", action="store_true", help="Record the allocation in .paper-menu.json")
    args = parser.parse_args()

    try:
        project = Path(args.project).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError(f"project is not a directory: {project}")
        label = normalize_label(args.result)
        extensions = {
            "code": normalize_extension(args.code_ext),
            "figure": normalize_extension(args.figure_ext),
            "table": normalize_extension(args.table_ext),
        }
        state_path, state = load_state(project)
        disk_labels, disk_indices = scan_existing(project)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    registered_by_index: dict[int, dict[str, object]] = {}
    for item in state["result_units"]:
        if isinstance(item, dict) and isinstance(item.get("index"), int):
            registered_by_index[item["index"]] = item
    occupied = disk_indices | set(registered_by_index)

    if args.index is not None:
        if args.index < 1:
            parser.error("--index must be a positive integer")
        index = args.index
        if index not in occupied:
            parser.error(f"index {index} has not been allocated; omit --index to allocate the next sequential number")
        known_labels = set(disk_labels.get(index, set()))
        registered = registered_by_index.get(index)
        if registered and isinstance(registered.get("label"), str):
            known_labels.add(registered["label"])
        if known_labels and known_labels != {label}:
            parser.error(
                f"index {index} is already associated with {sorted(known_labels)}; requested label is {label!r}"
            )
    else:
        duplicate = [
            index for index, item in registered_by_index.items()
            if item.get("label") == label
        ]
        if duplicate:
            parser.error(f"label is already registered at index {duplicate[0]}; pass --index {duplicate[0]} to extend it")
        index = max(occupied, default=0) + 1

    unique_kinds = list(dict.fromkeys(args.kinds))
    paths: dict[str, str] = {}
    registered_paths: dict[str, str] = {}
    for kind in unique_kinds:
        filename = f"{index}_得到{label}结果{extensions[kind]}"
        artifact_path = project / KIND_DIRECTORIES[kind] / filename
        if artifact_path.exists():
            parser.error(f"artifact path already exists; refusing to overwrite: {artifact_path}")
        paths[kind] = str(artifact_path)
        registered_paths[kind] = str(Path(KIND_DIRECTORIES[kind]) / filename)

    now = datetime.now(timezone.utc).isoformat()
    if args.reserve:
        existing = registered_by_index.get(index)
        if existing:
            existing_paths = existing.setdefault("paths", {})
            if not isinstance(existing_paths, dict):
                parser.error(f"registered result {index} has malformed paths")
            collisions = set(existing_paths) & set(registered_paths)
            if collisions:
                parser.error(f"result {index} already reserves kinds: {sorted(collisions)}")
            existing_paths.update(registered_paths)
            existing["updated_at_utc"] = now
        else:
            state["result_units"].append({
                "index": index,
                "label": label,
                "paths": registered_paths,
                "status": "planned",
                "created_at_utc": now,
            })
            state["result_units"].sort(key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)
        atomic_write_json(state_path, state)

    payload = {
        "project": str(project),
        "index": index,
        "label": label,
        "paths": paths,
        "reserved": bool(args.reserve),
        "note": "No artifact files were created; write verified outputs to these paths.",
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

