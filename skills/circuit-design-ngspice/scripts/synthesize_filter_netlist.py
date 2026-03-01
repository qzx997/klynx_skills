#!/usr/bin/env python3
"""Synthesize primitive-only passive filter starter netlists."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


G3_BUTTERWORTH = (1.0, 2.0, 1.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synthesize passive filter starter netlist")
    parser.add_argument(
        "--filter-type",
        required=True,
        choices=["lpf", "hpf", "bpf"],
        help="Filter class",
    )
    parser.add_argument(
        "--topology",
        required=True,
        choices=["pi", "t", "ladder"],
        help="Topology family",
    )
    parser.add_argument("--order", type=int, default=3, help="Filter order (current implementation targets 3)")
    parser.add_argument("--response", default="butterworth", help="Approximation family label")
    parser.add_argument("--ripple-db", type=float, default=0.5, help="Ripple target label for documentation")
    parser.add_argument("--z0", type=float, default=50.0, help="System impedance in ohms")
    parser.add_argument("--f-c", type=float, default=0.0, help="Cutoff frequency for LPF/HPF (Hz)")
    parser.add_argument("--f-low", type=float, default=0.0, help="Low edge frequency for BPF (Hz)")
    parser.add_argument("--f-high", type=float, default=0.0, help="High edge frequency for BPF (Hz)")
    parser.add_argument("--output-path", required=True, help="Output netlist path")
    return parser


def eng(value: float) -> str:
    if value == 0.0:
        return "0"
    units = [
        (1e-12, "p"),
        (1e-9, "n"),
        (1e-6, "u"),
        (1e-3, "m"),
        (1.0, ""),
        (1e3, "k"),
        (1e6, "meg"),
        (1e9, "g"),
    ]
    abs_v = abs(value)
    best_scale, best_suffix = units[0]
    for scale, suffix in units:
        scaled = abs_v / scale
        if 1.0 <= scaled < 1000.0:
            best_scale, best_suffix = scale, suffix
            break
        best_scale, best_suffix = scale, suffix
    scaled = value / best_scale
    return f"{scaled:.6g}{best_suffix}"


def ensure_valid_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    if args.order != 3:
        errors.append("only 3rd-order synthesis is currently supported")
    if args.z0 <= 0:
        errors.append("z0 must be positive")
    if args.filter_type in {"lpf", "hpf"} and args.f_c <= 0:
        errors.append("f-c must be positive for lpf/hpf")
    if args.filter_type == "bpf":
        if args.f_low <= 0 or args.f_high <= 0:
            errors.append("f-low and f-high must be positive for bpf")
        if args.f_high <= args.f_low:
            errors.append("f-high must be greater than f-low")
    return errors


def synth_lpf(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    w0 = 2.0 * math.pi * args.f_c
    g1, g2, g3 = G3_BUTTERWORTH
    z0 = args.z0

    if args.topology == "pi":
        c1 = g1 / (w0 * z0)
        l1 = g2 * z0 / w0
        c2 = g3 / (w0 * z0)
        params = {"C1": eng(c1), "L1": eng(l1), "C2": eng(c2)}
        body = """* PI section (C-L-C)
C1 in 0 {C1}
L1 in out {L1}
C2 out 0 {C2}
"""
    else:
        l1 = g1 * z0 / w0
        c1 = g2 / (w0 * z0)
        l2 = g3 * z0 / w0
        params = {"L1": eng(l1), "C1": eng(c1), "L2": eng(l2)}
        body = """* T section (L-C-L)
L1 in n1 {L1}
C1 n1 0 {C1}
L2 n1 out {L2}
"""
    return body, params


def synth_hpf(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    w0 = 2.0 * math.pi * args.f_c
    g1, g2, g3 = G3_BUTTERWORTH
    z0 = args.z0

    if args.topology == "pi":
        l1 = z0 / (g1 * w0)
        c1 = 1.0 / (g2 * z0 * w0)
        l2 = z0 / (g3 * w0)
        params = {"L1": eng(l1), "C1": eng(c1), "L2": eng(l2)}
        body = """* PI section (L-C-L)
L1 in 0 {L1}
C1 in out {C1}
L2 out 0 {L2}
"""
    else:
        c1 = 1.0 / (g1 * z0 * w0)
        l1 = z0 / (g2 * w0)
        c2 = 1.0 / (g3 * z0 * w0)
        params = {"C1": eng(c1), "L1": eng(l1), "C2": eng(c2)}
        body = """* T section (C-L-C)
