#!/usr/bin/env python3
"""Create a new Paper Menu project without overwriting existing content."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
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

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def validate_name(raw_name: str) -> str:
    name = unicodedata.normalize("NFC", raw_name.strip())
    if not name or name in {".", ".."}:
        raise ValueError("project name must be a non-empty single directory name")
    if name.endswith((" ", ".")):
        raise ValueError("project name must not end with a space or period")
    if any(character in name for character in '<>:"/\\|?*'):
        raise ValueError("project name contains a path separator or reserved character")
    if any(ord(character) < 32 for character in name):
        raise ValueError("project name contains a control character")
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        raise ValueError("project name is reserved on Windows")
    if len(name) > 120:
        raise ValueError("project name is longer than 120 characters")
    return name


def emit(payload: dict[str, object]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="Existing parent directory")
    parser.add_argument("--name", required=True, help="Name of the new project folder")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the planned tree only")
    args = parser.parse_args()

    try:
        project_name = validate_name(args.name)
        parent = Path(args.parent).expanduser().resolve(strict=True)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not parent.is_dir():
        parser.error(f"parent is not a directory: {parent}")
    if not os.access(parent, os.W_OK):
        parser.error(f"parent is not writable: {parent}")

    target = parent / project_name
    if target.exists():
        parser.error(f"target already exists; refusing to overwrite: {target}")

    planned = {
        "status": "dry-run" if args.dry_run else "created",
        "project": str(target),
        "directories": [str(target / directory) for directory in DIRECTORIES],
        "state_file": str(target / ".paper-menu.json"),
        "ledger": str(target / DIRECTORIES[-1] / "0_论文执行台账.md"),
    }
    if args.dry_run:
        emit(planned)
        return 0

    temporary = parent / f".{project_name}.paper-menu-{uuid.uuid4().hex}.tmp"
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        temporary.mkdir()
        for directory in DIRECTORIES:
            (temporary / directory).mkdir()
        state = {
            "schema_version": 1,
            "project_name": project_name,
            "created_at_utc": created_at,
            "workflow_status": "scaffolded",
            "fixed_directories": list(DIRECTORIES),
            "approved_skill_plan": [],
            "result_units": [],
        }
        (temporary / ".paper-menu.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        ledger = f"""# 论文执行台账

- 项目：{project_name}
- 创建时间（UTC）：{created_at}
- 状态：已创建目录，等待记录获批的 Skill 调用计划

## 已批准的 Skill 调用计划

待 Paper Menu 写入。

## 阶段状态

| 阶段 | 使用的 Skill | 输入 | 产物 | 验证 | 状态/限制 |
|---|---|---|---|---|---|

## 结果索引

| 编号 | 中性结果标签 | 代码 | 图片 | 表格 | 状态 |
|---:|---|---|---|---|---|

## 决策与偏离记录

记录所有改变研究问题、方法、证据边界、Skill 顺序或目录契约的决定。
"""
        (temporary / DIRECTORIES[-1] / "0_论文执行台账.md").write_text(ledger, encoding="utf-8")
        temporary.replace(target)
    except Exception:
        safe_prefix = f".{project_name}.paper-menu-"
        if temporary.exists() and temporary.parent == parent and temporary.name.startswith(safe_prefix):
            shutil.rmtree(temporary)
        raise

    emit(planned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

