---
name: circuit-design-ngspice
description: Design and iterate circuits with ngspice by running a simulation-feedback loop that writes netlists, executes batch simulation, parses measured metrics, applies netlist patches, and exports final artifacts for JSON and netlistsvg rendering. Use when tasks require target-driven circuit tuning, repeated ngspice runs, or automated netlist refinement toward spec compliance.
---

# Circuit Design Ngspice

## Overview

Run a closed-loop circuit design workflow with ngspice. The optimized flow supports:
- measurement capability probing,
- split AC/power simulation,
- parser-level failure diagnostics,
- mandatory component-parameter completeness checks,
- explicit schematic I/O terminal inference and validation,
- spec normalization across linear/dB metrics,
- passive filter topology references and metric packs,
- RF/LNA bench metric extraction (`S11/S22/K/NF/P1dB/IP3`),
- op-amp stability/transient extraction (`PM/UGF/SR/settling`),
- oscillator startup/frequency/amplitude extraction,
- Buck/LDO power quality/regulation extraction,
- PVT corner and Monte Carlo regression runners,
- model registry scan/validation/sync utilities,
- optional passive filter netlist synthesis helper,
- optional coordinate-descent auto tuning,
- optional RF target pack injection,
- automatic final report generation with architecture/results analysis.

## Required Inputs

Collect these fields before running the loop:
- Circuit type and topology intent.
- Required metrics and thresholds.
- Input conditions and sweep ranges.
- Hard constraints: max power, temp, cost, size, or component limits.
- Termination policy: `max_iter`, `patience`, timeout.
- Primitive-only policy (default required): use only `R/C/L/Q/M/D/V/I`; no black-box macros.

If any required input is missing, ask concise clarification questions before generating the first netlist.

## Workflow

1. Normalize requirement text into `work/<job_id>/spec.json`.
2. Run `scripts/normalize_spec.py` on `spec.json` to derive metric aliases and `targets_eval`.
3. (Optional) Apply metric pack with `scripts/apply_rf_metric_pack.py`.
4. Probe ngspice measurement compatibility via `scripts/probe_meas_capabilities.py`.
5. Initialize workspace folders: `work/<job_id>/active/`, `work/<job_id>/iterations/`, `work/<job_id>/final/`.
6. Create or update candidate netlist in `work/<job_id>/active/design.cir`.
7. Enforce strict parameter completeness on `active/design.cir` using `scripts/strict_param_check.py`, then run `scripts/validate_netlist_primitives.py`.
8. For each loop, allocate `work/<job_id>/iterations/iter_<NNN>/` and snapshot inputs there.
9. Validate the iteration netlist (primitive + parameter completeness) and save `iterations/iter_<NNN>/primitive_validation.json`.
10. Prefer split simulation using `scripts/run_dual_analysis.py` (AC + power runs) to avoid mixed-analysis `.meas` conflicts.
11. Parse metrics with `scripts/parse_results.py` and write `metrics.json` in the same iteration folder.
12. For specialized domains, run extractor scripts (`extract_rf_lna_metrics.py`, `extract_opamp_metrics.py`, `extract_oscillator_metrics.py`, `extract_power_metrics.py`) and merge into `metrics.json`.
13. Validate spec/metrics naming against `references/metric_schema.json` using `scripts/validate_metric_schema.py`.
14. Compare metrics to targets using `scripts/evaluate_against_spec.py` and write `evaluation.json`.
15. If fail, store patch plan and apply via `scripts/patch_netlist.py`.
16. Optional: run parameter-only coordinate descent with `scripts/auto_tune_netlist.py`.
17. Repeat until pass or termination criteria fire.
18. On pass, export JSON with `scripts/netlist_to_json.py`.
19. Render diagram with `scripts/render_netlistsvg.py` (analog skin, transistor/FET compatible).
20. Generate simulation plots with `scripts/plot_sim_results.py` into `final/plots/`.
21. Validate schematic I/O visibility with `scripts/validate_schematic_io.py`.
22. Optional robustness pass: run `scripts/run_pvt_sweep.py` and `scripts/run_monte_carlo.py`.
23. Optional model consistency gate: run `scripts/manage_model_library.py --action validate`.
24. Generate `final/report.md` with `scripts/generate_report.py`, then export `report.html`/`report.pdf` with `scripts/export_report_bundle.py`.
25. Audit report completeness using `scripts/audit_report_completeness.py`.
26. Optional whole-suite execution: run `scripts/run_full_regression.py` and diagnose failures via `scripts/diagnose_failures.py`.
27. Publish final bundle in `work/<job_id>/final/` and keep historical iterations under `work/<job_id>/iterations/`.

