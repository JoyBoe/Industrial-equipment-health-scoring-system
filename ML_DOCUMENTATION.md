# ML Documentation

How the machine learning works, what has been measured, and — most importantly —
**what has not**.

---

## 0. Summary of what is real and what is not

| Component | Method | Fitted to real data? | Calibrated? |
|---|---|---|---|
| Anomaly detection | Isolation Forest ⊕ band degradation ⊕ EWMA drift | **No** — generated fault-free data | Score scale yes, absolute meaning no |
| Failure risk | Wear-out hazard model | **No** — no failure history exists | **No** — base rate is a placeholder |
| Health score | Weighted, auditable penalty engine | N/A (not learned) | N/A |
| Explanation | Templates filled from computed values | N/A | N/A |
| Baselines | Engineering defaults, 10 equipment types | **No** — not measured from any asset | N/A |

There is **no supervised model**, because there is no labelled failure data.
None of the figures below are evidence about real equipment.

The telemetry everything here is trained and evaluated on is generated — see
[`data/SYNTHETIC_DATA.md`](data/SYNTHETIC_DATA.md) for exactly what it models
and, more usefully, what it does not.

---

## 1. Why unsupervised

No real industrial failure dataset was available to this project. The realistic
public alternatives — CWRU, Paderborn, XJTU-SY, NASA SMAP/MSL — are either
unavailable offline or use incompatible units and sampling regimes. Bolting one
on would have produced a model that could not be applied to the data this system
actually ingests.

So the pipeline is unsupervised by necessity, not by preference. The
`RiskEstimator` protocol exists so a supervised model can replace the current
one without the API, scorer, or alerting layer changing.

---

## 2. Pipeline

```
raw telemetry (8 channels, 1 min)
   │
   ├─ validation      Pydantic; ranges far outside legal values
   │
   ├─ imputation      trailing median → series median → column mean
   │
   ├─ feature engineering   51 features (§3)
   │
   ├─ anomaly detection     §4  →  anomaly_score ∈ [0,1], affected parameters
   │
   ├─ risk estimation       §5  →  30-day probability
   │
   ├─ health score          §6  →  0–100, decomposable
   │
   ├─ classification        §7  →  status, with policy caps
   │
   ├─ explainability        §8  →  factors, counterfactuals, narrative
   │
   └─ alerting              §9
```

---

## 3. Features

51 features from 8 channels. Raw values alone are a poor model input: a point
sample cannot distinguish "briefly off-nominal" from "degrading".

| Group | Count | Purpose |
|---|---|---|
| raw | 8 | Level |
| `*_mean` (15 min) | 8 | Smoothed level; suppresses single-sample noise |
| `*_std` (15 min) | 8 | **Rising variance is an early fault signal**, often before the mean moves |
| `*_delta` | 8 | Rate of change between samples |
| `*_slope` | 8 | Sustained trend — distinguishes transient from wear |
| `*_z` | 8 | Deviation from the machine's own mean, in sigmas |
| `power_residual` | 1 | Measured power vs power implied by V and I |
| `power_per_load` | 1 | Power per unit load — detects drag or slip |
| `current_per_rpm` | 1 | Low speed at high current is the stall signature |

### Causality

All rolling operations are strictly backward-looking. `shift(1)` before
`rolling()` means a value is never imputed from itself or from a future sample.
`tests/test_features.py::test_features_are_causal` truncates a frame and asserts
that features before the cut are unchanged — a centred window would leak the
future and make every accuracy figure below meaningless.

### Why these two features matter

`power_residual` and `current_per_rpm` encode relationships that no single
channel reveals. A machine drawing more current at lower speed is stalling or
binding — both channels look individually plausible. These scalars are what let
the detector flag that without hand-coding a rule.

---

## 4. Anomaly detection

Three detectors run together because each covers a different failure shape.

### 4.1 Isolation Forest

Multivariate and learned. 100 trees, `contamination="auto"`, 51 features,
trained on 11,880 rows of healthy operation across 8 equipment types.

It catches unusual **combinations** of channels that no per-parameter threshold
would flag.

### 4.2 Calibration

