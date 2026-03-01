#!/usr/bin/env python3
"""Diagnose regression failures and provide actionable patch suggestions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CONVERGENCE_PATTERNS = [
    re.compile(r"timestep too small", re.IGNORECASE),
    re.compile(r"singular matrix", re.IGNORECASE),
    re.compile(r"no convergence", re.IGNORECASE),
    re.compile(r"iteration limit", re.IGNORECASE),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose regression failures")
    parser.add_argument("--summary-path", default="", help="Regression summary JSON path")
    parser.add_argument("--work-root", default="", help="Fallback work root (uses regression_summary.json)")
    parser.add_argument("--output-path", default="", help="Optional output JSON path")
    parser.add_argument("--max-log-bytes", type=int, default=250000, help="Max bytes to scan per log file")
    return parser


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def scan_logs_for_convergence(final_dir: Path, max_bytes: int) -> tuple[bool, list[str]]:
    findings: list[str] = []
    for log_path in sorted(final_dir.glob("*ngspice*.log")) + sorted(final_dir.glob("*.log")):
        if not log_path.exists():
            continue
        text = log_path.read_text(encoding="utf-8-sig", errors="ignore")
        if max_bytes > 0:
            text = text[-max_bytes:]
        for pattern in CONVERGENCE_PATTERNS:
            if pattern.search(text):
                findings.append(f"{log_path.name}: {pattern.pattern}")
                break
    return (len(findings) > 0), findings


def gap_recommendations(gaps: list[dict]) -> list[str]:
    recs: list[str] = []
    names = [str(g.get("name", "")).lower() for g in gaps]
    if any(("s11" in n or "s22" in n) for n in names):
        recs.append("retune input/output matching network (L/C values, source/load termination).")
    if any(("nf" in n or "noise" in n) for n in names):
        recs.append("increase device bias current and reduce source degeneration for lower noise.")
    if any(("pm" in n or "stability" in n) for n in names):
        recs.append("increase compensation capacitor or lower high-frequency gain path.")
    if any(("slew" in n or "settling" in n) for n in names):
        recs.append("raise tail/current-source bias and reduce output load capacitance if allowed.")
    if any(("efficiency" in n or "ripple" in n or "reg_" in n) for n in names):
        recs.append("re-balance switching frequency and LC output filter; tune duty/feedback setpoints.")
    if any(("startup" in n or "f_osc" in n) for n in names):
        recs.append("increase oscillator loop gain and reduce loading on oscillation nodes.")
    if any(("il_db" in n or "attn_stop" in n or "ripple_db" in n) for n in names):
        recs.append("increase filter order or retune resonator coupling coefficients.")
    if not recs:
        recs.append("run targeted auto-tuning on top 2-3 sensitive parameters from failed metrics.")
    return recs


def analyze_case(case: dict, *, max_log_bytes: int) -> dict:
    case_id = str(case.get("id", "case"))
    final_dir = Path(case.get("final_dir", ""))
    case_root = Path(case.get("case_root", ""))

    evaluation = read_json(final_dir / "evaluation.json")
    strict_json = read_json(final_dir / "strict_param_validation.json")
    primitive_json = read_json(final_dir / "primitive_validation.json")
    schema_json = read_json(final_dir / "schema_validation.json")
    model_json = read_json(final_dir / "model_validate.json")

    categories: list[str] = []
    evidence: list[str] = []
    recommendations: list[str] = []

    strict_ok = bool(strict_json.get("ok", False))
    primitive_ok = bool(primitive_json.get("ok", False))
    schema_ok = bool(schema_json.get("ok", False))
    eval_pass = bool(evaluation.get("pass", False))
    steps = case.get("steps", {}) if isinstance(case.get("steps"), dict) else {}

    if not strict_ok or not primitive_ok:
        categories.append("parameter_integrity")
        evidence.append("strict/primitive validation failed")
        recommendations.append("run strict_param_check and fix missing/unparseable component parameters first.")

    if not schema_ok:
        categories.append("metric_schema_issue")
        unknown_targets = schema_json.get("unknown_target_metrics", [])
        if unknown_targets:
            evidence.append("unknown target metrics: " + ", ".join(str(x) for x in unknown_targets[:8]))
        recommendations.append("align spec target names with references/metric_schema.json.")

    conv_hit, conv_evidence = scan_logs_for_convergence(final_dir, max_log_bytes)
    if conv_hit:
        categories.append("convergence_issue")
        evidence.extend(conv_evidence[:5])
        recommendations.append("add robust .options (method=gear, reltol/abstol) and better initial conditions.")

    missing_models = model_json.get("missing_models", [])
    if isinstance(missing_models, list) and missing_models:
        categories.append("model_issue")
        evidence.append(f"missing models: {len(missing_models)}")
        recommendations.append("add missing .model cards or sync registry with manage_model_library --action sync-registry.")

    if not eval_pass:
        gaps = evaluation.get("gaps", []) if isinstance(evaluation.get("gaps"), list) else []
        missing_metrics = evaluation.get("missing_metrics", [])
        failed_metrics = evaluation.get("failed_metrics", [])

        if gaps:
            categories.append("spec_gap")
            evidence.append(f"target gaps: {len(gaps)}")
            recommendations.extend(gap_recommendations(gaps))
        if missing_metrics or failed_metrics:
            categories.append("measurement_extraction_issue")
            evidence.append(
                f"missing_metrics={len(missing_metrics) if isinstance(missing_metrics, list) else 0}, "
                f"failed_metrics={len(failed_metrics) if isinstance(failed_metrics, list) else 0}"
            )
            recommendations.append("check .meas directives and extractor script mapping for the failed domain.")

    if not categories and case.get("pipeline_ok") and eval_pass:
        categories.append("pass")
        evidence.append("no blocking issue detected")

    if not categories and not case.get("pipeline_ok"):
        failed_steps = []
        for name, payload in steps.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("ok", False):
                failed_steps.append(name)
        categories.append("execution_pipeline_issue")
        if failed_steps:
            evidence.append("failed steps: " + ", ".join(failed_steps))
        recommendations.append("inspect failed step stderr/stdout in regression_summary.json and rerun that stage.")

    dedup_recs = []
    seen = set()
    for item in recommendations:
        if item in seen:
            continue
        seen.add(item)
        dedup_recs.append(item)

    return {
        "id": case_id,
        "categories": categories,
        "evidence": evidence,
        "recommendations": dedup_recs,
        "pipeline_ok": bool(case.get("pipeline_ok")),
        "evaluation_pass": eval_pass,
        "case_root": str(case_root),
        "final_dir": str(final_dir),
    }


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output_path).resolve() if args.output_path else None

    if args.summary_path.strip():
        summary_path = Path(args.summary_path).resolve()
    elif args.work_root.strip():
        summary_path = Path(args.work_root).resolve() / "regression_summary.json"
    else:
        summary_path = Path("regression_summary.json").resolve()

    result = {
        "ok": False,
        "summary_path": str(summary_path),
        "cases": [],
        "category_counts": {},
        "warnings": [],
    }

    summary = read_json(summary_path)
    if not summary:
        result["warnings"].append(f"summary missing or invalid: {summary_path}")
        payload = json.dumps(result, ensure_ascii=False)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    cases = summary.get("cases", [])
    if not isinstance(cases, list):
        cases = []

    for case in cases:
        if not isinstance(case, dict):
            continue
        diag = analyze_case(case, max_log_bytes=max(10000, args.max_log_bytes))
        result["cases"].append(diag)
        for cat in diag.get("categories", []):
            result["category_counts"][cat] = int(result["category_counts"].get(cat, 0)) + 1

    has_hard_fail = any(
        "pass" not in row.get("categories", [])
        and (not row.get("pipeline_ok") or not row.get("evaluation_pass"))
        for row in result["cases"]
    )
    result["ok"] = not has_hard_fail

    payload = json.dumps(result, ensure_ascii=False)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
