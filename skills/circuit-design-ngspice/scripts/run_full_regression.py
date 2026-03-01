#!/usr/bin/env python3
"""Run full benchmark regression across circuit domains and update scoreboard."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent


def default_suite_path() -> Path:
    return SKILL_ROOT / "assets" / "benchmarks" / "fullstack_suite.json"


def default_scoreboard_path() -> Path:
    return SKILL_ROOT / "references" / "benchmark_scoreboard.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full benchmark regression suite")
    parser.add_argument("--work-root", required=True, help="Output root directory for this regression run")
    parser.add_argument("--suite-path", default="", help="Benchmark suite JSON path")
    parser.add_argument("--scoreboard-path", default="", help="Scoreboard JSON path")
    parser.add_argument("--ngspice-bin", default="", help="ngspice executable override")
    parser.add_argument("--skip-common-tests", action="store_true", help="Skip PVT/Monte Carlo runs")
    parser.add_argument("--skip-diagnosis", action="store_true", help="Skip post-run failure diagnosis")
    return parser


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_json_from_stdout(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass

    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def shrink_payload(value, *, depth: int = 0, max_depth: int = 4, max_list: int = 6):
    if depth >= max_depth:
        if isinstance(value, (dict, list)):
            return "<truncated>"
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[key] = shrink_payload(item, depth=depth + 1, max_depth=max_depth, max_list=max_list)
        return out
    if isinstance(value, list):
        if len(value) <= max_list:
            return [shrink_payload(item, depth=depth + 1, max_depth=max_depth, max_list=max_list) for item in value]
        head = [shrink_payload(item, depth=depth + 1, max_depth=max_depth, max_list=max_list) for item in value[:max_list]]
        head.append(f"... ({len(value) - max_list} more)")
        return head
    return value


def run_script(script_name: str, *args: str, cwd: Path | None = None) -> dict:
    script_path = SCRIPT_DIR / script_name
    cmd = [sys.executable, str(script_path), *args]
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = parse_json_from_stdout(completed.stdout)
    return {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "cmd": cmd,
        "stdout": completed.stdout.strip()[:2000],
        "stderr": completed.stderr.strip()[:1000],
        "payload": shrink_payload(payload),
    }


def ensure_targets(spec: dict) -> dict:
    out = dict(spec)
    targets_eval = out.get("targets_eval")
    targets = out.get("targets")
    if isinstance(targets_eval, dict):
        if not isinstance(targets, dict):
            out["targets"] = dict(targets_eval)
    elif isinstance(targets, dict):
        out["targets_eval"] = dict(targets)
    else:
        out["targets_eval"] = {}
        out["targets"] = {}
    return out


def merge_metrics(base_payload: dict, extract_payload: dict) -> dict:
    merged = {
        "metrics": {},
        "failed_metrics": {},
        "raw_measure_lines": [],
        "analysis_context": {},
        "warnings": [],
    }

    if isinstance(base_payload.get("metrics"), dict):
        merged["metrics"].update(base_payload.get("metrics", {}))
    if isinstance(base_payload.get("failed_metrics"), dict):
        merged["failed_metrics"].update(base_payload.get("failed_metrics", {}))
    if isinstance(base_payload.get("analysis_context"), dict):
        merged["analysis_context"].update(base_payload.get("analysis_context", {}))
    if isinstance(base_payload.get("raw_measure_lines"), list):
        merged["raw_measure_lines"].extend(base_payload.get("raw_measure_lines", []))
    if isinstance(base_payload.get("warnings"), list):
        merged["warnings"].extend(base_payload.get("warnings", []))

    if isinstance(extract_payload.get("metrics"), dict):
        merged["metrics"].update(extract_payload.get("metrics", {}))
    if isinstance(extract_payload.get("failed_metrics"), dict):
        merged["failed_metrics"].update(extract_payload.get("failed_metrics", {}))
    if isinstance(extract_payload.get("analysis_context"), dict):
        merged["analysis_context"].update(extract_payload.get("analysis_context", {}))
    if isinstance(extract_payload.get("warnings"), list):
        merged["warnings"].extend(extract_payload.get("warnings", []))

    return merged


def extractor_command(extractor: str, final_dir: Path) -> list[str]:
    if extractor == "rf_lna":
        return [
            "extract_rf_lna_metrics.py",
            "--forward-log-path",
            str(final_dir / "ngspice.log"),
            "--allow-partial",
            "--output-path",
            str(final_dir / "extract_result.json"),
        ]
    if extractor == "opamp":
        return [
            "extract_opamp_metrics.py",
            "--ac-log-path",
            str(final_dir / "ac_ngspice.log"),
            "--tran-log-path",
            str(final_dir / "power_ngspice.log"),
            "--output-path",
            str(final_dir / "extract_result.json"),
        ]
    if extractor == "oscillator":
        return [
            "extract_oscillator_metrics.py",
            "--log-path",
            str(final_dir / "ngspice.log"),
            "--allow-no-startup",
            "--output-path",
            str(final_dir / "extract_result.json"),
        ]
    if extractor == "power":
        return [
            "extract_power_metrics.py",
            "--steady-log-path",
            str(final_dir / "ngspice.log"),
            "--line-log-path",
            str(final_dir / "ngspice.log"),
            "--load-log-path",
            str(final_dir / "ngspice.log"),
            "--transient-log-path",
            str(final_dir / "ngspice.log"),
            "--allow-partial",
            "--output-path",
            str(final_dir / "extract_result.json"),
        ]
    return []


def run_case(case_cfg: dict, *, work_root: Path, ngspice_bin: str) -> dict:
    case_id = str(case_cfg.get("id", "")).strip() or "case_unknown"
    domain = str(case_cfg.get("domain", "")).strip().lower()
    run_mode = str(case_cfg.get("run_mode", "single")).strip().lower()
    extractor = str(case_cfg.get("extractor", "")).strip().lower()

    case_root = work_root / case_id
    final_dir = case_root / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    template_relpath = str(case_cfg.get("template_relpath", "")).strip()
    template_path = (SKILL_ROOT / template_relpath).resolve()
    design_path = final_dir / "design.cir"
    spec_path = case_root / "spec.json"
    metrics_path = final_dir / "metrics.json"
    evaluation_path = final_dir / "evaluation.json"

    result = {
        "id": case_id,
        "domain": domain,
        "run_mode": run_mode,
        "extractor": extractor,
        "case_root": str(case_root),
        "final_dir": str(final_dir),
        "template_path": str(template_path),
        "design_path": str(design_path),
        "spec_path": str(spec_path),
        "metrics_path": str(metrics_path),
        "evaluation_path": str(evaluation_path),
        "steps": {},
        "pipeline_ok": False,
        "evaluation_pass": False,
    }

    if not template_path.exists():
        result["steps"]["prepare"] = {"ok": False, "error": f"template not found: {template_path}"}
        return result

    shutil.copyfile(template_path, design_path)
    spec_payload = ensure_targets(case_cfg.get("spec", {}) if isinstance(case_cfg.get("spec"), dict) else {})
    if domain and isinstance(spec_payload.get("metric_pack_notes"), dict):
        spec_payload["metric_pack_notes"]["domain"] = domain
    elif domain:
        spec_payload["metric_pack_notes"] = {"domain": domain}
    write_json(spec_path, spec_payload)
    result["steps"]["prepare"] = {"ok": True}

    strict = run_script(
        "strict_param_check.py",
        "--netlist-path",
        str(design_path),
        "--allow-expression",
        "--output-path",
        str(final_dir / "strict_param_validation.json"),
    )
    result["steps"]["strict_param_check"] = strict

    primitive = run_script(
        "validate_netlist_primitives.py",
        "--netlist-path",
        str(design_path),
    )
    write_json(final_dir / "primitive_validation.json", primitive.get("payload", {}))
    result["steps"]["primitive_validation"] = primitive

    if run_mode == "dual":
        dual_args = [
            "--work-dir",
            str(final_dir),
            "--netlist-path",
            str(design_path),
        ]
        if ngspice_bin.strip():
            dual_args.extend(["--ngspice-bin", ngspice_bin.strip()])
        run_step = run_script("run_dual_analysis.py", *dual_args)
        result["steps"]["simulate"] = run_step
        run_payload = read_json(metrics_path)
    else:
        run_args = [
            "--netlist-path",
            str(design_path),
            "--work-dir",
            str(final_dir),
        ]
        if ngspice_bin.strip():
            run_args.extend(["--ngspice-bin", ngspice_bin.strip()])
        run_step = run_script("run_ngspice.py", *run_args)
        result["steps"]["simulate"] = run_step

        parse_args = [
            "--log-path",
            str(final_dir / "ngspice.log"),
        ]
        parse_step = run_script("parse_results.py", *parse_args)
        result["steps"]["parse_results"] = parse_step
        run_payload = parse_step.get("payload", {})
        write_json(metrics_path, run_payload if isinstance(run_payload, dict) else {})

    extract_payload: dict = {}
    if extractor:
        cmd = extractor_command(extractor, final_dir)
        if cmd:
            extract_step = run_script(cmd[0], *cmd[1:])
            result["steps"]["extract_metrics"] = extract_step
            extract_payload = extract_step.get("payload", {}) if isinstance(extract_step.get("payload"), dict) else {}
        else:
            result["steps"]["extract_metrics"] = {"ok": False, "error": f"unknown extractor: {extractor}"}
    else:
        result["steps"]["extract_metrics"] = {"ok": True, "payload": {}}

    merged = merge_metrics(run_payload if isinstance(run_payload, dict) else {}, extract_payload)
    write_json(metrics_path, merged)

    schema_step = run_script(
        "validate_metric_schema.py",
        "--spec-path",
        str(spec_path),
        "--metrics-path",
        str(metrics_path),
        "--domain",
        domain,
        "--strict-targets",
        "--output-path",
        str(final_dir / "schema_validation.json"),
    )
    result["steps"]["schema_validation"] = schema_step

    eval_step = run_script(
        "evaluate_against_spec.py",
        "--spec-path",
        str(spec_path),
        "--metrics-path",
        str(metrics_path),
    )
    result["steps"]["evaluate"] = eval_step
    write_json(evaluation_path, eval_step.get("payload", {}))
    eval_payload = eval_step.get("payload", {}) if isinstance(eval_step.get("payload"), dict) else {}
    result["evaluation_pass"] = bool(eval_payload.get("pass", False))

    net_json_step = run_script(
        "netlist_to_json.py",
        "--netlist-path",
        str(design_path),
        "--json-path",
        str(final_dir / "design.json"),
        "--input-node",
        str(case_cfg.get("input_node", "in")),
        "--output-node",
        str(case_cfg.get("output_node", "out")),
    )
    result["steps"]["netlist_to_json"] = net_json_step

    render_step = run_script(
        "render_netlistsvg.py",
        "--json-path",
        str(final_dir / "design.json"),
        "--svg-path",
        str(final_dir / "schematic.svg"),
        "--skin-profile",
        "analog",
    )
    result["steps"]["render_schematic"] = render_step

    io_step = run_script(
        "validate_schematic_io.py",
        "--svg-path",
        str(final_dir / "schematic.svg"),
        "--output-path",
        str(final_dir / "schematic_io_validation.json"),
    )
    result["steps"]["schematic_io"] = io_step

    model_step = run_script(
        "manage_model_library.py",
        "--action",
        "validate",
        "--netlist-path",
        str(design_path),
        "--output-path",
        str(final_dir / "model_validate.json"),
    )
    result["steps"]["model_validate"] = model_step

    plot_results: list[dict] = []
    for plot_cfg in case_cfg.get("plots", []):
        if not isinstance(plot_cfg, dict):
            continue
        log_path = final_dir / str(plot_cfg.get("log_relpath", "ngspice.log"))
        svg_path = final_dir / str(plot_cfg.get("svg_relpath", "plots/sim_plot.svg"))
        mode = str(plot_cfg.get("plot_mode", "auto"))
        title = f"{case_id} - {mode.upper()} plot"
        plot_step = run_script(
            "plot_sim_results.py",
            "--log-path",
            str(log_path),
            "--svg-path",
            str(svg_path),
            "--plot-mode",
            mode,
            "--title",
            title,
        )
        plot_results.append(plot_step)
    result["steps"]["plots"] = {"ok": all(p.get("ok", False) for p in plot_results), "runs": plot_results}

    report_step = run_script(
        "generate_report.py",
        "--job-dir",
        str(case_root),
        "--output-path",
        str(final_dir / "report.md"),
        "--title",
        f"Design Report - {case_id}",
    )
    result["steps"]["report_md"] = report_step

    export_step = run_script(
        "export_report_bundle.py",
        "--report-path",
        str(final_dir / "report.md"),
        "--html-path",
        str(final_dir / "report.html"),
        "--pdf-path",
        str(final_dir / "report.pdf"),
    )
    result["steps"]["report_export"] = export_step

    audit_step = run_script(
        "audit_report_completeness.py",
        "--report-path",
        str(final_dir / "report.md"),
        "--output-path",
        str(final_dir / "report_audit.json"),
    )
    result["steps"]["report_audit"] = audit_step

    critical_steps = [
        "prepare",
        "strict_param_check",
        "primitive_validation",
        "simulate",
        "extract_metrics",
        "schema_validation",
        "netlist_to_json",
        "render_schematic",
        "schematic_io",
        "model_validate",
        "report_md",
        "report_audit",
    ]
    result["pipeline_ok"] = all(bool(result["steps"].get(name, {}).get("ok", False)) for name in critical_steps)
    result["metrics_count"] = len(merged.get("metrics", {}))
    return result


def run_common_tests(suite: dict, case_rows: list[dict], work_root: Path, ngspice_bin: str) -> dict:
    common_cfg = suite.get("common_tests", {})
    if not isinstance(common_cfg, dict):
        common_cfg = {}
    out = {"pvt": {}, "monte_carlo": {}}
    case_map = {row.get("id"): row for row in case_rows}

    pvt_cfg = common_cfg.get("pvt", {})
    if isinstance(pvt_cfg, dict) and pvt_cfg.get("enabled"):
        ref_id = str(pvt_cfg.get("reference_case_id", "")).strip()
        ref_case = case_map.get(ref_id, {})
        ref_dir = Path(ref_case.get("final_dir", ""))
        ref_root = Path(ref_case.get("case_root", ""))
        if ref_dir.exists() and ref_root.exists():
            pvt_dir = work_root / "common" / "pvt"
            pvt_dir.mkdir(parents=True, exist_ok=True)
            corners_path = pvt_dir / "corners.json"
            corners_payload = {"corners": pvt_cfg.get("corners", [])}
            write_json(corners_path, corners_payload)
            cmd = [
                "run_pvt_sweep.py",
                "--netlist-path",
                str(ref_dir / "design.cir"),
                "--work-dir",
                str(pvt_dir),
                "--corners-path",
                str(corners_path),
                "--spec-path",
                str(ref_root / "spec.json"),
                "--output-path",
                str(pvt_dir / "pvt_summary.json"),
            ]
            if ngspice_bin.strip():
                cmd.extend(["--ngspice-bin", ngspice_bin.strip()])
            out["pvt"] = run_script(cmd[0], *cmd[1:])
        else:
            out["pvt"] = {"ok": False, "error": f"reference case for PVT missing: {ref_id}"}

    mc_cfg = common_cfg.get("monte_carlo", {})
    if isinstance(mc_cfg, dict) and mc_cfg.get("enabled"):
        ref_id = str(mc_cfg.get("reference_case_id", "")).strip()
        ref_case = case_map.get(ref_id, {})
        ref_dir = Path(ref_case.get("final_dir", ""))
        ref_root = Path(ref_case.get("case_root", ""))
        if ref_dir.exists() and ref_root.exists():
            mc_dir = work_root / "common" / "monte_carlo"
            mc_dir.mkdir(parents=True, exist_ok=True)
            params_path = mc_dir / "params.json"
            params_payload = mc_cfg.get("params", {})
            write_json(params_path, params_payload if isinstance(params_payload, dict) else {})
            samples = str(int(mc_cfg.get("samples", 12)))
            seed = str(int(mc_cfg.get("seed", 20260301)))
            cmd = [
                "run_monte_carlo.py",
                "--netlist-path",
                str(ref_dir / "design.cir"),
                "--param-stats-path",
                str(params_path),
                "--samples",
                samples,
                "--seed",
                seed,
                "--work-dir",
                str(mc_dir),
                "--spec-path",
                str(ref_root / "spec.json"),
                "--output-path",
                str(mc_dir / "monte_carlo_summary.json"),
            ]
            if ngspice_bin.strip():
                cmd.extend(["--ngspice-bin", ngspice_bin.strip()])
            out["monte_carlo"] = run_script(cmd[0], *cmd[1:])
        else:
            out["monte_carlo"] = {"ok": False, "error": f"reference case for Monte Carlo missing: {ref_id}"}

    return out


def update_scoreboard(scoreboard_path: Path, suite_name: str, summary: dict) -> dict:
    board = read_json(scoreboard_path)
    if not isinstance(board.get("runs"), list):
        board["runs"] = []
    board["suite_name"] = suite_name
    board["schema_version"] = board.get("schema_version", "1.0.0")

    total = len(summary.get("cases", []))
    pipeline_pass = sum(1 for row in summary.get("cases", []) if row.get("pipeline_ok"))
    eval_pass = sum(1 for row in summary.get("cases", []) if row.get("evaluation_pass"))
    report_audit_pass = sum(
        1
        for row in summary.get("cases", [])
        if bool(row.get("steps", {}).get("report_audit", {}).get("ok", False))
    )

    total_safe = total if total > 0 else 1
    score = round(
        (pipeline_pass / total_safe) * 70.0
        + (eval_pass / total_safe) * 20.0
        + (report_audit_pass / total_safe) * 10.0,
        2,
    )

    run_entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "work_root": summary.get("work_root", ""),
        "suite_name": suite_name,
        "total_cases": total,
        "pipeline_pass_count": pipeline_pass,
        "eval_pass_count": eval_pass,
        "report_audit_pass_count": report_audit_pass,
        "score": score,
        "summary_path": summary.get("summary_path", ""),
    }

    prev = board["runs"][-1] if board["runs"] else None
    if isinstance(prev, dict):
        run_entry["delta_vs_previous"] = {
            "score_delta": round(score - float(prev.get("score", 0.0)), 2),
            "pipeline_pass_delta": pipeline_pass - int(prev.get("pipeline_pass_count", 0)),
            "eval_pass_delta": eval_pass - int(prev.get("eval_pass_count", 0)),
        }

    board["runs"].append(run_entry)
    board["runs"] = board["runs"][-30:]
    write_json(scoreboard_path, board)
    return run_entry


def main() -> int:
    args = build_parser().parse_args()
    work_root = Path(args.work_root).resolve()
    suite_path = Path(args.suite_path).resolve() if args.suite_path else default_suite_path()
    scoreboard_path = (
        Path(args.scoreboard_path).resolve() if args.scoreboard_path else default_scoreboard_path()
    )

    result = {
        "ok": False,
        "suite_path": str(suite_path),
        "scoreboard_path": str(scoreboard_path),
        "work_root": str(work_root),
        "cases": [],
        "common_tests": {},
        "scoreboard_entry": {},
        "summary_path": "",
        "warnings": [],
    }

    work_root.mkdir(parents=True, exist_ok=True)
    if not suite_path.exists():
        result["warnings"].append(f"suite not found: {suite_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    suite = read_json(suite_path)
    if not suite:
        result["warnings"].append("invalid suite JSON")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    suite_name = str(suite.get("suite_name", "fullstack_regression")).strip() or "fullstack_regression"
    cases = suite.get("cases", [])
    if not isinstance(cases, list) or not cases:
        result["warnings"].append("suite has no cases")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    for case_cfg in cases:
        if not isinstance(case_cfg, dict):
            continue
        case_result = run_case(case_cfg, work_root=work_root, ngspice_bin=args.ngspice_bin)
        result["cases"].append(case_result)

    if not args.skip_common_tests:
        result["common_tests"] = run_common_tests(
            suite,
            result["cases"],
            work_root,
            args.ngspice_bin,
        )

    summary_path = work_root / "regression_summary.json"
    result["summary_path"] = str(summary_path)

    total_cases = len(result["cases"])
    pipeline_pass = sum(1 for row in result["cases"] if row.get("pipeline_ok"))
    result["ok"] = total_cases > 0 and pipeline_pass == total_cases

    write_json(summary_path, result)

    scoreboard_entry = update_scoreboard(scoreboard_path, suite_name, result)
    result["scoreboard_entry"] = scoreboard_entry
    write_json(summary_path, result)

    if not args.skip_diagnosis:
        diag_path = work_root / "diagnosis.json"
        diag_step = run_script(
            "diagnose_failures.py",
            "--summary-path",
            str(summary_path),
            "--output-path",
            str(diag_path),
        )
        result["diagnosis"] = diag_step
        write_json(summary_path, result)

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
