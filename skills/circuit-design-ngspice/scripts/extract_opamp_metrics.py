#!/usr/bin/env python3
"""Extract op-amp stability and slew metrics from ngspice log tables.

Metrics produced (when data is available):
- gain_cl_dc_db
- ugf_hz
- pm_deg
- stability_flag (1 for pm_deg >= 45, else 0)
- slew_pos_vus
- slew_neg_vus
- settling_time_us
- overshoot_pct
"""

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
    parser = argparse.ArgumentParser(description="Extract op-amp metrics from ngspice log")
    parser.add_argument(
        "--log-path",
        default="",
        help="Single ngspice log path containing both AC and TRAN tables (legacy mode)",
    )
    parser.add_argument("--ac-log-path", default="", help="AC ngspice log path")
    parser.add_argument("--tran-log-path", default="", help="Transient ngspice log path")
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    parser.add_argument("--pm-pass-threshold", type=float, default=45.0, help="Stability flag PM threshold")
    parser.add_argument(
        "--ac-loop-model",
        default="closed_loop",
        choices=["closed_loop", "open_loop"],
        help="Interpret AC transfer as closed-loop H(s) or open-loop A(s).",
    )
    parser.add_argument(
        "--settling-tol-pct",
        type=float,
        default=2.0,
        help="Settling tolerance band as percentage of step amplitude",
    )
    parser.add_argument(
        "--require-settling",
        action="store_true",
        help="Require settling_time_us for success exit code",
    )
    return parser



def parse_table_rows(lines: list[str], start_idx: int) -> tuple[list[tuple[float, float, float | None]], int]:
    rows: list[tuple[float, float, float | None]] = []
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
        m_complex = ROW_COMPLEX_RE.match(line)
        if m_complex:
            x = float(m_complex.group(2))
            y_real = float(m_complex.group(3))
            y_imag = float(m_complex.group(4))
            rows.append((x, y_real, y_imag))
            i += 1
            continue
        m_real = ROW_REAL_RE.match(line)
        if m_real:
            x = float(m_real.group(2))
            y_real = float(m_real.group(3))
            rows.append((x, y_real, None))
            i += 1
            continue
        if rows:
            break
        i += 1
    return rows, i



def find_analysis_table(lines: list[str], analysis_key: str) -> list[tuple[float, float, float | None]]:
    key = analysis_key.lower()
    for idx, line in enumerate(lines):
        if key not in line.lower():
            continue
        rows, _ = parse_table_rows(lines, idx + 1)
        if rows:
            return rows
    return []



def unwrap_phase_deg(phases: list[float]) -> list[float]:
    if not phases:
        return []
    out = [phases[0]]
    for phase in phases[1:]:
        value = phase
        prev = out[-1]
        while value - prev > 180.0:
            value -= 360.0
        while value - prev < -180.0:
            value += 360.0
        out.append(value)
    return out



def phase_margin_from_loop(
    freqs: list[float],
    loop_mag_db: list[float],
    loop_phase_deg_unwrapped: list[float],
) -> tuple[float | None, float | None]:
    if len(freqs) < 2:
        return None, None

    best_cross = None
    for i in range(len(freqs) - 1):
        y1 = loop_mag_db[i]
        y2 = loop_mag_db[i + 1]
        if not (math.isfinite(y1) and math.isfinite(y2)):
            continue

        crosses = (y1 == 0.0) or (y2 == 0.0) or ((y1 > 0.0 and y2 < 0.0) or (y1 < 0.0 and y2 > 0.0))
        if not crosses:
            continue

        if y1 == y2:
            t = 0.0
        else:
            t = (0.0 - y1) / (y2 - y1)
            t = max(0.0, min(1.0, t))

        x1 = math.log10(max(freqs[i], 1e-30))
        x2 = math.log10(max(freqs[i + 1], 1e-30))
        ugf = 10.0 ** (x1 + t * (x2 - x1))
        phase_at_ugf = loop_phase_deg_unwrapped[i] + t * (
            loop_phase_deg_unwrapped[i + 1] - loop_phase_deg_unwrapped[i]
        )

        preferred = 0 if (y1 >= 0.0 and y2 <= 0.0) else 1
        cand = (preferred, i, ugf, phase_at_ugf)
        if best_cross is None or cand < best_cross:
            best_cross = cand

    if best_cross is None:
        # Fallback: nearest point to 0 dB.
        idx = min(range(len(freqs)), key=lambda k: abs(loop_mag_db[k]))
        ugf = freqs[idx]
        phase_at_ugf = loop_phase_deg_unwrapped[idx]
    else:
        _, _, ugf, phase_at_ugf = best_cross

    phase_wrapped = phase_at_ugf
    while phase_wrapped > 0.0:
        phase_wrapped -= 360.0
    while phase_wrapped <= -360.0:
        phase_wrapped += 360.0
    pm = 180.0 + phase_wrapped
    return ugf, pm



