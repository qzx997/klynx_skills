# Metric Pack Templates

Use this reference when enabling RF/filter-oriented target packs.

## Supported Pack

### `lna_basic`

Suggested target schema:

```json
{
  "targets": {
    "s11_db_max": { "max": -10.0 },
    "s22_db_max": { "max": -10.0 },
    "k_min": { "min": 1.0 },
    "nf_db_max": { "max": 3.0 },
    "p1db_dbm_min": { "min": 20.0 },
    "ip3_dbm_min": { "min": 30.0 }
  }
}
```

### `bpf_basic_50ohm`

Suggested target schema:

```json
{
  "targets": {
    "il_db": { "max": 3.0 },
    "rl_db": { "min": 10.0 },
    "bw_hz": { "min": 20000000.0 },
    "ripple_db": { "max": 1.5 },
    "attn_stop_db": { "min": 30.0 }
  }
}
```

### `lpf_basic_50ohm`

```json
{
  "targets": {
    "il_db": { "max": 2.0 },
    "rl_db": { "min": 10.0 },
    "f_3db_hz": { "max": 1050000000.0 },
    "ripple_db": { "max": 1.0 },
    "attn_stop_db": { "min": 20.0 }
  }
}
```

### `hpf_basic_50ohm`

```json
{
  "targets": {
    "il_db": { "max": 2.0 },
    "rl_db": { "min": 10.0 },
    "f_3db_hz": { "min": 100000000.0 },
    "ripple_db": { "max": 1.0 },
    "attn_stop_db": { "min": 20.0 }
  }
}
```

### `opamp_eval_basic`

```json
{
  "targets": {
    "pm_deg": { "min": 45.0 },
    "ugf_hz": { "min": 1000000.0 },
    "slew_pos_vus": { "min": 1.0 },
    "slew_neg_vus": { "min": 1.0 },
    "settling_time_us": { "max": 10.0 },
    "stability_flag": { "min": 1.0 }
  }
}
```

### `oscillator_eval_basic`

```json
{
  "targets": {
    "startup_flag": { "min": 1.0 },
    "f_osc_hz": { "min": 1000000.0 },
    "vpp_steady_v": { "min": 0.2 },
    "startup_time_us": { "max": 200.0 }
  }
}
```

### `power_eval_basic`

```json
{
  "targets": {
    "efficiency_pct": { "min": 70.0 },
    "v_ripple_mv": { "max": 100.0 },
    "line_reg_mv_per_v": { "max": 50.0 },
    "load_reg_mv_per_a": { "max": 100.0 },
    "recovery_time_us": { "max": 200.0 }
  }
}
```

## Notes

1. S-parameter and RF linearity metrics are testbench-dependent in ngspice.
2. Metric formulas should be mapped to your excitation/termination style.
3. Keep RF metrics optional unless your netlist includes matching test structures.
4. Filter metrics can use insertion-loss-oriented proxies when full S-parameter benches are unavailable.
