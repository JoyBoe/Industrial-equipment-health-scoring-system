# Industrial Equipment Health Scoring System

An AI-assisted condition monitoring system for industrial equipment. It ingests
multi-parameter sensor telemetry, detects abnormal behaviour, estimates failure
risk, and fuses those signals into a **0–100 health score that can be explained
point by point**.

The defining property of this system is that it is **not a black box**. Every
point deducted from a score is attributable to a named parameter, and the
explanation shown to an operator is a formatting pass over the arithmetic that
produced the number — not a separate guess that might disagree with it.

> ### ⚠️ Read this before trusting any number
>
> The telemetry in this repository is **synthetic** (see
> [`data/SYNTHETIC_DATA.md`](data/SYNTHETIC_DATA.md)) and the failure-risk model
> is **uncalibrated**. Specifically:
>
> - **No real failure history was available.** The risk model is a wear-out
>   hazard model whose base rate is a *placeholder* (`APP_BASE_HAZARD_PER_HOUR`),
>   not fitted to observed failures. Reported probabilities are a **relative
>   index** — useful for ranking machines, not for predicting dates.
> - **The baseline operating limits are engineering defaults**, not measured
>   from any real asset. They are deliberately conservative and will be wrong
>   for your equipment.
> - The detector is trained on generated fault-free data and evaluated against
>   generated faults. It has **never seen real failure data**.
>
> Every API response carries `calibrated: false` for this reason. Do not deploy
> this against real equipment without completing
> [`ML_DOCUMENTATION.md`](ML_DOCUMENTATION.md) § *Going to production*.

---

## Quick start

```bash
# 1. Backend
python3 -m venv .venv
make install

# 2. Data and model  (synthetic — see the warning above)
make seed          # 8 equipment units
make seed-data     # 23,040 telemetry readings, 48 h per unit
make train         # fit the Isolation Forest on healthy data

# 3. Run
make run           # API on http://127.0.0.1:8000  (docs at /docs)
make frontend      # dashboard on http://127.0.0.1:5173
```

Then, in a second terminal, refresh the fleet so the dashboard has something to
show:

```bash
curl -X POST "http://127.0.0.1:8000/api/health/score-all?window=1440&strategy=recent_worst"
curl -X POST "http://127.0.0.1:8000/api/alerts/evaluate" -H 'Content-Type: application/json' -d '{"window":1440}'
```

`recent_worst` is the right choice for a fleet view: it scores the whole window
and keeps the worst result, so a degradation cannot hide behind a recovered
newest reading. Measured on the seeded fleet it separates the 8 units across
27 points and two status bands. The default `latest` scores the newest sample
only, where every unit is genuinely nominal and they all land around 99.4.

See [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) § `POST /health/score-all`.

## Tests

```bash
make test           # 552 backend tests
make test-frontend  # 177 frontend tests (needs the backend running)
make test-all
make coverage       # frontend coverage report
make clean          # remove caches, generated data and build output
make distclean      # also discard the trained model (forces a retrain)
```

Data and the model are **build artifacts**: `make clean` removes the database so
you can prove the seed scripts reproduce it, and `make distclean` removes the
detector as well.

The frontend page tests run against the **live backend** rather than a mock, so a
contract change fails in CI rather than in the browser. They skip automatically if
the API is unreachable.

## What the system produces

For any machine, given its telemetry:

| Output | Meaning |
|---|---|
| `health_score` | 0–100, auditable to the point |
| `status` | Excellent / Healthy / Warning / Poor / Critical |
| `anomaly_score`, `anomaly_detected` | Is the current pattern abnormal? |
| `failure_probability`, `risk_level` | 30-day risk — **uncalibrated index** |
| `contributing_factors` | Each parameter's actual point cost |
| `attribution` | Counterfactual: what fixing each channel is worth |
| `explanation`, `recommendation` | Generated from the above, not asserted |
| `trending_parameters` | Movement away from normal, still inside limits |
| `score_breakdown` | Full arithmetic, persisted with the assessment |

## Example

```bash
curl -X POST http://127.0.0.1:8000/api/health/score \
  -H 'Content-Type: application/json' \
  -d '{"equipment_id":"EQ-002","temperature":118,"vibration":9.5,"pressure":4.2,
       "rpm":1150,"current":44,"voltage":400,"power":26,"load":88,"persist":false}'
```

