# API Documentation

34 operations across 31 paths under `/api`. Interactive documentation is served at
`http://localhost:8000/docs` (Swagger UI) and `/redoc`; the machine-readable
spec is at `/openapi.json`.

**Convention:** paths below are relative to the `/api` prefix. This file
documents intent, contracts, and worked examples; the live `/docs` is
authoritative for exact field lists and is regenerated from the code.

All **34 operations across 31 paths** and all **47 settings** are
documented below, and `tests/test_documentation.py` fails if either drifts.

---

## Contents

- [Conventions](#conventions)
- [Sensor channels](#sensor-channels)
- [System](#system)
- [Equipment](#equipment)
- [Sensor data](#sensor-data)
- [Anomaly detection](#anomaly-detection)
- [Health scoring](#health-scoring)
- [Failure prediction](#failure-prediction)
- [Calibration and failure history](#calibration-and-failure-history)
- [Explainability](#explainability)
- [Alerts](#alerts)
- [Dashboard](#dashboard)
- [Configuration](#configuration)
- [Error handling](#error-handling)

---

## Conventions

**Base URL** — `http://localhost:8000/api`

**Authentication** — none. The API is unauthenticated and must not be exposed
publicly as-is.

**Content type** — `application/json` for all request and response bodies.

**Identifiers** — case-insensitive. `eq-001` and `EQ-001` refer to the same
unit; responses always return the canonical upper-case form.

**Timestamps** — ISO 8601. The API accepts timezone-aware or naive values; a
naive value is interpreted as **UTC**. Stored values are UTC-naive.

**Status codes**

| Code | Meaning |
|---|---|
| 200 / 201 | Success |
| 404 | Equipment, alert, or assessment not found |
| 409 | Equipment ID already registered |
| 422 | Validation failed — body lists the offending fields |
| 503 | Service up, database unreachable (from `/api/health` only) |

---

## Sensor channels

All telemetry uses fixed units across equipment types. Acceptable ranges are
**per equipment type**, not global — see `ml/baselines.py`.

| Channel | Unit | Notes |
|---|---|---|
| `temperature` | °C | −50…300 accepted; alarm limits are per type |
| `vibration` | mm/s RMS | 0…500 |
| `pressure` | bar | 0…1000 |
| `rpm` | rev/min | 0…60,000; 0 for stationary assets |
| `current` | A | 0…10,000 |
| `voltage` | V | 0…1000 |
| `power` | kW | 0…10,000 |
| `load` | % of rated | 0…200 |

Accepted ranges are set **far wider than any legal operating value**. They exist
to reject corrupt or spoofed data, not to judge severity. An alarming reading
such as 140 °C is accepted and scored; that is the scorer's job, not the
validator's.

### `GET /sensor/channels`

Returns this mapping at runtime, so a client can label axes and format values
from the API rather than hard-coding the units.


---

## System

### `GET /api/health`

Liveness plus a database reachability probe. Reports **503** when the database
is unreachable, so monitoring can distinguish "service down" from "service up,
database down".

Note the full path: this is `/api/health`, **not** `/health`. The unprefixed
form is a common convention and it returns **404** here, so a monitoring probe
pointed at `/health` will report the service as down when it is up.

```json
{
  "status": "ok",
  "app": "Industrial Equipment Health Scoring System",
  "version": "0.1.0",
  "environment": "development",
  "database": "connected",
  "sensor_features": ["temperature", "vibration", "..."],
  "timestamp": "2026-09-30T06:17:06.308547+00:00"
}
```

---

## Equipment

### `GET /equipment`

Query parameters:

| Name | Type | Notes |
|---|---|---|
| `status` | string | Health status filter |
| `equipment_type` | string | |
| `location` | string | |
| `search` | string | Matches ID, name, or location, case-insensitive |
| `active_only` | bool | Default `true`; retired units excluded |
| `min_health` / `max_health` | float | 0…100 |
| `sort_by` | string | `equipment_id`, `equipment_name`, `health_score`, `last_updated`, `operating_hours` |
| `sort_order` | string | `asc` \| `desc` |
| `limit` | int | 1…500, default 100 |
| `offset` | int | ≥ 0 |

```bash
curl "http://localhost:8000/api/equipment?status=Warning&sort_by=health_score&sort_order=asc"
```

### `POST /equipment`

```json
{
  "equipment_id": "EQ-001",
  "equipment_name": "Hydraulic Pump A",
  "equipment_type": "Hydraulic Pump",
  "location": "Production Line 1",
  "manufacturer": "Bosch Rexroth",
  "installation_date": "2021-03-15",
  "operating_hours": 12480.0,
  "rated_power_kw": 15.0
}
```

Only `equipment_id`, `equipment_name`, `equipment_type` and `location` are
required. IDs are canonicalised to upper case; `eq-001` and `EQ-001` cannot both
be registered (**409** on the second attempt).

### `GET /equipment/{equipment_id}`

Full detail, including cached `health_score`, `status`, `risk_level`.

### `PATCH /equipment/{equipment_id}`

Partial update; omitted fields are unchanged. `equipment_id` **cannot** be
changed — it is the stable external key, and changing it would orphan history.
Sending it returns **422** rather than being silently ignored.

### `DELETE /equipment/{equipment_id}`

**Retires** the unit by default; history is preserved and the unit disappears
from active listings but remains retrievable.

`?hard=true` permanently deletes the unit and all dependent records. This is
opt-in because deletion destroys the telemetry and assessment history that makes
the audit trail useful.

### `GET /equipment/filter-options`

Distinct types and locations, for filter dropdowns.

---

## Sensor data

### `POST /sensor/readings`

Ingest one sample.

```json
{
  "equipment_id": "EQ-001",
  "temperature": 65.0,
  "vibration": 3.2,
  "pressure": 100.0,
  "rpm": 1450.0,
  "current": 22.0,
  "voltage": 400.0,
  "power": 13.5,
  "load": 70.0,
  "timestamp": "2026-09-30T06:36:00Z"
}
```

Only `equipment_id` is required. **Partial readings are accepted** — a sample
with just temperature and vibration is stored with the rest as `null` and imputed
downstream. Rejecting it would discard good data and make the machine appear to
have a telemetry outage.

`data_quality` is classified automatically: `complete` (all 8), `partial` (6–7),
`sparse` (fewer).

**422** for physically impossible combinations: no channels at all, an
implausible value, or zero power with non-zero current. **404** for an unknown
equipment ID.

### `POST /sensor/readings/batch`

```json
{ "readings": [ { "equipment_id": "EQ-001", "temperature": 65.0 } ] }
```

Up to 5,000 per request, one transaction. Rows referencing unknown equipment are
**skipped and counted** in `rejected` rather than failing the batch — a gateway
replay should not be lost because one sample names a deleted machine.

### `GET /sensor/readings/{equipment_id}`

Raw history. `start`, `end`, `limit` (1…10,000), `offset`, `order` (`asc`/`desc`).

### `GET /sensor/readings/{equipment_id}/chart`

Chart-ready telemetry with `hours` (1…2160) and `max_points` (10…2000). Downsampled
evenly with the first and last samples always retained.

A 48-hour series at 1-minute cadence is ~2,900 points, which no chart renders
usefully. Downsampling happens server-side so a dashboard poll does not ship
them all.

### `GET /sensor/readings/{equipment_id}/latest`

Newest sample, or **404** if the machine has no telemetry.

---

## Anomaly detection

### `POST /anomaly/detect`

Assess a single sample without writing it to history.

Supply `equipment_id` (uses that machine's type) or `equipment_type`. An
`equipment_id` request **is** recorded in the assessment history, because the
result then describes real equipment; a type-only request is exploratory and is
never persisted.

```json
{
  "equipment_id": "EQ-001",
  "temperature": 130.0,
  "vibration": 3.2,
  "pressure": 100.0,
  "rpm": 1450.0,
  "current": 22.0,
  "voltage": 400.0,
  "power": 13.5,
  "load": 70.0
}
```

```json
{
  "equipment_id": "EQ-001",
  "equipment_type": "Hydraulic Pump",
  "anomaly_score": 0.9,
  "anomaly_detected": true,
  "threshold": 0.5,
  "affected_parameters": ["temperature"],
  "parameter_scores": {
    "temperature": {
      "value": 130.0, "degradation": 1.0, "direction": "high",
      "overshoot": 0.28, "drift": 0.0
    }
  },
  "method": "band+forest_prior",
  "model_version": "if-1.0.0"
}
```

`affected_parameters` is selective by design. A drift threshold of 1σ reported
~2.3 affected parameters even on healthy machines; the threshold is 2σ so the
field stays meaningful.

### `POST /anomaly/detect/{equipment_id}`

Score recent history and persist. `window` (20…2000, default 60). This is the
path that uses the drift detector, which needs ordered history.

### `GET /anomaly/{equipment_id}`

Stored assessments, newest first. `detected_only`, `limit`, `offset`.

### `GET /anomaly/{equipment_id}/summary`

```json
{
  "equipment_id": "EQ-001",
  "assessed": 2882,
  "detected": 84,
  "detection_rate": 0.0291,
  "max_anomaly_score": 0.9,
  "mean_anomaly_score": 0.187,
  "most_common_parameters": ["pressure", "temperature"],
  "last_detected_at": "2026-09-30T05:12:00"
}
```

---

## Health scoring

### `POST /health/score`

Score supplied values. Supply `equipment_id` (resolves the machine's type and
persists by default) or `equipment_type` (scores without persisting). Set
`"persist": false` to score a named machine without writing an assessment.

```bash
curl -X POST http://localhost:8000/api/health/score \
  -H 'Content-Type: application/json' \
  -d '{"equipment_id":"EQ-002","temperature":118,"vibration":9.5,"pressure":4.2,
       "rpm":1150,"current":44,"voltage":400,"power":26,"load":88,"persist":false}'
```

```json
{
  "equipment_id": "EQ-002",
  "equipment_type": "Industrial Motor",
  "health_score": 69.9,
  "status": "Warning",
  "risk_level": "Moderate",
  "anomaly_score": 1.0,
  "anomaly_detected": true,
  "failure_probability": 0.2006,
  "calibrated": false,
  "contributing_factors": [
    {
      "parameter": "temperature", "value": 118.0,
      "impact": "severe", "penalty": 13.51,
      "contribution_share": 0.4492, "degradation": 0.9,
      "direction": "high", "source": "sensor"
    },
    {
      "parameter": "vibration", "value": 9.5,
      "impact": "significant", "penalty": 9.26,
      "contribution_share": 0.3079, "degradation": 0.5385,
      "direction": "high", "source": "sensor"
    }
  ],
  "explanation": "Health score 69.9. The score is reduced primarily because operating temperature has risen above the normal range (now 118) (-14 points), followed by vibration has increased above the normal operating range (now 9.5) (-9 points).",
  "recommendation": "Plan maintenance within the next scheduled window ...",
  "score_breakdown": {
    "sensor_penalty": 26.06,
    "anomaly_penalty": 3.6,
    "risk_penalty": 0.43,
    "parameter_penalties": { "temperature": 13.51, "vibration": 9.26, "power": 3.01 },
    "critical_parameter": false,
    "critical_floor_points": 0.0,
    "budgets": { "sensor": 70.0, "anomaly": 18.0, "risk": 12.0 }
  }
}
```

The factors above plus `anomaly_pattern` (3.60) and `failure_risk` (0.43) sum to
30.10, and `100 − 30.10 = 69.90`.

`calibrated` is present on every response that reports a `failure_probability` —
this endpoint, `/equipment/{id}/health`, `/predict`, and
`/equipment/{id}/risk` — so a client can never read an uncalibrated index
without being told. It reflects `APP_RISK_MODEL_CALIBRATED` live.

**`contributing_factors` reconciles with `score_breakdown`**: the penalties sum
to `sensor_penalty + anomaly_penalty + risk_penalty`, and
`health_score = 100 − that`. Asserted by the test suite.

`impact` labels are derived from each factor's *share* of the total deduction, so
the label reflects actual contribution rather than a fixed threshold.

### `GET /equipment/{equipment_id}/health`

Latest **stored** assessment, with `previous_score` and `delta`. **404** if the
machine has never been assessed. Carries `calibrated` alongside
`failure_probability` — a stored probability is still a relative index until the
risk model has been fitted.

### `GET /equipment/{equipment_id}/health/trend`

History for charting, oldest first. `limit` 1…2000.

```json
{
  "equipment_id": "EQ-002", "count": 12,
  "latest": 71.24, "first": 90.15,
  "min_score": 71.24, "max_score": 90.15, "average_score": 82.4,
  "points": [ { "timestamp": "...", "health_score": 90.15, "status": "Healthy" } ]
}
```

### `POST /equipment/{equipment_id}/health/assess`

Recompute from stored telemetry. `window` (20…2000, default 60). **404** if the
machine has no telemetry.

### `POST /health/score-all`

Score the whole fleet. `window` and `strategy`:

- `latest` *(default)* — score the newest reading. The instantaneous condition.
- `recent_worst` — score across the window and keep the worst result. It does
  not let a degradation hide behind a recovered latest reading, and it is the
  right choice for **ranking a fleet**.

`recent_worst` refreshes the cached columns without appending to history, so the
stored record remains what the machine's instantaneous condition actually was.

#### Which strategy to use

They answer different questions. Measured on the seeded 8-unit fleet at
`window=1440`:

| Strategy | Scores | Spread | Statuses |
|---|---|---|---|
| `latest` | 99.30 – 99.89 | 0.59 | Excellent |
| `recent_worst` | 42.20 – 69.25 | **27.05** | Poor, Warning |

`latest` is flat here because at the newest sample every machine is genuinely
nominal — the injected faults had already recovered. It reports the *instant*.

`recent_worst` is the discriminating view and ranks correctly: Air Compressor
42.20 (severity 0.651) down to Boiler 69.25 (severity 0.044).

#### Reading a floored score

The **critical floor** (`ml/scoring.py`) raises the sensor term whenever a
channel is at its alarm limit, so a confirmed excursion can never report as
Healthy. It interpolates on how far past the limit the worst channel is, so
machines past their limits still separate. Each response's `score_breakdown`
reports:

| Field | Meaning |
|---|---|
| `sensor_penalty` | The floored value that produced the score |
| `unfloored_sensor_penalty` | The sensor term *before* the policy raised it |
| `worst_excursion_severity` | Depth past the alarm limit, in band spans |
| `critical_floor_points` | Points the policy added |

Use `worst_excursion_severity` to rank breaching machines and
`unfloored_sensor_penalty` to see raw degradation, so the policy never hides
anything. See `ML_DOCUMENTATION.md` §12.

> ⚠️ `failure_probability`, `risk_level` and `risk_penalty` in this response are
> **uncalibrated**. The base hazard rate is a placeholder, not fitted to failure
> data, so the probability is a *relative index* for ranking machines — not a
> failure frequency. `calibrated: false` appears on every response that reports
> one. See § *Failure prediction* and `ML_DOCUMENTATION.md` §5.5.

### `GET /health/thresholds`

The status thresholds, risk thresholds, point budgets and weights in force.
Published so a score can be reproduced from configuration.

```json
{
  "status_thresholds": { "excellent_min": 90.0, "healthy_min": 75.0,
                         "warning_min": 50.0, "poor_min": 25.0 },
  "risk_thresholds": { "moderate_min": 0.1, "high_min": 0.25, "severe_min": 0.5 },
  "point_budgets": { "sensor": 70.0, "anomaly": 18.0, "risk": 12.0 },
  "weights": { "temperature": 0.22, "vibration": 0.28, "...": 0.0 }
}
```

---

## Failure prediction

> ⚠️ **`calibrated: false`.** The base hazard rate is a placeholder, not fitted to
> failure data. Probabilities are a **relative index** for ranking machines, not
> measured failure frequencies. See `ML_DOCUMENTATION.md` §5.

### `POST /predict`

```json
{
  "equipment_type": "Industrial Motor",
  "temperature": 118, "vibration": 9.5, "pressure": 4.2,
  "rpm": 1150, "current": 44, "voltage": 400, "power": 26, "load": 88,
  "horizon_hours": 720
}
```

`horizon_hours` defaults to **720 (30 days)**, the decision horizon the risk
*level* is judged on. All three horizons are always returned regardless.

```json
{
  "failure_probability": 0.1772,
  "risk_level": "Moderate",
  "horizon_hours": 720,
  "horizon_probabilities": { "24": 0.0065, "168": 0.0445, "720": 0.1772 },
  "dominant_driver": "temperature",
  "drivers": [
    { "parameter": "temperature", "degradation": 1.0,    "weight": 0.193,
      "hazard_multiplier": 54.598, "share": 0.7976 },
    { "parameter": "vibration",   "degradation": 0.5385, "weight": 0.2456,
      "hazard_multiplier": 8.618,  "share": 0.1602 }
  ],
  "method": "degradation_hazard",
  "calibrated": false,
  "notes": "Base hazard rate is a placeholder, not fitted to failure data. Treat this as a relative risk index..."
}
```

`hazard_multiplier` is `exp(4 × degradation)` — note the sharp rise: 8.6× at
half degradation versus 54.6× at the alarm limit. That asymmetry is the
wear-out behaviour a linear model cannot express.

`anomaly_score` may be supplied to override detector evidence; otherwise it is
derived from the machine's stored history.

### `GET /equipment/{equipment_id}/risk`

Risk from the machine's recent readings. `window` 20…2000.

### `GET /predict/model-info`

Model configuration and calibration state — base hazard, implied MTBF, hazard
sensitivity, thresholds, and a plain-language caveat.

---

## Calibration and failure history

Everything below is **administrative**: these endpoints change what the risk
model *is*, whereas `/predict` only asks it a question. `POST /predict/calibrate`
is the only operation that changes reported probabilities, and it is deliberately
explicit — recording a failure never recalibrates silently as a side effect.

The base hazard `lambda_0` is fitted as `1 / MTBF`, the mean service interval
between closed failures. With no history it is a placeholder, which is why
`calibrated: false` appears on every response carrying a probability.

### `GET /predict/calibration`

The live calibration state. Always returns a `reason`, so an uncalibrated
instance is told how many usable records it is short of rather than only that it
has not been fitted.

```json
{
  "calibrated": false,
  "base_hazard_per_hour": 2e-05,
  "suggested_base_hazard_per_hour": null,
  "implied_mtbf_hours": null,
  "implied_mtbf_years": null,
  "candidate_failures": 0,
  "usable": 0,
  "excluded_catalytic": 0,
  "excluded_outliers": 0,
  "mean_interval_hours": null,
  "stdev_interval_hours": null,
  "coefficient_of_variation": null,
  "thin_sample": false,
  "reason": "Need at least 5 usable closed failure records to calibrate; found 0. Record failures with POST /api/equipment/{equipment_id}/failures",
  "minimum_required": 5,
  "calibrated_at": null,
  "persisted_sample_size": null,
  "caveats": []
}
```

`base_hazard_per_hour` is the rate the model is **using**; it equals
`APP_BASE_HAZARD_PER_HOUR` until a fit is applied.

### `POST /predict/calibrate`

Fit `lambda_0 = 1 / MTBF` from the failure table and persist it. **200** with
`applied: false` when the sample is too small — nothing is written, and the
`reason` states the shortfall. On success `calibrated` becomes `true` in this
and every subsequent response that reports a probability.

Below 5 usable records the fit is refused outright. A rate fitted from a handful
of intervals would be confident and wrong, which is worse than an honest
placeholder: it converts a labelled relative index into a false measurement.

Records excluded from the fit:

| Excluded | Why |
|---|---|
| `is_catalytic: true` | Not wear-out (operator error, supply surge, impact) — the model describes wear |
| Interval < 1 hour | Almost certainly a data-entry error; would drag MTBF down |
| Interval > 1,000,000 hours | A single 1970 commissioning date would make the fleet look immortal |

`thin_sample: true` means the count passed but the spread of intervals is wide
(`coefficient_of_variation > 0.75`). The fit is still applied; the flag says how
much to trust it.

Persisted in `model_calibration`, not in `.env`, so a deployed instance keeps its
fit across restarts and a fitted rate is not silently overridden by an unrelated
environment edit.

### `DELETE /predict/calibration`

Discard the persisted fit and revert to `APP_BASE_HAZARD_PER_HOUR`. Returns
`cleared: false` if there was nothing to discard. Exposed rather than hidden:
recalibrating against a new batch of records should start from that evidence,
not silently average with a stale fit.

### `GET /equipment/{equipment_id}/failures`

Closed failure cycles for one machine, newest first. **404** if the machine is
unknown. `interval_hours` is the quantity the fit actually consumes.

```json
{
  "equipment_id": "EQ-001",
  "failures": [
    {
      "id": 3,
      "equipment_id": "EQ-001",
      "interval_start": "2025-01-01T00:00:00",
      "failed_at": "2025-08-14T06:12:00",
      "interval_hours": 5214.2,
      "is_catalytic": false,
      "failure_mode": "bearing wear",
      "note": "Outer race spalling"
    }
  ],
  "total": 1
}
```

### `POST /equipment/{equipment_id}/failures`

Record one closed failure cycle. **201**. **404** unknown machine; **422** if
`failed_at` precedes `interval_start`.

Naive timestamps are read as **UTC**, so operators can enter
`2025-01-01T00:00:00` without a suffix.

Recording does **not** recalibrate. Fitting is a separate, deliberate act.

| Field | Meaning |
|---|---|
| `interval_start` | Commissioning, or the previous return to service |
| `failed_at` | When the machine failed |
| `is_catalytic` | `true` for exogenous failures; excluded from the fit |
| `failure_mode` | Free text, ≤ 64 chars, never read by the model |
| `note` | Free text, ≤ 2000 chars, never read by the model |

### `DELETE /equipment/{equipment_id}/failures/{failure_id}`

Remove one record. **204** on success, **404** if the machine or the record is
unknown. The machine must exist even when the record does not.

---

## Explainability

### `GET /equipment/{equipment_id}/explain`

The full explanation for a machine. `window` 20…2000, default 60.

```json
{
  "health_score": 71.24,
  "status": "Warning",
  "explanation": "Health score 71.24. The score is reduced primarily because vibration has increased above the normal operating range (now 13) (-17 points).",
  "recommendation": "Inspect bearings and coupling alignment ...",
  "contributing_factors": [ /* as in /health/score */ ],
  "attribution": [
    {
      "parameter": "vibration", "value": 13.0, "nominal": 2.8,
      "expected_range": [0.0, 6.0], "alarm_limit": 12.5, "unit": "mm/s RMS",
      "direct_penalty": 17.19, "counterfactual_penalty": 88.5,
      "marginal_contribution": 17.19, "is_dominant": true,
      "would_fix": "Reduce vibration from 13 to at or below 6"
    }
  ],
  "operating_bands": [ { "parameter": "rpm", "nominal": 1450.0,
                          "ok_low": 1160.0, "ok_high": 1566.0,
                          "alarm_high": 1740.0, "low_relevant": true } ],
  "trending_parameters": [ { "parameter": "pressure", "drift": 2.67 } ],
  "suppressed_by_policy": [
    { "parameter": "temperature",
      "reason": "Charged 7.6 points by the linear weighting, but another channel's confirmed excursion is pinning the score at its floor..." }
  ],
  "trend": { "available": true, "change_over_window": -18.9, "direction": "declining" }
}
```

- **`attribution[].marginal_contribution`** — score improvement if *this channel
  alone* returned to nominal. The direct question an operator has.
- **`operating_bands`** — limits for every channel, including healthy ones, so
  charts can draw alarm lines and an operator can judge a reading absolutely.
- **`suppressed_by_policy`** — channels that are out of range but not charged,
  because the budget is saturated or another channel's breach pins the score.
  Reading the factor list alone would call them fine.
- **`trending_parameters`** — moving persistently while still *inside* limits.
  This is where early gradual wear shows, before it costs any points.
- **`trend`** — `available: false` with a stated `reason` when there is no
  assessment history. Clients must render that rather than an empty box.

**404** if the machine has no telemetry.

### `POST /explain`

The same payload for supplied values, without reading history. Used for ad-hoc
"what would this reading mean".

---

## Alerts

### `GET /alerts`

`equipment_id`, `severity` (`Info`/`Warning`/`Critical`), `alert_type`,
`resolved`, `limit` (1…1000), `offset`. Newest first.

```json
{
  "items": [
    {
      "alert_id": "2dadefb953bc4e3a97a404359743650c",
      "equipment_id": "EQ-007",
      "timestamp": "2026-09-30T06:41:12",
      "alert_type": "excessive_vibration",
      "severity": "Critical",
      "description": "Vibration is 10.08 mm/s RMS above its normal operating range (acceptable up to 5.5 mm/s RMS, alarm limit 13 mm/s RMS). It is 79% of the way to the alarm limit.",
      "recommended_action": "Inspect bearings and coupling alignment ...",
      "observed_value": "10.08 mm/s RMS",
      "threshold_value": "degradation >= 0.50",
      "resolved": false, "resolved_at": null, "acknowledged": false
    }
  ],
  "total": 13, "limit": 100, "offset": 0,
  "counts_by_severity": { "Critical": 13, "Warning": 0, "Info": 0 },
  "unresolved_total": 13
}
```

Alert **types**: `low_health_score`, `high_temperature`, `excessive_vibration`,
`pressure_instability`, `anomaly_detected`, `high_failure_risk`.

### `GET /alerts/summary`

Counts for dashboard badges: `unresolved_total`, `counts_by_severity`, and
flattened `critical` / `warning` / `info`.

### `POST /alerts/evaluate`

Run the rules engine and reconcile alerts with current state.

```json
{ "equipment_id": "EQ-001", "window": 1440 }
```

Omit `equipment_id` to evaluate the whole fleet.

Opens alerts for newly fired conditions and, with `APP_AUTO_RESOLVE_ALERTS`,
closes those whose condition has cleared. Repeated evaluation of an unchanged
condition opens **nothing** — deduplication is enforced by a partial unique index
on `(equipment, type) WHERE resolved = false`.

```json
{ "equipment_id": "EQ-001", "opened": 2, "resolved": 0, "skipped": 0,
  "opened_alerts": [ /* ... */ ] }
```

The context is built from the **worst reading in the window**, matching the
dashboard and the detail page. Scoring only the newest sample would make a
recent excursion invisible and let an alert clear before anyone saw it.

### `POST /alerts/{alert_id}/resolve`

Optional `{"note": "bearing replaced"}`; the note is retained on the record.

### `POST /alerts/{alert_id}/acknowledge`

Marks seen **without** resolving. The condition is still active.

---

## Dashboard

### `GET /dashboard/summary`

One request for the whole KPI row, so the dashboard's first paint is not one
request per statistic.

```json
{
  "total_equipment": 8,
  "assessed_equipment": 8,
  "unassessed_equipment": 0,
  "average_health_score": 71.4,
  "counts_by_status": { "Excellent": 0, "Healthy": 0, "Warning": 8, "Poor": 0, "Critical": 0 },
  "open_alerts": 13,
  "alerts_by_severity": { "Critical": 13, "Warning": 0, "Info": 0 },
  "needs_attention": [
    { "equipment_id": "EQ-007", "equipment_name": "Boiler", "health_score": 71.24,
      "status": "Warning", "risk_level": "High", "location": "Utilities Bay" }
  ],
  "last_assessed_at": "2026-09-30T06:41:12",
  "generated_at": "2026-09-30T06:41:15+00:00"
}
```

**Unassessed units are reported separately** rather than folded into a health
band, so a fleet that has never been scored looks unscored rather than healthy.
`counts_by_status` always contains all five keys; the dashboard renders a bar per
band.

---

## Configuration

Every setting is an environment variable prefixed `APP_`, read from the
environment then `.env`. No secrets are in the repository; see `.env.example`.

### Application

| Variable | Default | Purpose |
|---|---|---|
| `APP_APP_NAME` | `Industrial Equipment Health Scoring System` | Shown in `/health` and the OpenAPI title |
| `APP_APP_VERSION` | `0.1.0` | Shown in `/health` |
| `APP_ENVIRONMENT` | `development` | |
| `APP_DEBUG` | `false` | Enables SQL echo and the 500 traceback log |
| `APP_LOG_LEVEL` | `INFO` | |
| `APP_API_PREFIX` | `/api` | |
| `APP_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated |
| `APP_HOST` | `0.0.0.0` | Used by `make run` |
| `APP_PORT` | `8000` | Used by `make run` |

### Database

| Variable | Default | Purpose |
|---|---|---|
| `APP_DATABASE_URL` | SQLite in `data/` | PostgreSQL URL supported but untested |
| `APP_MODELS_DIR` | `models/` | Trained artifacts |
| `APP_DATA_DIR` | `data/` | |

### Scoring weights

Relative importance within the sensor budget. Renormalised over the channels
actually reported, so adding a channel cannot change the meaning of the others.

| Variable | Default |
|---|---|
| `APP_TEMPERATURE_WEIGHT` | 0.22 |
| `APP_VIBRATION_WEIGHT` | 0.28 |
| `APP_PRESSURE_WEIGHT` | 0.14 |
| `APP_POWER_WEIGHT` | 0.18 |
| `APP_CURRENT_WEIGHT` | 0.10 |
| `APP_RPM_WEIGHT` | 0.08 |
| `APP_VOLTAGE_WEIGHT` | 0.06 |
| `APP_LOAD_WEIGHT` | 0.08 |

The two model-derived terms have their own weights, applied within their own
budget:

| Variable | Default |
|---|---|
| `APP_ANOMALY_WEIGHT` | 0.20 |
| `APP_RISK_WEIGHT` | 0.18 |

### Point budgets

Must total ≤ 100; validated at scoring time.

| Variable | Default |
|---|---|
| `APP_SENSOR_POINTS_BUDGET` | 70.0 |
| `APP_ANOMALY_POINTS_BUDGET` | 18.0 |
| `APP_RISK_POINTS_BUDGET` | 12.0 |

### Status thresholds

| Variable | Default | Band |
|---|---|---|
| `APP_EXCELLENT_MIN` | 90.0 | ≥ 90 Excellent |
| `APP_HEALTHY_MIN` | 75.0 | ≥ 75 Healthy |
| `APP_WARNING_MIN` | 50.0 | ≥ 50 Warning |
| `APP_POOR_MIN` | 25.0 | ≥ 25 Poor, else Critical |

### Risk model

| Variable | Default | Purpose |
|---|---|---|
| `APP_BASE_HAZARD_PER_HOUR` | `2.0e-5` | ⚠️ **Placeholder.** Set to 1/MTBF from real failure data |
| `APP_RISK_MODEL_CALIBRATED` | `false` | ⚠️ Set `true` only after fitting the base rate |
| `APP_RISK_HAZARD_SENSITIVITY` | 4.0 | Wear-out exponent; e^4 ≈ 55× at full degradation |
| `APP_RISK_ANOMALY_MULTIPLIER` | 2.0 | Extra hazard from detector evidence |
| `APP_RISK_MODERATE_MIN` | 0.10 | 30-day probability thresholds |
| `APP_RISK_HIGH_MIN` | 0.25 | |
| `APP_RISK_SEVERE_MIN` | 0.50 | |

### Status caps

| Variable | Default | Purpose |
|---|---|---|
| `APP_ANOMALY_STATUS_CAP_MIN` | 0.80 | Anomaly score forcing at most Warning |
| `APP_HIGH_RISK_STATUS_CAP` | `Warning` | |
| `APP_SEVERE_RISK_STATUS_CAP` | `Poor` | |

### Alert rules

Thresholds are band degradation (0…1), not raw values, so one setting is
meaningful across equipment types.

| Variable | Default |
|---|---|
| `APP_ALERT_TEMPERATURE_THRESHOLD` | 0.50 |
| `APP_ALERT_VIBRATION_THRESHOLD` | 0.50 |
| `APP_ALERT_PRESSURE_THRESHOLD` | 0.60 |
| `APP_ALERT_ANOMALY_THRESHOLD` | 0.50 |
| `APP_ALERT_RISK_THRESHOLD` | 0.25 |
| `APP_ALERT_HEALTH_THRESHOLD` | 50.0 |
| `APP_AUTO_RESOLVE_ALERTS` | `true` |
| `APP_ALERT_PAGE_SIZE` | 100 |

---

## Error handling

Errors return FastAPI's shape:

```json
{ "detail": "Equipment 'EQ-999' not found" }
```

Validation failures return the field list:

```json
{
  "detail": [
    { "loc": ["body", "temperature"], "msg": "Input should be less than or equal to 300" }
  ]
}
```

An unhandled exception returns a **generic** 500 — the traceback is logged
server-side, never returned. Unexpected client states become 4xx:

| Situation | Response |
|---|---|
| Unknown equipment or alert | 404 |
| Duplicate equipment ID | 409 |
| No channels, implausible value, inconsistent combination | 422 |
| No telemetry when telemetry is required | 404 |
| No assessment when an assessment is required | 404 |
