# Architecture

How the system is put together, why, and what the constraints are. This document
describes the structure as built, including the places where a simpler design
was rejected for a stated reason.

## 1. Design goals

1. **Explainability over accuracy.** A score nobody can act on is not useful. The
   architecture is arranged so that every point deducted is traceable to a named
   cause, and the explanation is derived from the same arithmetic.
2. **Honesty about uncertainty.** Where the system cannot know something, it says
   so rather than presenting a confident number.
3. **Per-equipment engineering knowledge.** A pump and a boiler have nothing in
   common in acceptable temperature. Limits live in data, not in a global
   threshold table.
4. **Testable seams.** Business logic is separated from HTTP so it can be tested
   without a web server, and SQL is isolated so query behaviour is auditable.

## 2. Layering

Backend uses a strict one-way dependency rule:

```
routers/  ──►  services/  ──►  repositories/  ──►  models/  ──►  database
   │              │                                       │
   │              └──────────►  ml/  ─────────────────────┘
   │                                  │
   └──────────────►  schemas/  ◄──────┘
```

| Layer | May import | Must never |
|---|---|---|
| `routers/` | schemas, services, config, database (for `Depends`) | contain business logic, build SQL |
| `services/` | repositories, ml, schemas, config | import FastAPI route types for logic |
| `repositories/` | models, schemas | contain business rules |
| `ml/` | config, models (types only) | perform I/O, import services |
| `schemas/` | config, models (enums) | import repositories or services |
| `models/` | database (Base) | import anything above |

**Why.** Business logic in routers cannot be unit tested without HTTP, and SQL
spread across the codebase cannot be reviewed. Every violation of this rule
would be a review comment.

### Notable placement decisions

- **`ml/scoring.py` holds the scoring arithmetic, not `services/`.** It is pure
  maths with no I/O, so it is directly testable and reusable from both the
  health service and the explainability service. Putting it in `services/` would
  have coupled the maths to the database.
- **`services/explainability.py` is separate from `services/health_service.py`.**
  Explanation is a distinct concern with its own vocabulary and is reused by the
  prediction endpoint.
