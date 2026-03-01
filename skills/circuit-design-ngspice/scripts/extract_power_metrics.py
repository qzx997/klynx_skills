#!/usr/bin/env python3
"""Extract Buck/LDO power metrics from ngspice logs."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from parse_results import parse_measurements


ROW_COMPLEX_RE = re.compile(
    r"^\s*(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*$"
)
ROW_REAL_RE = re.compile(r"^\s*(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$")

ALIASES = {
    "efficiency_pct": ["efficiency_pct", "eff_pct", "eta_pct"],
    "pin_w": ["pin_w", "pin_avg_w", "pin_dc_w"],
    "pout_w": ["pout_w", "pout_avg_w"],
    "vout_max": ["vout_max", "vout_hi"],
    "vout_min": ["vout_min", "vout_lo"],
    "line_vout_low": ["line_vout_low", "vout_line_low"],
    "line_vout_high": ["line_vout_high", "vout_line_high"],
    "line_vin_low": ["line_vin_low", "vin_line_low"],
    "line_vin_high": ["line_vin_high", "vin_line_high"],
    "load_vout_light": ["load_vout_light", "vout_load_light"],
    "load_vout_heavy": ["load_vout_heavy", "vout_load_heavy"],
    "load_iout_light": ["load_iout_light", "iout_load_light"],
    "load_iout_heavy": ["load_iout_heavy", "iout_load_heavy"],
    "recovery_time_us": ["recovery_time_us", "tran_recovery_us"],
}

REQUIRED = ["efficiency_pct", "v_ripple_mv", "line_reg_mv_per_v", "load_reg_mv_per_a", "recovery_time_us"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract power metrics")
    parser.add_argument("--steady-log-path", required=True, help="Steady-state log for efficiency/ripple")
    parser.add_argument("--line-log-path", default="", help="Line-regulation log")
    parser.add_argument("--load-log-path", default="", help="Load-regulation log")
    parser.add_argument("--transient-log-path", default="", help="Transient recovery log")
    parser.add_argument("--settling-tol-pct", type=float, default=2.0, help="Recovery settling tolerance (%%)")
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    parser.add_argument("--allow-partial", action="store_true", help="Exit 0 even with missing targets")
    return parser


def read_text(path_value: str, warnings: list[str], label: str) -> str:
    if not path_value.strip():
        return ""
    path = Path(path_value).resolve()
    if not path.exists():
        warnings.append(f"{label} not found: {path}")
        return ""
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def parse_measure_pool(text: str) -> dict[str, float]:
    if not text:
        return {}
    parsed = parse_measurements(text, set())
    return {k: float(v) for k, v in parsed.get("metrics", {}).items()}


def lookup(pool: dict[str, float], key: str) -> tuple[float | None, str]:
    for alias in ALIASES.get(key, [key]):
        if alias in pool:
            return float(pool[alias]), alias
    return None, ""


def parse_table_rows(lines: list[str], start_idx: int) -> tuple[list[tuple[float, float]], int]:
    rows: list[tuple[float, float]] = []
    i = start_idx

    def next_nonempty(idx: int) -> str:
        j = idx
        while j < len(lines):
            candidate = lines[j].replace("\x0c", "").strip()
            if candidate:
                return candidate
            j += 1
        return ""

    while i < len(lines):
        line = lines[i].replace("\x0c", "").strip()
        if not line:
            if rows:
                upcoming = next_nonempty(i + 1).lower()
                if upcoming.startswith("index") or ROW_COMPLEX_RE.match(upcoming) or ROW_REAL_RE.match(upcoming):
                    i += 1
                    continue
                break
            i += 1
            continue
        if line.lower().startswith("index") or set(line) == {"-"}:
            i += 1
            continue
        m_real = ROW_REAL_RE.match(line)
        if m_real:
            rows.append((float(m_real.group(2)), float(m_real.group(3))))
            i += 1
            continue
        m_complex = ROW_COMPLEX_RE.match(line)
        if m_complex:
            rows.append((float(m_complex.group(2)), float(m_complex.group(3))))
            i += 1
            continue
        if rows:
            break
        i += 1
    return rows, i


def find_tran_rows(lines: list[str]) -> list[tuple[float, float]]:
    for idx, line in enumerate(lines):
        if "transient analysis" not in line.lower():
            continue
        rows, _ = parse_table_rows(lines, idx + 1)
        if rows:
            return rows
    return []


def derive_recovery_time_us(tran_text: str, settling_tol_pct: float, warnings: list[str]) -> float | None:
    if not tran_text:
        return None
    rows = find_tran_rows(tran_text.splitlines())
    if len(rows) < 30:
        warnings.append("transient table not found or too short for recovery-time extraction")
        return None

    times = [r[0] for r in rows]
    values = [r[1] for r in rows]
    n = len(values)

    derivs: list[float] = []
    for i in range(n - 1):
        dt = times[i + 1] - times[i]
        if dt <= 0.0:
            derivs.append(0.0)
            continue
        derivs.append((values[i + 1] - values[i]) / dt)
    if not derivs:
        return None

    dist_idx = max(range(len(derivs)), key=lambda i: abs(derivs[i]))
    t_dist = times[dist_idx]

    tail_n = max(10, int(0.1 * n))
    final = sum(values[-tail_n:]) / tail_n
    tol = max(1e-6, abs(final) * max(settling_tol_pct, 0.01) / 100.0)

    settle_idx = None
    for i in range(dist_idx + 1, n):
        if all(abs(v - final) <= tol for v in values[i:]):
            settle_idx = i
            break
    if settle_idx is None:
        warnings.append("transient waveform does not settle within tolerance")
        return None
    return max(0.0, (times[settle_idx] - t_dist) * 1e6)


def main() -> int:
    args = build_parser().parse_args()

    result = {
        "metrics": {},
        "analysis_context": {},
        "sources": {},
        "warnings": [],
        "missing_metrics": [],
    }

    steady_text = read_text(args.steady_log_path, result["warnings"], "steady log")
    line_text = read_text(args.line_log_path, result["warnings"], "line log")
    load_text = read_text(args.load_log_path, result["warnings"], "load log")
    transient_text = read_text(args.transient_log_path, result["warnings"], "transient log")

    steady_pool = parse_measure_pool(steady_text)
    line_pool = parse_measure_pool(line_text)
    load_pool = parse_measure_pool(load_text)
    transient_pool = parse_measure_pool(transient_text)

    merged_pool = {}
    merged_pool.update(steady_pool)
    merged_pool.update(line_pool)
    merged_pool.update(load_pool)
    merged_pool.update(transient_pool)

    efficiency, source = lookup(merged_pool, "efficiency_pct")
    if efficiency is None:
        pin, pin_src = lookup(merged_pool, "pin_w")
        pout, pout_src = lookup(merged_pool, "pout_w")
        if pin is not None and pout is not None and pin > 1e-12:
            efficiency = (pout / pin) * 100.0
            source = f"derived_from_{pout_src}_{pin_src}"
    if efficiency is not None:
        result["metrics"]["efficiency_pct"] = efficiency
        result["sources"]["efficiency_pct"] = source or "unknown"
        result["analysis_context"]["efficiency_pct"] = "meas_or_derived"

    vout_max, vmax_src = lookup(merged_pool, "vout_max")
    vout_min, vmin_src = lookup(merged_pool, "vout_min")
    if vout_max is not None and vout_min is not None:
        result["metrics"]["v_ripple_mv"] = max(0.0, (vout_max - vout_min) * 1000.0)
        result["sources"]["v_ripple_mv"] = f"derived_from_{vmax_src}_{vmin_src}"
        result["analysis_context"]["v_ripple_mv"] = "derived"

    line_vout_low, line_vout_low_src = lookup(merged_pool, "line_vout_low")
    line_vout_high, line_vout_high_src = lookup(merged_pool, "line_vout_high")
    line_vin_low, line_vin_low_src = lookup(merged_pool, "line_vin_low")
    line_vin_high, line_vin_high_src = lookup(merged_pool, "line_vin_high")
    if (
        line_vout_low is not None
        and line_vout_high is not None
        and line_vin_low is not None
        and line_vin_high is not None
    ):
        dvin = line_vin_high - line_vin_low
        if abs(dvin) > 1e-12:
            line_reg = abs(line_vout_high - line_vout_low) / abs(dvin) * 1000.0
            result["metrics"]["line_reg_mv_per_v"] = line_reg
            result["sources"]["line_reg_mv_per_v"] = (
                f"derived_from_{line_vout_low_src}_{line_vout_high_src}_{line_vin_low_src}_{line_vin_high_src}"
            )
            result["analysis_context"]["line_reg_mv_per_v"] = "derived"

    load_vout_light, load_vout_light_src = lookup(merged_pool, "load_vout_light")
    load_vout_heavy, load_vout_heavy_src = lookup(merged_pool, "load_vout_heavy")
    load_iout_light, load_iout_light_src = lookup(merged_pool, "load_iout_light")
    load_iout_heavy, load_iout_heavy_src = lookup(merged_pool, "load_iout_heavy")
    if (
        load_vout_light is not None
        and load_vout_heavy is not None
        and load_iout_light is not None
        and load_iout_heavy is not None
    ):
        diout = load_iout_heavy - load_iout_light
        if abs(diout) > 1e-12:
            load_reg = abs(load_vout_light - load_vout_heavy) / abs(diout) * 1000.0
            result["metrics"]["load_reg_mv_per_a"] = load_reg
            result["sources"]["load_reg_mv_per_a"] = (
                f"derived_from_{load_vout_light_src}_{load_vout_heavy_src}_{load_iout_light_src}_{load_iout_heavy_src}"
            )
            result["analysis_context"]["load_reg_mv_per_a"] = "derived"

    recovery, recovery_src = lookup(merged_pool, "recovery_time_us")
    if recovery is None:
        recovery = derive_recovery_time_us(transient_text, args.settling_tol_pct, result["warnings"])
        recovery_src = "derived_from_transient_table" if recovery is not None else ""
    if recovery is not None and math.isfinite(recovery):
        result["metrics"]["recovery_time_us"] = recovery
        result["sources"]["recovery_time_us"] = recovery_src or "unknown"
        result["analysis_context"]["recovery_time_us"] = "meas_or_derived"

    for metric_name in REQUIRED:
        if metric_name not in result["metrics"]:
            result["missing_metrics"].append(metric_name)

    payload = json.dumps(result, ensure_ascii=False)
    if args.output_path:
        out_path = Path(args.output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if result["missing_metrics"] and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
