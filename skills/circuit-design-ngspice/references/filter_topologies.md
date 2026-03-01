# Filter Topologies Reference

Use this reference when creating passive RF filters in primitive-only mode (`R/C/L/V/I` only).

## 1) Topology Selection

### L-Section (2nd order equivalent behavior)
- Use for simple impedance transformation plus one-sided filtering.
- Good for narrow constraints and low part count.
- Not ideal for steep stopband rejection.

### PI Network (C-L-C or L-C-L)
- Use when source and load are both defined (for example 50 ohm to 50 ohm).
- Stronger stopband shaping than L-section for the same practical complexity.
- Common for LPF output harmonic filtering.

### T Network (L-C-L or C-L-C)
- Dual of PI network.
- Better when series branch control is convenient (layout or inductor current handling).
- Often used for HPF/BPF interstage matching.

### Ladder (higher order)
- Cascade alternating series and shunt elements.
- Preferred for 3rd order and above, especially when passband ripple and stopband attenuation are both constrained.
- Supports Bessel/Chebyshev/Elliptic style responses.

## 2) Approximation Family Guidance

- Bessel: best group-delay flatness, gentler roll-off.
- Butterworth: flat passband magnitude, moderate roll-off.
- Chebyshev: passband ripple for steeper transition.
- Elliptic (Cauer): ripple in passband and stopband with transmission zeros; sharpest transition.

Use Bessel when waveform fidelity matters, Elliptic when rejection selectivity dominates.

## 3) 50-Ohm Practice

1. Always include source and load terminations explicitly in netlist.
   - Example: `Rsrc src in 50`, `Rload out 0 50`.
2. Use consistent port naming (`in`, `out`) to stabilize rendering and report parsing.
3. Measure insertion/return loss at clearly defined passband frequencies.

## 4) Frequency Transform Hints

- LPF to HPF: swap `L <-> C` with frequency scaling.
- LPF to BPF: each prototype element maps to a resonant branch pair.
- Keep center frequency `f0 = sqrt(f_low * f_high)` for BPF designs.

## 5) Practical RF Notes

- Use high-Q inductors near passband center to reduce insertion loss.
- Prefer C0G/NP0 capacitors for stable RF behavior.
- Include parasitic-aware guardband in targets when moving from simulation to PCB.
- Above ~100 MHz, layout parasitics can shift effective cutoff and ripple.

