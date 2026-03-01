#!/usr/bin/env python3
"""Split one SPICE netlist into AC and power-analysis netlists."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MEAS_NAME_RE = re.compile(r"^\s*\.meas(?:ure)?\s+\w+\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
COMMENT_PREFIXES = ("*", ";", "//", "$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split SPICE netlist into AC and power runs")
    parser.add_argument("--netlist-path", required=True, help="Input netlist path")
    parser.add_argument("--ac-netlist-path", required=True, help="Output AC netlist path")
    parser.add_argument("--power-netlist-path", required=True, help="Output power netlist path")
    parser.add_argument(
        "--power-analysis",
        default="tran",
        choices=["tran", "op", "dc"],
        help="Analysis used for power extraction",
    )
    parser.add_argument("--power-tran-step", default="1n", help="Transient step when power-analysis=tran")
    parser.add_argument("--power-tran-stop", default="2n", help="Transient stop time when power-analysis=tran")
    parser.add_argument(
        "--power-sample-time",
        default="2n",
        help="Sample time for power measurement when power-analysis=tran",
    )
    parser.add_argument("--supply-source", default="VDD", help="Supply source instance name")
    parser.add_argument(
        "--supply-voltage-expr",
        default="VDD_SUPPLY",
        help="Expression used to compute pdc_w from idd_a",
    )
    return parser


def directive_name(stripped: str) -> str:
    parts = stripped.split(maxsplit=1)
    return parts[0].lower() if parts else ""


def second_token(stripped: str) -> str:
    tokens = stripped.split()
    if len(tokens) < 2:
        return ""
    return tokens[1].lower()


def is_comment_or_blank(stripped: str) -> bool:
    if not stripped:
        return True
    return stripped.startswith(COMMENT_PREFIXES)


def collect_meas_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        match = MEAS_NAME_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def ensure_power_measurements(
    power_lines: list[str],
    *,
    power_analysis: str,
    supply_source: str,
    supply_voltage_expr: str,
    power_sample_time: str,
    warnings: list[str],
) -> list[str]:
    out = list(power_lines)
    existing_meas = collect_meas_names(out)

    analysis = power_analysis.lower()
    if analysis == "tran":
        measurement_lines = [
            f".meas tran idd_a FIND i({supply_source}) AT={power_sample_time}",
            f".meas tran pdc_w PARAM='{supply_voltage_expr}*(-1)*idd_a'",
            ".meas tran idd_ma PARAM='(-1)*idd_a*1000'",
        ]
    elif analysis == "op":
        measurement_lines = [
            f".meas op idd_a FIND i({supply_source})",
            f".meas op pdc_w PARAM='{supply_voltage_expr}*(-1)*idd_a'",
            ".meas op idd_ma PARAM='(-1)*idd_a*1000'",
        ]
    else:
        measurement_lines = [
            f".meas dc idd_a FIND i({supply_source})",
            f".meas dc pdc_w PARAM='{supply_voltage_expr}*(-1)*idd_a'",
            ".meas dc idd_ma PARAM='(-1)*idd_a*1000'",
        ]

    for line in measurement_lines:
        metric_name = line.split()[2]
        if metric_name in existing_meas:
            continue
        out.append(line)
        existing_meas.add(metric_name)
        warnings.append(f"inserted power measurement: {metric_name}")

    # Ensure a matching print directive exists for the selected analysis.
    analysis_print = f".print {analysis} i({supply_source})"
    has_print = any(
        directive_name(line.strip().lstrip("\ufeff")) == ".print"
        and second_token(line.strip().lstrip("\ufeff")) == analysis
        for line in out
    )
    if not has_print:
        out.append(analysis_print)
        warnings.append(f"inserted power print directive for {analysis}")

    return out


def ensure_power_analysis_card(
    power_lines: list[str],
    *,
    power_analysis: str,
    power_tran_step: str,
    power_tran_stop: str,
    warnings: list[str],
) -> list[str]:
    out = list(power_lines)
    analysis = power_analysis.lower()
    has_card = False
    for line in out:
        stripped = line.strip().lstrip("\ufeff")
        if directive_name(stripped) == f".{analysis}":
            has_card = True
            break

    if not has_card:
        if analysis == "tran":
            out.append(f".tran {power_tran_step} {power_tran_stop}")
        elif analysis == "op":
            out.append(".op")
        else:
            out.append(".dc TEMP 27 27 1")
        warnings.append(f"inserted missing .{analysis} analysis card")
    return out


def main() -> int:
    args = build_parser().parse_args()

    netlist_path = Path(args.netlist_path).resolve()
    ac_path = Path(args.ac_netlist_path).resolve()
    power_path = Path(args.power_netlist_path).resolve()

    result = {
        "ok": False,
        "ac_netlist_path": str(ac_path),
        "power_netlist_path": str(power_path),
        "warnings": [],
    }

    if not netlist_path.exists():
        result["warnings"].append(f"netlist not found: {netlist_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    original_lines = netlist_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    ac_lines: list[str] = []
    power_lines: list[str] = []

    power_analysis = args.power_analysis.lower()

    for line in original_lines:
        stripped = line.strip().lstrip("\ufeff")
        if stripped.lower() == ".end":
            continue

        if is_comment_or_blank(stripped):
            ac_lines.append(line)
            power_lines.append(line)
            continue

        if not stripped.startswith("."):
            ac_lines.append(line)
            power_lines.append(line)
            continue

        dname = directive_name(stripped)
        token2 = second_token(stripped)

        if dname in {".ac", ".tran", ".op", ".dc"}:
            if dname == ".ac":
                ac_lines.append(line)
            elif dname == f".{power_analysis}":
                power_lines.append(line)
            continue

        if dname in {".print", ".plot"}:
            if token2 == "ac":
                ac_lines.append(line)
            elif token2 == power_analysis:
                power_lines.append(line)
            else:
                if token2 == "":
                    ac_lines.append(line)
                    power_lines.append(line)
                    result["warnings"].append(f"kept ambiguous directive in both netlists: {stripped}")
            continue

        if dname in {".meas", ".measure"}:
            if token2 == "ac":
                ac_lines.append(line)
            elif token2 == power_analysis:
                power_lines.append(line)
            continue

        # Non-analysis directives are copied to both outputs.
        ac_lines.append(line)
        power_lines.append(line)

    power_lines = ensure_power_analysis_card(
        power_lines,
        power_analysis=power_analysis,
        power_tran_step=args.power_tran_step,
        power_tran_stop=args.power_tran_stop,
        warnings=result["warnings"],
    )
    power_lines = ensure_power_measurements(
        power_lines,
        power_analysis=power_analysis,
        supply_source=args.supply_source,
        supply_voltage_expr=args.supply_voltage_expr,
        power_sample_time=args.power_sample_time,
        warnings=result["warnings"],
    )

    ac_lines.append(".end")
    power_lines.append(".end")

    ac_path.parent.mkdir(parents=True, exist_ok=True)
    power_path.parent.mkdir(parents=True, exist_ok=True)
    ac_path.write_text("\n".join(ac_lines) + "\n", encoding="utf-8")
    power_path.write_text("\n".join(power_lines) + "\n", encoding="utf-8")

    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
