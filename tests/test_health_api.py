"""Health scoring API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.models.health_score import HealthScore

EQUIPMENT = {
    "equipment_id": "EQ-H01",
    "equipment_name": "Health Test Motor",
    "equipment_type": "Industrial Motor",
    "location": "Test Bay",
}

# At this machine's nominal for every channel.
NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}

OVERHEATING = {**NOMINAL, "temperature": 120.0, "vibration": 3.0}


@pytest.fixture
def equipment(client: TestClient):
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    yield response.json()
    client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


def feed(client: TestClient, count: int, **overrides) -> None:
    for i in range(count):
        payload = {
            "equipment_id": "EQ-H01", **NOMINAL, **overrides,
            "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


# --- POST /health/score ---

def test_score_healthy_values(client: TestClient, equipment) -> None:
    response = client.post("/api/health/score", json={"equipment_id": "EQ-H01", **NOMINAL})

    assert response.status_code == 200
    body = response.json()
    # Scoring by equipment_id also runs the anomaly detector, so a small
    # deduction is expected even at nominal values.
    assert body["health_score"] >= 99.0
    assert body["status"] == "Excellent"
    assert [f for f in body["contributing_factors"] if f["source"] == "sensor"] == []
    assert body["explanation"]
    assert body["recommendation"]


def test_score_overheating(client: TestClient, equipment) -> None:
    response = client.post("/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING})

    body = response.json()
    assert body["health_score"] < 75
    assert body["status"] in {"Warning", "Poor", "Critical"}
    assert any(f["parameter"] == "temperature" for f in body["contributing_factors"])


def test_score_response_matches_brief_contract(client: TestClient, equipment) -> None:
    """The response must carry every field the specification names."""
    body = client.post(
        "/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING}
    ).json()

    for field in (
        "equipment_id", "health_score", "status", "risk_level", "anomaly_score",
        "anomaly_detected", "failure_probability", "contributing_factors",
        "explanation", "recommendation",
    ):
        assert field in body, f"missing {field}"

    for factor in body["contributing_factors"]:
        assert "parameter" in factor
        assert "impact" in factor
        assert "penalty" in factor


def test_score_breakdown_reconciles(client: TestClient, equipment) -> None:
    """The published breakdown must add up to the published score."""
    body = client.post(
        "/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING}
    ).json()
    breakdown = body["score_breakdown"]

    total = breakdown["sensor_penalty"] + breakdown["anomaly_penalty"] + breakdown["risk_penalty"]
    assert body["health_score"] == pytest.approx(100.0 - total, abs=0.05)

    factors_total = sum(f["penalty"] for f in body["contributing_factors"])
    assert factors_total == pytest.approx(total, abs=0.05)


def test_score_by_equipment_type_without_persisting(client: TestClient) -> None:
    response = client.post(
        "/api/health/score", json={"equipment_type": "Industrial Motor", **NOMINAL}
    )

    assert response.status_code == 200
    # Nominal is not a perfect score: a healthy machine still carries baseline
    # failure risk.
    assert response.json()["health_score"] >= 99.0
    assert response.json()["status"] == "Excellent"


def test_same_value_scores_differently_by_type(client: TestClient) -> None:
    values = {"temperature": 170.0, "vibration": 2.8}
    motor = client.post(
        "/api/health/score", json={"equipment_type": "Industrial Motor", **values}
    ).json()
    boiler = client.post(
        "/api/health/score", json={"equipment_type": "Boiler", **values}
    ).json()

    assert motor["health_score"] < boiler["health_score"]


def test_score_unknown_equipment_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/health/score", json={"equipment_id": "EQ-NOPE", **NOMINAL}
    )

    assert response.status_code == 404


def test_score_requires_target(client: TestClient) -> None:
    assert client.post("/api/health/score", json=NOMINAL).status_code == 422


def test_score_requires_a_channel(client: TestClient, equipment) -> None:
    assert client.post(
        "/api/health/score", json={"equipment_id": "EQ-H01"}
    ).status_code == 422


def test_score_rejects_out_of_range_value(client: TestClient, equipment) -> None:
    assert client.post(
        "/api/health/score", json={"equipment_id": "EQ-H01", "temperature": 9999}
    ).status_code == 422


def test_score_persists_by_default(client: TestClient, equipment, db: Session) -> None:
    client.post("/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING})

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    count = db.scalar(
        select(func.count(HealthScore.id)).where(HealthScore.equipment_db_id == row.id)
    )
    assert count == 1


def test_score_respects_persist_false(client: TestClient, equipment, db: Session) -> None:
    client.post(
        "/api/health/score",
        json={"equipment_id": "EQ-H01", **OVERHEATING, "persist": False},
    )

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    count = db.scalar(
        select(func.count(HealthScore.id)).where(HealthScore.equipment_db_id == row.id)
    )
    assert count == 0


def test_scoring_refreshes_equipment_cache(
    client: TestClient, equipment, db: Session
) -> None:
    """The dashboard reads cached columns, so they must track the score."""
    client.post("/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING})
    db.expire_all()

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    assert row.status in {"Warning", "Poor", "Critical"}
    assert row.health_score is not None
    assert row.last_assessed_at is not None


def test_history_is_append_only(client: TestClient, equipment, db: Session) -> None:
    """Each assessment is retained, so the trend is auditable."""
    for temperature in (68.0, 90.0, 120.0):
        client.post(
            "/api/health/score",
            json={"equipment_id": "EQ-H01", **NOMINAL, "temperature": temperature},
        )

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    scores = [
        s.health_score
        for s in db.scalars(
            select(HealthScore)
            .where(HealthScore.equipment_db_id == row.id)
            .order_by(HealthScore.id)
        ).all()
    ]

    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)


# --- GET /equipment/{id}/health ---

def test_get_health_after_scoring(client: TestClient, equipment) -> None:
    client.post("/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING})

    body = client.get("/api/equipment/EQ-H01/health").json()

    assert body["equipment_id"] == "EQ-H01"
    assert body["status"] in {"Warning", "Poor", "Critical"}
    assert body["explanation"]


def test_get_health_reports_delta(client: TestClient, equipment) -> None:
    client.post("/api/health/score", json={"equipment_id": "EQ-H01", **NOMINAL})
    healthy = client.get("/api/equipment/EQ-H01/health").json()["health_score"]
    client.post("/api/health/score", json={"equipment_id": "EQ-H01", **OVERHEATING})

    body = client.get("/api/equipment/EQ-H01/health").json()

    assert body["previous_score"] == pytest.approx(healthy)
    assert body["delta"] < 0


def test_get_health_without_assessment_returns_404(client: TestClient, equipment) -> None:
    response = client.get("/api/equipment/EQ-H01/health")

    assert response.status_code == 404
    assert "no health assessment" in response.json()["detail"].lower()


def test_get_health_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/equipment/EQ-NOPE/health").status_code == 404


# --- Trend ---

def test_health_trend(client: TestClient, equipment) -> None:
    for temperature in (68.0, 80.0, 100.0, 120.0):
        client.post(
            "/api/health/score",
            json={"equipment_id": "EQ-H01", **NOMINAL, "temperature": temperature},
        )

    body = client.get("/api/equipment/EQ-H01/health/trend").json()

    assert body["count"] == 4
    assert body["latest"] == body["points"][-1]["health_score"]
    assert body["max_score"] >= body["min_score"]
    scores = [p["health_score"] for p in body["points"]]
    assert scores == sorted(scores, reverse=True)


def test_health_trend_empty(client: TestClient, equipment) -> None:
    body = client.get("/api/equipment/EQ-H01/health/trend").json()

    assert body["count"] == 0
    assert body["points"] == []


def test_health_trend_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/equipment/EQ-NOPE/health/trend").status_code == 404


# --- Assess from telemetry ---

def test_assess_from_stored_telemetry(client: TestClient, equipment) -> None:
    feed(client, 40, temperature=118.0)

    response = client.post("/api/equipment/EQ-H01/health/assess")

    assert response.status_code == 200
    body = response.json()
    assert body["health_score"] < 75
    assert "temperature" in [f["parameter"] for f in body["contributing_factors"]]


def test_assess_healthy_telemetry_scores_well(client: TestClient, equipment) -> None:
    feed(client, 40)

    body = client.post("/api/equipment/EQ-H01/health/assess").json()

    assert body["health_score"] >= 90


def test_assess_without_telemetry_returns_404(client: TestClient, equipment) -> None:
    response = client.post("/api/equipment/EQ-H01/health/assess")

    assert response.status_code == 404
    assert "no telemetry" in response.json()["detail"].lower()


def test_assess_unknown_equipment(client: TestClient) -> None:
    assert client.post("/api/equipment/EQ-NOPE/health/assess").status_code == 404


# --- Fleet and configuration ---

def test_score_all_fleet(client: TestClient, equipment) -> None:
    feed(client, 40)

    response = client.post("/api/health/score-all")

    assert response.status_code == 200
    assert response.json()["scored"] >= 1


def test_thresholds_endpoint_exposes_configuration(client: TestClient) -> None:
    body = client.get("/api/health/thresholds").json()

    assert body["status_thresholds"]["excellent_min"] == 90.0
    assert body["point_budgets"]["sensor"] == 70.0
    assert "vibration" in body["weights"]


def test_thresholds_sum_to_a_full_score(client: TestClient) -> None:
    """The budgets must partition the 100 available points."""
    budgets = client.get("/api/health/thresholds").json()["point_budgets"]

    assert sum(budgets.values()) == pytest.approx(100.0)


# --- Fleet strategies ---

def test_score_all_recent_worst_strategy(
    client: TestClient, equipment, db: Session
) -> None:
    """`recent_worst` refreshes the fleet from the worst reading in the window."""
    feed(client, 40)
    feed(client, 1, temperature=125.0)

    response = client.post("/api/health/score-all", params={"strategy": "recent_worst"})
    assert response.status_code == 200
    assert response.json()["scored"] >= 1

    db.expire_all()
    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    assert row.health_score < 75
    assert row.status in {"Warning", "Poor", "Critical"}


def test_recent_worst_does_not_append_history(
    client: TestClient, equipment, db: Session
) -> None:
    """The stored history must stay a record of instantaneous condition."""
    feed(client, 40)

    client.post("/api/health/score-all", params={"strategy": "recent_worst"})

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-H01"))
    count = db.scalar(
        select(func.count(HealthScore.id)).where(HealthScore.equipment_db_id == row.id)
    )
    assert count == 0


def test_score_all_rejects_unknown_strategy(client: TestClient) -> None:
    assert client.post(
        "/api/health/score-all", params={"strategy": "nonsense"}
    ).status_code == 422


def test_recent_worst_is_never_better_than_latest(
    client: TestClient, equipment
) -> None:
    """A worst-case view can only report equal or worse health."""
    feed(client, 60, temperature=125.0)

    client.post("/api/health/score-all", params={"strategy": "latest"})
    latest = client.get("/api/equipment/EQ-H01").json()["health_score"]

    client.post("/api/health/score-all", params={"strategy": "recent_worst"})
    worst = client.get("/api/equipment/EQ-H01").json()["health_score"]

    assert worst <= latest
