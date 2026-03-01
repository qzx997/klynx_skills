#!/usr/bin/env python3
"""Run PVT corner simulations and aggregate metric extraction results."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from shutil import which

from parse_results import parse_measurements
from run_ngspice import resolve_ngspice_bin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PVT corner sweep")
    parser.add_argument("--netlist-path", required=True, help="Base netlist path")
    parser.add_argument("--work-dir", required=True, help="Output work directory")
    parser.add_argument("--corners-path", default="", help="JSON path for corner definitions")
    parser.add_argument("--metric", action="append", default=[], help="Metric filter (repeatable)")
    parser.add_argument("--spec-path", default="", help="Optional spec path for pass/fail at each corner")
    parser.add_argument("--timeout-sec", type=int, default=90, help="Timeout per corner")
    parser.add_argument("--ngspice-bin", default="", help="ngspice executable override")
    parser.add_argument("--output-path", default="", help="Optional summary JSON path")
    parser.add_argument("--require-pass", action="store_true", help="Return non-zero if any corner fails spec")
    return parser


def resolve_executable(cli_value: str) -> str:
    exe, _ = resolve_ngspice_bin(cli_value)
    if Path(exe).exists():
        return str(Path(exe).resolve())
    return which(exe) or ""


def load_corners(path_value: str) -> list[dict]:
    if not path_value.strip():
        return [{"name": "tt_27c", "temp_c": 27.0, "param_overrides": {}}]
    path = Path(path_value).resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("corners"), list):
        raw = payload["corners"]
    elif isinstance(payload, list):
        raw = payload
    else:
        return [{"name": "tt_27c", "temp_c": 27.0, "param_overrides": {}}]

    corners: list[dict] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", f"corner_{idx+1}")).strip() or f"corner_{idx+1}"
        temp_c = item.get("temp_c", 27.0)
        try:
            temp_value = float(temp_c)
        except (TypeError, ValueError):
            temp_value = 27.0
        overrides = item.get("param_overrides", item.get("params", {}))
        if not isinstance(overrides, dict):
            overrides = {}
        corners.append({"name": name, "temp_c": temp_value, "param_overrides": overrides})
    return corners or [{"name": "tt_27c", "temp_c": 27.0, "param_overrides": {}}]


def build_corner_netlist(base_text: str, *, temp_c: float, param_overrides: dict[str, str]) -> str:
    lines = base_text.splitlines()
    body: list[str] = []
    end_seen = False
    for raw in lines:
        stripped = raw.strip().lstrip("\ufeff")
        if stripped.lower().startswith(".temp"):
            continue
        if stripped.lower() == ".end":
            end_seen = True
            continue
        body.append(raw.rstrip())
    body.append(f".temp {temp_c}")
    for key, value in sorted(param_overrides.items(), key=lambda kv: kv[0].lower()):
        body.append(f".param {key}={value}")
    body.append(".end")
    if not end_seen and base_text.endswith("\n"):
        return "\n".join(body) + "\n"
    return "\n".join(body) + "\n"


def load_targets(spec_path: str) -> tuple[dict[str, dict], dict[str, list[str]]]:
    if not spec_path.strip():
        return {}, {}
    path = Path(spec_path).resolve()
    if not path.exists():
        return {}, {}
    spec = json.loads(path.read_text(encoding="utf-8-sig"))
    targets = spec.get("targets_eval") if isinstance(spec.get("targets_eval"), dict) else spec.get("targets", {})
    aliases = spec.get("metric_aliases", {})
    if not isinstance(targets, dict):
        targets = {}
    if not isinstance(aliases, dict):
        aliases = {}
    return targets, aliases


def resolve_metric(name: str, metrics: dict[str, float], aliases: dict[str, list[str]]) -> float | None:
    if name in metrics:
        return metrics[name]
    for alias in aliases.get(name, []):
        if alias in metrics:
            return metrics[alias]
    return None


def evaluate_targets(
    metrics: dict[str, float],
    *,
    targets: dict[str, dict],
    aliases: dict[str, list[str]],
) -> tuple[bool, list[dict]]:
    if not targets:
        return True, []
    gaps: list[dict] = []
    for name, rule in targets.items():
        if not isinstance(rule, dict):
            gaps.append({"name": name, "reason": "invalid_rule"})
            continue
        actual = resolve_metric(name, metrics, aliases)
        if actual is None:
            gaps.append({"name": name, "reason": "missing_metric"})
            continue
        if "min" in rule and actual < float(rule["min"]):
            gaps.append({"name": name, "kind": "min", "target": float(rule["min"]), "actual": actual})
        if "max" in rule and actual > float(rule["max"]):
            gaps.append({"name": name, "kind": "max", "target": float(rule["max"]), "actual": actual})
    return (len(gaps) == 0), gaps


def run_corner(executable: str, netlist_path: Path, log_path: Path, timeout_sec: int) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [executable, "-b", "-o", str(log_path), str(netlist_path)],
            capture_output=True,
            text=True,
            timeout=max(1, timeout_sec),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_sec}s"
    if completed.returncode != 0:
        return False, completed.stderr.strip()
    return log_path.exists(), completed.stderr.strip()


def main() -> int:
    args = build_parser().parse_args()
    result = {
        "ok": False,
        "work_dir": "",
        "summary_path": "",
        "corner_count": 0,
        "pass_count": 0,
        "run_error_count": 0,
        "rows": [],
        "warnings": [],
    }

    netlist_path = Path(args.netlist_path).resolve()
    work_dir = Path(args.work_dir).resolve()
    result["work_dir"] = str(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if not netlist_path.exists():
        result["warnings"].append(f"netlist not found: {netlist_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    executable = resolve_executable(args.ngspice_bin)
    if not executable:
        result["warnings"].append("ngspice executable not found")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    base_text = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
    corners = load_corners(args.corners_path)
    targets, aliases = load_targets(args.spec_path)
    wanted = {m.strip() for m in args.metric if m.strip()}
    if not wanted and targets:
        wanted = set(targets.keys())

    for corner in corners:
        name = str(corner.get("name", "corner")).strip() or "corner"
        temp_c = float(corner.get("temp_c", 27.0))
        overrides = corner.get("param_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}

        corner_dir = work_dir / "corners" / name
        corner_dir.mkdir(parents=True, exist_ok=True)
        corner_netlist = corner_dir / "design.cir"
        corner_log = corner_dir / "ngspice.log"
        corner_metrics_path = corner_dir / "metrics.json"
        corner_eval_path = corner_dir / "evaluation.json"

        corner_text = build_corner_netlist(base_text, temp_c=temp_c, param_overrides=overrides)
        corner_netlist.write_text(corner_text, encoding="utf-8")

        ok_run, stderr = run_corner(executable, corner_netlist, corner_log, args.timeout_sec)
        row = {
            "corner": name,
            "temp_c": temp_c,
            "param_overrides": overrides,
            "netlist_path": str(corner_netlist),
            "log_path": str(corner_log),
            "metrics_path": str(corner_metrics_path),
            "evaluation_path": str(corner_eval_path),
            "run_ok": ok_run,
            "stderr": stderr,
            "metrics": {},
            "pass": False,
            "gaps": [],
        }
        if not ok_run:
            result["run_error_count"] += 1
            result["rows"].append(row)
            continue

        parsed = parse_measurements(corner_log.read_text(encoding="utf-8-sig", errors="ignore"), wanted)
        metrics = {k: float(v) for k, v in parsed.get("metrics", {}).items()}
        row["metrics"] = metrics
        corner_metrics_path.write_text(
            json.dumps(
                {
                    "metrics": metrics,
                    "failed_metrics": parsed.get("failed_metrics", {}),
                    "analysis_context": parsed.get("analysis_context", {}),
                    "warnings": parsed.get("warnings", []),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        eval_pass, gaps = evaluate_targets(metrics, targets=targets, aliases=aliases)
        row["pass"] = eval_pass
        row["gaps"] = gaps
        corner_eval_path.write_text(
            json.dumps({"pass": eval_pass, "gaps": gaps}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if eval_pass:
            result["pass_count"] += 1
        result["rows"].append(row)

    result["corner_count"] = len(corners)
    result["ok"] = result["run_error_count"] == 0
    summary_path = Path(args.output_path).resolve() if args.output_path else work_dir / "pvt_summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["summary_path"] = str(summary_path)
    print(json.dumps(result, ensure_ascii=False))

    if not result["ok"]:
        return 1
    if args.require_pass and targets and result["pass_count"] != result["corner_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
