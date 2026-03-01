# Advanced Validation Flows

Use this document for corner/statistical robustness checks after nominal design closure.

## PVT Corner Sweep

Corner schema (`corners.json`):

```json
[
  { "name": "tt_27c", "temp_c": 27, "param_overrides": { "VIN": "12" } },
  { "name": "ss_125c", "temp_c": 125, "param_overrides": { "VIN": "10.8" } },
  { "name": "ff_m40c", "temp_c": -40, "param_overrides": { "VIN": "13.2" } }
]
```

Run:

```powershell
python scripts/run_pvt_sweep.py --netlist-path work/job/final/design.cir --work-dir work/job/pvt --corners-path work/job/corners.json --metric gain_db --metric bw_hz --spec-path work/job/spec.json
```

Outputs:
- `work/job/pvt/corners/<corner>/design.cir`
- `work/job/pvt/corners/<corner>/ngspice.log`
- `work/job/pvt/corners/<corner>/metrics.json`
- `work/job/pvt/pvt_summary.json`

## Monte Carlo Sweep

Parameter stats schema (`mc_params.json`):

```json
{
  "RBIAS": { "nominal": "1k", "sigma_pct": 5.0 },
  "COUT": { "nominal": "100p", "sigma_pct": 10.0 }
}
```

Run:

```powershell
python scripts/run_monte_carlo.py --netlist-path work/job/final/design.cir --param-stats-path work/job/mc_params.json --samples 40 --work-dir work/job/monte --metric gain_db --spec-path work/job/spec.json
```

Outputs:
- `work/job/monte/samples/sample_<NNN>/design.cir`
- `work/job/monte/samples/sample_<NNN>/ngspice.log`
- `work/job/monte/samples/sample_<NNN>/metrics.json`
- `work/job/monte/monte_carlo_summary.json`

## Model Registry

Scan:

```powershell
python scripts/manage_model_library.py --action scan --netlist-path work/job/final/design.cir
```

Validate against registry:

```powershell
python scripts/manage_model_library.py --action validate --netlist-path work/job/final/design.cir --registry-path .codex/skills/circuit-design-ngspice/references/model_registry.json
```

Sync local `.model` cards into registry:

```powershell
python scripts/manage_model_library.py --action sync-registry --netlist-path work/job/final/design.cir --registry-path .codex/skills/circuit-design-ngspice/references/model_registry.json
```

## Full Regression + Scoreboard

Benchmark suite run:

```powershell
python scripts/run_full_regression.py --work-root work/skill_regression_20260228_fullstack_v5 --suite-path .codex/skills/circuit-design-ngspice/assets/benchmarks/fullstack_suite.json
```

Artifacts:
- `work/<run_id>/regression_summary.json`
- `work/<run_id>/diagnosis.json`
- `references/benchmark_scoreboard.json` (run history and score deltas)

Score meaning:
- `pipeline_pass_count`: end-to-end workflow health.
- `eval_pass_count`: spec attainment under suite thresholds.
- `score`: weighted aggregate used for regression guardrails.

## Metric Schema Gate

Run before final evaluation to keep naming and rule direction consistent:

```powershell
python scripts/validate_metric_schema.py --spec-path work/job/spec.json --metrics-path work/job/final/metrics.json --domain opamp --strict-targets --output-path work/job/final/schema_validation.json
```

## Report Completeness + Export

Generate markdown then publish HTML/PDF and run quality audit:

```powershell
python scripts/generate_report.py --job-dir work/job --output-path work/job/final/report.md
python scripts/export_report_bundle.py --report-path work/job/final/report.md --html-path work/job/final/report.html --pdf-path work/job/final/report.pdf
python scripts/audit_report_completeness.py --report-path work/job/final/report.md --output-path work/job/final/report_audit.json
```
