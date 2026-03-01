# Netlist Style Guide

Use this guide when writing or patching ngspice netlists.

## Rules

1. Start files with a one-line title and one-line purpose comment.
2. Keep node naming consistent and meaningful (`vin`, `vout`, `fb`, `gnd`).
3. Use `.param` for tunable values instead of hard-coding every component value.
4. Keep one component per line.
5. Keep simulation directives grouped at the end (`.op`, `.ac`, `.tran`, `.meas`, `.end`).
6. Add `.meas` entries for every metric in `spec.targets`.
7. Primitive-only mode: allow instance prefixes `R/C/L/Q/M/D/V/I` only.
8. Primitive-only mode: forbid black-box patterns (`X*`, `E/F/G/H/B/A/U*`, `.subckt/.ends/.include/.lib`).
9. Avoid hidden dependencies on external model files unless explicitly approved.
10. Prefer split AC/power simulation netlists when `.meas` compatibility is uncertain.
11. Write passive values with explicit unit suffixes when practical (`50ohm`, `22p`, `4.7n`, `10u`).

## Suggested Layout

```spice
* Title
* Purpose

.param R_FB=10k
.param C_COMP=22p

V1 vin 0 DC 12
R1 vin n1 1k
C1 n1 0 10n

.op
.ac dec 100 10 1e6
.meas ac gain_db FIND vdb(vout) AT=1k
.end
```

## Measurement Naming

Use lower snake_case names:
- `gain_db`
- `bw_hz`
- `v_ripple_mv`
- `iq_ma`