## Tool Usage Contract

Use only these script interfaces unless the user explicitly requests custom behavior.

Windows note:
- In PowerShell, avoid `>` for JSON artifacts (it may emit UTF-16).
- Prefer `... | Set-Content -Encoding utf8 <path>` to keep downstream JSON readers compatible.

### Normalize Spec
Command:
`python scripts/normalize_spec.py --spec-path <path> --output-path <path>`

Notes:
- Derives linear/dB counterparts by default.
- Adds `targets_eval` and `metric_aliases`.

### Apply Metric Pack
Command:
`python scripts/apply_rf_metric_pack.py --spec-path <path> --pack lna_basic --output-path <path>`

Notes:
- Adds optional RF/filter targets to spec.
- Can emit measurement snippet template with `--snippet-path`.
- Supported packs include:
  - `lna_basic`
  - `bpf_basic_50ohm`
  - `lpf_basic_50ohm`
  - `hpf_basic_50ohm`
  - `opamp_eval_basic`
  - `oscillator_eval_basic`
  - `power_eval_basic`

### Synthesize Filter Netlist
Command:
`python scripts/synthesize_filter_netlist.py --filter-type bpf --topology ladder --order 3 --f-low 20e6 --f-high 50e6 --z0 50 --output-path <path>`

Notes:
- Emits primitive-only passive filter starter netlists with explicit `in`/`out` nodes.
- Supports `lpf`, `hpf`, `bpf` and topologies `pi`, `t`, `ladder`.
- Uses Butterworth 3rd-order coefficients as baseline and emits parameterized `.param` values.

### Probe Measurement Capability
Command:
`python scripts/probe_meas_capabilities.py --output-path <path>`

Output includes:
- probe-by-probe pass/fail,
- capability flags,
- `recommended_profile` (`run_mode`, gain/power measurement strategy).

### Run Simulation (Single Netlist)
Command:
`python scripts/run_ngspice.py --netlist-path <path> --work-dir <dir> --timeout-sec 60`

Path resolution order for ngspice:
1. `--ngspice-bin`
2. env `NGSPICE_BIN`
3. `tool_paths.json` field `ngspice_bin`
4. system `PATH` (`ngspice`)

Output JSON keys:
- `ok`
- `return_code`
- `log_path`
- `stderr`
- `artifacts`

### Split Netlist by Analysis
Command:
`python scripts/split_netlist_analyses.py --netlist-path <path> --ac-netlist-path <path> --power-netlist-path <path> --power-analysis tran`

Notes:
- Keeps AC directives in AC netlist.
- Keeps power directives in power netlist.
- Injects missing power measurement lines (`idd_a`, `pdc_w`, `idd_ma`) when needed.

### Run Dual Analysis (Preferred)
Command:
`python scripts/run_dual_analysis.py --work-dir <dir> --netlist-path <path> --spec-path <spec.json>`

Output artifacts:
- `ac_ngspice.log`
- `power_ngspice.log`
- `ac_metrics.json`
- `power_metrics.json`
- `metrics.json` (merged)
- `evaluation.json` (when spec provided)

### Parse Metrics
Command:
`python scripts/parse_results.py --log-path <path> --metric gain_db --metric bw_hz`

Output JSON keys:
- `metrics`
- `failed_metrics`
- `raw_measure_lines`
- `analysis_context`
- `warnings`
- `missing_metrics` (when filters provided)
- `failed_requested_metrics` (when filters provided)

### Evaluate Metrics
Command:
`python scripts/evaluate_against_spec.py --spec-path <path> --metrics-path <path>`

Notes:
- Uses `spec.targets_eval` when available.
- Falls back to `spec.targets`.
- Supports alias fallback via `spec.metric_aliases`.

