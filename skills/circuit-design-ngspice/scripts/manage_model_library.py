#!/usr/bin/env python3
"""Scan and validate primitive-device models, with optional registry sync."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from netlist_to_json import merge_continuation_lines, parse_component_line, strip_inline_comment


MODEL_DEF_RE = re.compile(
    r"^\s*\.model\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Model management helper")
    parser.add_argument(
        "--action",
        required=True,
        choices=["scan", "validate", "sync-registry"],
        help="Operation mode",
    )
    parser.add_argument("--netlist-path", required=True, help="Netlist path")
    parser.add_argument("--registry-path", default="", help="Registry JSON path")
    parser.add_argument("--output-path", default="", help="Optional output JSON path")
    return parser


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "model_registry.json"


def parse_model_defs(merged_lines: list[tuple[int, str]]) -> dict[str, dict]:
    models: dict[str, dict] = {}
    for line_no, line in merged_lines:
        stripped = strip_inline_comment(line)
        if not stripped:
            continue
        match = MODEL_DEF_RE.match(stripped)
        if not match:
            continue
        name = match.group(1)
        mtype = match.group(2)
        models[name.upper()] = {"name": name, "type": mtype.lower(), "line_no": line_no}
    return models


def parse_used_models(merged_lines: list[tuple[int, str]]) -> dict[str, list[dict]]:
    warnings: list[str] = []
    used: dict[str, list[dict]] = {}
    for line_no, line in merged_lines:
        comp = parse_component_line(line, line_no, warnings)
        if not comp:
            continue
        ctype = str(comp.get("type", ""))
        if ctype not in {"diode", "bjt", "mosfet"}:
            continue
        value = str(comp.get("value", "")).strip()
        if not value:
            continue
        model_name = value.split()[0]
        key = model_name.upper()
        used.setdefault(key, []).append(
            {
                "ref": comp.get("name", ""),
                "type": ctype,
                "line_no": comp.get("line_no"),
                "model_token": model_name,
            }
        )
    return used


def load_registry(path: Path) -> dict:
    if not path.exists():
        return {"models": {}}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return {"models": {}}
    models = payload.get("models", {})
    if not isinstance(models, dict):
        models = {}
    payload["models"] = models
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    netlist_path = Path(args.netlist_path).resolve()
    registry_path = Path(args.registry_path).resolve() if args.registry_path else default_registry_path()

    result = {
        "ok": False,
        "action": args.action,
        "netlist_path": str(netlist_path),
        "registry_path": str(registry_path),
        "local_models": {},
        "used_models": {},
        "missing_models": [],
        "warnings": [],
    }

    if not netlist_path.exists():
        result["warnings"].append(f"netlist not found: {netlist_path}")
        payload = json.dumps(result, ensure_ascii=False)
        if args.output_path:
            write_json(Path(args.output_path).resolve(), result)
        print(payload)
        return 1

    content = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
    merged_lines = merge_continuation_lines(content)
    local_models = parse_model_defs(merged_lines)
    used_models = parse_used_models(merged_lines)

    result["local_models"] = local_models
    result["used_models"] = used_models

    registry = load_registry(registry_path)
    registry_models = registry.get("models", {})
    if not isinstance(registry_models, dict):
        registry_models = {}

    if args.action == "scan":
        result["ok"] = True

    elif args.action == "validate":
        missing: list[dict] = []
        for key, users in used_models.items():
            if key in local_models:
                continue
            if key in {k.upper(): v for k, v in registry_models.items()}:
                continue
            for usage in users:
                missing.append(
                    {
                        "model": usage["model_token"],
                        "ref": usage["ref"],
                        "line_no": usage["line_no"],
                        "device_type": usage["type"],
                    }
                )
        result["missing_models"] = missing
        result["ok"] = len(missing) == 0

    elif args.action == "sync-registry":
        models_upper = {k.upper(): k for k in registry_models.keys()}
        added = 0
        for key, meta in local_models.items():
            if key in models_upper:
                reg_key = models_upper[key]
            else:
                reg_key = meta["name"]
                added += 1
            registry_models[reg_key] = {
                "type": meta["type"],
                "source": str(netlist_path),
                "line_no": meta["line_no"],
            }
        registry["models"] = registry_models
        write_json(registry_path, registry)
        result["registry_added"] = added
        result["registry_total"] = len(registry_models)
        result["ok"] = True

    if args.output_path:
        write_json(Path(args.output_path).resolve(), result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