C1 in n1 {C1}
L1 n1 0 {L1}
C2 n1 out {C2}
"""
    return body, params


def synth_bpf_ladder(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    # Practical hybrid ladder: HP shaping at f_low + LP shaping at f_high.
    w_low = 2.0 * math.pi * args.f_low
    w_high = 2.0 * math.pi * args.f_high
    g1, g2, g3 = G3_BUTTERWORTH
    z0 = args.z0

    c_ser1 = 1.0 / (g1 * z0 * w_low)
    l_sh1 = z0 / (g2 * w_low)
    c_ser2 = 1.0 / (g3 * z0 * w_low)

    l_ser1 = g1 * z0 / w_high
    c_sh1 = g2 / (z0 * w_high)
    l_ser2 = g3 * z0 / w_high

    params = {
        "C_SER1": eng(c_ser1),
        "L_SH1": eng(l_sh1),
        "C_SER2": eng(c_ser2),
        "L_SER1": eng(l_ser1),
        "C_SH1": eng(c_sh1),
        "L_SER2": eng(l_ser2),
    }
    body = """* Ladder BPF: high-pass shaping + low-pass shaping
C_SER1 in n1 {C_SER1}
L_SH1 n1 0 {L_SH1}
C_SER2 n1 n2 {C_SER2}
L_SER1 n2 n3 {L_SER1}
C_SH1 n3 0 {C_SH1}
L_SER2 n3 out {L_SER2}
"""
    return body, params


def build_netlist(args: argparse.Namespace) -> tuple[str, dict]:
    warnings: list[str] = []
    response = args.response.strip().lower()
    if response != "butterworth":
        warnings.append(
            f"response '{args.response}' is currently mapped to Butterworth 3rd-order coefficients"
        )

    topology = args.topology
    if args.filter_type == "bpf" and topology != "ladder":
        warnings.append(f"topology '{topology}' is remapped to 'ladder' for bpf synthesis")
        topology = "ladder"
    if args.filter_type in {"lpf", "hpf"} and topology == "ladder":
        warnings.append(f"topology '{topology}' is remapped to 'pi' for {args.filter_type}")
        topology = "pi"

    if args.filter_type == "lpf":
        body, element_params = synth_lpf(
            argparse.Namespace(**{**vars(args), "topology": topology})
        )
        f_start = max(args.f_c / 1000.0, 1.0)
        f_stop = args.f_c * 20.0
        f_pass = args.f_c / 5.0
        f_stop_attn = args.f_c * 2.0
    elif args.filter_type == "hpf":
        body, element_params = synth_hpf(
            argparse.Namespace(**{**vars(args), "topology": topology})
        )
        f_start = max(args.f_c / 100.0, 1.0)
        f_stop = args.f_c * 20.0
        f_pass = args.f_c * 2.0
        f_stop_attn = args.f_c / 5.0
    else:
        body, element_params = synth_bpf_ladder(args)
        f_start = max(args.f_low / 10.0, 1.0)
        f_stop = args.f_high * 10.0
        f_pass = math.sqrt(args.f_low * args.f_high)
        f_stop_attn = args.f_high * 3.0

    params = {
        "RSRC": f"{eng(args.z0)}ohm",
        "RLOAD": f"{eng(args.z0)}ohm",
        "F_START": eng(f_start),
        "F_STOP": eng(f_stop),
        "F_PASS": eng(f_pass),
        "F_STOP_ATTN": eng(f_stop_attn),
        **element_params,
    }

    lines = [
        f"* Auto-synthesized {args.filter_type.upper()} filter ({topology})",
        f"* Order={args.order}, Response={args.response}, Z0={args.z0} ohm, ripple={args.ripple_db} dB",
        "",
    ]
    for key, value in params.items():
        lines.append(f".param {key}={value}")
    lines.extend(
        [
            "",
            "Vin src 0 AC 1",
            "Rsrc src in {RSRC}",
            "",
            body.rstrip(),
            "",
            "Rload out 0 {RLOAD}",
            "",
            ".ac dec 300 {F_START} {F_STOP}",
            "",
            "* Metric placeholders (adjust for testbench-specific definitions)",
            ".meas ac g_pass_mag FIND mag(v(out)) AT={F_PASS}",
            ".meas ac g_stop_mag FIND mag(v(out)) AT={F_STOP_ATTN}",
            ".meas ac il_db FIND mag(v(out)) AT={F_PASS}",
            ".meas ac attn_stop_db FIND mag(v(out)) AT={F_STOP_ATTN}",
            ".meas ac bw_hz PARAM='0'",
            ".meas ac ripple_db PARAM='0'",
            ".meas ac rl_db PARAM='0'",
            "",
            ".print ac v(out)",
            ".end",
            "",
        ]
    )
    payload = {
        "filter_type": args.filter_type,
        "topology_requested": args.topology,
        "topology_emitted": topology,
        "response": args.response,
        "order": args.order,
        "z0_ohm": args.z0,
        "warnings": warnings,
        "params": params,
    }
    return "\n".join(lines), payload


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output_path).resolve()
    result = {"ok": False, "output_path": str(output_path), "warnings": [], "errors": []}

    errors = ensure_valid_args(args)
    if errors:
        result["errors"] = errors
        print(json.dumps(result, ensure_ascii=False))
        return 1

    netlist_text, payload = build_netlist(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(netlist_text, encoding="utf-8")

    result["ok"] = True
    result["warnings"] = payload.get("warnings", [])
    result["synthesis"] = payload
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
