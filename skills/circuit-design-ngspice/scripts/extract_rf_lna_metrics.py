#!/usr/bin/env python3
"""Extract RF/LNA metrics from ngspice logs and optional linearity sweep CSV.

Target metrics:
- s11_db_max
- s22_db_max
- k_min
- nf_db_max
- p1db_dbm_min
- ip3_dbm_min
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

from parse_results import parse_measurements


NOISE_FIGURE_RE = re.compile(r"noise\s+figure\s*[:=]\s*([-+0-9.eE]+)", re.IGNORECASE)

ALIASES = {
    "s11_db_max": ["s11_db_max", "s11_db", "s11_db_at_f0"],
    "s22_db_max": ["s22_db_max", "s22_db", "s22_db_at_f0"],
    "k_min": ["k_min", "k_factor", "k", "rollet_k"],
    "nf_db_max": ["nf_db_max", "nf_db", "noise_figure_db"],
    "p1db_dbm_min": ["p1db_dbm_min", "p1db_dbm", "p1db_out_dbm", "p1db_in_dbm"],
    "ip3_dbm_min": ["ip3_dbm_min", "ip3_dbm", "oip3_dbm", "iip3_dbm"],
    "s21_db": ["s21_db", "gain_db", "gain_fwd_db"],
    "s12_db": ["s12_db", "gain_rev_db"],
    "zin_re": ["zin_re", "zin_real_ohm"],
    "zin_im": ["zin_im", "zin_imag_ohm"],
    "zout_re": ["zout_re", "zout_real_ohm"],
    "zout_im": ["zout_im", "zout_imag_ohm"],
}

REQUIRED = ["s11_db_max", "s22_db_max", "k_min", "nf_db_max", "p1db_dbm_min", "ip3_dbm_min"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract RF/LNA metrics")
    parser.add_argument("--forward-log-path", required=True, help="Forward two-port AC/ngspice log")
    parser.add_argument("--reverse-log-path", default="", help="Reverse two-port AC/ngspice log")
    parser.add_argument("--noise-log-path", default="", help="Noise analysis log")
    parser.add_argument("--linearity-log-path", default="", help="Linearity (.meas) log")
    parser.add_argument("--linearity-csv-path", default="", help="Optional sweep CSV for P1dB/IP3 extraction")
    parser.add_argument("--z0-ohm", type=float, default=50.0, help="Reference impedance")
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    parser.add_argument("--allow-partial", action="store_true", help="Exit 0 even when some targets missing")
    return parser


def read_text(path_value: str, warnings: list[str], label: str) -> str:
    if not path_value.strip():
        return ""
    path = Path(path_value).resolve()
    if not path.exists():
        warnings.append(f"{label} not found: {path}")
        return ""
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def lookup_metric(pool: dict[str, float], key: str) -> tuple[float | None, str]:
    for alias in ALIASES.get(key, [key]):
        if alias in pool:
            return float(pool[alias]), alias
    return None, ""


def reflection_db(z_re: float, z_im: float, z0: float) -> float | None:
    z = complex(z_re, z_im)
    den = z + complex(z0, 0.0)
    if abs(den) < 1e-20:
        return None
    gamma = (z - complex(z0, 0.0)) / den
    mag = abs(gamma)
    if mag <= 0.0:
        return -300.0
    return 20.0 * math.log10(max(mag, 1e-15))


def k_factor_from_sparams(s11_db: float, s22_db: float, s21_db: float, s12_db: float) -> float | None:
    s11 = 10.0 ** (s11_db / 20.0)
    s22 = 10.0 ** (s22_db / 20.0)
    s21 = 10.0 ** (s21_db / 20.0)
    s12 = 10.0 ** (s12_db / 20.0)
    den = 2.0 * abs(s21 * s12)
    if den <= 1e-20:
        return None
    delta = abs(s11 * s22 - s12 * s21)
    return (1.0 - s11 * s11 - s22 * s22 + delta * delta) / den


def parse_measure_pool(text: str) -> dict[str, float]:
    if not text:
        return {}
    parsed = parse_measurements(text, set())
    return {k: float(v) for k, v in parsed.get("metrics", {}).items()}


def parse_noise_figure(text: str) -> float | None:
    if not text:
        return None
    match = NOISE_FIGURE_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def try_linearity_csv(path_value: str, warnings: list[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if not path_value.strip():
        return metrics
    csv_path = Path(path_value).resolve()
    if not csv_path.exists():
        warnings.append(f"linearity csv not found: {csv_path}")
        return metrics

    rows: list[dict[str, float]] = []
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                parsed: dict[str, float] = {}
                for k, v in row.items():
                    if k is None or v is None:
                        continue
                    key = k.strip().lower()
                    value = v.strip()
                    if not value:
                        continue
                    try:
                        parsed[key] = float(value)
                    except ValueError:
                        continue
                if parsed:
                    rows.append(parsed)
    except OSError as exc:
        warnings.append(f"failed to read linearity csv: {exc}")
        return metrics

    if not rows:
        warnings.append("linearity csv has no numeric rows")
        return metrics

    with_pin_pout = [r for r in rows if "pin_dbm" in r and "pout_dbm" in r]
    if len(with_pin_pout) >= 4:
        with_pin_pout.sort(key=lambda r: r["pin_dbm"])
        gains = [r["pout_dbm"] - r["pin_dbm"] for r in with_pin_pout]
        g_small = sum(gains[: min(3, len(gains))]) / min(3, len(gains))
        p1db_out = None
        for r in with_pin_pout:
            gain = r["pout_dbm"] - r["pin_dbm"]
            if gain <= g_small - 1.0:
                p1db_out = r["pout_dbm"]
                break
        if p1db_out is not None:
            metrics["p1db_dbm_min"] = p1db_out

    with_im3 = [r for r in rows if "fund_dbm" in r and "im3_dbm" in r]
    if with_im3:
        oip3_values = []
        for r in with_im3:
            delta = r["fund_dbm"] - r["im3_dbm"]
            if delta > 0:
                oip3_values.append(r["fund_dbm"] + delta / 2.0)
        if oip3_values:
            oip3_values.sort()
            metrics["ip3_dbm_min"] = oip3_values[len(oip3_values) // 2]

    return metrics


def main() -> int:
    args = build_parser().parse_args()

    result = {
        "metrics": {},
        "analysis_context": {},
        "sources": {},
        "warnings": [],
        "missing_metrics": [],
    }

    forward_text = read_text(args.forward_log_path, result["warnings"], "forward log")
    reverse_text = read_text(args.reverse_log_path, result["warnings"], "reverse log")
    noise_text = read_text(args.noise_log_path, result["warnings"], "noise log")
    linearity_text = read_text(args.linearity_log_path, result["warnings"], "linearity log")

    forward_pool = parse_measure_pool(forward_text)
    reverse_pool = parse_measure_pool(reverse_text)
    noise_pool = parse_measure_pool(noise_text)
    linearity_pool = parse_measure_pool(linearity_text)
    csv_metrics = try_linearity_csv(args.linearity_csv_path, result["warnings"])

    merged_pool = {}
    merged_pool.update(forward_pool)
    merged_pool.update(reverse_pool)
    merged_pool.update(noise_pool)
    merged_pool.update(linearity_pool)
    merged_pool.update(csv_metrics)

    for name in ("s11_db_max", "s22_db_max", "k_min", "nf_db_max", "p1db_dbm_min", "ip3_dbm_min"):
        value, source = lookup_metric(merged_pool, name)
        if value is not None:
            result["metrics"][name] = value
            result["sources"][name] = source

    if "s11_db_max" not in result["metrics"]:
        zin_re, _ = lookup_metric(merged_pool, "zin_re")
        zin_im, _ = lookup_metric(merged_pool, "zin_im")
        if zin_re is not None and zin_im is not None:
            value = reflection_db(zin_re, zin_im, args.z0_ohm)
            if value is not None:
                result["metrics"]["s11_db_max"] = value
                result["sources"]["s11_db_max"] = "derived_from_zin"

    if "s22_db_max" not in result["metrics"]:
        zout_re, _ = lookup_metric(merged_pool, "zout_re")
        zout_im, _ = lookup_metric(merged_pool, "zout_im")
        if zout_re is not None and zout_im is not None:
            value = reflection_db(zout_re, zout_im, args.z0_ohm)
            if value is not None:
                result["metrics"]["s22_db_max"] = value
                result["sources"]["s22_db_max"] = "derived_from_zout"

    if "k_min" not in result["metrics"]:
        s11_db = result["metrics"].get("s11_db_max")
        s22_db = result["metrics"].get("s22_db_max")
        s21_db, s21_src = lookup_metric(merged_pool, "s21_db")
        s12_db, s12_src = lookup_metric(merged_pool, "s12_db")
        if s11_db is not None and s22_db is not None and s21_db is not None and s12_db is not None:
            k_val = k_factor_from_sparams(s11_db, s22_db, s21_db, s12_db)
            if k_val is not None:
                result["metrics"]["k_min"] = k_val
                result["sources"]["k_min"] = f"derived_from_{s21_src}_{s12_src}"

    if "nf_db_max" not in result["metrics"]:
        nf_from_text = parse_noise_figure(noise_text)
        if nf_from_text is not None:
            result["metrics"]["nf_db_max"] = nf_from_text
            result["sources"]["nf_db_max"] = "noise_table"

    for name in REQUIRED:
        if name not in result["metrics"]:
            result["missing_metrics"].append(name)

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
