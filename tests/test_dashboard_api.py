"""Dashboard summary endpoint tests.

The dashboard's first paint is a single request, so every field it renders
must be correct and must not silently become undefined when a machine has
never been assessed.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

EQUIPMENT = [
    {
        "equipment_id": "EQ-D01",
        "equipment_name": "Summary Pump",
        "equipment_type": "Hydraulic Pump",
        "location": "Test Bay",
    },
    {
        "equipment_id": "EQ-D02",
        "equipment_name": "Summary Motor",
        "equipment_type": "Industrial Motor",
        "location": "Test Bay",
    },
]

MOTOR_NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}

#: Per-type nominals. A pump and a motor have completely different acceptable
#: ranges -- feeding a motor's 4.2 bar to a pump is a genuine fault, and the
#: per-type profiles are supposed to catch it.
PUMP_NOMINAL = {**MOTOR_NOMINAL, "pressure": 100.0, "current": 22.0, "power": 13.0}

NOMINALS = {"EQ-D01": PUMP_NOMINAL, "EQ-D02": MOTOR_NOMINAL}


@pytest.fixture
def fleet(client: TestClient):
    for row in EQUIPMENT:
        assert client.post("/api/equipment", json=row).status_code == 201
    yield
    for row in EQUIPMENT:
        client.delete(f"/api/equipment/{row['equipment_id']}?hard=true")


def feed(client: TestClient, equipment_id: str, count: int, **overrides) -> None:
    from datetime import UTC, datetime, timedelta

    # Naive timestamps, as a real gateway sends. The API must accept them.
    base = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for i in range(count):
        payload = {
            "equipment_id": equipment_id, **NOMINALS[equipment_id], **overrides,
            "timestamp": (base - timedelta(minutes=count - i)).isoformat(),
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


# --- Shape ---

def test_summary_returns_all_dashboard_fields(client: TestClient) -> None:
    body = client.get("/api/dashboard/summary").json()

    for field in (
        "total_equipment", "assessed_equipment", "unassessed_equipment",
        "average_health_score", "counts_by_status", "open_alerts",
        "alerts_by_severity", "needs_attention", "last_assessed_at", "generated_at",
    ):
        assert field in body, f"missing {field}"


def test_counts_by_status_covers_every_band(client: TestClient) -> None:
    """The dashboard renders a bar per band, so a missing key breaks it."""
    counts = client.get("/api/dashboard/summary").json()["counts_by_status"]

    assert set(counts) == {"Excellent", "Healthy", "Warning", "Poor", "Critical"}


def test_alerts_by_severity_covers_every_level(client: TestClient) -> None:
    counts = client.get("/api/dashboard/summary").json()["alerts_by_severity"]

    assert set(counts) == {"Critical", "Warning", "Info"}


# --- Unassessed fleet ---

def test_unassessed_fleet_is_reported_honestly(client: TestClient, fleet) -> None:
    """A fleet that has never been scored must not look healthy.

    Folding unassessed units into a health band would make an empty database
    render as a wall of green, which is the most dangerous possible default.
    """
    body = client.get("/api/dashboard/summary").json()

    assert body["total_equipment"] >= 2
    assert body["assessed_equipment"] == 0
    assert body["unassessed_equipment"] == body["total_equipment"]
    assert body["average_health_score"] is None
    assert sum(body["counts_by_status"].values()) == 0


# --- Assessed fleet ---

def test_assessed_equipment_updates_counts(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40)
    feed(client, "EQ-D02", 40)

    client.post("/api/health/score-all", params={"window": 60, "strategy": "recent_worst"})
    body = client.get("/api/dashboard/summary").json()

    assert body["assessed_equipment"] >= 2
    assert body["unassessed_equipment"] == 0
    assert body["average_health_score"] is not None
    assert sum(body["counts_by_status"].values()) == body["assessed_equipment"]


def test_degraded_machine_appears_in_needs_attention(
    client: TestClient, fleet
) -> None:
    feed(client, "EQ-D01", 40, temperature=130.0)
    feed(client, "EQ-D02", 40)

    client.post("/api/health/score-all", params={"window": 60, "strategy": "recent_worst"})
    body = client.get("/api/dashboard/summary").json()

    attention = body["needs_attention"]
    assert attention, "a machine at 130 C should need attention"
    # Worst first.
    scores = [row["health_score"] for row in attention]
    assert scores == sorted(scores)
    for row in attention:
        assert row["status"] in {"Warning", "Poor", "Critical"}
        assert set(row) >= {
            "equipment_id", "equipment_name", "equipment_type",
            "location", "health_score", "status", "risk_level", "last_updated",
        }


def test_needs_attention_is_capped(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40, temperature=130.0)
    client.post("/api/health/score-all", params={"window": 60, "strategy": "recent_worst"})

    body = client.get("/api/dashboard/summary").json()

    assert len(body["needs_attention"]) <= 5


def test_healthy_fleet_has_no_needs_attention(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40)
    feed(client, "EQ-D02", 40)
    client.post("/api/health/score-all", params={"window": 60, "strategy": "recent_worst"})

    body = client.get("/api/dashboard/summary").json()

    assert body["needs_attention"] == []


# --- Alerts ---

def test_summary_reflects_open_alerts(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40, temperature=130.0)
    client.post("/api/alerts/evaluate", json={"window": 60})

    body = client.get("/api/dashboard/summary").json()

    assert body["open_alerts"] >= 1
    assert body["alerts_by_severity"]["Critical"] >= 1


def test_resolved_alerts_leave_the_open_count(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40, temperature=130.0)
    client.post("/api/alerts/evaluate", json={"window": 60})
    before = client.get("/api/dashboard/summary").json()["open_alerts"]

    alert = client.get("/api/alerts", params={"resolved": False}).json()["items"][0]
    client.post(f"/api/alerts/{alert['alert_id']}/resolve")

    after = client.get("/api/dashboard/summary").json()["open_alerts"]
    assert after == before - 1


# --- Retired units ---

def test_retired_equipment_is_excluded_from_totals(client: TestClient, fleet) -> None:
    feed(client, "EQ-D01", 40)
    client.post("/api/health/score-all", params={"window": 60, "strategy": "recent_worst"})
    before = client.get("/api/dashboard/summary").json()["total_equipment"]

    client.delete("/api/equipment/EQ-D02")

    body = client.get("/api/dashboard/summary").json()
    assert body["total_equipment"] == before - 1
