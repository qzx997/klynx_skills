#!/usr/bin/env python3
"""Convert SPICE-like netlists to JSON artifacts.

Supported formats:
- netlistsvg: analog-skin-friendly netlistsvg JSON.
- spice-components-v1: parsed component list for debugging/auditing.
"""

from __future__ import annotations

import argparse
import json
import re
import math
from pathlib import Path


TYPE_BY_PREFIX = {
    "R": "resistor",
    "C": "capacitor",
    "L": "inductor",
    "V": "voltage_source",
    "I": "current_source",
    "D": "diode",
    "Q": "bjt",
    "M": "mosfet",
    "X": "subckt",
    "E": "vcvs",
    "F": "cccs",
    "G": "vccs",
    "H": "ccvs",
    "B": "behavioral",
    "A": "xspice",
    "U": "ic",
}

COMMENT_PREFIXES = ("*", ";", "//", "$")
PARAM_REQUIRED_TYPES = {
    "resistor",
    "capacitor",
    "inductor",
    "voltage_source",
    "current_source",
    "diode",
    "bjt",
    "mosfet",
}
PARAM_ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")
PARAM_REF_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SCALAR_TOKEN_RE = re.compile(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([A-Za-z\u00b5\u03a9]*)\s*$")
EXPR_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
EXPR_SAFE_RE = re.compile(r"^[0-9eE+\-*/().\s]+$")

DISPLAY_UNIT_BY_TYPE = {
    "resistor": "ohm",
    "capacitor": "F",
    "inductor": "H",
}
SOURCE_UNIT_BY_TYPE = {
    "voltage_source": "V",
    "current_source": "A",
}
ENG_PREFIXES = ("meg", "t", "g", "k", "m", "u", "n", "p", "f")
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


def infer_type(name: str) -> str:
    lead = name[:1].upper()
    return TYPE_BY_PREFIX.get(lead, "unknown")


def strip_inline_comment(line: str) -> str:
    text = line.lstrip("\ufeff")
    stripped = text.lstrip()
    if not stripped:
        return ""
    if stripped.startswith(COMMENT_PREFIXES):
        return ""

    in_quote = False
    escaped = False
    out = []
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == "'":
            in_quote = not in_quote
            out.append(ch)
            continue
        if ch == ";" and not in_quote:
            break
        out.append(ch)
    return "".join(out).strip()


def merge_continuation_lines(text: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    current = ""
    current_line = 0

    for line_no, raw in enumerate(text.splitlines(), start=1):
        raw = raw.lstrip("\ufeff")
        stripped = raw.strip()

        if not stripped:
            if current:
                entries.append((current_line, current))
                current = ""
                current_line = 0
            continue

        if stripped.startswith("+"):
            cont = stripped[1:].strip()
            if current:
                if cont:
                    current = f"{current} {cont}"
            else:
                current = cont
                current_line = line_no
            continue

        if current:
            entries.append((current_line, current))
        current = raw.rstrip()
        current_line = line_no

    if current:
        entries.append((current_line, current))
    return entries


def parse_param_assignments(merged_lines: list[tuple[int, str]]) -> dict[str, str]:
    params: dict[str, str] = {}
    for _, line in merged_lines:
        stripped = strip_inline_comment(line)
        if not stripped:
            continue
        if not stripped.lower().startswith(".param"):
            continue
        body = stripped[6:].strip()
        if not body:
            continue

        matches = list(PARAM_ASSIGN_RE.finditer(body))
        if not matches:
            continue

        for idx, match in enumerate(matches):
            name = match.group(1).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
            value = body[start:end].strip()
            if not name or not value:
                continue
            params[name.upper()] = value
    return params


def _resolve_param_name(name: str, params: dict[str, str], stack: set[str], depth: int) -> str:
    key = name.upper()
    if key not in params:
        return "{" + name + "}"
    if key in stack or depth > 12:
        return params[key]

    stack.add(key)
    base = params[key]
    resolved = PARAM_REF_RE.sub(
        lambda m: _resolve_param_name(m.group(1), params, stack, depth + 1),
        base,
    )
    stack.remove(key)
    return resolved


def resolve_param_refs(value: str, params: dict[str, str]) -> str:
    if not value or not params:
        return value

    resolved = value
    for _ in range(8):
        updated = PARAM_REF_RE.sub(
            lambda m: _resolve_param_name(m.group(1), params, set(), 0),
            resolved,
        )
        if updated == resolved:
            break
        resolved = updated
    return resolved


def apply_value_resolution(comp: dict, params: dict[str, str]) -> None:
    raw = str(comp.get("value", ""))
    resolved = resolve_param_refs(raw, params)
    resolved = resolve_braced_expression(resolved, params)
    comp["value_raw"] = raw
    comp["value_resolved"] = resolved
    comp["value"] = resolved


def _split_suffix(token_suffix: str) -> tuple[str, str]:
    suffix = token_suffix.strip().replace("µ", "u").replace("Ω", "ohm").lower()
    if not suffix:
        return "", ""
    for prefix in ENG_PREFIXES:
        if suffix.startswith(prefix):
            return prefix, suffix[len(prefix) :]
    return "", suffix


def parse_numeric_scalar(token: str) -> float | None:
    match = SCALAR_TOKEN_RE.match(token.strip())
    if not match:
        return None
    magnitude = float(match.group(1))
    suffix = match.group(2) or ""
    prefix, _unit_tail = _split_suffix(suffix)
    if prefix not in UNIT_SCALE:
        return None
    return magnitude * UNIT_SCALE[prefix]


def _resolve_param_numeric(
    name: str,
    params: dict[str, str],
    stack: set[str],
) -> float | None:
    key = name.upper()
    if key not in params:
        return None
    if key in stack:
        return None
    stack.add(key)

    raw = str(params[key]).strip()
    resolved = resolve_param_refs(raw, params).strip()

    numeric = parse_numeric_scalar(resolved)
    if numeric is not None:
        stack.remove(key)
        return numeric

    expr_numeric = evaluate_expression_numeric(resolved, params, stack)
    stack.remove(key)
    return expr_numeric


def evaluate_expression_numeric(
    expr_token: str,
    params: dict[str, str],
    stack: set[str] | None = None,
) -> float | None:
    token = expr_token.strip()
    if token.startswith("{") and token.endswith("}"):
        token = token[1:-1].strip()
    if not token:
        return None

    if stack is None:
        stack = set()

    substituted = token
    for ident in set(EXPR_IDENT_RE.findall(token)):
        lower = ident.lower()
        if lower in {"e", "pi"}:
            continue
        value = _resolve_param_numeric(ident, params, stack)
        if value is None:
            return None
        substituted = re.sub(rf"\b{re.escape(ident)}\b", f"({value})", substituted)

    substituted = substituted.replace("pi", str(math.pi)).replace("PI", str(math.pi))
    if not EXPR_SAFE_RE.match(substituted):
        return None
    try:
        return float(eval(substituted, {"__builtins__": {}}, {}))
    except Exception:
        return None


def resolve_braced_expression(value: str, params: dict[str, str]) -> str:
    token = value.strip()
    if not (token.startswith("{") and token.endswith("}")):
        return value
    numeric = evaluate_expression_numeric(token, params)
    if numeric is None:
        return value
    return f"{numeric:.6g}"


def format_value_for_display(comp_type: str, value: str) -> str:
    unit = DISPLAY_UNIT_BY_TYPE.get(comp_type, "")
    if not unit:
        src_unit = SOURCE_UNIT_BY_TYPE.get(comp_type, "")
        if src_unit:
            return format_source_value_for_display(value, src_unit)
        return value

    text = (value or "").strip()
    if not text:
        return value

    parts = text.split()
    candidate = text
    if len(parts) == 2:
        compact = f"{parts[0]}{parts[1]}"
        if SCALAR_TOKEN_RE.match(compact):
            candidate = compact
    elif len(parts) > 2:
        return value

    match = SCALAR_TOKEN_RE.match(candidate)
    if not match:
        return value

    magnitude = match.group(1)
    suffix = match.group(2) or ""
    prefix, unit_tail = _split_suffix(suffix)
    if unit_tail:
        return f"{magnitude}{suffix}"
    if prefix:
        return f"{magnitude}{prefix}{unit}"
    return f"{magnitude}{unit}"


def format_source_value_for_display(value: str, unit: str) -> str:
    text = (value or "").strip()
    if not text:
        return value

    # Plain scalar source declaration, e.g. "5" -> "5V".
    if SCALAR_TOKEN_RE.match(text):
        return format_scalar_token_with_unit(text, unit)

    # Normalize DC/AC scalar tokens, e.g. "DC 5 AC 0.01" -> "DC 5V AC 0.01V".
    dc_ac_re = re.compile(r"(?i)\b(DC|AC)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?[A-Za-z\u00b5\u03a9]*)")

    def _dc_ac_repl(match: re.Match) -> str:
        key = match.group(1).upper()
        token = match.group(2)
        formatted = format_scalar_token_with_unit(token, unit)
        return f"{key} {formatted}"

    text = dc_ac_re.sub(_dc_ac_repl, text)
    return text


def format_scalar_token_with_unit(token: str, unit: str) -> str:
    match = SCALAR_TOKEN_RE.match(token.strip())
    if not match:
        return token
    magnitude = match.group(1)
    suffix = match.group(2) or ""
    prefix, unit_tail = _split_suffix(suffix)
    if unit_tail:
        return f"{magnitude}{suffix}"
    if prefix:
        return f"{magnitude}{prefix}{unit}"
    return f"{magnitude}{unit}"


def is_control_directive(line: str, directive: str) -> bool:
    stripped = strip_inline_comment(line)
    return bool(stripped) and stripped.lower().startswith(directive)


def parse_component_line(line: str, line_no: int, warnings: list[str]) -> dict | None:
    stripped = strip_inline_comment(line)
    if not stripped:
        return None
    if stripped.startswith(".") or stripped.startswith("+"):
        return None

    tokens = stripped.split()
    if len(tokens) < 3:
        return None

    name = tokens[0]
    prefix = name[:1].upper()
    if not prefix.isalpha():
        warnings.append(f"line {line_no}: invalid instance prefix for token '{name}'")
        return None

    comp_type = infer_type(name)
    if comp_type == "unknown":
        warnings.append(
            f"line {line_no}: unsupported or ambiguous instance prefix '{prefix}' in '{name}'"
        )
        return None

    nodes: list[str] = []
    value_tokens: list[str] = []

    if comp_type in {"resistor", "capacitor", "inductor", "diode"}:
        if len(tokens) < 3:
            warnings.append(f"line {line_no}: {name} has too few tokens for 2-terminal element")
            return None
        nodes = tokens[1:3]
        value_tokens = tokens[3:]
    elif comp_type in {"voltage_source", "current_source"}:
        if len(tokens) < 3:
            warnings.append(f"line {line_no}: {name} has too few tokens for source")
            return None
        nodes = tokens[1:3]
        value_tokens = tokens[3:]
    elif comp_type == "bjt":
        if len(tokens) < 4:
            warnings.append(f"line {line_no}: {name} has too few tokens for BJT nodes")
            return None
        nodes = tokens[1:4]  # C B E
        value_tokens = tokens[4:]
    elif comp_type == "mosfet":
        if len(tokens) < 5:
            warnings.append(f"line {line_no}: {name} has too few tokens for MOSFET nodes")
            return None
        nodes = tokens[1:5]  # D G S B
        value_tokens = tokens[5:]
    elif comp_type in {"vcvs", "vccs"}:
        if len(tokens) < 5:
            warnings.append(f"line {line_no}: {name} has too few tokens for controlled source nodes")
            return None
        nodes = tokens[1:5]  # OUT+ OUT- CTRL+ CTRL-
        value_tokens = tokens[5:]
    elif comp_type in {"cccs", "ccvs"}:
        if len(tokens) < 3:
            warnings.append(f"line {line_no}: {name} has too few tokens for controlled source nodes")
            return None
        nodes = tokens[1:3]  # OUT+ OUT-
        value_tokens = tokens[3:]
    elif comp_type == "subckt":
        if len(tokens) < 3:
            warnings.append(f"line {line_no}: {name} has too few tokens for subckt call")
            return None
        nodes = tokens[1:-1]
        value_tokens = [tokens[-1]]
    else:
        nodes = tokens[1:-1] if len(tokens) > 3 else tokens[1:]
        value_tokens = tokens[-1:] if len(tokens) > 1 else []

    value = " ".join(value_tokens).strip()
    param_missing = comp_type in PARAM_REQUIRED_TYPES and not value
    if param_missing:
        warnings.append(
            f"line {line_no}: {name} has missing parameter/value field; emitting '<missing_param>'"
        )
        value = "<missing_param>"

    return {
        "name": name,
        "type": comp_type,
        "nodes": nodes,
        "value": value,
        "param_missing": param_missing,
        "raw": stripped,
        "line_no": line_no,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert netlist to JSON")
    parser.add_argument("--netlist-path", required=True, help="Input netlist path")
    parser.add_argument("--json-path", required=True, help="Output JSON path")
    parser.add_argument(
        "--input-node",
        default="",
        help="Optional explicit input node name for netlistsvg terminal rendering",
    )
    parser.add_argument(
        "--output-node",
        default="",
        help="Optional explicit output node name for netlistsvg terminal rendering",
    )
    parser.add_argument(
        "--format",
        default="netlistsvg",
        choices=["netlistsvg", "spice-components-v1"],
        help="Output JSON format",
    )
    parser.add_argument(
        "--show-all-netnames",
        action="store_true",
        help="Show labels for all nets. By default, internal auto-generated nets are hidden.",
    )
    return parser


def sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "unnamed"


def rail_symbol_for_node(node: str) -> str | None:
    value = node.strip().lower()
    if value in {"0", "gnd", "agnd", "dgnd", "pgnd"} or value.endswith("gnd"):
        return "gnd"
    if value.startswith("vcc") or value.startswith("vdd"):
        return "vcc"
    if value.startswith("vee") or value.startswith("vss"):
        return "vee"
    return None


def bjt_symbol(value: str) -> str:
    return "q_pnp" if "pnp" in value.lower() else "q_npn"


def mos_symbol(value: str) -> str:
    lower = value.lower()
    if "pmos" in lower or lower.startswith("p"):
        return "m_pmos"
    return "m_nmos"


def generic_ports(comp: dict) -> tuple[list[str], dict[str, str]]:
    comp_type = comp["type"]
    node_count = len(comp["nodes"])
    if comp_type == "mosfet":
        pin_names = ["D", "G", "S", "B"][:node_count]
        directions = {"D": "input", "G": "input", "S": "output", "B": "input"}
        return pin_names, {pin: directions.get(pin, "input") for pin in pin_names}
    if comp_type in {"vcvs", "vccs"}:
        pin_names = ["OUTP", "OUTN", "CTRLP", "CTRLN"][:node_count]
        directions = {"OUTP": "output", "OUTN": "output", "CTRLP": "input", "CTRLN": "input"}
        return pin_names, {pin: directions.get(pin, "input") for pin in pin_names}
    if comp_type in {"cccs", "ccvs"}:
        pin_names = ["OUTP", "OUTN"][:node_count]
        directions = {"OUTP": "output", "OUTN": "output"}
        return pin_names, {pin: directions.get(pin, "input") for pin in pin_names}

    in_count = max(1, node_count - 1) if node_count > 1 else 1
    pin_names = [f"in{i}" for i in range(in_count)]
    if node_count > in_count:
        pin_names.extend(f"out{i}" for i in range(node_count - in_count))
    directions = {name: ("input" if name.startswith("in") else "output") for name in pin_names}
    return pin_names[:node_count], directions


def choose_two_terminal_orientation(nodes: list[str]) -> str:
    if len(nodes) != 2:
        return "v"
    a = str(nodes[0])
    b = str(nodes[1])
    a_rail = rail_symbol_for_node(a) is not None
    b_rail = rail_symbol_for_node(b) is not None
    if a_rail ^ b_rail:
        return "v"
    return "h"


def should_hide_netname(node: str, important_nodes: set[str]) -> bool:
    lower = node.strip().lower()
    if not lower:
        return False
    if node in important_nodes:
        return False
    if rail_symbol_for_node(node) is not None:
        return False
    if lower in {"in", "out", "vin", "vout", "rf_in", "rf_out", "input", "output"}:
        return False
    if lower.startswith(("in", "out", "vin", "vout", "rf_in", "rf_out")):
        return False

    if lower.startswith(("net_", "node_", "int_", "x_")):
        return True
    if lower.startswith("n_"):
        return True
    if re.fullmatch(r"n\d+", lower):
        return True
    if re.fullmatch(r"[a-z]\d+", lower):
        return True
    if lower.startswith("n") and ("_" in lower or any(ch.isdigit() for ch in lower[1:])):
        return True
    return False


def component_to_cell(comp: dict, bit_for: callable) -> tuple[str, dict]:
    comp_type = comp["type"]
    nodes = comp["nodes"]
    value = comp["value"]
    display_value = format_value_for_display(comp_type, value)
    ref = comp["name"]

    alias = "generic"
    pin_names: list[str]
    port_directions: dict[str, str]
    if comp_type == "resistor":
        alias = f"r_{choose_two_terminal_orientation(nodes)}"
        pin_names = ["A", "B"]
        port_directions = {"A": "input", "B": "output"}
    elif comp_type == "capacitor":
        alias = f"c_{choose_two_terminal_orientation(nodes)}"
        pin_names = ["A", "B"]
        port_directions = {"A": "input", "B": "output"}
    elif comp_type == "inductor":
        alias = f"l_{choose_two_terminal_orientation(nodes)}"
        pin_names = ["A", "B"]
        port_directions = {"A": "input", "B": "output"}
    elif comp_type == "voltage_source":
        alias = "v"
        pin_names = ["+", "-"]
        port_directions = {"+": "output", "-": "input"}
    elif comp_type == "current_source":
        alias = "i"
        pin_names = ["+", "-"]
        port_directions = {"+": "output", "-": "input"}
    elif comp_type == "diode":
        alias = f"d_{choose_two_terminal_orientation(nodes)}"
        pin_names = ["+", "-"]
        port_directions = {"+": "input", "-": "output"}
    elif comp_type == "bjt":
        alias = bjt_symbol(value)
        pin_names = ["C", "B", "E"]
        port_directions = {"C": "input", "B": "input", "E": "output"}
    elif comp_type == "mosfet":
        alias = mos_symbol(value)
        pin_names = ["D", "G", "S", "B"]
        port_directions = {"D": "input", "G": "input", "S": "output", "B": "input"}
    else:
        pin_names, port_directions = generic_ports(comp)

    pin_names = pin_names[: len(nodes)]
    connections = {pin: [bit_for(node)] for pin, node in zip(pin_names, nodes)}

    cell = {
        "type": alias,
        "port_directions": {pin: port_directions.get(pin, "input") for pin in pin_names},
        "connections": connections,
        "attributes": {
            "ref": ref,
            "value": display_value,
            "value_spice": value,
            "value_raw": comp.get("value_raw", value),
            "raw": comp["raw"],
            "spice_type": comp_type,
            "line_no": comp.get("line_no"),
            "param_missing": bool(comp.get("param_missing", False)),
        },
    }
    return ref, cell


def add_rail_symbols(cells: dict, node_to_bit: dict[str, int]) -> None:
    existing = set(cells)
    for node, bit in sorted(node_to_bit.items(), key=lambda item: item[1]):
        rail = rail_symbol_for_node(node)
        if rail is None:
            continue
        base = f"{rail}_{sanitize_id(node)}"
        name = base
        suffix = 2
        while name in existing:
            name = f"{base}_{suffix}"
            suffix += 1
        existing.add(name)
        cells[name] = {
            "type": rail,
            "port_directions": {"A": "input" if rail in {"gnd", "vee"} else "output"},
            "connections": {"A": [bit]},
            "attributes": {"name": node},
        }


def is_ground_node(node: str) -> bool:
    return rail_symbol_for_node(node) == "gnd"


def infer_io_nodes(
    components: list[dict],
    *,
    explicit_input: str,
    explicit_output: str,
    warnings: list[str],
) -> tuple[str | None, str | None]:
    nodes_in_order: list[str] = []
    seen_nodes: set[str] = set()

    def remember(node: str) -> None:
        if node not in seen_nodes:
            seen_nodes.add(node)
            nodes_in_order.append(node)

    for comp in components:
        for node in comp.get("nodes", []):
            remember(str(node))

    if not nodes_in_order:
        return None, None

    lower_to_original: dict[str, str] = {}
    for node in nodes_in_order:
        lower_to_original.setdefault(node.lower(), node)

    explicit_in = explicit_input.strip()
    explicit_out = explicit_output.strip()
    if explicit_in and explicit_in not in seen_nodes:
        warnings.append(f"explicit input node not found in netlist, ignored: {explicit_in}")
        explicit_in = ""
    if explicit_out and explicit_out not in seen_nodes:
        warnings.append(f"explicit output node not found in netlist, ignored: {explicit_out}")
        explicit_out = ""

    source_positive: list[str] = []
    for comp in components:
        ctype = str(comp.get("type", ""))
        if ctype not in {"voltage_source", "current_source"}:
            continue
        nodes = [str(n) for n in comp.get("nodes", [])]
        if not nodes:
            continue
        if not is_ground_node(nodes[0]):
            source_positive.append(nodes[0])

    preferred_in_names = ("in", "vin", "input", "rf_in")
    preferred_out_names = ("out", "vout", "output", "rf_out")

    def pick_named(preferred: tuple[str, ...], reject: set[str]) -> str | None:
        for name in preferred:
            match = lower_to_original.get(name)
            if match and match not in reject:
                return match
        return None

    in_node = explicit_in or pick_named(preferred_in_names, set())
    if in_node is None:
        for node in source_positive:
            if node not in {in_node}:
                in_node = node
                break
    if in_node is None:
        in_node = next((n for n in nodes_in_order if not is_ground_node(n)), None)

    out_node = explicit_out or pick_named(preferred_out_names, {in_node} if in_node else set())
    if out_node is None:
        load_candidates: list[str] = []
        for comp in components:
            ctype = str(comp.get("type", ""))
            if ctype != "resistor":
                continue
            nodes = [str(n) for n in comp.get("nodes", [])]
            if len(nodes) < 2:
                continue
            a, b = nodes[0], nodes[1]
            other = None
            if is_ground_node(a) and not is_ground_node(b):
                other = b
            elif is_ground_node(b) and not is_ground_node(a):
                other = a
            if other and other != in_node:
                load_candidates.append(other)
        if load_candidates:
            out_node = load_candidates[-1]

    if out_node is None:
        for node in reversed(nodes_in_order):
            if is_ground_node(node):
                continue
            if in_node and node == in_node:
                continue
            out_node = node
            break

    if in_node and out_node and in_node == out_node:
        for node in reversed(nodes_in_order):
            if is_ground_node(node) or node == in_node:
                continue
            out_node = node
            break

    return in_node, out_node


def build_netlistsvg_payload(
    netlist_path: Path,
    components: list[dict],
    warnings: list[str],
    *,
    explicit_input: str,
    explicit_output: str,
    show_all_netnames: bool = False,
) -> dict:
    node_to_bit: dict[str, int] = {}
    next_bit = 1

    def bit_for(node: str) -> int:
        nonlocal next_bit
        if node not in node_to_bit:
            node_to_bit[node] = next_bit
            next_bit += 1
        return node_to_bit[node]

    for comp in components:
        for node in comp["nodes"]:
            bit_for(node)

    cells: dict[str, dict] = {}
    for comp in components:
        name, cell = component_to_cell(comp, bit_for)
        if name in cells:
            name = f"{name}_{len(cells)+1}"
        cells[name] = cell

    inferred_input, inferred_output = infer_io_nodes(
        components,
        explicit_input=explicit_input,
        explicit_output=explicit_output,
        warnings=warnings,
    )
    add_rail_symbols(cells, node_to_bit)

    important_nodes = {n for n in (inferred_input, inferred_output) if n}
    netnames = {}
    for node, bit in sorted(node_to_bit.items(), key=lambda item: item[1]):
        hide_name = 0 if show_all_netnames else int(should_hide_netname(node, important_nodes))
        netnames[node] = {"hide_name": hide_name, "bits": [bit], "attributes": {}}

    ports: dict[str, dict] = {}
    if inferred_input and inferred_input in node_to_bit:
        ports["IN"] = {"direction": "input", "bits": [node_to_bit[inferred_input]]}
    if inferred_output and inferred_output in node_to_bit:
        ports["OUT"] = {"direction": "output", "bits": [node_to_bit[inferred_output]]}

    module_name = sanitize_id(netlist_path.stem) or "top"
    return {
        "creator": "circuit-design-ngspice/netlist_to_json.py",
        "source_netlist": str(netlist_path),
        "warnings": warnings,
        "io_inference": {
            "input_node": inferred_input,
            "output_node": inferred_output,
            "explicit_input_node": explicit_input.strip() or None,
            "explicit_output_node": explicit_output.strip() or None,
        },
        "param_issues": [
            {"name": c["name"], "line_no": c.get("line_no"), "raw": c.get("raw", "")}
            for c in components
            if c.get("param_missing")
        ],
        "modules": {
            module_name: {
                "ports": ports,
                "cells": cells,
                "netnames": netnames,
            }
        },
    }


def main() -> int:
    args = build_parser().parse_args()
    netlist_path = Path(args.netlist_path).resolve()
    json_path = Path(args.json_path).resolve()

    result = {"ok": False, "json_path": str(json_path), "error": "", "warnings": []}
    if not netlist_path.exists():
        result["error"] = f"netlist not found: {netlist_path}"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    warnings: list[str] = []
    components = []
    nodes = set()
    content = netlist_path.read_text(encoding="utf-8-sig", errors="ignore")
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
        if not comp:
            continue
        apply_value_resolution(comp, params)
        components.append(comp)
        for node in comp["nodes"]:
            nodes.add(node)

    if args.format == "spice-components-v1":
        inferred_input, inferred_output = infer_io_nodes(
            components,
            explicit_input=args.input_node,
            explicit_output=args.output_node,
            warnings=warnings,
        )
        payload = {
            "format": "spice-components-v1",
            "source_netlist": str(netlist_path),
            "components": components,
            "nodes": sorted(nodes),
            "params": params,
            "io_inference": {
                "input_node": inferred_input,
                "output_node": inferred_output,
                "explicit_input_node": args.input_node.strip() or None,
                "explicit_output_node": args.output_node.strip() or None,
            },
            "warnings": warnings,
        }
    else:
        payload = build_netlistsvg_payload(
            netlist_path,
            components,
            warnings,
            explicit_input=args.input_node,
            explicit_output=args.output_node,
            show_all_netnames=bool(args.show_all_netnames),
        )

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result["ok"] = True
    result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
