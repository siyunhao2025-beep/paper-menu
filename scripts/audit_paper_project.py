#!/usr/bin/env python3
"""Audit a Paper Menu project structure, naming, registry, and final files."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


DIRECTORIES = (
    "1_数据",
    "2_代码",
    "3_图片",
    "4_表格",
    "5_参考文献",
    "6_最终Latex代码+PDF+Word",
)

KIND_DIRECTORIES = {
    "code": "2_代码",
    "figure": "3_图片",
    "table": "4_表格",
}

RESULT_PATTERN = re.compile(r"^(\d+)_得到(.+?)结果(\.[^.]+)$")


def add(collection: list[dict[str, str]], code: str, message: str) -> None:
    collection.append({"code": code, "message": message})


def visible_children(path: Path) -> list[Path]:
    return sorted((child for child in path.iterdir() if not child.name.startswith(".")), key=lambda child: child.name)


def inspect_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return "word/document.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Paper Menu project root")
    parser.add_argument("--final", action="store_true", help="Require final references and all three deliverables")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve(strict=False)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not project.is_dir():
        add(errors, "PROJECT_MISSING", f"project directory does not exist: {project}")
        return finish(project, args, errors, warnings, {})

    for directory in DIRECTORIES:
        if not (project / directory).is_dir():
            add(errors, "DIRECTORY_MISSING", f"missing fixed directory: {directory}")
    extra_directories = [
        child.name for child in project.iterdir()
        if child.is_dir() and not child.name.startswith(".") and child.name not in DIRECTORIES
    ]
    if extra_directories:
        add(warnings, "EXTRA_ROOT_DIRECTORIES", f"non-contract root directories: {extra_directories}")

    state_path = project / ".paper-menu.json"
    state: dict[str, object] = {}
    if not state_path.is_file():
        add(errors, "STATE_MISSING", "missing .paper-menu.json")
    else:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            add(errors, "STATE_INVALID", f"invalid .paper-menu.json: {exc}")
        else:
            if state.get("schema_version") != 1 or not isinstance(state.get("result_units"), list):
                add(errors, "STATE_SCHEMA", "unsupported or malformed state schema")

    observed: dict[int, dict[str, str]] = {}
    artifact_counts = {kind: 0 for kind in KIND_DIRECTORIES}
    for kind, directory in KIND_DIRECTORIES.items():
        category = project / directory
        if not category.is_dir():
            continue
        for child in visible_children(category):
            match = RESULT_PATTERN.fullmatch(child.name)
            if not match or not child.is_file():
                add(errors, "RESULT_NAME_INVALID", f"{directory}/{child.name} does not match N_得到XX结果.ext")
                continue
            index, label = int(match.group(1)), match.group(2)
            if index < 1 or not label.strip():
                add(errors, "RESULT_NAME_INVALID", f"invalid result name: {directory}/{child.name}")
                continue
            artifact_counts[kind] += 1
            unit = observed.setdefault(index, {})
            previous_label = unit.get("label")
            if previous_label and previous_label != label:
                add(errors, "RESULT_LABEL_CONFLICT", f"result {index} uses both {previous_label!r} and {label!r}")
            unit["label"] = label
            if kind in unit:
                add(errors, "RESULT_KIND_DUPLICATE", f"result {index} has more than one {kind} artifact")
            unit[kind] = str(child)

    registry = state.get("result_units", []) if isinstance(state, dict) else []
    if isinstance(registry, list):
        registered_indices: set[int] = set()
        registered_kinds: dict[int, set[str]] = {}
        for item in registry:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                add(errors, "REGISTRY_ENTRY_INVALID", f"malformed result registry entry: {item!r}")
                continue
            index = item["index"]
            if index in registered_indices:
                add(errors, "REGISTRY_INDEX_DUPLICATE", f"result {index} appears more than once in the registry")
            registered_indices.add(index)
            label = item.get("label")
            paths = item.get("paths")
            if not isinstance(label, str) or not isinstance(paths, dict):
                add(errors, "REGISTRY_ENTRY_INVALID", f"result {index} has malformed label or paths")
                continue
            disk_label = observed.get(index, {}).get("label")
            if disk_label and disk_label != label:
                add(errors, "REGISTRY_LABEL_CONFLICT", f"result {index}: registry {label!r}, disk {disk_label!r}")
            for kind, raw_path in paths.items():
                if kind not in KIND_DIRECTORIES or not isinstance(raw_path, str):
                    add(errors, "REGISTRY_PATH_INVALID", f"result {index} has invalid registered path for {kind!r}")
                    continue
                expected = Path(raw_path)
                if not expected.is_absolute():
                    expected = project / expected
                try:
                    resolved_expected = expected.resolve(strict=False)
                    resolved_expected.relative_to(project)
                except ValueError:
                    add(errors, "REGISTRY_PATH_ESCAPE", f"result {index} path escapes project: {expected}")
                    continue
                expected_category = (project / KIND_DIRECTORIES[kind]).resolve(strict=False)
                if resolved_expected.parent != expected_category:
                    add(errors, "REGISTRY_CATEGORY_MISMATCH", f"result {index} {kind} path is outside {KIND_DIRECTORIES[kind]}")
                    continue
                expected_match = RESULT_PATTERN.fullmatch(expected.name)
                if not expected_match or int(expected_match.group(1)) != index or expected_match.group(2) != label:
                    add(errors, "REGISTRY_NAME_MISMATCH", f"result {index} {kind} path violates its registered index or label")
                    continue
                registered_kinds.setdefault(index, set()).add(kind)
                if not expected.is_file():
                    target = errors if args.final else warnings
                    add(target, "RESERVED_ARTIFACT_MISSING", f"result {index} reserved {kind} is missing: {expected}")
        unregistered = sorted(set(observed) - registered_indices)
        if unregistered:
            target = errors if args.final else warnings
            add(target, "UNREGISTERED_RESULTS", f"result numbers exist on disk but not in the registry: {unregistered}")
        for index in sorted(set(observed) & registered_indices):
            observed_kinds = set(observed[index]) & set(KIND_DIRECTORIES)
            missing_kind_registrations = sorted(observed_kinds - registered_kinds.get(index, set()))
            if missing_kind_registrations:
                target = errors if args.final else warnings
                add(
                    target,
                    "UNREGISTERED_ARTIFACTS",
                    f"result {index} has artifacts not recorded in the registry: {missing_kind_registrations}",
                )

    all_indices = set(observed)
    if isinstance(registry, list):
        all_indices.update(
            item["index"] for item in registry
            if isinstance(item, dict) and isinstance(item.get("index"), int) and item["index"] > 0
        )
    if all_indices:
        missing_indices = sorted(set(range(1, max(all_indices) + 1)) - all_indices)
        if missing_indices:
            add(errors, "RESULT_INDEX_GAP", f"result numbering is not continuous; missing: {missing_indices}")

    labels_to_indices: dict[str, set[int]] = {}
    for index, unit in observed.items():
        if isinstance(unit.get("label"), str):
            labels_to_indices.setdefault(unit["label"], set()).add(index)
    duplicate_labels = {label: sorted(indices) for label, indices in labels_to_indices.items() if len(indices) > 1}
    if duplicate_labels:
        add(warnings, "RESULT_LABEL_REUSED", f"the same result label appears under multiple numbers: {duplicate_labels}")

    final_directory = project / DIRECTORIES[-1]
    ledger = final_directory / "0_论文执行台账.md"
    if not ledger.is_file() or ledger.stat().st_size == 0:
        add(errors, "LEDGER_MISSING", "missing or empty 0_论文执行台账.md")

    final_files = {
        "tex": final_directory / "1_论文正文.tex",
        "pdf": final_directory / "2_论文正文.pdf",
        "docx": final_directory / "3_论文正文.docx",
    }
    if args.final:
        references = project / "5_参考文献"
        if references.is_dir() and not [child for child in visible_children(references) if child.is_file() and child.stat().st_size > 0]:
            add(errors, "REFERENCES_MISSING", "final audit requires at least one non-empty reference record")
        for kind, path in final_files.items():
            if not path.is_file() or path.stat().st_size == 0:
                add(errors, "FINAL_FILE_MISSING", f"missing or empty final {kind}: {path.name}")
        tex = final_files["tex"]
        if tex.is_file() and tex.stat().st_size:
            try:
                tex_text = tex.read_text(encoding="utf-8", errors="replace")
                if "\\documentclass" not in tex_text or "\\begin{document}" not in tex_text:
                    add(errors, "TEX_INVALID", "1_论文正文.tex lacks a document class or document body")
            except OSError as exc:
                add(errors, "TEX_UNREADABLE", str(exc))
        pdf = final_files["pdf"]
        if pdf.is_file() and pdf.stat().st_size:
            try:
                pdf_bytes = pdf.read_bytes()
                if pdf_bytes[:5] != b"%PDF-" or b"%%EOF" not in pdf_bytes[-1024:]:
                    add(errors, "PDF_INVALID", "2_论文正文.pdf lacks a valid PDF signature or EOF marker")
            except OSError as exc:
                add(errors, "PDF_UNREADABLE", str(exc))
        docx = final_files["docx"]
        if docx.is_file() and docx.stat().st_size and not inspect_docx(docx):
            add(errors, "DOCX_INVALID", "3_论文正文.docx is not a readable Word document")
        if tex.is_file() and tex.stat().st_size:
            for kind in ("pdf", "docx"):
                path = final_files[kind]
                if path.is_file() and path.stat().st_mtime + 1 < tex.stat().st_mtime:
                    add(errors, "FINAL_FILE_STALE", f"{path.name} is older than the final LaTeX source")

    stats = {
        "registered_results": len(registry) if isinstance(registry, list) else 0,
        "observed_results": len(observed),
        "artifact_counts": artifact_counts,
    }
    return finish(project, args, errors, warnings, stats)


def finish(
    project: Path,
    args: argparse.Namespace,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    stats: dict[str, object],
) -> int:
    payload = {
        "project": str(project),
        "mode": "final" if args.final else "progress",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }
    if args.format == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Paper Menu audit: {'PASS' if not errors else 'FAIL'}")
        print(f"Project: {project}")
        for issue in errors:
            print(f"ERROR [{issue['code']}] {issue['message']}")
        for issue in warnings:
            print(f"WARN  [{issue['code']}] {issue['message']}")
        if stats:
            print("Stats: " + json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

