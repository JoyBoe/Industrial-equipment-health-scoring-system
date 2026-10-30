# Synthetic Data Notice

> **The telemetry in this project is synthetic. It is generated code, not
> measurements from real equipment.**
>
> No real industrial dataset was available to this project. The data produced by
> `backend/app/ml/synthetic.py` and written by `python -m app.seed_telemetry` is
> a physics-informed simulation intended **only for development, testing, and
> demonstrating the system**.
>
> Do not use these results to make real maintenance decisions, and do not treat
> the reported health scores as evidence about any real machine. Before
> deploying against a real fleet, retrain on that fleet's own history and
> re-derive the baseline profiles (see *Replacing this data* below).

## Why simulate at all?

The health score has to be computed from actual sensor values, and the detector
has to be trained on something. Independent random numbers would satisfy both
requirements only nominally: a uniform-noise dataset has no learnable structure,
so detection accuracy measured against it would be meaningless and would flatter
the system.

The generator therefore models the relationships a machine actually has.

## What is modelled

| Relationship | Implementation |
|---|---|
| Duty cycle | Smooth ~8 h sinusoidal load pattern with a small mean-reverting perturbation |
| Electrical power | Derived as `P = sqrt(3)·V·I·PF/1000`, never sampled independently |
| Power factor | Degrades slightly as load rises, as on a real electrical feed |
| Thermal behaviour | First-order lag (~25 min time constant) toward a load-derived target |
| Vibration | Scales with speed and load, as a bearing/alignment signature does |
| Pressure | Setpoint with load droop; variance widens before the mean drifts |
| Brownout | Mild voltage sag above 85 % load |

Consequences worth noting: `current` and `power` correlate at ~0.93, load and
temperature at ~0.44, and rpm and current are *negatively* correlated (−0.43)
because an overloaded machine stalls while still drawing current. These are
consequences of the model, not hand-coded correlations.

## Regimes

`regime` and `fault_type` are recorded as **ground truth for evaluation only**.
The unsupervised detector and the health scorer never read them. They exist so
detector precision and recall can be measured against a known answer.

| Regime | Share | Signature |
|---|---|---|
| `nominal` | ~70 % | within baseline for every channel |
| `degraded` | ~15 % | subtle: vibration +27 %, temperature +4 C, and **pressure variance ~4×** before the mean moves |
| `bearing_fault` | ~5 % | vibration ~3× nominal, with a periodic impact component |
| `overheat` | ~4 % | temperature +28 C sustained, past the alarm band |
| `cavitation` | ~3 % | pressure collapses to ~0.6× its normal floor, plus vibration and ripple |
| `overload` | ~3 % | current +~40 %, speed sags ~28 % |

The `degraded` regime is deliberately subtle. If gradual wear were as extreme as
an acute failure, the system could not distinguish "drifting toward failure"
from "already failed" — which is the entire purpose of the Warning band.

## Design decisions that were wrong the first time

Recorded because they are the kind of defect that silently produces a system
which appears to work:

1. **One fault duration for all modes.** A thermal runaway and a bearing impact
   are not the same phenomenon. Giving each a ~10-sample burst made
   overtemperature *statistically identical to nominal* — the 25-minute thermal
   lag never had time to move. Durations are now mode-specific.

2. **Rolling a fault per sample at a fixed probability.** A 4 % chance of a
   150-sample event requests ~170 events from a 4320-sample series, so the
   machine never returned to nominal and *nominal* mean temperature ended up
   higher than *degraded*. The timeline is now planned up front, so the realised
   regime mix matches the requested mix.

3. **A forward-cascade bug in the free-window search** silently dropped
   bearing, cavitation, and overload events entirely, depending on the seed.

4. **Nearly-white load noise.** A large per-step innovation made load
   effectively white noise, and the thermal lag averaged it away, collapsing the
   load–temperature correlation to ~0.05. Real load is smooth; it is now
   dominated by the duty cycle.

Each of these was caught by a structural test (`test_synthetic_data.py`), not by
inspection, which is why those tests assert correlation, regime separation, and
mix fidelity rather than merely "it runs".

## Determinism

Generation is seeded per unit (`seed = 1000 + index`). Re-running
`make seed-data` reproduces byte-identical telemetry, which is what makes
regressions in the scorer debuggable.

## Replacing this data with real data

Nothing downstream is aware of the generator. To use real telemetry:

1. Replace the source in `app/seed_telemetry.py` with a loader for your historian
   (or POST to `/api/sensor/readings/batch` from your gateway).
2. **Re-derive the baseline profiles** in `app/ml/baselines.py`. The shipped
   values are generic engineering defaults, not measured from any asset. They
   are deliberately conservative and will be wrong for your equipment.
3. Fit the detector on your own healthy history
   (`python -m app.seed_telemetry --healthy` demonstrates the fault-free path).
4. Recalibrate the risk model against your actual failure history. With no
   labelled failures, risk is necessarily a heuristic — see `ML_DOCUMENTATION.md`.

Because sensor units are fixed across equipment types (temperature in °C,
vibration in mm/s RMS, pressure in bar, and so on), data that uses different
units must be converted during ingestion. The per-type limits live in
`baselines.py`, not in the ingestion path, so a unit change requires editing
that module and re-deriving the bands.