- **`ml/attribution.py` is separate from `ml/scoring.py`.** The scoring engine
  produces the number; attribution answers a different question ("what is fixing
  this worth?") by re-running the scorer with one channel changed.
- **Calibration is split: `ml/calibration.py` fits, `services/calibration_service.py`
  reads and writes.** The fitting rule (`λ₀ = 1/MTBF`, its exclusions, its refusal
  below five usable records) is pure arithmetic and must be testable without a
  database; querying the failure table and persisting the result is I/O and must
  not be reachable from `ml/`. The service also owns `effective_calibration()`,
  the single place the rest of the application asks "is this instance calibrated,
  and at what rate?" — so health scoring, prediction and explanation cannot drift
  apart and report different states for one deployment.

## 3. Data model

Seven tables. `equipment` carries denormalised health columns as a deliberate
cache; everything else is normalised.

```
equipment ─┬─< sensor_readings
           ├─< health_scores
           ├─< anomalies
           ├─< alerts
           └─< failures

model_calibration   (singleton row, id = 1)
```

### `equipment`

Master record. `health_score`, `status`, `risk_level`, `failure_probability` and
`last_assessed_at` are **caches** of the latest assessment, rewritten by the
scoring service.

*Why denormalise.* The dashboard's fleet table needs these for every unit on
every poll. Without the cache, rendering the page is one correlated subquery per
row. The columns are never read as a source of truth — the `health_scores` table
is authoritative and the detail view recomputes rather than trusting the cache.

### `health_scores` — append-only

An assessment is never updated or deleted. Each row stores the full
`score_breakdown`, the `contributing_factors`, and the `model_version` that
produced it.

*Why.* Weights and model versions change. Storing only the score would make
past explanations unreproducible, and an audit trail that cannot explain itself
is not an audit trail. Appending also means the trend chart reflects what the
system actually concluded at each point in time, including later-discredited
versions.

### `alerts` — deduplicated while open

A partial unique index on `(equipment_db_id, alert_type) WHERE resolved = false`
means a machine oscillating across a threshold produces one alert, not thousands.
Because the index is partial, a resolved alert does not block the same condition
firing again later.

*Why partial rather than a timestamp window.* A time-window dedupe still emits
one alert per window and loses the true open/closed state an operator needs.

### Timezone handling

SQLite has no timezone type. All timestamps are stored **UTC-naive**. Conversion
happens at exactly one place (`repositories/_naive_utc`), and the API accepts
either aware or naive input, interpreting naive as UTC.

*Why this matters.* An earlier chart window used `datetime.now()` in local time
against UTC-naive storage and silently dropped the most recent 5.5 hours on a
UTC+5:30 host. `tests/test_consistency.py` now pins this.

`failures` follows the same rule in `schemas/calibration.py`, because schemas
must not import repositories. Both convert to UTC *before* dropping the offset,
so an entered `+05:30` does not shift the interval it describes.

### `failures` — the raw material for calibration

One row per closed failure cycle: `interval_start` (commissioning or the last
return to service) through `failed_at`.

*Why a cycle rather than an instant.* MTBF is the mean of the *intervals*
between failures, so a row needs both ends. Storing only the failure instant
would leave nothing to average.

`is_catalytic` marks exogenous failures — operator error, supply surge, external
impact. The hazard model describes wear, so those rows are excluded from the fit
rather than quietly counted.

Recording a row **never** recalibrates. The fit is a separate, explicit call, so
it always has a visible result instead of occurring as a side effect of data
entry.

### `model_calibration` — a measurement stored with its evidence

A singleton row holding the fitted `lambda_0`, the sample size behind it, and
when it was made.

*Why the database and not `.env`.* A rate derived from a year of failure history
is a genuine measurement, and a measurement belongs with the data that produced
it. It must also survive a restart without an operator editing environment
variables, and must not be silently overridden by an unrelated `.env` edit. The
environment holds the *placeholder*; the database holds the *fit*, and the fit
wins.

`DELETE /api/predict/calibration` discards it, because recalibrating against a
new batch of records should start from that evidence rather than silently
averaging with a stale fit.

## 4. The scoring path

```
values (8 channels)
   │
   ├─► baselines.get_profile(equipment_type)        per-type limits
   ├─► degradation.evaluate_all(values, profile)    → 0..1 per channel
   ├─► normalise_weights(active channels)           weights renormalise over
   │                                               what was actually reported
   ├─► sensor_penalty = Σ budget_sensor · w · d     (capped at 70)
   ├─► critical floor                                 alarm breach ⇒ not Healthy
   ├─► risk.estimate_risk(...)                        wear-out hazard
   ├─► health_score = 100 − sensor − anomaly − risk
   ├─► apply_status_caps(...)                         status never contradicts risk
   └─► build_factors / generate_explanation           derived from the same pass
```

### Why the points are partitioned

The brief suggested `100 − temperature − vibration − ...`. Implemented directly,
the terms could sum past 100 and the "budgets" would be arbitrary. Partitioning
70/18/12 means:

- The sum can never exceed 100 (validated at call time).
- The ML terms are **capped at a minority**, so a retrained or misbehaving model
  degrades the score gracefully instead of controlling it.
- Each term has an owner, which is what makes the explanation decomposable.

### Why weights are renormalised

A machine reporting only temperature and vibration is scored against a budget
split for eight channels otherwise — it would be penalised for parameters it
never measured. `normalise_weights` redistributes across the active set.

### Why a critical floor exists

Linear weighting means one channel at its alarm limit costs only its weight share
of 70 — about 17 points for vibration — leaving the machine at ~83, i.e.
"Healthy". A confirmed excursion is not a healthy machine. The floor is derived
from the configured threshold (`100 − healthy_min + ε`), so retuning the bands
retunes the policy, and the uplift is published as its own `confirmed_excursion`
line item so the breakdown still reconciles.

**Why severity-aware.** The floor used to be a constant, which meant every
machine that breached any limit was pinned to an identical penalty and the fleet
view could not rank them (measured spread: 0.32 points across 8 machines). It
now interpolates on how far past the alarm limit the worst channel is, so
machines past their limits still separate. The un-floored penalty is published
alongside it, so the policy is non-destructive — see `ML_DOCUMENTATION.md` §12.

### Why status caps exist

The anomaly and risk budgets are small by design, so a machine with pristine
sensors and a Severe failure risk scored 95 and was labelled Healthy, with the
recommendation "continue routine monitoring". `apply_status_caps` prevents the
status from being better than the models support. This is the most important
safety property in the scoring layer.

## 5. Request flows

### Ad-hoc scoring — `POST /api/health/score`

No history required. Resolves the profile from `equipment_type` or
`equipment_id`, optionally runs the anomaly detector over the supplied values,
estimates risk, scores, explains. Persists only when a real machine is named.

### Telemetry-driven assessment — `POST /api/equipment/{id}/health/assess`

Reads recent readings, runs `detect_series` once over the ordered history (so the
drift detector sees it), then scores.

### The shared reading — `worst_reading_in_window`

Three call sites independently chose their own reading during development, and
the result was a machine with an open Critical vibration alert whose detail page
read "Excellent, no action required". They now share
`health_service.worst_reading_in_window`, and the window itself is a single
frontend constant. `tests/test_consistency.py` pins the agreement.

*Why worst rather than newest.* Readings land in normal operating gaps most of
the time. An alert or a health reading based only on the newest sample would
report "all clear" while a machine was failing an hour ago. A condition is a
statement about a period, not an instant.

## 6. Frontend structure

```
App.jsx            routes + the two root-level polls
  Layout           shell, offline banner, alert badge
  ErrorBoundary    keeps the app usable when a view crashes
  SystemStatus     shared connectivity + alert count
pages/             Dashboard, Alerts, EquipmentList, EquipmentDetail
components/        ui.jsx, cards, insights, table
charts/            ChartFrame, SensorChart, HealthTrendChart
hooks/useApi       the only place that knows about loading/error/abort
services/api       typed client; normalises FastAPI errors
```

`useApi` centralises request lifecycle, so no component can forget the
`AbortController` cleanup that otherwise leaks a request per filter change.

Mutations follow a **refetch** discipline rather than optimistic updates: after
resolving an alert or rescoring, the affected query is refetched. With a backend
that owns all the rules, an optimistic local guess would be wrong more often
than it was right.

## 7. Cross-cutting concerns

| Concern | Approach |
|---|---|
| Validation | Pydantic at the boundary; ranges far outside legal values so the scorer, not the validator, judges severity |
| Timezones | UTC everywhere; conversion isolated in one helper |
| Errors | Domain errors in services → mapped to HTTP in routers; one catch-all 500 that logs and does not leak internals |
| Configuration | `pydantic-settings`, 41 `APP_*` variables, `.env`, no secrets in code |
| Migrations | `create_all` at startup. **No Alembic** — schema changes are not yet managed |
| Concurrency | SQLite WAL; partial unique index absorbs concurrent alert writes |
| Polling | 30 s at the root, shared via context rather than polled per page |

## 8. Deliberate non-goals

- **Deep learning.** No justification at this data volume, and it would make the
  system unexplainable, defeating the primary goal.
- **A separate ML service.** The model is loaded in-process. Splitting it out
  would add deployment complexity for a few hundred milliseconds of inference.
- **Optimistic UI.** See above.
- **A background scheduler.** Alert evaluation is currently on-demand. A
  production deployment needs a periodic trigger; that is infrastructure, not
  application logic.

## 9. Known architectural debt

Recorded by the final audit. Items 1–5 are design debt; 6–8 were found by
grepping for unreferenced code and are dead rather than missing.

1. **No migrations.** Schema changes require manual coordination.
2. **Unauthenticated API.** Fine locally, not for exposure.
3. **SQLite only in practice.** The PostgreSQL path is configured but untested.
4. **Circular-import risk** in `ml/schemas ↔ config`. Currently managed with
   local imports; a shared constants module would be cleaner.
5. **`alerts` has no retention policy.** Long-running deployments will grow the
   table without bound.
6. **N+1 queries in `health_service.score_all`.** Each machine is assessed
   independently, so a fleet pass issues several queries per unit. The batched
   helpers that would fix it exist but are unused:
   `repositories.health_score.fleet_latest`, `repositories.anomaly.fleet_counts`,
   `repositories.sensor_reading.latest_timestamps` and
   `equipment_ids_with_history`. Not yet wired up — the refactor is not worth its
   regression risk at current fleet sizes, but they are not accidental omissions.
7. **Dead helpers.** `repositories.alert.resolve_by_type`,
   `repositories.sensor_reading.get_by_id`, `ml.baselines.expected_power_kw` and
   `services.anomaly_service.reset_detector_cache` have no callers. The last is
   worth wiring up: without it, retraining requires an API restart. The other
   three are plausible future needs and harmless, but a reader should not assume
   they are exercised.
8. **`schemas.equipment.EquipmentSummary`** is declared and unreferenced; the
   list endpoint returns the full ORM-shaped record instead of the summary DTO
   that was designed for it.

The critical-floor compression is not listed here because it is a scoring-policy
issue rather than an architectural one; it is documented in
`ML_DOCUMENTATION.md` § 12.
