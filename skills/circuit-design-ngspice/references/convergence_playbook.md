# Convergence Playbook

Use this playbook when the optimization loop stalls or oscillates.

## Baseline Policy

1. Change one major degree of freedom at a time.
2. Keep every iteration traceable with a short patch rationale.
3. Re-run the same simulation settings for comparability.
4. Stop on `max_iter` or `patience` and report best-so-far.
5. Prefer parameter coordinate-descent before topology edits.

## Patch Priority

1. Parameter tuning:
   - resistor ratios
   - compensation capacitors
   - bias current
2. Local topology edits:
   - add damping resistor
   - move RC corner
3. Global topology changes:
   - only when local edits fail repeatedly

## Rollback Rule

Trigger rollback if:
- any hard-constraint metric regresses by more than 10 percent, or
- two consecutive iterations increase total gap score.

For `auto_tune_netlist.py`, rollback keeps the previous best parameter set and skips acceptance of regressing candidates.

## Failure Classification

Use one of:
- `syntax_error`
- `model_missing`
- `non_convergent`
- `timeout`
- `unknown`

Return explicit next action for each class.
