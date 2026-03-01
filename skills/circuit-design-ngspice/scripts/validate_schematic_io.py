#!/usr/bin/env python3
"""Validate that rendered schematic SVG contains visible input/output terminals."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


INPUT_PATTERNS = [
    re.compile(r's:type="inputExt"'),
    re.compile(r's:type="\$_inputExt_"'),
    re.compile(r'id="cell_port_input'),
]

OUTPUT_PATTERNS = [
    re.compile(r's:type="outputExt"'),
    re.compile(r's:type="\$_outputExt_"'),
    re.compile(r'id="cell_port_output'),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate schematic has visible IN/OUT terminals")
    parser.add_argument("--svg-path", required=True, help="Path to schematic SVG")
    parser.add_argument(
        "--allow-missing-input",
        action="store_true",
        help="Do not fail when an input terminal marker is missing",
    )
    parser.add_argument(
        "--allow-missing-output",
        action="store_true",
        help="Do not fail when an output terminal marker is missing",
    )
    parser.add_argument(
        "--output-path",
        default="",
        help="Optional path to write JSON validation output",
    )
    return parser


def has_any_pattern(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def main() -> int:
    args = build_parser().parse_args()
    svg_path = Path(args.svg_path).resolve()
    output_path = Path(args.output_path).resolve() if args.output_path else None

    result = {
        "ok": False,
        "svg_path": str(svg_path),
        "has_input_terminal": False,
        "has_output_terminal": False,
        "warnings": [],
        "error": "",
    }

    if not svg_path.exists():
        result["error"] = f"svg not found: {svg_path}"
        payload = json.dumps(result, ensure_ascii=False)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    text = svg_path.read_text(encoding="utf-8-sig", errors="ignore")
    has_input = has_any_pattern(text, INPUT_PATTERNS)
    has_output = has_any_pattern(text, OUTPUT_PATTERNS)
    result["has_input_terminal"] = has_input
    result["has_output_terminal"] = has_output

    require_input = not args.allow_missing_input
    require_output = not args.allow_missing_output

    if require_input and not has_input:
        result["warnings"].append("missing visible input terminal marker in SVG")
    if require_output and not has_output:
        result["warnings"].append("missing visible output terminal marker in SVG")

    result["ok"] = (
        (has_input or not require_input)
        and (has_output or not require_output)
        and not result["error"]
    )

    payload = json.dumps(result, ensure_ascii=False)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

