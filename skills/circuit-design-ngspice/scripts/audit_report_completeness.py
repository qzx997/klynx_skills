#!/usr/bin/env python3
"""Audit report completeness: tables, figures, and key analysis sections."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|\s*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit markdown report completeness")
    parser.add_argument("--report-path", required=True, help="Report markdown path")
    parser.add_argument("--output-path", default="", help="Optional audit JSON output")
    parser.add_argument(
        "--require-sections",
        action="append",
        default=[],
        help="Custom required section title (repeatable)",
    )
    return parser


def default_required_sections() -> list[str]:
    return [
        "## 2. Design Approach and Architecture",
        "## 4. Simulation Results and Analysis",
        "## 6. Risks and Follow-up",
    ]


def parse_table_count(lines: list[str]) -> tuple[int, int]:
    table_count = 0
    incomplete_count = 0
    idx = 0
    while idx < len(lines) - 1:
        if not TABLE_LINE_RE.match(lines[idx]):
            idx += 1
            continue
        if TABLE_SEP_RE.match(lines[idx + 1]):
            table_count += 1
            idx += 2
            while idx < len(lines) and TABLE_LINE_RE.match(lines[idx]):
                idx += 1
            continue
        # Pipe-like line without a separator row is treated as incomplete table.
        incomplete_count += 1
        idx += 1
    return table_count, incomplete_count


def main() -> int:
    args = build_parser().parse_args()
    report_path = Path(args.report_path).resolve()
    output_path = Path(args.output_path).resolve() if args.output_path else None

    result = {
        "ok": False,
        "report_path": str(report_path),
        "table_count": 0,
        "incomplete_table_blocks": 0,
        "image_count": 0,
        "missing_images": [],
        "missing_sections": [],
        "warnings": [],
    }

    if not report_path.exists():
        result["warnings"].append(f"report not found: {report_path}")
        payload = json.dumps(result, ensure_ascii=False)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    text = report_path.read_text(encoding="utf-8-sig", errors="ignore")
    lines = text.splitlines()

    table_count, incomplete_count = parse_table_count(lines)
    result["table_count"] = table_count
    result["incomplete_table_blocks"] = incomplete_count

    required_sections = args.require_sections if args.require_sections else default_required_sections()
    for header in required_sections:
        if header not in text:
            result["missing_sections"].append(header)

    image_paths = IMAGE_RE.findall(text)
    result["image_count"] = len(image_paths)
    for item in image_paths:
        if item.startswith("http://") or item.startswith("https://"):
            continue
        img_path = (report_path.parent / item).resolve()
        if not img_path.exists():
            result["missing_images"].append(item)

    if table_count == 0:
        result["warnings"].append("no markdown tables found")
    if incomplete_count > 0:
        result["warnings"].append(f"incomplete markdown table blocks: {incomplete_count}")
    if result["missing_images"]:
        result["warnings"].append("one or more image links are broken")
    if result["missing_sections"]:
        result["warnings"].append("missing required report sections")

    result["ok"] = (
        table_count > 0
        and incomplete_count == 0
        and not result["missing_images"]
        and not result["missing_sections"]
    )

    payload = json.dumps(result, ensure_ascii=False)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