### Patch Netlist
Command:
`python scripts/patch_netlist.py --netlist-path <path> --patch-plan-path <plan.json> --output-path <path>`

Patch plan schema:
```json
{
  "set_param": {
    "R_FB": "12k",
    "C_COMP": "22p"
  },
  "replace_text": [
    {
      "old": "R1 in out 10k",
      "new": "R1 in out 12k"
    }
  ]
}
```

### Auto Tune Netlist
Command:
`python scripts/auto_tune_netlist.py --netlist-path <path> --spec-path <path> --param-space-path <path> --work-dir <dir> --analysis-mode dual`

Behavior:
- bounded coordinate descent over `.param` values,
- parameter-first optimization,
- rollback on >10% hard-metric regression,
- patience-based termination.

### Validate Primitive-Only Netlist
Command:
`python scripts/validate_netlist_primitives.py --netlist-path <path>`

Policy:
- Allowed instance prefixes only: `R`, `C`, `L`, `Q`, `M`, `D`, `V`, `I`.
- Forbidden directives: `.subckt`, `.ends`, `.include`, `.lib`.
- Forbidden instance prefixes: `X`, `E`, `F`, `G`, `H`, `B`, `A`, `U`.
- Every primitive instance must include explicit parameter/model tokens (no blank value fields).

Output JSON keys:
- `ok`
- `netlist_path`
- `violations`
- `warnings`
- `summary`

### Strict Parameter Gate
Command:
`python scripts/strict_param_check.py --netlist-path <path> --allow-expression --output-path <strict_param_validation.json>`

Policy:
- Fail-fast on missing component parameter/model tokens.
- Validate scalar parseability for passive/source/geometric assignments.
- Enforce explicit unit suffixes for capacitor/inductor scalar values.

### Export JSON
Command:
`python scripts/netlist_to_json.py --netlist-path <path> --json-path <path>`

Notes:
- Input handling is BOM-safe (`utf-8-sig`).
- Supports continuation-line (`+`) netlists before parsing.
- Includes parser warnings for unsupported/ambiguous instance prefixes.
- Emits explicit `<missing_param>` marker when an instance value is absent.

### Render SVG
Command:
`python scripts/render_netlistsvg.py --json-path <path> --svg-path <path> --skin-profile analog`

Skin resolution priority:
1. skill `assets/skins/analog.svg`
2. skill root `analog.svg`
3. npm package skin (`netlistsvg/lib/analog.svg`)

### Validate Schematic I/O
Command:
`python scripts/validate_schematic_io.py --svg-path <path> --output-path <path>`

Notes:
- Checks for visible input and output terminal markers (`inputExt`/`outputExt`) in rendered SVG.
- Use as a quality gate for filter jobs where clear ports are required.

### Plot Simulation Results
Command:
`python scripts/plot_sim_results.py --log-path <path> --svg-path <path> --plot-mode auto --title "<title>"`

Output JSON keys:
- `ok`
- `plot_path`
- `mode`
- `points`
- `warnings`
- `error`

### Extract RF/LNA Bench Metrics
Command:
`python scripts/extract_rf_lna_metrics.py --forward-log-path <fwd_log> --reverse-log-path <rev_log> --noise-log-path <noise_log> --linearity-log-path <lin_log> --output-path <metrics.json>`

Output JSON keys:
- `metrics` (`s11_db_max`, `s22_db_max`, `k_min`, `nf_db_max`, `p1db_dbm_min`, `ip3_dbm_min`)
- `sources`
- `analysis_context`
- `warnings`
- `missing_metrics`

Notes:
- Reads direct `.meas` outputs first.
- Supports fallback derivation from `zin/zout` and optional linearity sweep CSV.

### Extract Op-Amp Stability/Slew Metrics
Command:
`python scripts/extract_opamp_metrics.py --ac-log-path <ac_log> --tran-log-path <tran_log> --ac-loop-model open_loop --output-path <metrics.json>`

Legacy single-log mode:
`python scripts/extract_opamp_metrics.py --log-path <log> --output-path <metrics.json>`

Output JSON keys:
- `metrics` (includes `pm_deg`, `ugf_hz`, `slew_pos_vus`, `slew_neg_vus`, `settling_time_us`, `stability_flag`)
- `analysis_context`
- `warnings`

