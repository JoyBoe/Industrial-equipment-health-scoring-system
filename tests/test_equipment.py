"""Equipment CRUD, filtering, and schema validation tests.

Each test uses a unique equipment ID and cleans up after itself, so the suite
is order-independent.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.equipment import Equipment

pytestmark = pytest.mark.usefixtures("client")

VALID_PAYLOAD = {
    "equipment_id": "EQ-T01",
    "equipment_name": "Test Pump",
    "equipment_type": "Hydraulic Pump",
    "location": "Test Bay",
    "operating_hours": 100.0,
}


@pytest.fixture
def make_equipment(client: TestClient):
    """Factory that registers equipment and removes it afterwards.

    Yields the factory; the `client` fixture remains injectable in the same
    test for follow-up requests.
    """
    created: list[str] = []

    def _make(**overrides) -> dict:
        payload = {**VALID_PAYLOAD, **overrides}
        response = client.post("/api/equipment", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        created.append(body["equipment_id"])
        return body

    yield _make

    for equipment_id in created:
        client.delete(f"/api/equipment/{equipment_id}?hard=true")


# --- Creation ---

def test_create_equipment(make_equipment) -> None:
    body = make_equipment(equipment_id="EQ-CRT1")

    assert body["equipment_id"] == "EQ-CRT1"
    assert body["is_active"] is True
    assert body["health_score"] is None
    assert body["health_status"] is None


def test_create_equipment_canonicalises_id(make_equipment) -> None:
    # Lower-case input must not create a second row for the same unit.
    body = make_equipment(equipment_id="  eq-crt2 ")

    assert body["equipment_id"] == "EQ-CRT2"


def test_duplicate_equipment_id_returns_409(client: TestClient, make_equipment) -> None:
    make_equipment(equipment_id="EQ-DUP1")

    response = client.post(
        "/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-DUP1"}
    )

    assert response.status_code == 409


def test_duplicate_detection_is_case_insensitive(client: TestClient) -> None:
    first = client.post("/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-DUP2"})
    assert first.status_code == 201
    try:
        second = client.post(
            "/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "eq-dup2"}
        )
        assert second.status_code == 409
    finally:
        client.delete("/api/equipment/EQ-DUP2?hard=true")


# --- Validation ---

@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"equipment_id": "A"}, "id too short"),
        ({"equipment_name": "x"}, "name too short"),
        ({"operating_hours": -5}, "negative hours"),
        ({"operating_hours": 999999}, "implausible hours"),
        ({"rated_power_kw": 0}, "zero power"),
        ({"installation_date": "2099-01-01"}, "future installation"),
        ({"location": "   "}, "blank location"),
    ],
)
def test_invalid_equipment_payloads_rejected(client: TestClient, overrides, reason) -> None:
    response = client.post("/api/equipment", json={**VALID_PAYLOAD, **overrides})

    assert response.status_code == 422, reason


def test_missing_required_fields_rejected(client: TestClient) -> None:
    response = client.post("/api/equipment", json={"equipment_id": "EQ-NOFIELDS"})

    assert response.status_code == 422
    assert len(response.json()["detail"]) >= 3


def test_optional_fields_default_to_null(client: TestClient) -> None:
    response = client.post("/api/equipment", json=VALID_PAYLOAD)
    body = response.json()
    try:
        assert body["manufacturer"] is None
        assert body["installation_date"] is None
        assert body["rated_power_kw"] is None
    finally:
        client.delete(f"/api/equipment/{body['equipment_id']}?hard=true")


# --- Retrieval ---

def test_get_equipment_by_id(client: TestClient, make_equipment) -> None:
    created = make_equipment(equipment_id="EQ-GET1")

    response = client.get("/api/equipment/EQ-GET1")

    assert response.status_code == 200
    assert response.json()["equipment_id"] == created["equipment_id"]


def test_get_equipment_is_case_insensitive(client: TestClient, make_equipment) -> None:
    make_equipment(equipment_id="EQ-GET2")

    assert client.get("/api/equipment/eq-get2").status_code == 200


def test_get_unknown_equipment_returns_404(client: TestClient) -> None:
    response = client.get("/api/equipment/EQ-NOPE")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_equipment_returns_list(client: TestClient) -> None:
    response = client.get("/api/equipment")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


# --- Update ---

def test_patch_updates_only_supplied_fields(client: TestClient, make_equipment) -> None:
    make_equipment(equipment_id="EQ-UPD1", equipment_name="Original Name")

    response = client.patch("/api/equipment/EQ-UPD1", json={"location": "New Bay"})

    assert response.status_code == 200
    body = response.json()
    assert body["location"] == "New Bay"
    assert body["equipment_name"] == "Original Name"


def test_patch_unknown_equipment_returns_404(client: TestClient) -> None:
    # Validation runs before the lookup, so use a valid payload here to isolate
    # the 404 path.
    assert client.patch("/api/equipment/EQ-NOPE", json={"location": "Valid Bay"}).status_code == 404


def test_patch_validates_payload_before_lookup(client: TestClient) -> None:
    # An invalid payload is a 422 even for a non-existent unit: FastAPI
    # validates the body before the handler runs.
    assert client.patch("/api/equipment/EQ-NOPE", json={"location": "X"}).status_code == 422


def test_patch_cannot_change_equipment_id(client: TestClient) -> None:
    # equipment_id is the stable external key. An attempt to change it must be
    # rejected outright, not silently dropped.
    body = client.post("/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-UPD2"})
    assert body.status_code == 201
    try:
        response = client.patch("/api/equipment/EQ-UPD2", json={"equipment_id": "EQ-HIJACK"})

        assert response.status_code == 422
        assert client.get("/api/equipment/EQ-UPD2").json()["equipment_id"] == "EQ-UPD2"
    finally:
        client.delete("/api/equipment/EQ-UPD2?hard=true")


# --- Deletion ---

def test_delete_deactivates_by_default(client: TestClient) -> None:
    body = client.post("/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-DEL1"})
    assert body.status_code == 201

    response = client.delete("/api/equipment/EQ-DEL1")

    assert response.status_code == 200
    assert response.json()["kind"] == "deactivated"
    # Retired units disappear from the default active-only listing...
    ids = [row["equipment_id"] for row in client.get("/api/equipment").json()]
    assert "EQ-DEL1" not in ids
    # ...but remain retrievable directly, preserving history.
    assert client.get("/api/equipment/EQ-DEL1").json()["is_active"] is False


def test_hard_delete_removes_equipment(client: TestClient) -> None:
    client.post("/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-DEL2"})

    response = client.delete("/api/equipment/EQ-DEL2?hard=true")

    assert response.status_code == 200
    assert client.get("/api/equipment/EQ-DEL2").status_code == 404


def test_delete_unknown_equipment_returns_404(client: TestClient) -> None:
    assert client.delete("/api/equipment/EQ-NOPE").status_code == 404


# --- Filtering and sorting ---

def test_search_filter_matches_name(client: TestClient, make_equipment) -> None:
    make_equipment(equipment_id="EQ-FLT1", equipment_name="Filter Target Alpha")

    response = client.get("/api/equipment", params={"search": "Filter Target Alpha"})

    assert response.status_code == 200
    rows = response.json()
    assert rows, "expected at least the seeded unit"
    assert all("Filter Target" in row["equipment_name"] for row in rows)


def test_search_filter_is_case_insensitive(client: TestClient, make_equipment) -> None:
    make_equipment(equipment_id="EQ-FLT2", equipment_name="Unique Widget Machine")

    response = client.get("/api/equipment", params={"search": "unique widget"})

    assert [row["equipment_id"] for row in response.json()] == ["EQ-FLT2"]


def test_search_filter_with_no_match_returns_empty(client: TestClient) -> None:
    response = client.get("/api/equipment", params={"search": "zzz-no-such-unit-zzz"})

    assert response.status_code == 200
    assert response.json() == []


def test_sort_by_health_score_desc(client: TestClient) -> None:
    response = client.get(
        "/api/equipment", params={"sort_by": "health_score", "sort_order": "desc"}
    )

    assert response.status_code == 200
    scores = [
        row["health_score"] for row in response.json() if row["health_score"] is not None
    ]
    assert scores == sorted(scores, reverse=True)


def test_invalid_sort_order_rejected(client: TestClient) -> None:
    assert client.get("/api/equipment", params={"sort_order": "sideways"}).status_code == 422


def test_pagination_bounds_validated(client: TestClient) -> None:
    assert client.get("/api/equipment", params={"limit": 0}).status_code == 422
    assert client.get("/api/equipment", params={"limit": 100000}).status_code == 422
    assert client.get("/api/equipment", params={"offset": -1}).status_code == 422


def test_filter_options_lists_distinct_values(client: TestClient) -> None:
    response = client.get("/api/equipment/filter-options")

    assert response.status_code == 200
    body = response.json()
    assert "equipment_types" in body
    assert "locations" in body


def test_filter_options_route_not_shadowed_by_id_route(client: TestClient) -> None:
    # `/equipment/filter-options` must not be parsed as an equipment_id.
    assert client.get("/api/equipment/filter-options").status_code == 200


# --- Cascade ---

def test_hard_delete_cascades_to_dependent_rows(db: Session, client: TestClient) -> None:
    from app.models.sensor_reading import SensorReading

    client.post("/api/equipment", json={**VALID_PAYLOAD, "equipment_id": "EQ-CASC"})
    equipment = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-CASC"))
    db.add(SensorReading(equipment_db_id=equipment.id, temperature=50.0, vibration=2.0))
    db.commit()

    assert db.scalar(select(SensorReading).where(SensorReading.equipment_db_id == equipment.id))

    client.delete("/api/equipment/EQ-CASC?hard=true")

    remaining = db.scalar(
        select(SensorReading).where(SensorReading.equipment_db_id == equipment.id)
    )
    assert remaining is None
