# Metric Definitions

Use a stable metric schema so parsers and evaluators remain deterministic.

Canonical registry:
- `references/metric_schema.json`
- validation entrypoint: `scripts/validate_metric_schema.py`

## Spec Schema

```json
{
  "targets": {
    "gain_db": { "min": 40.0 },
    "bw_hz": { "min": 1000000.0 },
    "v_ripple_mv": { "max": 20.0 },
    "iq_ma": { "max": 5.0 }
  },
  "targets_eval": {
    "gain_db": { "min": 40.0 }
  },
  "metric_aliases": {
    "gain_db": ["gain_lin"]
  }
}
```

## Result Schema

```json
{
  "metrics": {
    "gain_db": 41.2,
    "bw_hz": 2100000.0,
    "v_ripple_mv": 12.7,
    "iq_ma": 3.9
  },
  "failed_metrics": {
    "gain_db": {
      "reason": "Error: Bad value.",
      "analysis": "ac"
    }
  },
  "analysis_context": {
    "gain_db": "ac"
  },
  "warnings": []
}
```

## Naming Conventions

1. Keep names lowercase and snake_case.
2. Include units in metric name suffix when needed (`_db`, `_hz`, `_mv`, `_ma`).
3. Use scalar metrics only for pass/fail evaluation.
4. Use separate artifacts for waveforms or curves.

## Optional RF Metrics

Common LNA extensions:
- `s11_db_max`
- `s22_db_max`
- `k_min`
- `nf_db_max`
- `p1db_dbm_min`
- `ip3_dbm_min`

## Optional Op-Amp Metrics

- `pm_deg`
- `ugf_hz`
- `slew_pos_vus`
- `slew_neg_vus`
- `settling_time_us`
- `stability_flag`

## Optional Oscillator Metrics

- `startup_flag`
- `f_osc_hz`
- `vpp_steady_v`
- `startup_time_us`

## Optional Power Metrics

- `efficiency_pct`
- `v_ripple_mv`
- `line_reg_mv_per_v`
- `load_reg_mv_per_a`
- `recovery_time_us`

## Corner/Statistical Extensions

- Use `run_pvt_sweep.py` for corner tables (TT/SS/FF and temperature rails).
- Use `run_monte_carlo.py` for sample distributions and yield estimation.
- Use `run_full_regression.py` + `benchmark_scoreboard.json` to track score deltas across skill revisions.