```json
{
  "health_score": 69.9,
  "status": "Warning",
  "risk_level": "Moderate",
  "anomaly_score": 1.0,
  "anomaly_detected": true,
  "failure_probability": 0.2006,
  "calibrated": false,
  "contributing_factors": [
    { "parameter": "temperature", "penalty": 13.51, "impact": "severe", "contribution_share": 0.45 },
    { "parameter": "vibration",   "penalty": 9.26, "impact": "significant", "contribution_share": 0.31 }
  ],
  "explanation": "Health score 69.9. The score is reduced primarily because operating temperature has risen above the normal range (now 118) (-14 points), followed by v...",
  "recommendation": "Plan maintenance within the next scheduled window ..."
}
```

The factor penalties (13.51 + 9.26 + 4.69 + …) sum to exactly the points
deducted. That is asserted by the test suite, not merely intended.

## How the score works

The 100 available points are **explicitly partitioned**, which is what makes the
score decomposable:

```
health_score = 100 − sensor_penalty − anomaly_penalty − risk_penalty

  sensor_penalty  ≤ 70   weighted per-parameter degradation vs engineering limits
  anomaly_penalty ≤ 18   Isolation Forest ⊕ band ⊕ drift, calibrated
  risk_penalty    ≤ 12   wear-out hazard over a 30-day horizon
```

Each parameter's degradation is its distance past the equipment type's
acceptable band, so a hydraulic pump and a boiler are judged against *their own*
limits rather than a global threshold table.

Two policy rules exist to stop the output contradicting itself:

- **Critical floor.** A parameter at or beyond its alarm limit can never report
  as "Healthy". The uplift is published as its own line item so the arithmetic
  still reconciles.
- **Status caps.** A machine with a Severe risk level is never reported as
  Healthy, however good its sensors look. Without this, a near-certain failure
  scored 95 and was called Healthy.

## Architecture at a glance

```
Sensor data → validate → features (51) → anomaly detection → failure risk
                                    ↓
                        weighted, auditable health score
                                    ↓
                        status · explanation · recommendation · alerts
                                    ↓
                              React dashboard
```

Backend is a strict layered design — routes → services → (ml | repositories) →
database — with a one-way dependency rule. See
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Repository layout

```
backend/app/
  main.py config.py database.py
  models/     5 ORM tables
  schemas/    Pydantic request/response contracts
  routers/    HTTP only
  services/   business logic, HTTP-free
  repositories/ all SQL
  ml/         baselines, features, synthetic, detector, scoring,
              risk, attribution, train, evaluate, registry
frontend/src/
  components/ charts/ pages/ services/ hooks/ utils/ context/
tests/                 552 backend tests
data/SYNTHETIC_DATA.md what the simulator does and does not model
models/                trained artifacts + provenance
```

## Documentation

| Document | Contents |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layering, data model, request flows, design decisions |
| [`ML_DOCUMENTATION.md`](ML_DOCUMENTATION.md) | Features, detector, risk model, evaluation, calibration, going to production |
| [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) | All 40 operations with examples |
| [`data/SYNTHETIC_DATA.md`](data/SYNTHETIC_DATA.md) | Synthetic data notice and limitations |
| [`PROJECT_ANALYSIS.md`](PROJECT_ANALYSIS.md) | Stage 1 analysis and roadmap |

## Configuration

Every tunable is an environment variable prefixed `APP_`. Copy `.env.example` to
`.env`; nothing is hard-coded and no secrets are committed. See
[`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) § Configuration for the full
list of 47 settings, including all scoring weights, thresholds, and alert rules.

## Current state

| | |
|---|---|
| Backend | 10,748 lines of app code, 6,570 lines of tests, 552 tests |
| Frontend | 3,468 lines of app code, 1,914 lines of tests, 177 tests, 88% coverage |
| Endpoints | 40 operations / 35 paths |
| Detector | 0.979 precision, 0.003 FPR, 0.939 per-event recall |
| Equipment profiles | 10 types |

Detector figures are measured against **generated** faults. Treat them as
evidence that the pipeline is wired correctly, not as a performance claim for
real equipment.

## Known limitations

1. **Risk is uncalibrated** — the most important caveat about the numbers. The
   system ships with no failure history, so the reported probability is a
   *relative index* for ranking machines, not a measured failure frequency.
   Every response carries `calibrated: false` until the base hazard is fitted
   from your own closed failure records: record them with
   `POST /api/equipment/{id}/failures`, then `POST /api/predict/calibrate`.
   `ML_DOCUMENTATION.md` §5.5 covers the rule and its limits.
2. **Baselines are generic defaults**, not per-asset.
3. **No supervised learning.** Without labelled failures, none is possible; the
   interface is ready for it (`RiskEstimator` protocol).
4. **No authentication.** The API is unauthenticated and must not be exposed
   publicly as-is.
5. **SQLite** is the default; the URL is swappable to PostgreSQL but that path
   is untested here.
6. **Synthetic data only** — no real-plant validation has been performed.

## License

Not specified. Add one before any external distribution.
