#!/usr/bin/env python3
"""Apply predefined metric target packs to a spec file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


METRIC_PACKS = {
    "lna_basic": {
        "domain": "rf",
        "description": "Basic RF-LNA targets for matching, stability, noise, and linearity",
        "targets": {
            "s11_db_max": {"max": -10.0},
            "s22_db_max": {"max": -10.0},
            "k_min": {"min": 1.0},
            "nf_db_max": {"max": 3.0},
            "p1db_dbm_min": {"min": 20.0},
            "ip3_dbm_min": {"min": 30.0},
        },
        "measurement_snippet": """* RF metric placeholders (topology/testbench dependent)
* .meas ac s11_db_max FIND <s11_db_expr> AT=<freq>
* .meas ac s22_db_max FIND <s22_db_expr> AT=<freq>
* .meas ac k_min FIND <k_factor_expr> AT=<freq>
* .meas ac nf_db_max FIND <nf_db_expr> AT=<freq>
* .meas ac p1db_dbm_min FIND <p1db_expr> AT=<freq>
* .meas ac ip3_dbm_min FIND <ip3_expr> AT=<freq>
""",
    },
    "bpf_basic_50ohm": {
        "domain": "filter",
        "description": "50-ohm bandpass filter targets for insertion loss, return loss, bandwidth, and stopband rejection",
        "targets": {
            "il_db": {"max": 3.0},
            "rl_db": {"min": 10.0},
            "bw_hz": {"min": 2.0e7},
            "ripple_db": {"max": 1.5},
            "attn_stop_db": {"min": 30.0},
        },
        "measurement_snippet": """* 50-ohm BPF metric placeholders
* il_db: insertion loss in passband (smaller is better)
* rl_db: return loss in passband (larger is better)
* bw_hz: -3 dB bandwidth
* ripple_db: passband ripple
* attn_stop_db: stopband attenuation
* .meas ac il_db FIND <il_db_expr> AT=<f_pass_center>
* .meas ac rl_db FIND <rl_db_expr> AT=<f_pass_center>
* .meas ac bw_hz PARAM='<f_high_3db> - <f_low_3db>'
* .meas ac ripple_db PARAM='<max_pass_db> - <min_pass_db>'
* .meas ac attn_stop_db FIND <attn_stop_expr> AT=<f_stop>
""",
    },
    "lpf_basic_50ohm": {
        "domain": "filter",
        "description": "50-ohm lowpass filter targets for passband loss, ripple, cutoff, and stopband attenuation",
        "targets": {
            "il_db": {"max": 2.0},
            "rl_db": {"min": 10.0},
            "f_3db_hz": {"max": 1.05e9},
            "ripple_db": {"max": 1.0},
            "attn_stop_db": {"min": 20.0},
        },
        "measurement_snippet": """* 50-ohm LPF metric placeholders
* .meas ac il_db FIND <il_db_expr> AT=<f_pass>
* .meas ac rl_db FIND <rl_db_expr> AT=<f_pass>
* .meas ac f_3db_hz WHEN <gain_db_expr>=<pass_ref_db_minus_3>
* .meas ac ripple_db PARAM='<max_pass_db> - <min_pass_db>'
* .meas ac attn_stop_db FIND <attn_stop_expr> AT=<f_stop>
""",
    },
    "hpf_basic_50ohm": {
        "domain": "filter",
        "description": "50-ohm highpass filter targets for passband loss, ripple, cutoff, and low-frequency attenuation",
        "targets": {
            "il_db": {"max": 2.0},
            "rl_db": {"min": 10.0},
            "f_3db_hz": {"min": 1.0e8},
            "ripple_db": {"max": 1.0},
            "attn_stop_db": {"min": 20.0},
        },
        "measurement_snippet": """* 50-ohm HPF metric placeholders
* .meas ac il_db FIND <il_db_expr> AT=<f_pass>
* .meas ac rl_db FIND <rl_db_expr> AT=<f_pass>
* .meas ac f_3db_hz WHEN <gain_db_expr>=<pass_ref_db_minus_3>
* .meas ac ripple_db PARAM='<max_pass_db> - <min_pass_db>'
* .meas ac attn_stop_db FIND <attn_stop_expr> AT=<f_stop_low>
""",
    },
    "opamp_eval_basic": {
        "domain": "opamp",
        "description": "Op-amp loop stability and transient response baseline targets",
        "targets": {
            "pm_deg": {"min": 45.0},
            "ugf_hz": {"min": 1.0e6},
            "slew_pos_vus": {"min": 1.0},
            "slew_neg_vus": {"min": 1.0},
            "settling_time_us": {"max": 10.0},
            "stability_flag": {"min": 1.0},
        },
        "measurement_snippet": """* Op-amp extraction is usually post-processed from AC + TRAN tables