Notes:
- Use `--ac-loop-model closed_loop` when AC table is closed-loop transfer `H(s)` and PM should be derived from `T = H/(1-H)`.
- Use `--ac-loop-model open_loop` when AC table is open-loop transfer `A(s)` and PM should be derived directly from `A(s)` at unity crossing.
- Add `--require-settling` when settling-time coverage is mandatory.

### Extract Oscillator Metrics
Command:
`python scripts/extract_oscillator_metrics.py --log-path <tran_log> --output-path <metrics.json>`

Output JSON keys:
- `metrics` (`startup_flag`, `f_osc_hz`, `vpp_steady_v`, `startup_time_us`)
- `analysis_context`
- `startup_pass`
- `warnings`

### Extract Power Metrics (Buck/LDO)
Command:
`python scripts/extract_power_metrics.py --steady-log-path <steady_log> --line-log-path <line_log> --load-log-path <load_log> --transient-log-path <tran_log> --output-path <metrics.json>`

Output JSON keys:
- `metrics` (`efficiency_pct`, `v_ripple_mv`, `line_reg_mv_per_v`, `load_reg_mv_per_a`, `recovery_time_us`)
- `sources`
- `analysis_context`
- `warnings`
- `missing_metrics`

### Run PVT Corner Sweep
Command:
`python scripts/run_pvt_sweep.py --netlist-path <path> --work-dir <dir> --corners-path <corners.json> --spec-path <spec.json>`

Output:
- `pvt_summary.json`
- per-corner `design.cir`, `ngspice.log`, `metrics.json`, `evaluation.json`

### Run Monte Carlo Sweep
Command:
`python scripts/run_monte_carlo.py --netlist-path <path> --param-stats-path <mc_params.json> --samples 40 --work-dir <dir> --spec-path <spec.json>`

Output:
- `monte_carlo_summary.json`
- per-sample `design.cir`, `ngspice.log`, `metrics.json`, `evaluation.json`

### Validate Metric Schema
Command:
`python scripts/validate_metric_schema.py --spec-path <spec.json> --metrics-path <metrics.json> --domain <domain> --strict-targets --output-path <schema_validation.json>`

Notes:
- Uses `references/metric_schema.json` as unified metric registry.
- Detects unknown target names, missing domain-required targets, and direction-rule mismatches.

### Diagnose Failures
Command:
`python scripts/diagnose_failures.py --summary-path <regression_summary.json> --output-path <diagnosis.json>`

Output:
- per-case categories (`convergence_issue`, `model_issue`, `spec_gap`, `measurement_extraction_issue`, etc.)
- actionable recommendations suitable for next iteration patching.

### Export Report Bundle
Command:
`python scripts/export_report_bundle.py --report-path <final/report.md> --html-path <final/report.html> --pdf-path <final/report.pdf>`

Notes:
- Tries `weasyprint`, then `wkhtmltopdf`, then headless chromium.
- Falls back to a minimal text-PDF backend if none are available.

### Audit Report Completeness
Command:
`python scripts/audit_report_completeness.py --report-path <final/report.md> --output-path <final/report_audit.json>`

Checks:
- markdown table integrity,
- figure link existence,
- required report sections.

### Run Full Regression
Command:
`python scripts/run_full_regression.py --work-root <work/skill_regression_...> --suite-path assets/benchmarks/fullstack_suite.json`

Outputs:
- per-case final bundles and run metadata,
- `regression_summary.json`,
- scoreboard update in `references/benchmark_scoreboard.json`,
- optional `diagnosis.json`.

### Manage Model Library
Command:
`python scripts/manage_model_library.py --action validate --netlist-path <path> --registry-path references/model_registry.json`

Actions:
- `scan`: list local `.model` definitions and usages.
- `validate`: check used model tokens against local + registry models.
- `sync-registry`: sync local models into registry for reuse.

### Generate Final Report
Command:
`python scripts/generate_report.py --job-dir <work/job_id> --title "<report title>"`

Report sections include:
- design objectives and metric table,
- design thought process and architecture explanation,
- full component parameter table,
- simulation figures (`schematic.svg`, all `final/plots/*.svg`),
- iteration convergence and result analysis,
- design evolution and residual risks.

