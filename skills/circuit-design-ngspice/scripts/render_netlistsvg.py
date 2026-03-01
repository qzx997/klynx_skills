#!/usr/bin/env python3
"""Render an SVG diagram by calling netlistsvg CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render schematic SVG with netlistsvg")
    parser.add_argument("--json-path", required=True, help="Input JSON path")
    parser.add_argument("--svg-path", required=True, help="Output SVG path")
    parser.add_argument("--netlistsvg-bin", default="netlistsvg", help="netlistsvg executable")
    parser.add_argument(
        "--skin-profile",
        default="analog",
        choices=["analog", "default", "none"],
        help="Skin profile to use; analog is recommended for SPICE circuits",
    )
    parser.add_argument("--skin-path", default="", help="Explicit netlistsvg skin path")
    parser.add_argument("--timeout-sec", type=int, default=30, help="Process timeout")
    return parser


def resolve_skin_path(resolved_bin: str, skin_profile: str, skin_path: str) -> str:
    explicit = (skin_path or "").strip()
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return str(p) if p.exists() else ""
    if skin_profile == "none":
        return ""

    skin_file = f"{skin_profile}.svg"
    bin_dir = Path(resolved_bin).resolve().parent
    skill_root = Path(__file__).resolve().parents[1]
    candidates = [
        skill_root / "assets" / "skins" / skin_file,
        skill_root / skin_file,
        bin_dir / "node_modules" / "netlistsvg" / "lib" / skin_file,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return ""


def main() -> int:
    args = build_parser().parse_args()
    json_path = Path(args.json_path).resolve()
    svg_path = Path(args.svg_path).resolve()
    result = {"ok": False, "svg_path": str(svg_path), "skin_path": "", "stderr": ""}

    if not json_path.exists():
        result["stderr"] = f"json not found: {json_path}"
        print(json.dumps(result, ensure_ascii=False))
        return 1
    resolved_bin = shutil.which(args.netlistsvg_bin)
    if resolved_bin is None and os.name == "nt":
        # npm on Windows usually exposes a .cmd shim.
        resolved_bin = shutil.which(f"{args.netlistsvg_bin}.cmd")

    if resolved_bin is None:
        result["stderr"] = f"netlistsvg not found in PATH: {args.netlistsvg_bin}"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    skin_path = resolve_skin_path(resolved_bin, args.skin_profile, args.skin_path)
    if args.skin_profile != "none" and not skin_path:
        result["stderr"] = (
            f"skin not found for profile={args.skin_profile}. "
            "Pass --skin-path explicitly or use --skin-profile none."
        )
        print(json.dumps(result, ensure_ascii=False))
        return 1

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [resolved_bin, str(json_path), "-o", str(svg_path)]
    if skin_path:
        cmd.extend(["--skin", skin_path])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(1, args.timeout_sec),
            check=False,
        )
    except FileNotFoundError as exc:
        result["stderr"] = f"netlistsvg executable error: {exc}"
        print(json.dumps(result, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired:
        result["stderr"] = f"netlistsvg timeout after {args.timeout_sec}s"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    result["ok"] = completed.returncode == 0 and svg_path.exists()
    result["skin_path"] = skin_path
    result["stderr"] = completed.stderr.strip()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