Raw `score_samples` output is neither a probability nor comparable across
retrains. It is mapped through percentile anchors of the healthy training
distribution:

| Training percentile | Mapped score |
|---|---|
| 0 (min) | 0.00 |
| 50 (median) | 0.15 |
| 90 | 0.35 |
| 99 | 0.60 |
| 99.9 | 0.80 |
| max | 1.00 |

**Why percentile-anchored.** An earlier version mapped the *median* to 0.5,
which meant half of all healthy telemetry sat at the anomaly threshold by
construction — 12.2% of healthy rows were flagged. Anchoring the median to 0.15
puts normal operation firmly in the lower half of the scale and leaves room above
the threshold for real faults.

This makes the scale **reproducible across retrains**: 0.70 means "as extreme as
the worst 1% of healthy operation" regardless of seed or data.

### 4.3 Band degradation

Deterministic, and the reason a finding can be attributed to a **named
parameter**. Each channel's distance past its equipment type's acceptable band,
mapped to 0–1.

### 4.4 EWMA drift

Catches slow movement away from normal **while every value is still inside its
limits** — precisely what a point-in-time detector is blind to. Normalised by
each channel's own scale so units are comparable, then smoothed with α = 0.15.

A channel is *named* as affected at 2σ. A lower floor reported ~2.3 affected
parameters even on healthy machines, which makes the field meaningless: if
everything is named, nothing is.

### 4.5 Fusion

```
combined = 0.55·max_band + 0.30·forest + 0.15·drift
```

Band deviation gets the greatest authority because it is grounded in engineering
limits and is directly explainable; the forest contributes where no channel has
left its band, which is exactly what it is good at. If any channel is at its
alarm limit, the score is floored at 0.9 regardless of the forest.

### 4.6 Measured performance

Evaluated on held-out generated data (seeds 11–15, disjoint from training seeds
0–7), 17,910 samples:

| Metric | Value |
|---|---|
| Precision | **0.979** |
| False-positive rate on nominal | **0.003** |
| Per-event recall | **0.939** (62 of 66 episodes) |
| Per-sample recall | 0.342 |

Per regime:

| Regime | Flag rate | Mean score |
|---|---|---|
| overload | 0.992 | 0.690 |
| bearing_fault | 0.760 | 0.627 |
| overheat | 0.556 | 0.574 |
| cavitation | 0.337 | 0.475 |
| degraded | 0.005 | 0.191 |
| nominal | 0.003 | 0.162 |

**Two recall figures are reported because they answer different questions.**
Per-sample recall is low (0.342) largely because injected faults ramp in over
several samples by design — a developing fault is genuinely not yet abnormal on
its first sample. Per-event recall (0.939) is what an operator cares about: was
the failure noticed at all? Reporting only one would mislead in the opposite
direction.

**These numbers are measured against generated faults.** They show the pipeline
is wired correctly. They are not a performance claim for real equipment.

---

## 5. Failure risk

### 5.1 A probability needs a horizon

`P(failure within H)` is meaningless without H. The API reports 24 h, 168 h and
720 h, and judges the **risk level on the 30-day horizon** — the horizon a
maintenance planner actually schedules against.

An earlier version classified risk on a 24-hour probability. At any realistic
industrial MTBF that is always a fraction of a percent, so it reported "Low" for
a machine sitting at its alarm limit and the whole channel was useless.

### 5.2 Wear-out hazard model

```
λ(d)  = λ₀ · exp(k · d)              hazard at degradation d
λ_eff = λ₀ · (1 + Σ wₚ·(exp(k·dₚ) − 1) + anomaly_multiplier · anomaly)
P(H)  = 1 − exp(−λ_eff · H)
```

`exp(k·d)` encodes **wear-out**: hazard stays flat while healthy and accelerates
sharply near an alarm limit. This is the wear-out region of the bathtub curve.

**Why not a linear blend.** A linear model cannot express that asymmetry — it
treats a reading at 90% of the alarm limit as only 10% worse than one at 80%.
`test_risk.py::test_wear_out_is_superlinear` asserts the later step costs more
than twice the earlier one.

At `k = 4`, a channel at its alarm limit multiplies the hazard ~55×.

