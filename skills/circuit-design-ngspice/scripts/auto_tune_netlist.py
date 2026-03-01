#!/usr/bin/env python3
"""Auto-tune .param values with bounded coordinate descent."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path


PARAM_ASSIGN_RE_TEMPLATE = r"(?i)(\b{name}\s*=\s*)([^\s;]+)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto tune SPICE parameters")
    parser.add_argument("--netlist-path", required=True, help="Input netlist path")
    parser.add_argument("--spec-path", required=True, help="Spec path")
    parser.add_argument("--param-space-path", required=True, help="Parameter space JSON path")
    parser.add_argument("--work-dir", required=True, help="Work directory")
    parser.add_argument("--max-iter", type=int, default=12, help="Max accepted iterations")
    parser.add_argument("--patience", type=int, default=4, help="Stop after N non-improving rounds")
    parser.add_argument(
        "--analysis-mode",
        choices=["single", "dual"],
        default="dual",
        help="Simulation mode for each candidate",
    )
    parser.add_argument("--timeout-sec", type=int, default=90, help="Timeout per simulation")
    parser.add_argument("--ngspice-bin", default="", help="Optional ngspice executable")
    return parser


def run_json_command(cmd: list[str]) -> tuple[dict, int, str]:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip() or "{}"
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": f"non-json output: {stdout[:300]}"}
    return payload, completed.returncode, completed.stderr.strip()


def load_targets(spec: dict) -> dict:
    if isinstance(spec.get("targets_eval"), dict) and spec["targets_eval"]:
        return spec["targets_eval"]
    if isinstance(spec.get("targets"), dict):
        return spec["targets"]
    return {}


def parse_param_assignments(netlist_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in netlist_text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(".param"):
            continue
        body = stripped[6:].strip()
        for token in body.split():
            if "=" not in token:
                continue
            name, value = token.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name:
                values[name] = value
    return values


def set_param_value(netlist_text: str, name: str, value: str) -> str:
    pattern = re.compile(PARAM_ASSIGN_RE_TEMPLATE.format(name=re.escape(name)))
    lines = netlist_text.splitlines()
    replaced = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.lower().startswith(".param"):
            continue
        if pattern.search(line):
            lines[idx] = pattern.sub(rf"\g<1>{value}", line)
            replaced = True

    if not replaced:
        insert_idx = len(lines)
        for idx, line in enumerate(lines):
            if line.strip().lower() == ".end":
                insert_idx = idx
                break
        lines.insert(insert_idx, f".param {name}={value}")

    return "\n".join(lines) + "\n"


def compute_score(evaluation: dict) -> float:
    if evaluation.get("pass"):
        return 0.0
    score = 0.0
    score += 1000.0 * len(evaluation.get("missing_metrics", []))
    score += 800.0 * len(evaluation.get("failed_metrics", []))
    for gap in evaluation.get("gaps", []):
        target = float(gap.get("target", 1.0))
        delta = abs(float(gap.get("delta", 0.0)))
        denom = max(abs(target), 1e-9)
        score += delta / denom
    return score


def critical_regression(
    *,
    candidate_metrics: dict,
    reference_metrics: dict,
    targets: dict,
) -> tuple[bool, str]:
    def target_distance(value: float, rule: dict) -> float:
        # 0 means within target window; positive means outside.
        if "min" in rule and "max" in rule:
            min_v = float(rule["min"])
            max_v = float(rule["max"])
            if value < min_v:
                denom = max(abs(min_v), 1e-9)
                return (min_v - value) / denom
            if value > max_v:
                denom = max(abs(max_v), 1e-9)
                return (value - max_v) / denom
            return 0.0
        if "min" in rule:
            min_v = float(rule["min"])
            if value < min_v:
                denom = max(abs(min_v), 1e-9)
                return (min_v - value) / denom
            return 0.0
        if "max" in rule:
            max_v = float(rule["max"])
            if value > max_v:
                denom = max(abs(max_v), 1e-9)
                return (value - max_v) / denom
            return 0.0
        return 0.0

    for metric_name, rule in targets.items():
        if not isinstance(rule, dict):
            continue
        if metric_name not in candidate_metrics or metric_name not in reference_metrics:
            continue
        candidate = float(candidate_metrics[metric_name])
        reference = float(reference_metrics[metric_name])

        ref_dist = target_distance(reference, rule)
        cand_dist = target_distance(candidate, rule)
        # Trigger rollback only when candidate moves farther away from target window.
        if cand_dist > 0 and cand_dist > (ref_dist * 1.10 + 1e-12):
            return True, f"{metric_name} target-distance regressed (ref={ref_dist:.4g}, cand={cand_dist:.4g})"
    return False, ""


def simulate_candidate(
    *,
    netlist_text: str,
    iteration_dir: Path,
    spec_path: Path,
    analysis_mode: str,
    timeout_sec: int,
    ngspice_bin: str,
) -> tuple[dict, bool]:
    iteration_dir.mkdir(parents=True, exist_ok=True)
    design_path = iteration_dir / "design.cir"
    design_path.write_text(netlist_text, encoding="utf-8")

    if analysis_mode == "dual":
        dual_script = Path(__file__).resolve().with_name("run_dual_analysis.py")
        cmd = [
            sys.executable,
            str(dual_script),
            "--work-dir",
            str(iteration_dir),
            "--netlist-path",
            str(design_path),
            "--spec-path",
            str(spec_path),
            "--timeout-sec",
            str(timeout_sec),
        ]
        if ngspice_bin:
            cmd.extend(["--ngspice-bin", ngspice_bin])
        payload, return_code, stderr = run_json_command(cmd)
        if stderr:
            payload.setdefault("warnings", []).append(stderr)

        metrics_path = iteration_dir / "metrics.json"
        eval_path = iteration_dir / "evaluation.json"
        metrics = {}
        evaluation = {}
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
        if eval_path.exists():
            evaluation = json.loads(eval_path.read_text(encoding="utf-8-sig"))
        valid_outputs = bool(metrics) and bool(evaluation)
        return {
            "runner_payload": payload,
            "runner_return_code": return_code,
            "metrics": metrics.get("metrics", metrics),
            "evaluation": evaluation,
        }, valid_outputs

    # Single-analysis path
    run_script = Path(__file__).resolve().with_name("run_ngspice.py")
    parse_script = Path(__file__).resolve().with_name("parse_results.py")
    eval_script = Path(__file__).resolve().with_name("evaluate_against_spec.py")

    run_cmd = [
        sys.executable,
        str(run_script),
        "--netlist-path",
        str(design_path),
        "--work-dir",
        str(iteration_dir),
        "--timeout-sec",
        str(timeout_sec),
    ]
    if ngspice_bin:
        run_cmd.extend(["--ngspice-bin", ngspice_bin])
    run_payload, run_rc, run_stderr = run_json_command(run_cmd)
    if run_stderr:
        run_payload.setdefault("stderr", run_stderr)

    log_path = iteration_dir / "ngspice.log"
    parse_cmd = [
        sys.executable,
        str(parse_script),
        "--log-path",
        str(log_path),
    ]
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    for metric_name in load_targets(spec).keys():
        parse_cmd.extend(["--metric", str(metric_name)])
    parse_payload, _, _ = run_json_command(parse_cmd)
    (iteration_dir / "metrics.json").write_text(
        json.dumps(parse_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    eval_cmd = [
        sys.executable,
        str(eval_script),
        "--spec-path",
        str(spec_path),
        "--metrics-path",
        str(iteration_dir / "metrics.json"),
    ]
    eval_payload, eval_rc, _ = run_json_command(eval_cmd)
    (iteration_dir / "evaluation.json").write_text(
        json.dumps(eval_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "runner_payload": run_payload,
        "runner_return_code": run_rc,
        "metrics": parse_payload.get("metrics", {}),
        "evaluation": eval_payload,
    }, run_rc == 0 and eval_rc in {0, 1}


def load_param_space(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw.get("params"), dict):
        params_raw = raw["params"]
        order = raw.get("order")
    else:
        params_raw = raw
        order = None

    params: dict[str, list[str]] = {}
    for name, config in params_raw.items():
        if isinstance(config, dict):
            values = config.get("values", [])
        elif isinstance(config, list):
            values = config
        else:
            values = [config]
        values = [str(v) for v in values]
        if values:
            params[str(name)] = values

    if isinstance(order, list):
        ordered = [str(x) for x in order if str(x) in params]
        for key in params:
            if key not in ordered:
                ordered.append(key)
        return ordered, params
    return sorted(params.keys()), params


def main() -> int:
    args = build_parser().parse_args()

    netlist_path = Path(args.netlist_path).resolve()
    spec_path = Path(args.spec_path).resolve()
    param_space_path = Path(args.param_space_path).resolve()
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "ok": False,
        "work_dir": str(work_dir),
        "best_netlist_path": str(work_dir / "best_design.cir"),
        "best_score": math.inf,
        "iterations": [],
        "warnings": [],
    }

    if not netlist_path.exists() or not spec_path.exists() or not param_space_path.exists():
        if not netlist_path.exists():
            result["warnings"].append(f"netlist not found: {netlist_path}")
        if not spec_path.exists():
            result["warnings"].append(f"spec not found: {spec_path}")
        if not param_space_path.exists():
            result["warnings"].append(f"param space not found: {param_space_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    order, params = load_param_space(param_space_path)
    if not order:
        result["warnings"].append("no parameters provided in param space")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    targets = load_targets(spec)

    current_text = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
    current_params = parse_param_assignments(current_text)

    best_text = current_text
    best_metrics: dict = {}
    best_eval: dict = {}
    best_score = math.inf
    no_improve_rounds = 0

    # Baseline run
    iter_count = 1
    baseline_dir = work_dir / f"iter_{iter_count:03d}"
    baseline_run, baseline_ok = simulate_candidate(
        netlist_text=current_text,
        iteration_dir=baseline_dir,
        spec_path=spec_path,
        analysis_mode=args.analysis_mode,
        timeout_sec=args.timeout_sec,
        ngspice_bin=args.ngspice_bin,
    )
    baseline_eval = baseline_run.get("evaluation", {})
    baseline_metrics = baseline_run.get("metrics", {})
    baseline_score = compute_score(baseline_eval)
    result["iterations"].append(
        {
            "iter": iter_count,
            "type": "baseline",
            "score": baseline_score,
            "pass": baseline_eval.get("pass", False),
            "accepted": True,
            "path": str(baseline_dir),
        }
    )

    if baseline_ok:
        best_text = current_text
        best_metrics = baseline_metrics
        best_eval = baseline_eval
        best_score = baseline_score
    else:
        result["warnings"].append("baseline simulation failed")

    if baseline_eval.get("pass", False):
        Path(result["best_netlist_path"]).write_text(best_text, encoding="utf-8")
        result["ok"] = True
        result["best_score"] = best_score
        (work_dir / "tuning_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    param_index = 0
    while iter_count < args.max_iter and no_improve_rounds < args.patience:
        param_name = order[param_index % len(order)]
        param_index += 1
        current_value = current_params.get(param_name, "")
        candidate_values = [v for v in params[param_name] if v != current_value]

        if not candidate_values:
            no_improve_rounds += 1
            continue

        round_best = None
        round_best_payload = None
        round_best_text = None
        round_best_metrics = None
        round_best_eval = None
        round_best_value = None

        for probe_idx, candidate_value in enumerate(candidate_values, start=1):
            probe_text = set_param_value(current_text, param_name, candidate_value)
            probe_dir = work_dir / "_probes" / f"iter_{iter_count+1:03d}_{param_name}_{probe_idx:02d}"
            probe_payload, probe_ok = simulate_candidate(
                netlist_text=probe_text,
                iteration_dir=probe_dir,
                spec_path=spec_path,
                analysis_mode=args.analysis_mode,
                timeout_sec=args.timeout_sec,
                ngspice_bin=args.ngspice_bin,
            )
            if not probe_ok:
                continue
            probe_eval = probe_payload.get("evaluation", {})
            probe_metrics = probe_payload.get("metrics", {})
            probe_score = compute_score(probe_eval)
            if round_best is None or probe_score < round_best:
                round_best = probe_score
                round_best_payload = probe_payload
                round_best_text = probe_text
                round_best_metrics = probe_metrics
                round_best_eval = probe_eval
                round_best_value = candidate_value

        iter_count += 1
        iter_dir = work_dir / f"iter_{iter_count:03d}"
        accepted = False
        reason = "no_valid_candidate"
        candidate_score = math.inf

        if round_best is not None:
            candidate_score = float(round_best)
            regressed, regression_reason = critical_regression(
                candidate_metrics=round_best_metrics or {},
                reference_metrics=best_metrics,
                targets=targets,
            )
            if regressed:
                reason = f"rollback:{regression_reason}"
                no_improve_rounds += 1
            elif candidate_score + 1e-12 < best_score:
                accepted = True
                reason = "improved"
                no_improve_rounds = 0
                current_text = round_best_text or current_text
                if round_best_value is not None:
                    current_params[param_name] = round_best_value
                best_text = current_text
                best_metrics = round_best_metrics or {}
                best_eval = round_best_eval or {}
                best_score = candidate_score
            else:
                reason = "no_improvement"
                no_improve_rounds += 1
        else:
            no_improve_rounds += 1

        iter_dir.mkdir(parents=True, exist_ok=True)
        if round_best_text is not None:
            (iter_dir / "design.cir").write_text(round_best_text, encoding="utf-8")
            (iter_dir / "metrics.json").write_text(
                json.dumps({"metrics": round_best_metrics or {}}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (iter_dir / "evaluation.json").write_text(
                json.dumps(round_best_eval or {}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            (iter_dir / "design.cir").write_text(current_text, encoding="utf-8")

        result["iterations"].append(
            {
                "iter": iter_count,
                "type": "tune_step",
                "param": param_name,
                "score": candidate_score,
                "pass": bool((round_best_eval or {}).get("pass", False)),
                "accepted": accepted,
                "reason": reason,
                "path": str(iter_dir),
            }
        )

        if accepted and best_eval.get("pass", False):
            break

    Path(result["best_netlist_path"]).write_text(best_text, encoding="utf-8")
    result["best_score"] = best_score
    result["best_pass"] = bool(best_eval.get("pass", False))
    result["best_metrics"] = best_metrics
    result["ok"] = bool(best_eval.get("pass", False))

    (work_dir / "tuning_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
