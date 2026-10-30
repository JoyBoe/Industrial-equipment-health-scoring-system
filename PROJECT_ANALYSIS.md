# PROJECT_ANALYSIS.md

**Project:** AI-Based Industrial Equipment Health Scoring System
**Directory:** `/home/joyboiee/Coding-Projects/Main-Projects/Industrial equipment health scoring system`
**Analysis date:** 2026-09-30
**Stage:** 1 — Project Analysis

---

## 1. Project Overview

Greenfield project. The directory was inspected recursively and found to be
**completely empty** — no source files, no hidden config, no VCS metadata, no
dependencies, no documentation. Nothing exists to preserve or migrate, so the
architecture below can be designed from scratch without conflicting with
prior work.

The goal is a production-shaped industrial monitoring system that ingests
multi-parameter machine telemetry (temperature, vibration, pressure, RPM,
current, voltage, power, load, operating hours), validates it, engineers
features, detects anomalies, estimates failure risk, and fuses those signals
into a transparent, explainable 0–100 health score exposed over a REST API and
rendered in a professional operations dashboard.

Key design constraint from the brief: **the score must be auditable**. Every
point deducted from 100 must be attributable to a named parameter with a
numeric contribution, so a user can answer "why is this machine at 63?".

---

## 2. Existing Structure

```
industrial-equipment-health-scoring-system/
└── (empty)
```

| Check | Result |
|---|---|
| Source files | none |
| Directories | none |
| `package.json` / `requirements.txt` / `pyproject.toml` | absent |
| Git repository | not initialized |
| Database files (`.db`, `.sqlite`) | absent |
| Serialized ML models (`joblib`, `pkl`) | absent |
| Datasets (`data/`) | absent |
| Docs (`README.md`, etc.) | absent |
| Tests | absent |
| `.env` / secrets | absent |

**Conclusion: empty project.**

---

## 3. Existing Technologies

None. No language, framework, or dependency has been selected yet. The
following were confirmed available on the host:

| Tool | Version |
|---|---|
| Python | 3.14.4 |
| pip | 25.1.1 |
| Node.js | 24.21.0 |
| npm | 11.19.0 |
| git | 2.53.0 |

Note: Python 3.14 is very new. Some scientific wheels (notably older
`scikit-learn` / `numpy` builds) may lag on 3.14. Stage 2 must verify
installability early and be prepared to pin a 3.12/3.13 interpreter if a
binary wheel is unavailable. The backend code itself targets 3.11+ syntax and
should avoid version-exclusive features.

---

## 4. Existing Functionality

None. There is no runnable application, no API, no database, and no ML code.

---

## 5. Existing Problems

Not applicable — no code exists, therefore no defects. The risks to manage
are *prospective*, and they define the architecture below:

1. **No labeled failure data.** Real industrial datasets (CWRU, Paderborn,
   XJTU-SY, NASA SMAP/MSL) are either unavailable offline or carry
   incompatible units. Mitigation: Isolation Forest / statistical detectors
   on a *correlated* synthetic dataset, with the loader isolated behind a
   provider interface so a real dataset can be swapped in later.
2. **Unit drift across machine types.** A hydraulic pump and a motor have
   entirely different acceptable temperature and vibration bands. A single
   global threshold table would be wrong. Mitigation: per-`equipment_type`
   baseline profiles.
3. **Uncalibrated scores.** Isolation Forest anomaly scores are not
   probabilities. They must be normalized and calibrated before entering
   the health formula, or the 0–100 output will be meaningless.
4. **Black-box risk.** If the health score is produced by a regressor alone,
   explainability is lost. Mitigation: a hybrid — ML provides anomaly/risk
   *features*, and a deterministic, inspectable weighted penalty engine
   produces the score.
5. **Python 3.14 wheel availability** (see §3).

---

## 6. Missing Components

Every component in the brief is missing. Broken out by layer:

**Backend / API**
- FastAPI application, app factory, CORS, lifespan, error handlers
- Routers: `equipment`, `sensors`, `health`, `anomaly`, `predict`, `alerts`
- Pydantic schemas with strict validation and range constraints
- Config module with env-var loading (`.env`, no hard-coded secrets)

**Data layer**
- SQLAlchemy engine/session setup, SQLite with WAL
- Models: `equipment`, `sensor_readings`, `health_scores`, `anomalies`, `alerts`
- Alembic (or lightweight `create_all` + seed for v1) migrations
- Seed script for equipment master data