### 5.3 Baseline hazard always applies

Healthy machines do fail. Reporting exactly zero would be a false reassurance,
and would make the channel useless for comparing two healthy machines. An
unusual-but-in-band pattern adds to the baseline rather than being ignored —
evidence the engineering limits do not explain is evidence of a worse state.

### 5.4 ⚠️ Calibration status: NOT CALIBRATED

```
APP_BASE_HAZARD_PER_HOUR = 2.0e-5      →  implied MTBF ≈ 50,000 h (5.7 years)
APP_RISK_MODEL_CALIBRATED = false
```

The base rate is a **placeholder**, not fitted to failure data. It was chosen as
a realistic order of magnitude for industrial rotating equipment, deliberately
on the conservative side.

Consequently:

- **The ranking between machines is meaningful.** A more degraded machine
  reliably carries more risk.
- **The absolute probability is not.** Do not present "21% chance of failure" to
  an operator as a measured frequency. It is a **relative index**: the correct
  reading is "this machine is worse than that one", never "this machine has a
  one-in-five chance of failing".

Until a fit is applied, every response carries `calibrated: false`,
`/api/predict/model-info` and `/api/predict/calibration` state it prominently,
the alert text says it, and the dashboard footer repeats it. §5.5 describes how
that flag becomes `true`.

### 5.5 Fitting the base rate

`λ₀ = 1 / MTBF`, where MTBF is the mean service interval between failures for
this fleet, computed from closed failure records.

The system implements this end to end rather than describing it as a future
step:

| Step | How |
|---|---|
| Record a failure | `POST /api/equipment/{equipment_id}/failures` |
| Inspect what would be fitted | `GET /api/predict/calibration` |
| Fit and persist | `POST /api/predict/calibrate` |
| Discard and start over | `DELETE /api/predict/calibration` |

Recording never recalibrates. Fitting is a separate, deliberate act, so a fit
always has a visible result instead of happening as a side effect of data entry.

**What is excluded from the fit**

| Excluded | Why |
|---|---|
| `is_catalytic: true` | Exogenous (operator error, supply surge, impact) — the hazard model describes wear |
| Interval < 1 h | Almost certainly a data-entry error; would drag MTBF down and make everything look near-failure |
| Interval > 1,000,000 h | A single 1970 commissioning date would make the whole fleet look immortal |

**Refusal below 5 usable records.** A rate fitted from a handful of intervals is
confident and wrong, which is worse than an obvious placeholder: it would turn a
labelled relative index into a false measurement. `POST /predict/calibrate`
applies nothing when it refuses, and the response states the exact shortfall.

**The fit is stored in the database** (`model_calibration`), not in `.env`. A
rate derived from a year of history is a measurement, and a measurement belongs
with the data that produced it — it must also survive a restart without an
operator editing environment variables, and must not be silently overridden by
an unrelated `.env` edit.

**Once fitted**, `calibrated: true` appears on every response that reports a
probability, and the number changes with it: the estimator is rebuilt from the
fitted rate rather than merely re-labelled.

**What calibration does not change.** The degradation exponent `k`, the channel
weights, and the anomaly multiplier are untouched — those remain engineering
judgement. Calibration fixes the *level* of the risk, not the shape of how
degradation maps to hazard.

**Limits that remain after a successful fit:**

- Censoring is not modelled. Machines still running contribute no interval, so
  a young fleet reads as a shorter MTBF than it truly has.
- The mean is unweighted: one equipment type with many failures dominates the
  fleet-wide rate. If machines differ materially, fit per equipment type rather
  than globally (see §13).
- `thin_sample: true` in the status response means the count passed but the
  spread of intervals is wide (`coefficient_of_variation > 0.75`). The fit still
  applies; the flag says how much to trust it.

### 5.6 If labelled failure data becomes available

Replace `DegradationHazardEstimator` with a `SupervisedRiskEstimator` behind the
existing `RiskEstimator` protocol. A gradient-boosted classifier on the same 51
features, target = failure within a defined horizon, is the natural choice. The
`RiskEstimator` interface is what makes this a drop-in change; nothing above the
service layer changes.

