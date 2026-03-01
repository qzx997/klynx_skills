# Repository Guidelines

## Project Structure & Module Organization
This repository currently contains one skill package: `skills/circuit-design-ngspice/`.
- `scripts/`: Python CLI tools for simulation runs, metric extraction, validation, patching, and reporting.
- `assets/templates/`: starter netlists (`*.cir`) for op-amp, filter, oscillator, buck/LDO, and RF benches.
- `assets/benchmarks/fullstack_suite.json`: regression case definitions.
- `references/`: metric schema, model registry, scoreboards, and design references.
- `agents/openai.yaml`: agent integration metadata.

Keep new automation in `scripts/`, reusable data in `references/`, and example netlists in `assets/templates/`.

## Build, Test, and Development Commands
No build system is defined; development is script-first with Python 3.
- `python3 -m py_compile skills/circuit-design-ngspice/scripts/*.py`: quick syntax check for all scripts.
- `python3 skills/circuit-design-ngspice/scripts/probe_meas_capabilities.py --output-path /tmp/probe.json`: verify local ngspice measurement support.
- `python3 skills/circuit-design-ngspice/scripts/run_ngspice.py --netlist-path skills/circuit-design-ngspice/assets/templates/rc_filter.cir --work-dir /tmp/ngspice-run`: run a single batch simulation.
- `python3 skills/circuit-design-ngspice/scripts/run_full_regression.py --work-root /tmp/ngspice-regression`: run benchmark regression and update results payloads.

## Coding Style & Naming Conventions
- Use Python 3 with 4-space indentation and PEP 8 style.
- Prefer standard library modules (`argparse`, `json`, `pathlib`, `subprocess`) unless a dependency is justified.
- Keep scripts CLI-oriented: expose options via `argparse`, return machine-readable JSON where possible.
- Naming patterns:
  - files/functions: `snake_case`
  - constants: `UPPER_SNAKE_CASE`
  - output artifacts: descriptive lowercase names (for example `metrics.json`, `evaluation.json`).

## Testing Guidelines
Testing is regression-driven rather than unit-test driven.
- Validate edited scripts with `py_compile`.
- For behavior changes, run at least one focused flow (`run_ngspice.py` or `run_dual_analysis.py`) and one schema/spec check (`validate_metric_schema.py`).
- For cross-domain changes, run `run_full_regression.py` against `assets/benchmarks/fullstack_suite.json`.
- Store temporary test outputs under `/tmp` or `work/<job_id>/`; do not commit generated runtime artifacts.

## Commit & Pull Request Guidelines
Git history is currently minimal (`circuit design and simulation based on ngspice`), so keep commit subjects short and descriptive.
- Recommended format: `<area>: <imperative summary>` (example: `scripts: tighten metric schema checks`).
- PRs should include:
  - purpose and impacted scripts/paths,
  - exact commands run for validation,
  - before/after metric or report impact when logic changes.