**ML / analytics**
- Synthetic telemetry generator (correlated, normal + degraded + anomalous regimes)
- Preprocessing: imputation, scaling, feature engineering (rolling stats, deltas, rates-of-change)
- Isolation Forest trainer + inference wrapper
- Statistical z-score / EWMA detectors as a secondary signal
- Per-type baseline profiles (normal bands, alarm limits)
- Model persistence via Joblib, with a versioned artifact + metadata

**Scoring / business logic**
- Configurable scoring weights and health-status thresholds
- Penalty computation per parameter with per-parameter contributions
- Risk level classification
- Explainability generator (natural-language + structured factor list)
- Recommendation engine
- Alert rules engine and alert persistence

**Frontend**
- React + Vite + Tailwind app shell, layout, navigation
- Dashboard: summary KPIs, equipment table, filters
- Equipment detail page: info panel, health/risk/anomaly cards, Recharts time series
- AI insights panel rendering live explanations
- Alerts view
- API client layer with error/loading states

**Quality / ops**
- `tests/` covering scoring, classification, anomaly, validation, CRUD, edge cases
- `README.md`, `ARCHITECTURE.md`, `ML_DOCUMENTATION.md`, `API_DOCUMENTATION.md`
- `.env.example`, `.gitignore`, `Makefile`/task runner, `.git` init

---

## 7. Recommended Architecture

Layered, backend-for-frontend. Strict one-way dependency rule:

```
routes → services → (ml | repositories) → database
                 ↘ config
```

- **Routers** parse HTTP only; no business logic, no SQL.
- **Services** own business logic (scoring, alerting, explainability) and are
  framework-agnostic and unit-testable without HTTP.
- **Repositories** own all SQLAlchemy queries. No other layer imports ORM
  models for querying.
- **ML layer** owns models, training, and inference; exposes plain Python
  objects, never HTTP concerns.
- **Schemas** (Pydantic) are the only validation boundary for external input.

### Health scoring design (the core)

Score is **deterministic and auditable**, with ML as a contributing feature
rather than the final authority.

For each parameter *p*, compute a normalized degradation
`d_p ∈ [0,1]` by mapping the value through its baseline profile's
soft limits (`ok` → 0 penalty, `alarm` → 1 penalty, linear in between with an
overtemperature quadratic tail). Then:

```
raw_penalty_p = w_p · d_p · PENALTY_SCALE
health_score  = clamp(round(100 − Σ raw_penalty_p − w_anomaly·A − w_risk·R), 0, 100)
```

`A` is the normalized Isolation Forest anomaly score, `R` is the calibrated
failure probability. Every `raw_penalty_p` is retained, so the explainability
layer is a *formatting pass over the score computation itself* — not a
separate guess. A parallel drop-off (RPS / index-vs-previous) is surfaced as
an additional factor when the score falls.

Weights and all thresholds live in a single config object, overridable by
environment variables, with the resolved config embedded in every API
response for reproducibility.

```
Industrial Sensor Data
        ↓  Data Validation (schema + range + business sanity)
      Preprocessing (impute, scale)
      Feature Engineering (rolling mean/std, deltas, RPS, ratios)
        ↓
   Anomaly Detection (Isolation Forest ⊕ z-score ⊕ EWMA)
        ↓
 Failure Risk Estimation (calibrated probability, index→failure horizon)
        ↓
   Health Score Engine (weighted, auditable penalties)
        ↓
   Health Classification (thresholds)
        ↓
   Explainability (structured factors + generated rationale)
        ↓
   Recommendation Engine
        ↓
   Alert Rules (persist, dedupe, resolve)
        ↓
   Dashboard (React + Recharts)
```

---

## 8. Proposed Technology Stack

**Backend**
- Python 3.12+ (fallback interpreter if 3.14 wheels unavailable)
- FastAPI + Uvicorn
- Pydantic v2 (`pydantic-settings`)
- SQLAlchemy 2.x (ORM + Core, `select()` style)
- Alembic for schema evolution

**Data / ML**
- SQLite (dev) via SQLAlchemy; PostgreSQL-ready via URL swap
- NumPy, Pandas, scikit-learn (IsolationForest, calibration utilities)
- Joblib for model + scaler persistence
- Statsmodels *not* required — z-score/EWMA implemented directly in NumPy

**Frontend**
- React 18 + Vite
- Tailwind CSS
- Recharts
- TanStack Query (caching, polling, cache invalidation)
- React Router
- Axios or native `fetch` wrapper

**Tooling**
- pytest + httpx (API tests)
- ESLint + Prettier
- `.env.example`, `.gitignore`, `Makefile`

Dependency policy: no library added without a stated purpose; no deep learning.

---

## 9. AI/ML Approach

### Phase A — Unsupervised (default path, no labels available)

