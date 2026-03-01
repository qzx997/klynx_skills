#!/usr/bin/env python3
"""Compare simulated metrics against target specs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate metrics against spec")
    parser.add_argument("--spec-path", required=True, help="Path to spec JSON")
    parser.add_argument("--metrics-path", required=True, help="Path to metrics JSON")
    return parser


def pick_targets(spec: dict) -> tuple[dict, str]:
    targets_eval = spec.get("targets_eval")
    if isinstance(targets_eval, dict) and targets_eval:
        return targets_eval, "targets_eval"
    targets = spec.get("targets")
    if isinstance(targets, dict):
        return targets, "targets"
    return {}, "none"


def resolve_actual(
    name: str,
    metrics: dict,
    failed: dict,
    metric_aliases: dict[str, list[str]],
) -> tuple[float | None, str | None, str | None]:
    if name in metrics:
        return metrics[name], name, None
    if name in failed:
        reason = failed[name].get("reason", "unknown")
        return None, None, f"{name} failed: {reason}"

    aliases = metric_aliases.get(name, [])
    for alias in aliases:
        if alias in metrics:
            return metrics[alias], alias, None
        if alias in failed:
            reason = failed[alias].get("reason", "unknown")
            return None, None, f"{alias} failed (alias for {name}): {reason}"
    return None, None, None


def main() -> int:
    args = build_parser().parse_args()
    spec_path = Path(args.spec_path).resolve()
    metrics_path = Path(args.metrics_path).resolve()

    result = {
        "pass": False,
        "gaps": [],
        "missing_metrics": [],
        "failed_metrics": [],
        "used_aliases": {},
        "target_source": "none",
    }

    if not spec_path.exists() or not metrics_path.exists():
        if not spec_path.exists():
            result["missing_metrics"].append(f"missing spec file: {spec_path}")
        if not metrics_path.exists():
            result["missing_metrics"].append(f"missing metrics file: {metrics_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    metrics_json = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
    metrics = metrics_json.get("metrics", metrics_json)
    failed = metrics_json.get("failed_metrics", {})

    targets, target_source = pick_targets(spec)
    result["target_source"] = target_source
    if not targets:
        result["missing_metrics"].append("spec targets missing or invalid")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    metric_aliases = spec.get("metric_aliases", {})
    if not isinstance(metric_aliases, dict):
        metric_aliases = {}

    all_pass = True
    for name, rule in targets.items():
        if not isinstance(rule, dict):
            result["gaps"].append({"name": name, "reason": "invalid rule format"})
            all_pass = False
            continue

        actual, source_metric, failure_reason = resolve_actual(name, metrics, failed, metric_aliases)
        if actual is None:
            if failure_reason:
                result["failed_metrics"].append({"name": name, "reason": failure_reason})
            else:
                result["missing_metrics"].append(name)
            all_pass = False
            continue

        if source_metric and source_metric != name:
            result["used_aliases"][name] = source_metric

        if "min" in rule and actual < rule["min"]:
            result["gaps"].append(
                {
                    "name": name,
                    "kind": "min",
                    "target": rule["min"],
                    "actual": actual,
                    "delta": actual - rule["min"],
                }
            )
            all_pass = False
        if "max" in rule and actual > rule["max"]:
            result["gaps"].append(
                {
                    "name": name,
                    "kind": "max",
                    "target": rule["max"],
                    "actual": actual,
                    "delta": actual - rule["max"],
                }
            )
            all_pass = False

    result["pass"] = all_pass
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