---

## 6. Health score

Not a model — a weighted, auditable penalty engine.

```
health_score = 100 − sensor_penalty − anomaly_penalty − risk_penalty
```

| Term | Budget | Source |
|---|---|---|
| sensor | 70 | Weighted per-channel degradation vs engineering limits |
| anomaly | 18 | Calibrated detector output |
| risk | 12 | 30-day hazard probability |

Every point has an owner. `test_health_scoring.py` asserts that the published
factor penalties sum to the points deducted, and a 27,000-case randomised check
verifies the same across all 10 equipment types.

**Why not a regressor.** A regressor cannot answer "why is this machine at 63?".
The design goal is a score an operator can act on, which requires the
decomposition to be exact.

### Defaults

Vibration 0.28 · temperature 0.22 · power 0.18 · pressure 0.14 · current 0.10 ·
load 0.08 · rpm 0.08 · voltage 0.06. Weights are **relative** and renormalised
over the channels actually reported.

All 41 settings are overridable via `APP_*` environment variables and exposed at
`GET /api/health/thresholds`, so a score can be reproduced from the configuration
in force at the time.

---

## 7. Classification

| Band | Score | Default threshold |
|---|---|---|
| Excellent | 90–100 | `APP_EXCELLENT_MIN=90` |
| Healthy | 75–89 | `APP_HEALTHY_MIN=75` |
| Warning | 50–74 | `APP_WARNING_MIN=50` |
| Poor | 25–49 | `APP_POOR_MIN=25` |
| Critical | 0–24 | — |

**Status caps.** Risk Severe caps the status at Poor; risk High caps at Warning;
an anomaly score ≥ 0.8 caps at Warning. Without this a machine could be labelled
Healthy while the risk model said it was about to fail.

---

## 8. Explainability

Every sentence is generated from a computed number.

- **Factors** — each parameter's actual point cost.
- **Counterfactual attribution** — the score recomputed with one channel at
  nominal. Answers "what is fixing this worth?", which the linear factor list
  cannot.
- **Policy masking** — flags a channel that is out of range but costing nothing,
  either because the budget is saturated or because a different channel's breach
  pins the score at its floor. Reading the factor list alone would tell an
  operator a failing channel was fine.
- **Drift** — channels moving while still in range.
- **Narrative** — templates filled from the above. No LLM, no inference.

---

## 9. Alert rules

Thresholds are expressed as **band degradation (0–1)**, not raw values, so a
single setting is meaningful across equipment types with different limits. A
vibration threshold of 0.5 means "halfway to the alarm limit" for a 2.8 mm/s
motor and a 0.9 mm/s turbine alike.

Severity escalates with magnitude: a channel 55% of the way to its alarm limit is
a Warning; one past the limit is Critical. Fixed severity per rule would make
those indistinguishable and would train operators to ignore alerts.

---

## 10. Baseline profiles

Ten equipment types with per-channel acceptance bands. Essential: 170 °C is
alarming on a motor and normal on a boiler; 12 °C is normal for a chiller and
suspiciously cold for a boiler.

**These are engineering defaults, not measured from any asset.** They are
deliberately conservative and will be wrong for your equipment. Derive them from
your own commissioning data and OEM specifications before production use.

`low_relevant` distinguishes channels where falling below the band is a fault
(pressure collapse, voltage sag) from channels where it is not (a cold chiller, a
low vibration reading). Applying low-side degradation to temperature would have
flagged every idle chiller.

---

## 11. Evaluation

```bash
make evaluate                      # default: 6 equipment types, seeds 11–16
python -m app.ml.evaluate --types "Hydraulic Pump,Boiler" --samples 5000
python -m app.ml.train --trees 200  # retrain
```

`train.py` warns when more than 5% of *healthy* training rows exceed the
threshold — a model that alarms on its own training distribution is worse than
no model.

Evaluation uses seeds disjoint from training so the numbers are not in-sample.

---

## 12. The critical floor

A parameter at or beyond its alarm limit is a *confirmed excursion*, not a trend.
Linear weighting alone lets such a machine report as "Healthy", because a single
channel only holds a fraction of the 70-point sensor budget. The critical floor
corrects that: when any channel is at its limit, the sensor term is raised to at
least the amount needed to cross the Healthy boundary.