- **Isolation Forest** as the primary multivariate detector over the
  standardized 8-feature vector (temperature, vibration, pressure, rpm,
  current, voltage, power, load). Trees ≈ 100, contamination set from config
  (default `auto`).
- **Feature engineering** before detection: rolling mean/std over short
  windows, first differences, per-minute rates of change, derived
  relationships (power ≈ √3·V·I·PF, vibration RMS × rpm interaction). These
  make gradual degradation visible, which a point-in-time detector misses.
- **Statistical detectors** as complementary, explainable signals: per-parameter
  z-score against the equipment type's baseline, and CUSUM/EWMA for drift.
  These are what make a factor attributable to a *named* parameter.
- **Baseline profiles**: per-`equipment_type` normal band + alarm limit
  (e.g. motor: temp ok ≤ 75 °C, alarm 95 °C; vibration ok ≤ 4.5 mm/s,
  alarm 11.0 mm/s). Stored as data, editable, not hard-coded in logic.

### Phase B — Supervised (only if labeled failure data is obtained)

- Gradient Boosting / Random Forest classifier on the same engineered feature
  set, target = failure within a defined horizon.
- Deferred behind the same `RiskEstimator` interface so the backend API does
  not change when the model is swapped.

### Synthetic dataset

Generated by a regime-mixing simulator, not independent random draws:

- **Nominal regime** — parameters coupled through physical relations
  (power from V/I, thermal rise from load and speed), with small correlated
  noise and a slow diurnal duty cycle.
- **Degraded regime** — progressive drift: vibration and temperature creep
  upward, current and power rise with load, pressure gains variance, RPM
  sags under load. This is what makes gradual failure learnable/detectable.
- **Fault regime** — discrete injected faults: bearing spall (vibration spike
  with harmonics), overheating (thermal runaway), cavitation (pressure
  collapse + vibration + current ripple), overload (current/power spike).
- Metadata records `regime` and `fault_type` **for evaluation and
  stratification only** — these labels are never fed to the unsupervised
  detector, and the health score never reads them. They exist to measure
  detector precision/recall against a known ground truth.
- `data/SYNTHETIC_DATA.md` documents that this is development/test data only.

### Calibration

Isolation Forest scores are converted to a bounded degradation via the
training-score distribution (empirical CDF on training data, persisted with
the model), giving a stable 0–1 `anomaly_score` that means the same thing
across machines and retrains.

### Deliberate non-goals

- No neural networks. The dataset size and the explainability requirement
  both argue against them.
- No LLM-generated numbers. Explanations are templates filled from actual
  computed contributions.

---

## 10. Development Roadmap

| Stage | Scope | Exit criteria |
|---|---|---|
| 1 | Project analysis *(this document)* | `PROJECT_ANALYSIS.md` exists |
| 2 | Backend architecture — app factory, config, DB wiring, health-check route, base structure | `/api/health` responds; settings load from env |
| 3 | Database & equipment management — models, repositories, CRUD routers, seed | Equipment CRUD works via API and persists |
| 4 | Sensor pipeline — synthetic generator, validation, ingestion, rolling features | Readings ingest and query; generator is correlated, not iid |
| 5 | Anomaly detection — Isolation Forest train/infer, z-score/EWMA, persistence | Anomaly endpoint returns score + affected params on real ingested data |
| 6 | Health score engine — profiles, configurable weights, penalties, status thresholds | Score is reproducible and every penalty is traceable |
| 7 | Failure risk prediction — calibrated risk, risk levels, horizon | Risk is calibrated, not raw model output |
| 8 | Explainability — factors, rationale, recommendations | Output answers "why this score" numerically |
| 9 | Alert system — rules, persistence, dedupe, resolve | Alerts generated from real state, listed/resolvable |
| 10 | Frontend dashboard — shell, KPIs, equipment table, styling | Dashboard renders live API data |
| 11 | Equipment detail & charts — time series, health history, insights panel | Charts show real ingested data |
| 12 | Frontend/backend integration — client layer, polling, error states | End-to-end flow with no mock data |
| 13 | Testing — scoring, classification, anomaly, validation, CRUD, edge cases | Suite passes; invalid input rejected |
| 14 | UI/UX improvement — density, responsiveness, status affordances | Review-ready industrial look |
| 15 | Documentation — README, ARCHITECTURE, ML_DOCUMENTATION, API_DOCUMENTATION | Docs match implemented behaviour |
| 16 | Final audit — stale docs, dead code, secret scan, full-stack smoke test | Clean end-to-end run from a fresh clone |

**Critical path risk:** Stage 5's dependency install on Python 3.14. Validate
`scikit-learn` wheel availability at the start of Stage 2.

---

*Stage 1 is analysis only — no application code was written or modified.*