* Required output metrics:
* - pm_deg
* - ugf_hz
* - slew_pos_vus / slew_neg_vus
* - settling_time_us
* - stability_flag
""",
    },
    "oscillator_eval_basic": {
        "domain": "oscillator",
        "description": "Oscillator startup and steady-state baseline targets",
        "targets": {
            "startup_flag": {"min": 1.0},
            "f_osc_hz": {"min": 1.0e6},
            "vpp_steady_v": {"min": 0.2},
            "startup_time_us": {"max": 200.0},
        },
        "measurement_snippet": """* Oscillator metrics are extracted from transient waveform
* Required output metrics:
* - startup_flag
* - f_osc_hz
* - vpp_steady_v
* - startup_time_us
""",
    },
    "power_eval_basic": {
        "domain": "power",
        "description": "Buck/LDO power quality and regulation baseline targets",
        "targets": {
            "efficiency_pct": {"min": 70.0},
            "v_ripple_mv": {"max": 100.0},
            "line_reg_mv_per_v": {"max": 50.0},
            "load_reg_mv_per_a": {"max": 100.0},
            "recovery_time_us": {"max": 200.0},
        },
        "measurement_snippet": """* Power metrics are extracted from steady, line/load sweep, and transient logs
* Required output metrics:
* - efficiency_pct
* - v_ripple_mv
* - line_reg_mv_per_v
* - load_reg_mv_per_a
* - recovery_time_us
""",
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply metric pack to spec")
    parser.add_argument("--spec-path", required=True, help="Input spec path")
    parser.add_argument("--output-path", default="", help="Output path (default overwrite)")
    parser.add_argument(
        "--pack",
        choices=sorted(METRIC_PACKS.keys()),
        default="lna_basic",
        help="Metric pack name",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite target values if metric already exists",
    )
    parser.add_argument(
        "--snippet-path",
        default="",
        help="Optional output path for measurement snippet template",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    spec_path = Path(args.spec_path).resolve()
    output_path = Path(args.output_path).resolve() if args.output_path else spec_path
    snippet_path = Path(args.snippet_path).resolve() if args.snippet_path else None

    result = {
        "ok": False,
        "pack": args.pack,
        "output_path": str(output_path),
        "added_metrics": [],
        "updated_metrics": [],
        "warnings": [],
    }

    if not spec_path.exists():
        result["warnings"].append(f"spec not found: {spec_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    targets = spec.get("targets", {})
    if not isinstance(targets, dict):
        targets = {}

    pack = METRIC_PACKS[args.pack]
    for metric_name, rule in pack["targets"].items():
        if metric_name in targets:
            if args.overwrite_existing:
                targets[metric_name] = rule
                result["updated_metrics"].append(metric_name)
            else:
                result["warnings"].append(f"metric already exists, skipped: {metric_name}")
            continue
        targets[metric_name] = rule
        result["added_metrics"].append(metric_name)

    spec["targets"] = targets
    if isinstance(spec.get("targets_eval"), dict):
        # Keep evaluator behavior deterministic by adding RF metrics to eval targets as well.
        spec["targets_eval"] = {**spec["targets_eval"], **{k: targets[k] for k in pack["targets"].keys()}}

    existing_packs = spec.get("metric_packs", [])
    if not isinstance(existing_packs, list):
        existing_packs = []
    if args.pack not in existing_packs:
        existing_packs.append(args.pack)
    spec["metric_packs"] = existing_packs
    # Keep backward compatibility with older automation that reads rf_metric_packs.
    spec["rf_metric_packs"] = existing_packs
    spec["metric_pack_notes"] = {
        "pack": args.pack,
        "domain": pack.get("domain", "generic"),
        "description": pack["description"],
        "measurement_template_required": True,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    if snippet_path is not None:
        snippet_path.parent.mkdir(parents=True, exist_ok=True)
        snippet_path.write_text(pack["measurement_snippet"], encoding="utf-8")
        result["snippet_path"] = str(snippet_path)

    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