## Analysis Execution Caveat (ngspice batch mode)

In this environment, batch runs typically execute only the first analysis block in a netlist.
For workflows that need both AC and TRAN metrics:
1. Prepare `design_ac.cir` and `design_tran.cir`.
2. Run `run_ngspice.py` twice (one per netlist).
3. Merge advanced metrics through `extract_opamp_metrics.py` using `--ac-log-path` and `--tran-log-path`.
4. Keep `final/design.cir` as the reference topology used for JSON/SVG/report generation.

## Convergence Rules

Apply these rules in order:
1. Prioritize hard constraints before optimization targets.
2. Prefer parameter-only changes before topology changes.
3. Change a small set of variables each iteration.
4. Use rollback when critical metrics regress by >10%.
5. Stop on pass.
6. Stop on `max_iter` or `patience` and report best-so-far design with remaining gaps.

## Output Requirements

Always produce:
- `final/design.cir`
- `final/strict_param_validation.json`
- `final/primitive_validation.json`
- `final/metrics.json`
- `final/schema_validation.json`
- `final/evaluation.json`
- `final/design.json`
- `final/schematic.svg`
- `final/plots/sim_plot.svg`
- `final/report.md`
- `final/report.html`
- `final/report.pdf`
- `final/report_audit.json`

Iteration history must be archived under:
- `iterations/iter_<NNN>/design.cir`
- `iterations/iter_<NNN>/primitive_validation.json`
- `iterations/iter_<NNN>/ngspice.log` (or `ac_ngspice.log` + `power_ngspice.log`)
- `iterations/iter_<NNN>/metrics.json`
- `iterations/iter_<NNN>/evaluation.json`
- `iterations/iter_<NNN>/patch_plan.json` (when patched)

`final/report.md` must include:
- Design thought process and architecture description.
- Final pass/fail table with target-vs-actual values.
- Iteration summary/convergence table.
- Full component parameter table (no blank parameters).
- Simulation figures section embedding `final/schematic.svg` and `final/plots/sim_plot.svg`.
- Simulation result analysis and residual risk items.

## Error Handling

If primitive validation fails:
1. Stop simulation for that iteration.
2. Classify violations (forbidden directive, forbidden instance, unknown prefix, missing parameters).
3. Apply targeted patch to remove black-box usage.
4. Fill missing component parameters/model names when reported.
5. Re-validate before running ngspice.

If ngspice execution fails:
1. Classify error: syntax/model/timeout/convergence.
2. Attempt one targeted auto-fix.
3. Re-run once.
4. If still failing, stop and return explicit remediation steps.

If parser reports failed metrics:
1. Inspect `failed_metrics` and `analysis_context`.
2. Adjust measurement syntax per probe profile.
3. Re-run parsing before patching topology.

If netlistsvg fails:
1. Return JSON artifact anyway.
2. Emit command, stderr, and likely cause.
3. Suggest fallback rendering or manual review.

## References and Assets

Load only what is needed:
- Netlist style rules: `references/netlist_style_guide.md`
- Metric naming and parsing semantics: `references/metric_definitions.md`
- Iteration and rollback strategy: `references/convergence_playbook.md`
- RF metric templates: `references/rf_metric_templates.md`
- Passive filter topology guidance: `references/filter_topologies.md`
- Corner/statistical flow guide: `references/advanced_validation_flows.md`
- Model registry: `references/model_registry.json`

Reusable templates:
- `assets/templates/opamp_noninv.cir`
- `assets/templates/buck_converter.cir`
- `assets/templates/rc_filter.cir`
- `assets/templates/filter_pi_lpf_50ohm.cir`
- `assets/templates/filter_t_hpf_50ohm.cir`
- `assets/templates/filter_ladder_bpf_50ohm.cir`
- `assets/templates/lna_common_emitter_rf_bench.cir`
- `assets/templates/opamp_mos_cascode_eval.cir`
- `assets/templates/oscillator_ring_mos_3stage.cir`
- `assets/templates/buck_mos_power_bench.cir`
- `assets/templates/ldo_mos_series_bench.cir`

Template note:
- In primitive-only mode, do not use templates that contain forbidden prefixes/directives unless rewritten to pass `validate_netlist_primitives.py`.
