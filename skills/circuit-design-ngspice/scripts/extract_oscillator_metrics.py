#!/usr/bin/env python3
"""Extract oscillator startup and steady-state metrics from transient log tables."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


ROW_COMPLEX_RE = re.compile(
    r"^\s*(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*$"
)
ROW_REAL_RE = re.compile(r"^\s*(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract oscillator metrics from transient log")
    parser.add_argument("--log-path", required=True, help="ngspice log path containing transient table")
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    parser.add_argument(
        "--steady-tail-ratio",
        type=float,
        default=0.30,
        help="Fraction of final samples treated as steady-state window",
    )
    parser.add_argument(
        "--startup-threshold-vpp",
        type=float,
        default=0.2,
        help="Minimum steady-state Vpp to classify as startup success",
    )
    parser.add_argument(
        "--startup-ratio",
        type=float,
        default=0.9,
        help="Startup-time threshold as a ratio of steady amplitude envelope",
    )
    parser.add_argument("--allow-no-startup", action="store_true", help="Exit 0 even when startup fails")
    return parser


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


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    p = min(1.0, max(0.0, p))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return ordered[idx]


def moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1 or len(values) <= 2:
        return list(values)
    out: list[float] = []
    acc = 0.0
    q: list[float] = []
    for value in values:
        q.append(value)
        acc += value
        if len(q) > window:
            acc -= q.pop(0)
        out.append(acc / len(q))
    return out


def estimate_frequency(times: list[float], values: list[float], center: float) -> float | None:
    rising_crossings: list[float] = []
    for i in range(len(values) - 1):
        v1 = values[i] - center
        v2 = values[i + 1] - center
        if not (v1 < 0.0 and v2 >= 0.0):
            continue
        dt = times[i + 1] - times[i]
        if dt <= 0.0:
            continue
        frac = 0.0 if v2 == v1 else (0.0 - v1) / (v2 - v1)
        frac = max(0.0, min(1.0, frac))
        rising_crossings.append(times[i] + frac * dt)
    if len(rising_crossings) < 3:
        return None
    periods = [
        rising_crossings[i + 1] - rising_crossings[i]
        for i in range(len(rising_crossings) - 1)
        if rising_crossings[i + 1] > rising_crossings[i]
    ]
    if not periods:
        return None
    period = sum(periods) / len(periods)
    if period <= 0.0:
        return None
    return 1.0 / period


def main() -> int:
    args = build_parser().parse_args()

    result = {
        "metrics": {},
        "analysis_context": {},
        "warnings": [],
        "startup_pass": False,
    }

    log_path = Path(args.log_path).resolve()
    if not log_path.exists():
        result["warnings"].append(f"log not found: {log_path}")
        payload = json.dumps(result, ensure_ascii=False)
        if args.output_path:
            Path(args.output_path).resolve().write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    lines = log_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    rows = find_tran_rows(lines)
    if len(rows) < 20:
        result["warnings"].append("transient table not found or too short")
        payload = json.dumps(result, ensure_ascii=False)
        if args.output_path:
            Path(args.output_path).resolve().write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    times = [r[0] for r in rows]
    values = [r[1] for r in rows]
    n = len(values)

    tail_n = max(20, int(n * max(0.05, min(0.9, args.steady_tail_ratio))))
    steady_values = values[-tail_n:]
    steady_times = times[-tail_n:]
    steady_mean = sum(steady_values) / len(steady_values)
    steady_max = max(steady_values)
    steady_min = min(steady_values)
    vpp = steady_max - steady_min

    result["metrics"]["vpp_steady_v"] = vpp
    result["metrics"]["vpk_steady_v"] = 0.5 * vpp
    result["metrics"]["dc_offset_v"] = steady_mean
    result["analysis_context"]["vpp_steady_v"] = "tran_table"
    result["analysis_context"]["vpk_steady_v"] = "tran_table"
    result["analysis_context"]["dc_offset_v"] = "tran_table"

    freq = estimate_frequency(steady_times, steady_values, steady_mean)
    if freq is not None and math.isfinite(freq):
        result["metrics"]["f_osc_hz"] = freq
        result["analysis_context"]["f_osc_hz"] = "tran_table"
    else:
        result["warnings"].append("unable to estimate oscillation frequency")

    envelope = moving_average([abs(v - steady_mean) for v in values], max(5, n // 200))
    steady_amp = percentile(envelope[-tail_n:], 0.9)
    startup_threshold = max(1e-6, steady_amp * max(0.1, min(1.5, args.startup_ratio)))
    startup_idx = None
    for i, amp in enumerate(envelope):
        if amp >= startup_threshold:
            startup_idx = i
            break
    if startup_idx is not None:
        result["metrics"]["startup_time_us"] = max(0.0, times[startup_idx] * 1e6)
        result["analysis_context"]["startup_time_us"] = "derived"
    else:
        result["warnings"].append("startup threshold was never crossed")

    startup_flag = 1.0 if (vpp >= args.startup_threshold_vpp and "f_osc_hz" in result["metrics"]) else 0.0
    result["metrics"]["startup_flag"] = startup_flag
    result["analysis_context"]["startup_flag"] = "derived"
    result["startup_pass"] = startup_flag >= 0.5

    payload = json.dumps(result, ensure_ascii=False)
    if args.output_path:
        out_path = Path(args.output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if result["startup_pass"] or args.allow_no_startup:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
