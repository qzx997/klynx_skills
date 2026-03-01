# Klynx Skills Repository

This repository contains reusable Codex skills for engineering workflows.  
Right now, it includes one skill focused on **circuit design and simulation with ngspice**.

## Current Skill

### `circuit-design-ngspice`
Path: `skills/circuit-design-ngspice/`

This skill provides a simulation-feedback loop for analog/RF/power circuit work:
- netlist validation and patching
- ngspice batch execution (single and split AC/power modes)
- metric extraction and spec evaluation
- report export and schematic SVG rendering

## Repository Layout

```text
skills/circuit-design-ngspice/
  scripts/      # Python CLI tools
  assets/       # starter netlists, skins, benchmark suite
  references/   # schema, model registry, design references
  agents/       # agent integration metadata
```

## Environment Requirements

- Python 3.9+ (recommended 3.10+)
- `ngspice` available in `PATH` or configured explicitly
- Node.js + npm (for `netlistsvg`)

Optional (for better report export):
- `python-markdown` (`pip install markdown`)
- `weasyprint` (`pip install weasyprint`) or `wkhtmltopdf` / Chromium for PDF output

## Install & Configure

### 1. Clone and enter repo
```bash
git clone <your-repo-url>
cd klynx_skills
```

### 2. Configure `ngspice` (required)
Choose one:

- CLI override per run:
```bash
python3 skills/circuit-design-ngspice/scripts/run_ngspice.py --ngspice-bin /usr/bin/ngspice ...
```

- Environment variable:
```bash
export NGSPICE_BIN=/usr/bin/ngspice
```

- Skill config file (`skills/circuit-design-ngspice/tool_paths.json`):
```json
{
  "ngspice_bin": "/usr/bin/ngspice"
}
```

### 3. Install `netlistsvg` (recommended)
```bash
npm install -g netlistsvg
```
Verify:
```bash
netlistsvg --help
```

## Quick Start

Run a sample simulation:
```bash
python3 skills/circuit-design-ngspice/scripts/run_ngspice.py \
  --netlist-path skills/circuit-design-ngspice/assets/templates/rc_filter.cir \
  --work-dir /tmp/ngspice-run
```

Convert netlist to JSON and render SVG:
```bash
python3 skills/circuit-design-ngspice/scripts/netlist_to_json.py \
  --netlist-path skills/circuit-design-ngspice/assets/templates/rc_filter.cir \
  --json-path /tmp/rc_filter.json

python3 skills/circuit-design-ngspice/scripts/render_netlistsvg.py \
  --json-path /tmp/rc_filter.json \
  --svg-path /tmp/rc_filter.svg \
  --skin-profile analog
```

## Development Status

This project is **actively under development**. APIs, script interfaces, and references may evolve.

## Contributing

Contributions are welcome. You can help by:
- improving simulation and extraction scripts
- adding benchmarks/templates/references
- fixing bugs and improving docs

Please open an issue or pull request with:
- a clear problem statement
- reproduction or validation commands
- expected vs actual behavior

