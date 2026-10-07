#!/usr/bin/env python3
"""Discover installed Agent Skills without executing them.

The output is an inventory aid. Relevance labels are heuristic; the orchestrating
agent must review metadata and fully read the actual research-related SKILL.md files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PRIMARY_TERMS = {
    "research-planning": (
        "academic", "research", "scientific", "paper", "manuscript", "journal",
        "thesis", "dissertation", "科研", "研究", "论文", "期刊", "学术",
    ),
    "literature-citation": (
        "literature", "citation", "reference", "bibliography", "doi", "pubmed",
        "scholar", "文献", "引用", "参考文献", "检索",
    ),
    "review-revision": (
        "peer review", "reviewer", "rebuttal", "revision", "response to review",
        "审稿", "同行评审", "返修", "回复审稿",
    ),
}

SUPPORT_TERMS = {
    "data-analysis": (
        "data analysis", "exploratory data", "statistics", "statistical", "modeling",
        "regression", "machine learning", "matlab", "实验", "统计", "建模", "数据分析",
    ),
    "figures-tables": (
        "figure", "plot", "chart", "diagram", "visualization", "table",
        "图片", "图表", "绘图", "可视化", "表格",
    ),
    "delivery-formats": (
        "latex", "pdf", "docx", "word document", "document creation",
        "排版", "文档", "幻灯片",
    ),
    "experiments-reproducibility": (
        "experiment", "simulation", "reproduc", "code availability", "data availability",
        "实验", "仿真", "复现", "可重复",
    ),
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[str, str]:
    """Extract name and description from the simple YAML used by Skills."""
    if not text.startswith("---"):
        return "", ""
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", text, re.DOTALL)
    if not match:
        return "", ""
    lines = match.group(1).splitlines()
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        key_match = re.match(r"^(name|description):\s*(.*)$", lines[index])
        if not key_match:
            index += 1
            continue
        key, raw = key_match.groups()
        if raw.strip() in {"|", ">", "|-", ">-", "|+", ">+"}:
            block: list[str] = []
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index][0].isspace()):
                block.append(lines[index].strip())
                index += 1
            values[key] = " ".join(part for part in block if part)
            continue
        values[key] = _unquote(raw)
        index += 1
    return values.get("name", ""), values.get("description", "")


def classify_source(path: Path, cwd: Path, home: Path) -> str:
    normalized = str(path).replace("\\", "/").lower()
    try:
        path.relative_to(cwd)
        return "project"
    except ValueError:
        pass
    if "/plugins/cache/" in normalized:
        return "plugin-cache"
    if "/skills/.system/" in normalized:
        return "system"
    if "/.agents/skills/" in normalized:
        return "user-agents"
    if "/.claude/skills/" in normalized:
        return "user-claude"
    try:
        path.relative_to(home)
        return "user-home"
    except ValueError:
        return "custom"


def default_roots(include_plugin_cache: bool) -> list[Path]:
    home = Path.home()
    cwd = Path.cwd()
    roots = [
        cwd / ".agents" / "skills",
        cwd / ".codex" / "skills",
        home / ".codex" / "skills",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
    ]
    configured_codex_home = os.environ.get("CODEX_HOME")
    if configured_codex_home:
        roots.append(Path(configured_codex_home).expanduser() / "skills")
    if include_plugin_cache:
        roots.append(home / ".codex" / "plugins" / "cache")
    return roots


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            resolved = path.expanduser().absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def iter_skill_files(root: Path, max_depth: int) -> Iterable[Path]:
    if not root.is_dir():
        return
    root_depth = len(root.parts)
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.parts) - root_depth
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".venv")]
        if depth >= max_depth:
            dirs[:] = []
        if "SKILL.md" in files:
            yield current_path / "SKILL.md"


def relevance(name: str, description: str, path: Path) -> tuple[str, list[str], list[str]]:
    haystack = f"{name} {description} {path.parent.name}".lower()
    identity = f"{name} {path.parent.name}".lower()
    primary_matches: list[str] = []
    support_matches: list[str] = []
    categories: list[str] = []
    for category, terms in PRIMARY_TERMS.items():
        found = [term for term in terms if term.lower() in haystack]
        if found:
            categories.append(category)
            primary_matches.extend(found)
    for category, terms in SUPPORT_TERMS.items():
        found = [term for term in terms if term.lower() in haystack]
        if found:
            categories.append(category)
            support_matches.extend(found)
    strong_phrases = (
        "academic paper", "research paper", "scientific paper", "manuscript", "journal",
        "literature", "citation", "bibliography", "peer review", "reviewer", "thesis",
        "论文", "文献", "引用", "期刊", "审稿",
    )
    identity_terms = ("academic", "research", "paper", "manuscript", "literature", "citation", "journal")
    if any(term in haystack for term in strong_phrases) or any(term in identity for term in identity_terms):
        label = "high"
    elif primary_matches or support_matches:
        label = "support"
    else:
        label = "unrelated"
    return label, sorted(set(primary_matches + support_matches)), sorted(set(categories))


def inspect_skill(path: Path, cwd: Path, home: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"{path}: {exc}"
    if len(raw) > 2_000_000:
        return None, f"{path}: SKILL.md exceeds 2 MB; inspect manually"
    text = raw.decode("utf-8-sig", errors="replace")
    name, description = parse_frontmatter(text)
    if not name:
        name = path.parent.name
    level, matches, categories = relevance(name, description, path)
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        modified = None
    return {
        "name": name,
        "description": description,
        "skill_md": str(path.resolve()),
        "skill_dir": str(path.parent.resolve()),
        "source": classify_source(path, cwd, home),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modified_at_utc": modified,
        "suggested_relevance": level,
        "matched_terms": matches,
        "suggested_capabilities": categories,
    }, None


def render_markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# Local Skill inventory",
        "",
        f"- Found: {summary['skills_found']}",
        f"- High relevance: {summary['high_relevance']}",
        f"- Supporting relevance: {summary['support_relevance']}",
        f"- Duplicate names: {summary['duplicate_name_groups']}",
        f"- Read warnings: {summary['read_warnings']}",
        "",
        "| Relevance | Name | Source | Description | SKILL.md |",
        "|---|---|---|---|---|",
    ]
    for item in payload["skills"]:
        description = str(item["description"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['suggested_relevance']} | {item['name']} | {item['source']} | "
            f"{description} | `{item['skill_md']}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=[], help="Additional skill root; repeatable")
    parser.add_argument(
        "--no-default-roots",
        action="store_true",
        help="Scan only paths supplied with --root (useful for isolated validation or custom installations)",
    )
    parser.add_argument("--include-plugin-cache", action="store_true", help="Scan ~/.codex/plugins/cache")
    parser.add_argument("--max-depth", type=int, default=10, help="Maximum directory depth below each root")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    if args.max_depth < 1 or args.max_depth > 30:
        parser.error("--max-depth must be between 1 and 30")

    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    requested_roots = ([] if args.no_default_roots else default_roots(args.include_plugin_cache))
    requested_roots += [Path(value) for value in args.root]
    if not requested_roots:
        parser.error("no skill roots selected; pass --root or remove --no-default-roots")
    roots = unique_paths(requested_roots)
    found: dict[str, Path] = {}
    for root in roots:
        for skill_file in iter_skill_files(root, args.max_depth) or ():
            key = os.path.normcase(str(skill_file.resolve()))
            found[key] = skill_file

    skills: list[dict[str, object]] = []
    warnings: list[str] = []
    for skill_file in sorted(found.values(), key=lambda item: os.path.normcase(str(item))):
        item, warning = inspect_skill(skill_file, cwd, home)
        if item:
            skills.append(item)
        if warning:
            warnings.append(warning)

    skills.sort(key=lambda item: (str(item["name"]).lower(), str(item["skill_md"]).lower()))
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in skills:
        by_name[str(item["name"]).lower()].append(item)
    duplicates = {
        group[0]["name"]: [
            {"skill_md": item["skill_md"], "sha256": item["sha256"], "source": item["source"]}
            for item in group
        ]
        for group in by_name.values()
        if len(group) > 1
    }

    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "roots_requested": [str(root) for root in roots],
        "roots_scanned": [str(root) for root in roots if root.is_dir()],
        "summary": {
            "skills_found": len(skills),
            "high_relevance": sum(item["suggested_relevance"] == "high" for item in skills),
            "support_relevance": sum(item["suggested_relevance"] == "support" for item in skills),
            "duplicate_name_groups": len(duplicates),
            "read_warnings": len(warnings),
        },
        "warnings": warnings,
        "duplicate_names": duplicates,
        "skills": skills,
        "note": (
            "Relevance is heuristic. Reconcile this inventory with the runtime Skill catalog, "
            "then fully read every genuinely research-related SKILL.md before routing."
        ),
    }
    if args.format == "markdown":
        sys.stdout.write(render_markdown(payload))
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

