#!/usr/bin/env python3
"""Generate a comprehensive markdown report for a circuit design run."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from netlist_to_json import (
    apply_value_resolution,
    is_control_directive,
    merge_continuation_lines,
    parse_component_line,
    parse_param_assignments,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate final markdown report for a design run")
    parser.add_argument("--job-dir", default="", help="Job directory (work/<job_id>)")
    parser.add_argument("--spec-path", default="", help="Spec JSON path")
    parser.add_argument("--netlist-path", default="", help="Final design netlist path")
    parser.add_argument("--metrics-path", default="", help="Final metrics JSON path")
    parser.add_argument("--evaluation-path", default="", help="Final evaluation JSON path")
    parser.add_argument("--schematic-path", default="", help="Final schematic SVG path")
    parser.add_argument("--plot-path", default="", help="Simulation plot SVG path")
    parser.add_argument("--output-path", default="", help="Output markdown path")
    parser.add_argument("--title", default="", help="Report title override")
    return parser


def to_path(value: str) -> Path | None:
    if not value.strip():
        return None
    return Path(value).expanduser().resolve()


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    job_dir = to_path(args.job_dir)
    defaults = {
        "spec_path": "spec.json",
        "netlist_path": "final/design.cir",
        "metrics_path": "final/metrics.json",
        "evaluation_path": "final/evaluation.json",
        "schematic_path": "final/schematic.svg",
        "plot_path": "final/plots/sim_plot.svg",
        "output_path": "final/report.md",
    }
    resolved: dict[str, Path] = {}
    for key, rel in defaults.items():
        explicit = to_path(getattr(args, key))
        if explicit is not None:
            resolved[key] = explicit
            continue
        if job_dir is None:
            raise ValueError(f"missing --{key.replace('_', '-')} and --job-dir")
        resolved[key] = (job_dir / rel).resolve()
    return resolved


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, OSError, json.JSONDecodeError):
        return {}


def format_num(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    f = float(value)
    if math.isfinite(f):
        if abs(f) >= 1000 or (abs(f) > 0 and abs(f) < 0.001):
            return f"{f:.4e}"
        return f"{f:.6g}"
    return str(value)


def format_target(rule: dict) -> str:
    if not isinstance(rule, dict):
        return "invalid"
    has_min = "min" in rule
    has_max = "max" in rule
    if has_min and has_max:
        return f"[{format_num(rule['min'])}, {format_num(rule['max'])}]"
    if has_min:
        return f">= {format_num(rule['min'])}"
    if has_max:
        return f"<= {format_num(rule['max'])}"
    return "n/a"


def resolve_actual_metric(
    name: str,
    metrics: dict,
    failed: dict,
    aliases: dict[str, list[str]],
) -> tuple[float | None, str | None, str]:
    if name in metrics:
        return metrics[name], name, "ok"
    if name in failed:
        return None, None, "failed"
    for alias in aliases.get(name, []):
        if alias in metrics:
            return metrics[alias], alias, "ok_alias"
        if alias in failed:
            return None, None, "failed_alias"
    return None, None, "missing"


def target_status(rule: dict, actual: float | None) -> str:
    if not isinstance(rule, dict):
        return "INVALID_RULE"
    if actual is None:
        return "MISSING"
    if "min" in rule and actual < float(rule["min"]):
        return "FAIL"
    if "max" in rule and actual > float(rule["max"]):
        return "FAIL"
    return "PASS"


def relative_path(from_file: Path, target: Path) -> str:
    return str(Path(target).resolve().relative_to(from_file.parent.resolve())).replace("\\", "/")


def safe_relative_path(from_file: Path, target: Path) -> str:
    try:
        return relative_path(from_file, target)
    except ValueError:
        return str(target).replace("\\", "/")


def parse_components(netlist_path: Path) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if not netlist_path.exists():
        return [], [f"netlist not found: {netlist_path}"]
    content = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
    components: list[dict] = []
    merged_lines = merge_continuation_lines(content)
    params = parse_param_assignments(merged_lines)
    in_control_block = False
    for line_no, line in merged_lines:
        if is_control_directive(line, ".control"):
            in_control_block = True
            continue
        if is_control_directive(line, ".endc"):
            in_control_block = False
            continue
        if in_control_block:
            continue

        comp = parse_component_line(line, line_no, warnings)
        if comp:
            apply_value_resolution(comp, params)
            components.append(comp)
    return components, warnings


def component_counts(components: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comp in components:
        ctype = str(comp.get("type", "unknown"))
        counts[ctype] = counts.get(ctype, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0]))


def infer_architecture(components: list[dict]) -> list[str]:
    if not components:
        return ["No components were parsed from the final netlist."]

    active = [c for c in components if c.get("type") in {"bjt", "mosfet"}]
    supplies = []
    for c in components:
        nodes = [str(n).lower() for n in c.get("nodes", [])]
        if any(n.startswith(("vcc", "vdd", "vee", "vss")) for n in nodes):
            supplies.append(c["name"])

    input_like = {"in", "vin", "rf_in", "input"}
    output_like = {"out", "vout", "rf_out", "output"}
    input_refs = [
        c["name"]
        for c in components
        if any(str(n).lower() in input_like for n in c.get("nodes", []))
    ]
    output_refs = [
        c["name"]
        for c in components
        if any(str(n).lower() in output_like for n in c.get("nodes", []))
    ]

    lines = [
        f"Active gain devices: {len(active)} ({', '.join(c['name'] for c in active[:8]) or 'none'}).",
        (
            "Bias and supply-coupled network elements: "
            f"{len(supplies)} ({', '.join(supplies[:10]) or 'none'})."
        ),
        (
            "Input-side connected parts: "
            f"{len(input_refs)} ({', '.join(input_refs[:10]) or 'none'})."
        ),
        (
            "Output-side connected parts: "
            f"{len(output_refs)} ({', '.join(output_refs[:10]) or 'none'})."
        ),
        "Architecture interpretation: input matching/coupling -> bias + active core -> output load/matching.",
    ]
    return lines


def infer_io_definition(components: list[dict]) -> dict[str, str | list[str]]:
    input_aliases = {"in", "vin", "rf_in", "input"}
    output_aliases = {"out", "vout", "rf_out", "output"}

    source_pos = ""
    source_neg = ""
    for comp in components:
        if comp.get("type") not in {"voltage_source", "current_source"}:
            continue
        nodes = [str(n) for n in comp.get("nodes", [])]
        if len(nodes) >= 2:
            source_pos = nodes[0]
            source_neg = nodes[1]
            break

    named_inputs = []
    named_outputs = []
    for comp in components:
        for node in comp.get("nodes", []):
            node_l = str(node).lower()
            if node_l in input_aliases and str(node) not in named_inputs:
                named_inputs.append(str(node))
            if node_l in output_aliases and str(node) not in named_outputs:
                named_outputs.append(str(node))

    input_node = named_inputs[0] if named_inputs else ""
    output_node = named_outputs[0] if named_outputs else ""

    if not input_node and source_pos:
        for comp in components:
            if comp.get("type") != "resistor":
                continue
            nodes = [str(n) for n in comp.get("nodes", [])]
            if source_pos in nodes:
                other = nodes[1] if nodes[0] == source_pos else nodes[0]
                if other != source_neg:
                    input_node = other
                    break
        if not input_node:
            input_node = source_pos

    if not output_node:
        for comp in reversed(components):
            if comp.get("type") != "resistor":
                continue
            nodes = [str(n) for n in comp.get("nodes", [])]
            if len(nodes) < 2:
                continue
            if "0" in nodes:
                other = nodes[1] if nodes[0] == "0" else nodes[0]
                if other != input_node:
                    output_node = other
                    break

    source_impedance_parts = []
    load_impedance_parts = []
    for comp in components:
        if comp.get("type") != "resistor":
            continue
        name = str(comp.get("name", ""))
        value = str(comp.get("value", ""))
        nodes = [str(n) for n in comp.get("nodes", [])]
        if source_pos and source_pos in nodes:
            source_impedance_parts.append(f"{name}={value}")
        if output_node and output_node in nodes and "0" in nodes:
            load_impedance_parts.append(f"{name}={value}")

    return {
        "input_node": input_node or "n/a",
        "output_node": output_node or "n/a",
        "source_node": source_pos or "n/a",
        "source_impedance_parts": source_impedance_parts,
        "load_impedance_parts": load_impedance_parts,
    }


def infer_topology_rationale(components: list[dict]) -> list[str]:
    counts = component_counts(components)
    r_count = counts.get("resistor", 0)
    l_count = counts.get("inductor", 0)
    c_count = counts.get("capacitor", 0)
    active_count = counts.get("bjt", 0) + counts.get("mosfet", 0)

    if active_count > 0:
        return [
            "Active-stage architecture detected (BJT/MOS devices present).",
            "Topology intent is amplifier-oriented, with passive matching/coupling around active core.",
            "Primary rationale: meet gain/bias targets while using passive networks for stability and bandwidth shaping.",
        ]

    if l_count > 0 and c_count > 0:
        if (c_count >= 2 and l_count == 1) or (l_count >= 2 and c_count == 1):
            return [
                "Passive LC network detected with compact 3-element profile.",
                "Topology likely PI/T-class section for impedance-terminated filtering.",
                "Primary rationale: moderate order with low part count and predictable 50-ohm integration.",
            ]
        return [
            "Passive LC ladder behavior detected (multiple L and C branches).",
            "Topology likely higher-order ladder/BPF cascade for sharper transition or wider control over response.",
            "Primary rationale: improve selectivity and stopband attenuation under source/load impedance constraints.",
        ]

    if r_count > 0 and c_count > 0:
        return [
            "Passive RC network detected.",
            "Topology rationale: low-complexity filtering or compensation where inductor use is not required.",
        ]

    return [
        "Topology inference is limited with current component mix.",
        "Use explicit topology naming in spec/report title for stronger traceability.",
    ]


def load_iteration_rows(job_dir: Path, target_metric_names: list[str]) -> list[dict]:
    rows: list[dict] = []
    iter_dir = job_dir / "iterations"
    if not iter_dir.exists():
        return rows
    for folder in sorted(iter_dir.glob("iter_*")):
        metrics = read_json(folder / "metrics.json")
        evaluation = read_json(folder / "evaluation.json")
        metrics_map = metrics.get("metrics", metrics if isinstance(metrics, dict) else {})
        row = {
            "iteration": folder.name,
            "pass": bool(evaluation.get("pass", False)) if evaluation else False,
            "metrics": {},
        }
        for name in target_metric_names:
            if name in metrics_map:
                row["metrics"][name] = metrics_map[name]
        rows.append(row)
    return rows


def load_component_value_map(netlist_path: Path) -> dict[str, str]:
    components, _ = parse_components(netlist_path)
    return {str(c["name"]): str(c.get("value", "")) for c in components}


def summarize_value_changes(first_netlist: Path, final_netlist: Path) -> list[str]:
    if not first_netlist.exists() or not final_netlist.exists():
        return []
    first = load_component_value_map(first_netlist)
    final = load_component_value_map(final_netlist)
    changed: list[str] = []
    for ref in sorted(set(first) & set(final)):
        if first[ref] != final[ref]:
            changed.append(f"{ref}: `{first[ref]}` -> `{final[ref]}`")
    return changed


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""

    def sanitize(cell: str) -> str:
        value = str(cell).replace("\r\n", "\n").replace("\r", "\n")
        value = value.replace("|", "\\|").replace("\n", "<br/>")
        return value if value.strip() else " "

    out = []
    norm_headers = [sanitize(h) for h in headers]
    out.append("| " + " | ".join(norm_headers) + " |")
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        normalized = list(row[: len(headers)])
        if len(normalized) < len(headers):
            normalized.extend([""] * (len(headers) - len(normalized)))
        out.append("| " + " | ".join(sanitize(cell) for cell in normalized) + " |")
    return "\n".join(out)


def build_report(
    *,
    title: str,
    paths: dict[str, Path],
    spec: dict,
    metrics_json: dict,
    evaluation: dict,
    components: list[dict],
    parse_warnings: list[str],
) -> str:
    netlist_path = paths["netlist_path"]
    output_path = paths["output_path"]
    metrics = metrics_json.get("metrics", metrics_json if isinstance(metrics_json, dict) else {})
    failed_metrics = metrics_json.get("failed_metrics", {})
    target_source = "targets_eval" if isinstance(spec.get("targets_eval"), dict) else "targets"
    targets = spec.get(target_source, {})
    if not isinstance(targets, dict):
        targets = {}
    aliases = spec.get("metric_aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}

    metric_rows: list[list[str]] = []
    metric_analysis: list[dict] = []
    target_metric_names = list(targets.keys())
    for metric_name, rule in targets.items():
        actual, used_name, status_kind = resolve_actual_metric(
            metric_name, metrics, failed_metrics, aliases
        )
        status = target_status(rule, actual)
        margin_text = "n/a"
        if actual is not None and isinstance(rule, dict):
            if "min" in rule:
                margin = float(actual) - float(rule["min"])
                margin_text = f"{format_num(margin)} over min"
            elif "max" in rule:
                margin = float(rule["max"]) - float(actual)
                margin_text = f"{format_num(margin)} headroom"
        metric_analysis.append(
            {
                "name": metric_name,
                "status": status,
                "margin": margin_text,
                "actual": actual,
                "rule": rule,
            }
        )
        source_note = ""
        if used_name and used_name != metric_name:
            source_note = f" (via {used_name})"
        if status_kind.startswith("failed"):
            status = "FAILED_MEAS"
        elif status_kind == "missing":
            status = "MISSING"
        metric_rows.append(
            [
                metric_name,
                format_target(rule),
                "n/a" if actual is None else format_num(actual) + source_note,
                status,
            ]
        )

    counts = component_counts(components)
    missing_param_components = [c for c in components if c.get("param_missing")]
    component_rows = [
        [
            str(c.get("name", "")),
            str(c.get("type", "")),
            ", ".join(str(n) for n in c.get("nodes", [])),
            str(c.get("value", "")),
        ]
        for c in components
    ]

    architecture_bullets = infer_architecture(components)
    io_def = infer_io_definition(components)
    topology_rationale = infer_topology_rationale(components)

    job_dir = paths["output_path"].parents[1] if paths["output_path"].parent.name == "final" else None
    iter_rows = load_iteration_rows(job_dir, target_metric_names[:5]) if job_dir else []

    value_changes: list[str] = []
    if job_dir:
        first_iter = job_dir / "iterations" / "iter_001" / "design.cir"
        value_changes = summarize_value_changes(first_iter, netlist_path)

    summary_line = "PASS" if evaluation.get("pass", False) else "FAIL"
    if not evaluation:
        summary_line = "UNKNOWN (evaluation.json missing)"

    md: list[str] = []
    md.append(f"# {title}")
    md.append("")
    md.append("## 1. Design Objectives")
    md.append(f"- Spec source: `{paths['spec_path']}`")
    md.append(f"- Evaluation result: **{summary_line}**")
    md.append(f"- Target source used: `{target_source}`")
    md.append("")
    if metric_rows:
        md.append("")
        md.append(markdown_table(["Metric", "Target", "Final", "Status"], metric_rows))
        md.append("")
    else:
        md.append("No valid target metrics were found in spec.")

    md.append("")
    md.append("## 2. Design Approach and Architecture")
    md.append("### 2.1 Design Thought Process")
    md.append("- Start from spec targets and hard constraints (gain/bandwidth/power).")
    md.append("- Keep primitive-only topology (R/C/L/Q/M/D/V/I) and tune parameters iteratively.")
    md.append("- Use measured metrics per iteration to apply targeted parameter updates.")
    md.append("")
    md.append("### 2.2 Circuit Architecture")
    for line in architecture_bullets:
        md.append(f"- {line}")
    md.append("")
    md.append(
        f"- Component type distribution: "
        + ", ".join(f"{k}={v}" for k, v in counts.items())
        if counts
        else "- Component type distribution: none"
    )

    md.append("")
    md.append("")
    md.append("### 2.3 I/O Definition")
    md.append(f"- Input node: `{io_def['input_node']}`")
    md.append(f"- Output node: `{io_def['output_node']}`")
    md.append(f"- Source excitation node: `{io_def['source_node']}`")
    src_parts = io_def.get("source_impedance_parts", [])
    load_parts = io_def.get("load_impedance_parts", [])
    md.append(
        "- Source-side impedance parts: "
        + (", ".join(src_parts) if src_parts else "not identified")
    )
    md.append(
        "- Load-side impedance parts: "
        + (", ".join(load_parts) if load_parts else "not identified")
    )

    md.append("")
    md.append("### 2.4 Topology Rationale")
    for line in topology_rationale:
        md.append(f"- {line}")

    md.append("")
    md.append("### 2.5 Component Parameterization")
    md.append(
        f"- Total parsed components: {len(components)}; "
        f"missing-parameter components: {len(missing_param_components)}."
    )
    if component_rows:
        md.append("")
        md.append(markdown_table(["Ref", "Type", "Nodes", "Parameter/Model"], component_rows))
        md.append("")
    else:
        md.append("No components parsed.")

    md.append("")
    md.append("## 3. Simulation Setup")
    md.append(f"- Netlist: `{paths['netlist_path']}`")
    md.append(f"- Metrics JSON: `{paths['metrics_path']}`")
    md.append(f"- Evaluation JSON: `{paths['evaluation_path']}`")
    if paths["schematic_path"].exists():
        md.append(f"- Schematic SVG: `{paths['schematic_path']}`")
    plot_paths: list[Path] = []
    if paths["plot_path"].exists():
        plot_paths.append(paths["plot_path"])
    plot_dir = paths["plot_path"].parent
    if plot_dir.exists():
        for candidate in sorted(plot_dir.glob("*.svg")):
            if candidate not in plot_paths:
                plot_paths.append(candidate)
    if plot_paths:
        md.append(f"- Plot SVG count: {len(plot_paths)}")
        for candidate in plot_paths:
            md.append(f"  - `{candidate}`")

    md.append("")
    md.append("## 4. Simulation Results and Analysis")
    md.append("### 4.1 Final Result Summary")
    eval_pass = bool(evaluation.get("pass", False))
    if eval_pass:
        md.append("- Evaluation pass: all required target checks are satisfied.")
    else:
        md.append("- Evaluation did not pass; details are listed below.")

    if evaluation.get("gaps"):
        md.append("- Remaining target gaps:")
        for gap in evaluation.get("gaps", []):
            md.append(
                f"  - {gap.get('name', 'unknown')}: "
                f"target {format_num(gap.get('target'))}, actual {format_num(gap.get('actual'))}"
            )
    if evaluation.get("missing_metrics"):
        md.append("- Missing metrics: " + ", ".join(str(x) for x in evaluation.get("missing_metrics", [])))
    if evaluation.get("failed_metrics"):
        md.append("- Failed measurements:")
        for item in evaluation.get("failed_metrics", []):
            md.append(f"  - {item.get('name', 'unknown')}: {item.get('reason', 'unknown')}")
    if metric_analysis:
        md.append("- Target margin snapshot:")
        for item in metric_analysis:
            md.append(
                f"  - {item['name']}: status={item['status']}, "
                f"actual={format_num(item['actual'])}, margin={item['margin']}"
            )
    if (
        not eval_pass
        and not evaluation.get("gaps")
        and not evaluation.get("missing_metrics")
        and not evaluation.get("failed_metrics")
    ):
        md.append("- Evaluation failed without explicit gaps; inspect metric extraction and rule mappings.")

    md.append("")
    md.append("### 4.2 Figures")
    if paths["schematic_path"].exists():
        md.append(f"![Final schematic]({safe_relative_path(output_path, paths['schematic_path'])})")
    else:
        md.append("- Schematic image missing.")
    if plot_paths:
        for candidate in plot_paths:
            md.append(f"![Simulation plot]({safe_relative_path(output_path, candidate)})")
    else:
        md.append("- Simulation plot missing.")

    md.append("")
    md.append("### 4.3 Iteration Convergence")
    if iter_rows:
        headers = ["Iteration", "Pass"] + target_metric_names[:5]
        rows = []
        for row in iter_rows:
            rows.append(
                [
                    row["iteration"],
                    "PASS" if row["pass"] else "FAIL",
                ]
                + [format_num(row["metrics"].get(name)) for name in target_metric_names[:5]]
            )
        md.append("")
        md.append(markdown_table(headers, rows))
        md.append("")
    else:
        md.append("No iteration history found.")

    md.append("")
    md.append("## 5. Design Evolution")
    if value_changes:
        md.append("- Parameter updates from first tracked iteration to final design:")
        for item in value_changes[:40]:
            md.append(f"  - {item}")
    else:
        md.append("- No tracked component value changes found (or iteration baseline missing).")

    md.append("")
    md.append("## 6. Risks and Follow-up")
    md.append("- Verify corner cases (temperature/process/supply variation) if not already covered.")
    md.append("- Add noise/stability/linearity checks when RF constraints require them.")
    md.append("- Keep parameter-completeness validation enabled to prevent ambiguous netlists.")

    if parse_warnings:
        md.append("")
        md.append("## Appendix: Parser Warnings")
        for warning in parse_warnings:
            md.append(f"- {warning}")

    return "\n".join(md).strip() + "\n"


def main() -> int:
    args = build_parser().parse_args()
    result = {"ok": False, "report_path": "", "warnings": [], "error": ""}
    try:
        paths = resolve_paths(args)
    except ValueError as exc:
        result["error"] = str(exc)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    spec = read_json(paths["spec_path"])
    metrics_json = read_json(paths["metrics_path"])
    evaluation = read_json(paths["evaluation_path"])
    components, parse_warnings = parse_components(paths["netlist_path"])

    title = args.title.strip()
    if not title:
        title = f"Design Report - {paths['output_path'].parents[1].name}"

    report = build_report(
        title=title,
        paths=paths,
        spec=spec,
        metrics_json=metrics_json,
        evaluation=evaluation,
        components=components,
        parse_warnings=parse_warnings,
    )
    paths["output_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["output_path"].write_text(report, encoding="utf-8")

    result["ok"] = True
    result["report_path"] = str(paths["output_path"])
    result["warnings"] = parse_warnings
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
