#!/usr/bin/env python3
"""Run Monte Carlo sweeps by perturbing .param values and aggregating metrics."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import subprocess
from pathlib import Path
from shutil import which

from parse_results import parse_measurements
from run_ngspice import resolve_ngspice_bin


VALUE_RE = re.compile(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([A-Za-z\u00b5\u03a9]+)?\s*$")
UNIT_SCALE = {
    "": 1.0,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}
KNOWN_UNIT_TAILS = {"", "ohm", "f", "h", "v", "a"}


def split_suffix(suffix: str) -> tuple[str, str]:
    raw = (suffix or "").replace("µ", "u").replace("Ω", "ohm").lower()
    if not raw:
        return "", ""
    if raw.startswith("meg"):
        return "meg", raw[3:]
    if raw[0] in {"t", "g", "k", "m", "u", "n", "p", "f"}:
        return raw[0], raw[1:]
    return "", raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Monte Carlo netlist sweep")
    parser.add_argument("--netlist-path", required=True, help="Base netlist path")
    parser.add_argument("--param-stats-path", required=True, help="JSON path for parameter sigma definitions")
    parser.add_argument("--samples", type=int, default=20, help="Number of Monte Carlo samples")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--work-dir", required=True, help="Output work directory")
    parser.add_argument("--metric", action="append", default=[], help="Metric filter (repeatable)")
    parser.add_argument("--spec-path", default="", help="Optional spec path for yield estimation")
    parser.add_argument("--timeout-sec", type=int, default=90, help="Timeout per sample")
    parser.add_argument("--ngspice-bin", default="", help="ngspice executable override")
    parser.add_argument("--output-path", default="", help="Optional summary JSON path")
    parser.add_argument("--require-yield", type=float, default=-1.0, help="Optional minimum yield percent")
    return parser


def resolve_executable(cli_value: str) -> str:
    exe, _ = resolve_ngspice_bin(cli_value)
    if Path(exe).exists():
        return str(Path(exe).resolve())
    return which(exe) or ""


def parse_spice_value(text: str) -> tuple[float, str]:
    match = VALUE_RE.match(str(text))
    if not match:
        raise ValueError(f"unsupported spice numeric format: {text}")
    base = float(match.group(1))
    raw_suffix = (match.group(2) or "").replace("µ", "u").replace("Ω", "ohm")
    prefix, unit_tail = split_suffix(raw_suffix)
    if prefix not in UNIT_SCALE:
        raise ValueError(f"unsupported unit prefix: {match.group(2) or ''}")
    if unit_tail not in KNOWN_UNIT_TAILS:
        raise ValueError(f"unsupported unit suffix: {match.group(2) or ''}")
    normalized_suffix = f"{prefix}{unit_tail}" if (prefix or unit_tail) else ""
    return base * UNIT_SCALE[prefix], normalized_suffix


def format_spice_value(value: float, unit_hint: str = "") -> str:
    prefix, unit_tail = split_suffix(unit_hint)
    if prefix in UNIT_SCALE and unit_tail in KNOWN_UNIT_TAILS and (prefix or unit_tail):
        scaled = value / UNIT_SCALE[prefix]
        return f"{scaled:.6g}{prefix}{unit_tail}"
    if value == 0.0:
        return "0"
    abs_v = abs(value)
    if abs_v >= 1e9:
        return f"{value / 1e9:.6g}g"
    if abs_v >= 1e6:
        return f"{value / 1e6:.6g}meg"
    if abs_v >= 1e3:
        return f"{value / 1e3:.6g}k"
    if abs_v >= 1.0:
        return f"{value:.6g}"
    if abs_v >= 1e-3:
        return f"{value * 1e3:.6g}m"
    if abs_v >= 1e-6:
        return f"{value * 1e6:.6g}u"
    if abs_v >= 1e-9:
        return f"{value * 1e9:.6g}n"
    if abs_v >= 1e-12:
        return f"{value * 1e12:.6g}p"
    return f"{value * 1e15:.6g}f"


def load_param_stats(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return {}
    stats: dict[str, dict] = {}
    for name, cfg in payload.items():
        if not isinstance(cfg, dict):
            continue
        if "nominal" not in cfg:
            continue
        sigma_pct = cfg.get("sigma_pct", cfg.get("sigma", 0.0))
        try:
            nominal_value, unit_hint = parse_spice_value(str(cfg["nominal"]))
            sigma_value = float(sigma_pct)
        except (ValueError, TypeError):
            continue
        if sigma_value < 0:
            sigma_value = 0.0
        stats[name] = {
            "nominal": nominal_value,
            "unit_hint": unit_hint,
            "sigma_pct": sigma_value,
            "min_scale": float(cfg.get("min_scale", 0.1)),
            "max_scale": float(cfg.get("max_scale", 10.0)),
        }
    return stats


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


def passes_targets(metrics: dict[str, float], targets: dict[str, dict], aliases: dict[str, list[str]]) -> bool:
    if not targets:
        return True
    for name, rule in targets.items():
        if not isinstance(rule, dict):
            return False
        actual = resolve_metric(name, metrics, aliases)
        if actual is None:
            return False
        if "min" in rule and actual < float(rule["min"]):
            return False
        if "max" in rule and actual > float(rule["max"]):
            return False
    return True


def build_sample_netlist(base_text: str, overrides: dict[str, str]) -> str:
    lines = base_text.splitlines()
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip().lstrip("\ufeff")
        if stripped.lower() == ".end":
            continue
        body.append(raw.rstrip())
    for key, value in sorted(overrides.items(), key=lambda kv: kv[0].lower()):
        body.append(f".param {key}={value}")
    body.append(".end")
    return "\n".join(body) + "\n"


def run_netlist(executable: str, netlist_path: Path, log_path: Path, timeout_sec: int) -> tuple[bool, str]:
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


def summarize_metric_stats(rows: list[dict]) -> dict[str, dict]:
    all_values: dict[str, list[float]] = {}
    for row in rows:
        for name, value in row.get("metrics", {}).items():
            all_values.setdefault(name, []).append(float(value))
    summary: dict[str, dict] = {}
    for name, values in sorted(all_values.items(), key=lambda kv: kv[0]):
        if not values:
            continue
        if len(values) == 1:
            std_value = 0.0
        else:
            std_value = statistics.stdev(values)
        summary[name] = {
            "count": len(values),
            "mean": statistics.mean(values),
            "std": std_value,
            "min": min(values),
            "max": max(values),
        }
    return summary


def main() -> int:
    args = build_parser().parse_args()
    result = {
        "ok": False,
        "work_dir": "",
        "summary_path": "",
        "samples": args.samples,
        "yield_count": 0,
        "yield_pct": 0.0,
        "run_error_count": 0,
        "rows": [],
        "metric_statistics": {},
        "warnings": [],
    }

    netlist_path = Path(args.netlist_path).resolve()
    stats_path = Path(args.param_stats_path).resolve()
    work_dir = Path(args.work_dir).resolve()
    result["work_dir"] = str(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not netlist_path.exists():
        result["warnings"].append(f"netlist not found: {netlist_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if not stats_path.exists():
        result["warnings"].append(f"param stats not found: {stats_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    executable = resolve_executable(args.ngspice_bin)
    if not executable:
        result["warnings"].append("ngspice executable not found")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    base_text = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
    param_stats = load_param_stats(stats_path)
    if not param_stats:
        result["warnings"].append("no valid parameter definitions in param-stats file")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    targets, aliases = load_targets(args.spec_path)
    wanted = {m.strip() for m in args.metric if m.strip()}
    if not wanted and targets:
        wanted = set(targets.keys())

    rng = random.Random(args.seed)
    total = max(1, int(args.samples))

    for idx in range(total):
        sample_name = f"sample_{idx+1:03d}"
        sample_dir = work_dir / "samples" / sample_name
        sample_dir.mkdir(parents=True, exist_ok=True)

        multipliers: dict[str, float] = {}
        overrides: dict[str, str] = {}
        for param_name, cfg in param_stats.items():
            sigma = float(cfg["sigma_pct"]) / 100.0
            scale = 1.0 + rng.gauss(0.0, sigma)
            scale = min(max(scale, float(cfg["min_scale"])), float(cfg["max_scale"]))
            value = float(cfg["nominal"]) * scale
            if value <= 0:
                value = max(1e-15, abs(float(cfg["nominal"])) * 0.01)
            multipliers[param_name] = scale
            overrides[param_name] = format_spice_value(value, str(cfg.get("unit_hint", "")))

        sample_netlist = sample_dir / "design.cir"
        sample_log = sample_dir / "ngspice.log"
        sample_metrics_path = sample_dir / "metrics.json"
        sample_eval_path = sample_dir / "evaluation.json"
        sample_netlist.write_text(build_sample_netlist(base_text, overrides), encoding="utf-8")

        ok_run, stderr = run_netlist(executable, sample_netlist, sample_log, args.timeout_sec)
        row = {
            "sample": sample_name,
            "run_ok": ok_run,
            "stderr": stderr,
            "netlist_path": str(sample_netlist),
            "log_path": str(sample_log),
            "metrics_path": str(sample_metrics_path),
            "evaluation_path": str(sample_eval_path),
            "overrides": overrides,
            "scale_factors": multipliers,
            "metrics": {},
            "pass": False,
        }
        if not ok_run:
            result["run_error_count"] += 1
            result["rows"].append(row)
            continue

        parsed = parse_measurements(sample_log.read_text(encoding="utf-8-sig", errors="ignore"), wanted)
        metrics = {k: float(v) for k, v in parsed.get("metrics", {}).items()}
        row["metrics"] = metrics
        row["pass"] = passes_targets(metrics, targets, aliases)
        if row["pass"]:
            result["yield_count"] += 1

        sample_metrics_path.write_text(
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
        sample_eval_path.write_text(
            json.dumps({"pass": row["pass"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["rows"].append(row)

    result["yield_pct"] = (result["yield_count"] / total) * 100.0
    result["metric_statistics"] = summarize_metric_stats(result["rows"])
    result["ok"] = result["run_error_count"] == 0

    summary_path = Path(args.output_path).resolve() if args.output_path else work_dir / "monte_carlo_summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["summary_path"] = str(summary_path)
    print(json.dumps(result, ensure_ascii=False))

    if not result["ok"]:
        return 1
    if args.require_yield >= 0.0 and result["yield_pct"] < float(args.require_yield):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
