#!/usr/bin/env python3
"""Validate spec/metrics payloads against the unified metric schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def default_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "metric_schema.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate metrics against schema")
    parser.add_argument("--schema-path", default="", help="Metric schema JSON path")
    parser.add_argument("--spec-path", default="", help="Optional spec JSON path")
    parser.add_argument("--metrics-path", default="", help="Optional metrics JSON path")
    parser.add_argument("--domain", default="", help="Optional explicit domain override")
    parser.add_argument(
        "--strict-targets",
        action="store_true",
        help="Fail when target metrics are unknown or missing required domain targets",
    )
    parser.add_argument(
        "--strict-measured",
        action="store_true",
        help="Fail when measured metrics are unknown or missing required domain metrics",
    )
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    return parser


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_domain(spec: dict, cli_domain: str) -> str:
    domain = cli_domain.strip().lower()
    if domain:
        return domain
    notes = spec.get("metric_pack_notes")
    if isinstance(notes, dict):
        value = str(notes.get("domain", "")).strip().lower()
        if value:
            return value
    circuit_type = str(spec.get("circuit_type", "")).strip().lower()
    return circuit_type


def metric_direction_warnings(name: str, rule: dict, direction: str) -> list[str]:
    warnings: list[str] = []
    if direction == "min":
        if "min" not in rule:
            warnings.append(f"{name}: expected 'min' bound from schema direction")
    elif direction == "max":
        if "max" not in rule:
            warnings.append(f"{name}: expected 'max' bound from schema direction")
    return warnings


def main() -> int:
    args = build_parser().parse_args()

    schema_path = Path(args.schema_path).resolve() if args.schema_path else default_schema_path()
    spec_path = Path(args.spec_path).resolve() if args.spec_path else None
    metrics_path = Path(args.metrics_path).resolve() if args.metrics_path else None
    output_path = Path(args.output_path).resolve() if args.output_path else None

    result = {
        "ok": False,
        "schema_path": str(schema_path),
        "domain": "",
        "unknown_target_metrics": [],
        "unknown_measured_metrics": [],
        "missing_required_targets": [],
        "missing_required_measured": [],
        "target_rule_warnings": [],
        "warnings": [],
    }

    schema = read_json(schema_path)
    if not schema:
        result["warnings"].append(f"invalid or missing schema: {schema_path}")
        payload = json.dumps(result, ensure_ascii=False)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    metric_defs = schema.get("metrics", {})
    domains = schema.get("domains", {})
    if not isinstance(metric_defs, dict):
        metric_defs = {}
    if not isinstance(domains, dict):
        domains = {}

    spec: dict = {}
    metrics_payload: dict = {}
    if spec_path:
        spec = read_json(spec_path)
        if not spec:
            result["warnings"].append(f"spec not found or invalid: {spec_path}")
    if metrics_path:
        metrics_payload = read_json(metrics_path)
        if not metrics_payload:
            result["warnings"].append(f"metrics not found or invalid: {metrics_path}")

    domain = resolve_domain(spec, args.domain)
    result["domain"] = domain
    required_targets: list[str] = []
    if domain and isinstance(domains.get(domain), dict):
        required_targets = [str(x) for x in domains[domain].get("required_targets", []) if str(x).strip()]

    targets = {}
    if isinstance(spec.get("targets_eval"), dict):
        targets = spec.get("targets_eval", {})
    elif isinstance(spec.get("targets"), dict):
        targets = spec.get("targets", {})

    if isinstance(targets, dict):
        for name, rule in targets.items():
            if name not in metric_defs:
                result["unknown_target_metrics"].append(name)
                continue
            if isinstance(rule, dict):
                direction = str(metric_defs[name].get("direction", "")).lower()
                result["target_rule_warnings"].extend(metric_direction_warnings(name, rule, direction))
            else:
                result["target_rule_warnings"].append(f"{name}: invalid rule (expected object)")
        if required_targets:
            for req in required_targets:
                if req not in targets:
                    result["missing_required_targets"].append(req)

    measured = metrics_payload.get("metrics", metrics_payload if isinstance(metrics_payload, dict) else {})
    if isinstance(measured, dict) and measured:
        for name in measured.keys():
            if name not in metric_defs:
                result["unknown_measured_metrics"].append(name)
        if required_targets:
            for req in required_targets:
                if req not in measured:
                    result["missing_required_measured"].append(req)

    ok = True
    if args.strict_targets and (result["unknown_target_metrics"] or result["missing_required_targets"]):
        ok = False
    if args.strict_measured and (result["unknown_measured_metrics"] or result["missing_required_measured"]):
        ok = False
    result["ok"] = ok

    payload = json.dumps(result, ensure_ascii=False)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
