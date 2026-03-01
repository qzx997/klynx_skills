#!/usr/bin/env python3
"""Plot ngspice simulation results from log files into SVG charts.

This script is dependency-light (stdlib only) and focuses on common batch
flows used by this skill:
- AC table -> magnitude (dB) vs frequency
- TRAN table -> value vs time
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
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot ngspice results from log to SVG")
    parser.add_argument("--log-path", required=True, help="Path to ngspice log")
    parser.add_argument("--svg-path", required=True, help="Output SVG path")
    parser.add_argument(
        "--plot-mode",
        default="auto",
        choices=["auto", "ac", "tran"],
        help="Plot mode. auto tries AC first, then TRAN.",
    )
    parser.add_argument("--title", default="", help="Plot title")
    parser.add_argument("--min-points", type=int, default=5, help="Minimum points required")
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
        # Stop once table has started and no longer matches row formats.
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


def parse_ac_points(lines: list[str]) -> list[tuple[float, float]]:
    rows = find_analysis_table(lines, "ac analysis")
    points: list[tuple[float, float]] = []
    for x, y_real, y_imag in rows:
        if y_imag is None:
            mag = abs(y_real)
        else:
            mag = math.sqrt(y_real * y_real + y_imag * y_imag)
        mag = max(mag, 1e-30)
        y_db = 20.0 * math.log10(mag)
        points.append((x, y_db))
    return points


def parse_tran_points(lines: list[str]) -> list[tuple[float, float]]:
    rows = find_analysis_table(lines, "transient analysis")
    points: list[tuple[float, float]] = []
    for x, y_real, _ in rows:
        points.append((x, y_real))
    return points


def si_format(value: float) -> str:
    if value == 0:
        return "0"
    prefixes = [
        (-12, "p"),
        (-9, "n"),
        (-6, "u"),
        (-3, "m"),
        (0, ""),
        (3, "k"),
        (6, "M"),
        (9, "G"),
    ]
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp = max(min(exp, 9), -12)
    factor = 10 ** exp
    scaled = value / factor
    suffix = dict(prefixes).get(exp, "")
    return f"{scaled:.3g}{suffix}"


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def render_svg(
    points: list[tuple[float, float]],
    *,
    title: str,
    x_label: str,
    y_label: str,
    log_x: bool,
) -> str:
    width = 1000
    height = 620
    margin_left = 90
    margin_right = 30
    margin_top = 50
    margin_bottom = 80

    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    if log_x:
        xs_t = [math.log10(max(x, 1e-30)) for x in xs]
        x_min = min(xs_t)
        x_max = max(xs_t)
    else:
        xs_t = xs
        x_min = min(xs)
        x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)

    if x_max == x_min:
        x_max = x_min + 1.0
    if y_max == y_min:
        y_max = y_min + 1.0

    def map_x(x_raw: float) -> float:
        x_use = math.log10(max(x_raw, 1e-30)) if log_x else x_raw
        return margin_left + (x_use - x_min) / (x_max - x_min) * plot_w

    def map_y(y: float) -> float:
        return margin_top + (1.0 - (y - y_min) / (y_max - y_min)) * plot_h

    polyline_points = " ".join(f"{map_x(x):.2f},{map_y(y):.2f}" for x, y in points)

    x_ticks = 6
    y_ticks = 6
    elements: list[str] = []
    elements.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" stroke="none" />'
    )
    elements.append(
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_w}" height="{plot_h}" '
        'fill="#fafafa" stroke="#dddddd" />'
    )

    for i in range(x_ticks + 1):
        t = i / x_ticks
        x = margin_left + t * plot_w
        elements.append(
            f'<line x1="{x:.2f}" y1="{margin_top}" x2="{x:.2f}" y2="{margin_top + plot_h}" '
            'stroke="#eeeeee" />'
        )
        if log_x:
            x_val = 10 ** (x_min + t * (x_max - x_min))
            label = si_format(x_val)
        else:
            x_val = x_min + t * (x_max - x_min)
            label = f"{x_val:.3g}"
        elements.append(
            f'<text x="{x:.2f}" y="{height - margin_bottom + 22}" text-anchor="middle" '
            'font-size="12" fill="#333333">'
            f"{svg_escape(label)}</text>"
        )

    for i in range(y_ticks + 1):
        t = i / y_ticks
        y = margin_top + (1.0 - t) * plot_h
        elements.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_w}" y2="{y:.2f}" '
            'stroke="#eeeeee" />'
        )
        y_val = y_min + t * (y_max - y_min)
        elements.append(
            f'<text x="{margin_left - 10}" y="{y + 4:.2f}" text-anchor="end" '
            'font-size="12" fill="#333333">'
            f"{svg_escape(f'{y_val:.3g}')}</text>"
        )

    elements.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" '
        f'y2="{margin_top + plot_h}" stroke="#444444" />'
    )
    elements.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" '
        f'y2="{margin_top + plot_h}" stroke="#444444" />'
    )
    elements.append(
        f'<polyline points="{polyline_points}" fill="none" stroke="#0b66c3" stroke-width="2.0" />'
    )

    title_text = title.strip() or "Simulation Plot"
    elements.append(
        f'<text x="{width/2:.2f}" y="26" text-anchor="middle" font-size="18" fill="#111111">'
        f"{svg_escape(title_text)}</text>"
    )
    elements.append(
        f'<text x="{width/2:.2f}" y="{height - 20}" text-anchor="middle" font-size="13" '
        f'fill="#222222">{svg_escape(x_label)}</text>'
    )
    elements.append(
        f'<text x="22" y="{height/2:.2f}" text-anchor="middle" font-size="13" fill="#222222" '
        'transform="rotate(-90 22 '
        f'{height/2:.2f})">{svg_escape(y_label)}</text>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elements)
        + "</svg>\n"
    )


def main() -> int:
    args = build_parser().parse_args()
    log_path = Path(args.log_path).resolve()
    svg_path = Path(args.svg_path).resolve()
    result = {
        "ok": False,
        "plot_path": str(svg_path),
        "mode": "",
        "points": 0,
        "warnings": [],
        "error": "",
    }

    if not log_path.exists():
        result["error"] = f"log not found: {log_path}"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    lines = log_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    mode = args.plot_mode
    points: list[tuple[float, float]] = []
    x_label = ""
    y_label = ""
    log_x = False

    if mode in {"auto", "ac"}:
        ac_points = parse_ac_points(lines)
        if ac_points:
            points = ac_points
            mode = "ac"
            x_label = "Frequency (Hz)"
            y_label = "Magnitude (dB)"
            log_x = True
        elif args.plot_mode == "ac":
            result["warnings"].append("no AC table found in log")

    if not points and mode in {"auto", "tran"}:
        tran_points = parse_tran_points(lines)
        if tran_points:
            points = tran_points
            mode = "tran"
            x_label = "Time (s)"
            y_label = "Value"
            log_x = False
        elif args.plot_mode == "tran":
            result["warnings"].append("no transient table found in log")

    if len(points) < max(2, args.min_points):
        if not result["warnings"]:
            result["warnings"].append("insufficient points for plotting")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    svg_text = render_svg(points, title=args.title, x_label=x_label, y_label=y_label, log_x=log_x)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg_text, encoding="utf-8")

    result["ok"] = True
    result["mode"] = mode
    result["points"] = len(points)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
