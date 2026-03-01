#!/usr/bin/env python3
"""Probe ngspice measurement capabilities and suggest a measurement profile."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from parse_results import parse_measurements
from run_ngspice import resolve_ngspice_bin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe ngspice measurement capabilities")
    parser.add_argument("--ngspice-bin", default="", help="ngspice executable name/path")
    parser.add_argument("--timeout-sec", type=int, default=20, help="Timeout per probe")
    parser.add_argument("--output-path", default="", help="Optional JSON output path")
    return parser


def resolve_executable(cli_value: str) -> tuple[str, str]:
    ngspice_bin, source = resolve_ngspice_bin(cli_value)
    if Path(ngspice_bin).exists():
        return str(Path(ngspice_bin).resolve()), source
    resolved = shutil.which(ngspice_bin) or ""
    return resolved, source


def run_probe(
    *,
    name: str,
    netlist_text: str,
    expected_metrics: set[str],
    executable: str,
    timeout_sec: int,
    temp_dir: Path,
) -> dict:
    cir_path = temp_dir / f"{name}.cir"
    log_path = temp_dir / f"{name}.log"
    cir_path.write_text(netlist_text, encoding="utf-8")

    probe_result = {
        "ok": False,
        "return_code": None,
        "expected_metrics": sorted(expected_metrics),
        "metrics": {},
        "failed_metrics": {},
        "missing_expected_metrics": [],
        "warnings": [],
        "log_path": str(log_path),
    }

    try:
        completed = subprocess.run(
            [executable, "-b", "-o", str(log_path), str(cir_path)],
            capture_output=True,
            text=True,
            timeout=max(1, timeout_sec),
            check=False,
        )
    except subprocess.TimeoutExpired:
        probe_result["warnings"].append(f"probe timeout after {timeout_sec}s")
        return probe_result

    probe_result["return_code"] = completed.returncode
    if not log_path.exists():
        probe_result["warnings"].append("probe log not generated")
        return probe_result

    parsed = parse_measurements(log_path.read_text(encoding="utf-8-sig", errors="ignore"), set())
    probe_result["metrics"] = parsed.get("metrics", {})
    probe_result["failed_metrics"] = parsed.get("failed_metrics", {})
    probe_result["warnings"].extend(parsed.get("warnings", []))

    present = set(probe_result["metrics"])
    failed = set(probe_result["failed_metrics"])
    missing = sorted(expected_metrics - present - failed)
    probe_result["missing_expected_metrics"] = missing

    failed_expected = expected_metrics & failed
    probe_result["ok"] = (
        completed.returncode == 0 and not missing and not failed_expected
    )
    return probe_result


def recommendation_from_probes(probes: dict[str, dict]) -> dict:
    supports_find_v = probes["ac_find_v"]["ok"]
    supports_find_vdb = probes["ac_find_vdb"]["ok"]
    supports_param_abs_log = probes["ac_param_abs_log"]["ok"]
    supports_op_meas = probes["op_find_current"]["ok"]
    supports_mixed_analysis = probes["mixed_ac_tran"]["ok"]

    if supports_find_v and supports_param_abs_log:
        gain_mode = "find_v_abs_db"
    elif supports_find_vdb:
        gain_mode = "find_vdb_direct"
    else:
        gain_mode = "manual_required"

    power_mode = "op" if supports_op_meas else "tran"
    run_mode = "single_netlist" if supports_mixed_analysis else "split_ac_power"

    return {
        "gain_measurement_mode": gain_mode,
        "power_measurement_mode": power_mode,
        "run_mode": run_mode,
        "supports_find_v": supports_find_v,
        "supports_find_vdb": supports_find_vdb,
        "supports_param_abs_log": supports_param_abs_log,
        "supports_op_meas": supports_op_meas,
        "supports_mixed_analysis": supports_mixed_analysis,
    }


def main() -> int:
    args = build_parser().parse_args()

    executable, source = resolve_executable(args.ngspice_bin)
    result = {
        "ok": False,
        "ngspice_bin": executable,
        "ngspice_source": source,
        "probes": {},
        "capabilities": {},
        "recommended_profile": {},
        "warnings": [],
    }

    if not executable:
        result["warnings"].append("ngspice executable not found")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    probe_texts: dict[str, tuple[str, set[str]]] = {
        "ac_find_v": (
            """
* probe ac find v
Vin in 0 AC 1
R1 in out 1k
C1 out 0 1n
.ac dec 10 1k 1Meg
.print ac v(out)
.meas ac m_find FIND v(out) AT=100k
.end
""".strip(),
            {"m_find"},
        ),
        "ac_find_vdb": (
            """
* probe ac find vdb
Vin in 0 AC 1
R1 in out 1k
C1 out 0 1n
.ac dec 10 1k 1Meg
.print ac v(out)
.meas ac m_vdb FIND vdb(out) AT=100k
.end
""".strip(),
            {"m_vdb"},
        ),
        "ac_param_abs_log": (
            """
* probe ac param
Vin in 0 AC 1
R1 in out 1k
C1 out 0 1n
.ac dec 10 1k 1Meg
.print ac v(out)
.meas ac m_find FIND v(out) AT=100k
.meas ac m_db PARAM='20*log10(abs(m_find))'
.end
""".strip(),
            {"m_find", "m_db"},
        ),
        "op_find_current": (
            """
* probe op current
V1 vdd 0 DC 5
R1 vdd 0 1k
.op
.print op i(V1)
.meas op m_op FIND i(V1)
.end
""".strip(),
            {"m_op"},
        ),
        "mixed_ac_tran": (
            """
* probe mixed analysis
V1 vdd 0 DC 5
Vin in 0 AC 1
R1 in out 1k
C1 out 0 1n
R2 vdd 0 1k
.ac dec 10 1k 1Meg
.tran 1n 2n
.print ac v(out)
.print tran i(V1)
.meas ac m_ac FIND v(out) AT=100k
.meas tran m_tran FIND i(V1) AT=2n
.end
""".strip(),
            {"m_ac", "m_tran"},
        ),
    }

    with tempfile.TemporaryDirectory(prefix="ngspice_probe_") as tmp:
        tmp_dir = Path(tmp)
        for name, (text, expected) in probe_texts.items():
            result["probes"][name] = run_probe(
                name=name,
                netlist_text=text,
                expected_metrics=expected,
                executable=executable,
                timeout_sec=args.timeout_sec,
                temp_dir=tmp_dir,
            )

    result["recommended_profile"] = recommendation_from_probes(result["probes"])
    result["capabilities"] = dict(result["recommended_profile"])
    result["ok"] = True

    if args.output_path:
        output_path = Path(args.output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