### The floor is severity-aware

The floor interpolates between the Healthy boundary and the full sensor budget,
driven by how far past the alarm limit the worst channel is:

```
healthy_boundary = 100 − healthy_min + 0.01        # = 25.01 by default
saturation       = clamp01(severity / 1.0)         # 0.0 at the limit
floor            = healthy_boundary + saturation × (sensor_budget − healthy_boundary)
```

`severity` is the worst channel's `overshoot`, measured in band spans past its
alarm limit. At exactly the limit the floor is 25.01 — the historical value, so
machines that are only marginally breached are unchanged. One band-width past
the limit the floor saturates at the full 70.

`normalized` cannot drive this, because it is capped at 1.0 at the alarm limit
and so carries no information about depth. `overshoot` is the quantity that
actually varies between breaching machines.

### Why it is not a constant

It used to be. The floor was `max(sensor_penalty, 25.01)`, which assigns every
breaching machine an identical penalty. Over a 24-hour window that flattened the
whole fleet: measured on the seeded 8 units, all 8 machines scored 71.24–71.56
and reported the same status. The fleet view could not distinguish one channel
marginally over its limit from four channels far over theirs.

The severity-aware floor, measured on the same fleet and window:

| Strategy | Score range | Spread | Stdev | Statuses |
|---|---|---|---|---|
| `latest` | 99.30 – 99.89 | 0.59 | 0.17 | Excellent |
| `recent_worst` | 42.20 – 69.25 | **27.05** | **8.31** | Poor, Warning |

`latest` is genuinely flat because at the newest sample every machine is nominal
— the injected faults had already recovered. `recent_worst` is the discriminating
view, and now ranks correctly: Air Compressor (worst, severity 0.651) down to
Boiler (severity 0.044).

### The floor is non-destructive

Raising a penalty to enforce a status would otherwise destroy information. The
breakdown now publishes:

- `unfloored_sensor_penalty` — the sensor term before the policy raised it, so a
  consumer can recover exactly how degraded a machine really is;
- `worst_excursion_severity` — the depth that drove the floor, and the value a
  fleet view should sort on;
- `critical_floor_points` — the points the policy added, surfaced as its own
  factor so the published penalties still reconcile with the score.

`tests/test_critical_floor.py` pins all three properties, plus the original
guarantee that a confirmed excursion never reports as Healthy.

---

## 13. Going to production

Ordered by importance. **Do not skip step 1.**

1. **Calibrate the risk base rate** from real failure history (§5.5): record
   closed failures with `POST /api/equipment/{id}/failures`, then
   `POST /api/predict/calibrate`. The fit persists in the database and the flag
   flips to `calibrated: true` on its own — do not set
   `APP_RISK_MODEL_CALIBRATED=true` by hand, because that would claim a fit that
   was never made. Until a real fit exists, every probability is an index and
   must be presented as one.
2. **Re-derive the baselines** for your fleet from commissioning data (§10). Ship
   defaults are placeholders.
3. **Retrain the detector on real healthy history**:
   `python -m app.seed_telemetry --healthy` shows the fault-free path; with real
   data, fit on a period the plant confirmed healthy.
4. **Re-evaluate against real faults** and re-tune thresholds. The figures in §4.6
   say nothing about your equipment.
5. **Add alerts on data-quality drift** — a stuck sensor is a common cause of
   false all-clear readings, and a real deployment needs it.
6. **Add authentication.** The API is unauthenticated.
7. **Move to PostgreSQL** and add migrations (Alembic is not yet set up).
8. **Add a scheduler** for periodic `POST /api/alerts/evaluate`; it is currently
   on-demand only.
9. **Log every model decision** (version, weights hash, input range) so a
   disputed score can be reconstructed. The `model_version` and `score_breakdown`
   fields already support this.

### Regression guard

`make test` must pass before any model or weight change ships. The suite asserts
structural properties — decomposability, monotonicity, causality, cross-surface
agreement — that a change in any single component would violate if it broke
something else.