def compute_ac_metrics(
    rows: list[tuple[float, float, float | None]],
    warnings: list[str],
    *,
    loop_model: str,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if len(rows) < 2:
        warnings.append("AC table not found or too short for stability metrics")
        return metrics

    freqs: list[float] = []
    h_vals: list[complex] = []
    for f, real, imag in rows:
        cval = complex(real, 0.0 if imag is None else imag)
        freqs.append(f)
        h_vals.append(cval)

    h0 = h_vals[0]
    h0_mag = abs(h0)
    if loop_model == "open_loop":
        metrics["gain_ol_dc_db"] = 20.0 * math.log10(max(h0_mag, 1e-30))
    else:
        metrics["gain_cl_dc_db"] = 20.0 * math.log10(max(h0_mag, 1e-30))

    loop_freqs: list[float] = []
    loop_mag_db: list[float] = []
    loop_phase_deg: list[float] = []
    for freq, h in zip(freqs, h_vals):
        if loop_model == "open_loop":
            loop_gain = h
        else:
            denom = 1.0 - h
            if abs(denom) < 1e-14:
                continue
            loop_gain = h / denom
        mag = abs(loop_gain)
        if mag <= 0.0:
            continue
        loop_freqs.append(freq)
        loop_mag_db.append(20.0 * math.log10(mag))
        loop_phase_deg.append(math.degrees(math.atan2(loop_gain.imag, loop_gain.real)))

    if len(loop_freqs) < 2:
        warnings.append("insufficient loop-gain points for phase margin extraction")
        return metrics

    loop_phase_unwrapped = unwrap_phase_deg(loop_phase_deg)
    ugf_hz, pm_deg = phase_margin_from_loop(loop_freqs, loop_mag_db, loop_phase_unwrapped)
    if ugf_hz is not None:
        metrics["ugf_hz"] = ugf_hz
    if pm_deg is not None:
        metrics["pm_deg"] = pm_deg
    return metrics



def compute_tran_metrics(rows: list[tuple[float, float, float | None]], warnings: list[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if len(rows) < 3:
        warnings.append("Transient table not found or too short for slew metrics")
        return metrics

    times = [r[0] for r in rows]
    values = [r[1] for r in rows]

    derivs: list[float] = []
    for i in range(len(times) - 1):
        dt = times[i + 1] - times[i]
        if dt <= 0.0:
            continue
        dv = values[i + 1] - values[i]
        derivs.append(dv / dt)

    if not derivs:
        warnings.append("unable to compute derivatives for slew rate")
        return metrics

    max_pos = max(derivs)
    min_neg = min(derivs)
    metrics["slew_pos_vus"] = max_pos / 1e6
    metrics["slew_neg_vus"] = abs(min_neg) / 1e6

    # Simple overshoot estimate on first rising edge.
    vmin = min(values)
    vmax = max(values)
    mid = 0.5 * (vmin + vmax)
    idx_cross = None
    for i in range(len(values) - 1):
        if values[i] < mid and values[i + 1] >= mid:
            idx_cross = i + 1
            break
    if idx_cross is not None:
        pre = values[max(0, idx_cross - 30) : idx_cross]
        baseline = sum(pre) / len(pre) if pre else values[0]
        tail_len = max(20, int(0.15 * (len(values) - idx_cross)))
        tail_end = min(len(values), idx_cross + tail_len)
        segment = values[idx_cross:tail_end]
        if segment:
            peak = max(segment)
            steady = sum(segment[max(0, len(segment) - 10) :]) / min(10, len(segment))
            denom = abs(steady - baseline)
            if denom > 1e-12:
                metrics["overshoot_pct"] = max(0.0, (peak - steady) / denom * 100.0)

    return metrics


def compute_settling_metrics(
    rows: list[tuple[float, float, float | None]],
    warnings: list[str],
    *,
    settling_tol_pct: float,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if len(rows) < 20:
        warnings.append("transient table too short for settling-time extraction")
        return metrics

    times = [r[0] for r in rows]
    values = [r[1] for r in rows]
    n = len(values)

    pre_n = max(5, int(0.08 * n))
    post_n = max(8, int(0.12 * n))
    initial = sum(values[:pre_n]) / pre_n
    final = sum(values[-post_n:]) / post_n
    step = final - initial
    step_abs = abs(step)
    if step_abs < 1e-9:
        warnings.append("step amplitude too small for settling-time extraction")
        return metrics

    tol = max(1e-6, step_abs * max(settling_tol_pct, 0.01) / 100.0)
    metrics["step_initial_v"] = initial
    metrics["step_final_v"] = final
    metrics["settling_band_v"] = tol

    if step >= 0.0:
        threshold = initial + 0.1 * step
        start_idx = next((i for i, v in enumerate(values) if v >= threshold), 0)
    else:
        threshold = initial + 0.9 * step
        start_idx = next((i for i, v in enumerate(values) if v <= threshold), 0)

    settle_idx = None
    for i in range(start_idx, n):
        if all(abs(v - final) <= tol for v in values[i:]):
            settle_idx = i
            break
    if settle_idx is None:
        warnings.append("waveform does not settle within tolerance; using simulation-end fallback")
        metrics["settling_time_us"] = max(0.0, (times[-1] - times[start_idx]) * 1e6)
        return metrics

    metrics["settling_time_us"] = max(0.0, (times[settle_idx] - times[start_idx]) * 1e6)
    return metrics


def main() -> int:
    args = build_parser().parse_args()

    result = {
        "metrics": {},
        "failed_metrics": {},
        "raw_measure_lines": [],
        "analysis_context": {},
        "warnings": [],
    }

    shared_log = Path(args.log_path).resolve() if args.log_path else None
    ac_log = Path(args.ac_log_path).resolve() if args.ac_log_path else None
    tran_log = Path(args.tran_log_path).resolve() if args.tran_log_path else None

    if shared_log is not None:
        if ac_log is None:
            ac_log = shared_log
        if tran_log is None:
            tran_log = shared_log

    if ac_log is None and tran_log is None:
        result["warnings"].append("no log path provided; use --log-path or --ac-log-path/--tran-log-path")
        payload = json.dumps(result, ensure_ascii=False)
        if args.output_path:
            Path(args.output_path).resolve().write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    ac_lines: list[str] = []
    tran_lines: list[str] = []

    if ac_log is not None:
        if ac_log.exists():
            ac_lines = ac_log.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        else:
            result["warnings"].append(f"AC log not found: {ac_log}")
    if tran_log is not None:
        if tran_log.exists():
            tran_lines = tran_log.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        else:
            result["warnings"].append(f"TRAN log not found: {tran_log}")

    if not ac_lines and not tran_lines:
        payload = json.dumps(result, ensure_ascii=False)
        if args.output_path:
            Path(args.output_path).resolve().write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 1

    ac_rows = find_analysis_table(ac_lines if ac_lines else tran_lines, "ac analysis")
    tran_rows = find_analysis_table(tran_lines if tran_lines else ac_lines, "transient analysis")

    ac_metrics = compute_ac_metrics(ac_rows, result["warnings"], loop_model=args.ac_loop_model)
    tran_metrics = compute_tran_metrics(tran_rows, result["warnings"])
    settling_metrics = compute_settling_metrics(
        tran_rows,
        result["warnings"],
        settling_tol_pct=args.settling_tol_pct,
    )

    result["metrics"].update(ac_metrics)
    result["metrics"].update(tran_metrics)
    result["metrics"].update(settling_metrics)

    if "pm_deg" in result["metrics"]:
        result["metrics"]["stability_flag"] = 1.0 if result["metrics"]["pm_deg"] >= args.pm_pass_threshold else 0.0

    for name in ac_metrics:
        result["analysis_context"][name] = "ac_table"
    for name in tran_metrics:
        result["analysis_context"][name] = "tran_table"
    for name in settling_metrics:
        result["analysis_context"][name] = "tran_table"
    if "stability_flag" in result["metrics"]:
        result["analysis_context"]["stability_flag"] = "derived"

    payload = json.dumps(result, ensure_ascii=False)
    if args.output_path:
        out_path = Path(args.output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)

    required = ["pm_deg", "ugf_hz", "slew_pos_vus", "slew_neg_vus"]
    if args.require_settling:
        required.append("settling_time_us")
    return 0 if all(k in result["metrics"] for k in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
