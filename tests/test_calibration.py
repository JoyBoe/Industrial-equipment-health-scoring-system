"""Tests for risk-model calibration and failure recording.

The system ships with no failure history, so it must do two opposite things
well: refuse to pretend it is calibrated, and *become* calibrated the moment
real evidence arrives. These cover both, plus the honesty rule that matters
most -- a calibrated flag must never disagree with the rate the probability was
actually computed from.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.ml.calibration import (
    MIN_FAILURES_TO_CALIBRATE,
    FailureInterval,
    clean_intervals,
    fit_base_hazard,
)
from app.models.calibration import ModelCalibration
from app.models.equipment import Equipment
from app.models.failure import Failure

EQUIPMENT_ID = "CAL-001"
#: An interval that lands exactly on a round 1000 h, so a fitted MTBF can be
#: asserted without fighting floating-point noise.
INTERVAL_HOURS = 1000.0


@pytest.fixture(autouse=True)
def rig(db):
    """Give every test a machine, and guarantee it leaves no calibration behind.

    The test database is shared across the session, so a fit applied here would
    silently turn later, unrelated assertions about `calibrated: false` into
    failures. Teardown runs even when a test fails, which is exactly when a
    leaked fit would do the most damage.
    """
    equipment = Equipment(
        equipment_id=EQUIPMENT_ID,
        equipment_name="Calibration Rig",
        equipment_type="Industrial Motor",
        location="Test Bay",
    )
    db.add(equipment)
    db.commit()

    yield equipment

    db.execute(delete(Failure))
    db.execute(delete(ModelCalibration))
    db.delete(equipment)
    db.commit()


# --- Pure fitting -----------------------------------------------------------


class TestFittingRule:
    """`lambda_0 = 1 / MTBF`, and the conditions under which it is refused."""

    def test_refuses_below_the_minimum_sample(self) -> None:
        records = [FailureInterval(hours=INTERVAL_HOURS)] * 4
        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is False
        assert report.suggested_base_hazard_per_hour is None
        assert report.current_base_hazard_per_hour == pytest.approx(2.0e-5)

    def test_reason_names_the_exact_shortfall(self) -> None:
        """An operator holding `calibrated: false` needs to know what to do next."""
        report = fit_base_hazard([], current_base_hazard_per_hour=2.0e-5)

        assert str(MIN_FAILURES_TO_CALIBRATE) in report.reason
        assert "found 0" in report.reason
        assert "POST /api/equipment/" in report.reason

    def test_fits_the_inverse_of_the_mean_interval(self) -> None:
        records = [FailureInterval(hours=1000.0)] * 5
        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is True
        assert report.suggested_base_hazard_per_hour == pytest.approx(1.0 / 1000.0)
        assert report.implied_mtbf_hours == pytest.approx(1000.0)

    def test_catalytic_failures_are_excluded_from_the_fit(self) -> None:
        """An operator error is not evidence about wear-out hazard."""
        records = [FailureInterval(hours=1000.0, is_catalytic=True)] * 3
        records += [FailureInterval(hours=5000.0)] * 5

        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is True
        assert report.excluded_catalytic == 3
        assert report.usable == 5
        # If the catalytic rows had leaked in, MTBF would have been 3200 h.
        assert report.mean_interval_hours == pytest.approx(5000.0)

    def test_out_of_range_intervals_are_excluded_and_counted_separately(self) -> None:
        """A 1970 commissioning date must not make the fleet look immortal."""
        records = [FailureInterval(hours=1000.0)] * 4
        records.append(FailureInterval(hours=None))
        records.append(FailureInterval(hours=0.001))
        records.append(FailureInterval(hours=9_000_000.0))

        intervals, catalytic, outliers = clean_intervals(records)

        assert catalytic == 0
        assert outliers == 3
        # Four usable records: still short, and the count must reflect that the
        # three failures were excluded rather than silently absorbed.
        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)
        assert report.usable == 4
        assert report.calibrated is False

    def test_a_wide_spread_is_flagged_as_a_thin_sample(self) -> None:
        records = [
            FailureInterval(hours=100.0),
            FailureInterval(hours=100.0),
            FailureInterval(hours=100.0),
            FailureInterval(hours=100.0),
            FailureInterval(hours=90_000.0),
        ]
        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is True, "the count passed, so the fit must apply"
        assert report.thin_sample is True

    def test_a_tight_sample_is_not_flagged_as_thin(self) -> None:
        records = [FailureInterval(hours=1000.0 + i) for i in range(6)]
        report = fit_base_hazard(records, current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is True
        assert report.thin_sample is False

    def test_an_empty_sample_is_not_thin(self) -> None:
        """Thinness is a property of a fit that exists, not of having none."""
        report = fit_base_hazard([], current_base_hazard_per_hour=2.0e-5)

        assert report.calibrated is False
        assert report.thin_sample is False


# --- API: status and fitting ------------------------------------------------


class TestCalibrationStatus:
    def test_reports_uncalibrated_with_an_actionable_reason(self, client) -> None:
        body = client.get("/api/predict/calibration").json()

        assert body["calibrated"] is False
        assert body["base_hazard_per_hour"] == pytest.approx(2.0e-5)
        assert str(MIN_FAILURES_TO_CALIBRATE) in body["reason"]
        assert body["minimum_required"] == MIN_FAILURES_TO_CALIBRATE
        assert body["calibrated_at"] is None
        assert body["caveats"] == []

    def test_calibration_status_endpoint_exists(self, client) -> None:
        assert client.get("/api/predict/calibration").status_code == 200

    def test_calibrating_without_history_applies_nothing(self, client) -> None:
        response = client.post("/api/predict/calibrate")

        assert response.status_code == 200
        body = response.json()
        assert body["applied"] is False
        assert body["status"]["calibrated"] is False
        assert "found 0" in body["status"]["reason"]

    def test_refused_fit_persists_no_record(self, client, db) -> None:
        """A rejected fit must not leave a stale rate looking current."""
        client.post("/api/predict/calibrate")

        assert db.get(ModelCalibration, 1) is None

    def test_clearing_when_nothing_was_set_reports_false(self, client) -> None:
        body = client.delete("/api/predict/calibration").json()

        assert body["cleared"] is False
        assert body["status"]["calibrated"] is False


class TestRecordingFailures:
    def test_unknown_equipment_is_a_404_on_read(self, client) -> None:
        assert client.get("/api/equipment/NOPE-001/failures").status_code == 404

    def test_unknown_equipment_is_a_404_on_write(self, client) -> None:
        payload = {
            "interval_start": "2025-01-01T00:00:00",
            "failed_at": "2025-06-01T00:00:00",
        }
        assert (
            client.post("/api/equipment/NOPE-001/failures", json=payload).status_code
            == 404
        )

    def test_a_failed_interval_starting_after_the_failure_is_rejected(self, client) -> None:
        payload = {
            "interval_start": "2025-06-01T00:00:00",
            "failed_at": "2025-01-01T00:00:00",
        }
        response = client.post(f"/api/equipment/{EQUIPMENT_ID}/failures", json=payload)

        assert response.status_code == 422, (
            "A backwards interval would produce a negative MTBF; it must be "
            "rejected as malformed input rather than reaching the fit."
        )

    def test_naive_timestamps_are_read_as_utc(self, client) -> None:
        """Operators enter commissioning dates without a UTC suffix."""
        payload = {
            "interval_start": "2025-01-01T00:00:00",
            "failed_at": "2025-01-11T00:00:00",
        }
        response = client.post(f"/api/equipment/{EQUIPMENT_ID}/failures", json=payload)

        assert response.status_code == 201
        assert response.json()["interval_hours"] == pytest.approx(240.0)

    def test_aware_and_naive_inputs_agree(self, client) -> None:
        """A `+00:00` suffix must not shift the interval it describes."""
        naive = client.post(
            f"/api/equipment/{EQUIPMENT_ID}/failures",
            json={
                "interval_start": "2025-01-01T00:00:00",
                "failed_at": "2025-01-11T00:00:00",
            },
        )
        aware = client.post(
            f"/api/equipment/{EQUIPMENT_ID}/failures",
            json={
                "interval_start": "2025-03-01T00:00:00+00:00",
                "failed_at": "2025-03-11T00:00:00+00:00",
            },
        )

        assert naive.status_code == 201
        assert aware.status_code == 201
        assert naive.json()["interval_hours"] == pytest.approx(
            aware.json()["interval_hours"]
        )

    def test_recording_does_not_recalibrate_silently(self, client, db) -> None:
        """Fitting must be a deliberate act, not a side effect of data entry."""
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE)

        assert client.get("/api/predict/calibration").json()["calibrated"] is False
        assert db.get(ModelCalibration, 1) is None

    def test_failures_list_is_scoped_to_one_machine(self, client, db) -> None:
        other = Equipment(
            equipment_id="CAL-002",
            equipment_name="Other Rig",
            equipment_type="Gearbox",
            location="Test Bay",
        )
        db.add(other)
        db.commit()
        try:
            client.post(
                f"/api/equipment/{EQUIPMENT_ID}/failures",
                json={
                    "interval_start": "2025-01-01T00:00:00",
                    "failed_at": "2025-02-01T00:00:00",
                },
            )
            body = client.get(f"/api/equipment/{EQUIPMENT_ID}/failures").json()
            other_body = client.get("/api/equipment/CAL-002/failures").json()

            assert body["total"] == 1
            assert other_body["total"] == 0
            assert body["failures"][0]["equipment_id"] == EQUIPMENT_ID
        finally:
            db.delete(other)
            db.commit()

    def test_deleting_a_missing_record_is_a_404(self, client) -> None:
        response = client.delete(f"/api/equipment/{EQUIPMENT_ID}/failures/999999")

        assert response.status_code == 404

    def test_delete_returns_204_and_removes_the_row(self, client) -> None:
        created = client.post(
            f"/api/equipment/{EQUIPMENT_ID}/failures",
            json={
                "interval_start": "2025-01-01T00:00:00",
                "failed_at": "2025-02-01T00:00:00",
            },
        ).json()

        response = client.delete(
            f"/api/equipment/{EQUIPMENT_ID}/failures/{created['id']}"
        )

        assert response.status_code == 204
        assert (
            client.get(f"/api/equipment/{EQUIPMENT_ID}/failures").json()["total"] == 0
        )


def _record_n_failures(
    client,
    count: int,
    interval_hours: float = INTERVAL_HOURS,
    is_catalytic: bool = False,
) -> None:
    """Record `count` consecutive wear-out cycles of equal length.

    Each cycle starts where the previous one ended, because that is what a
    failure record actually describes: commissioning (or the last repair)
    through to the next failure. Building every record against a single shared
    start date would produce 4000, 8000, 12000... hours and mean something quite
    different from the stated MTBF.
    """
    cursor = datetime(2020, 1, 1, tzinfo=UTC)
    for _ in range(count):
        failed_at = cursor + timedelta(hours=interval_hours)
        response = client.post(
            f"/api/equipment/{EQUIPMENT_ID}/failures",
            json={
                "interval_start": cursor.isoformat(),
                "failed_at": failed_at.isoformat(),
                "is_catalytic": is_catalytic,
            },
        )
        assert response.status_code == 201, response.text
        cursor = failed_at


class TestApplyingACalibration:
    def test_a_sufficient_sample_calibrates_the_model(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE)

        body = client.post("/api/predict/calibrate").json()

        assert body["applied"] is True
        assert body["status"]["calibrated"] is True
        assert body["status"]["calibrated_at"] is not None
        assert body["status"]["persisted_sample_size"] == MIN_FAILURES_TO_CALIBRATE

    def test_the_applied_rate_is_one_over_the_observed_mtbf(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)

        status = client.post("/api/predict/calibrate").json()["status"]

        assert status["base_hazard_per_hour"] == pytest.approx(1.0 / 4000.0)
        assert status["implied_mtbf_hours"] == pytest.approx(4000.0)
        assert status["implied_mtbf_years"] == pytest.approx(4000.0 / 8760, rel=0.01)

    def test_calibration_survives_across_requests(self, client) -> None:
        """A persisted fit, not a process-local flag: it must outlive the call."""
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE)
        client.post("/api/predict/calibrate")

        assert client.get("/api/predict/calibration").json()["calibrated"] is True

    def test_post_fit_caveats_are_reported(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE)
        body = client.post("/api/predict/calibrate").json()["status"]

        assert body["caveats"], "A successful fit does not remove every caveat"

    def test_catalytic_failures_alone_never_calibrate(self, client, db) -> None:
        """Five operator errors are still zero evidence about wear."""
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, is_catalytic=True)

        body = client.post("/api/predict/calibrate").json()

        assert body["applied"] is False
        assert body["status"]["excluded_catalytic"] == MIN_FAILURES_TO_CALIBRATE
        assert db.get(ModelCalibration, 1) is None

    def test_clearing_reverts_to_the_placeholder(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)
        client.post("/api/predict/calibrate")

        body = client.delete("/api/predict/calibration").json()

        assert body["cleared"] is True
        assert body["status"]["calibrated"] is False
        assert body["status"]["base_hazard_per_hour"] == pytest.approx(2.0e-5)


# --- The flag must match the number ----------------------------------------


class TestTheFlagMatchesTheNumber:
    """The failure mode the calibration flag exists to prevent.

    A response claiming `calibrated: true` while its probability came from the
    placeholder, or the reverse, is worse than either state alone: it makes the
    flag itself untrustworthy, and the flag is the only thing standing between
    a reader and a misinterpretation.
    """

    def test_predict_reports_uncalibrated_before_a_fit(self, client) -> None:
        body = client.post(
            "/api/predict", json={"equipment_type": "Industrial Motor"}
        ).json()

        assert body["calibrated"] is False
        assert "placeholder" in body["notes"]

    def test_predict_reports_calibrated_after_a_fit(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)
        client.post("/api/predict/calibrate")

        response = client.post(
            "/api/predict", json={"equipment_type": "Industrial Motor"}
        )
        body = response.json()

        assert body["calibrated"] is True, (
            "The model was fitted, so a response still claiming otherwise would "
            "understate the number it reports."
        )
        assert "placeholder" not in body["notes"]
        assert 0.0 < body["failure_probability"] < 1.0

    def test_a_fitted_model_responds_differently_to_a_healthy_machine(
        self, client
    ) -> None:
        """Calibration must change the numbers, not only the label.

        If the flag could flip while every probability stayed identical, the
        flag would be decorative.
        """
        payload = {"equipment_type": "Industrial Motor", "anomaly_score": 0.0}
        before = client.post("/api/predict", json=payload).json()

        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)
        client.post("/api/predict/calibrate")
        after = client.post("/api/predict", json=payload).json()

        assert before["calibrated"] is False
        assert after["calibrated"] is True
        assert after["failure_probability"] != before["failure_probability"]

    def test_health_score_reports_the_same_calibration_state(self, client) -> None:
        payload = {
            "equipment_type": "Industrial Motor",
            "temperature": 60.0,
            "vibration": 2.0,
        }
        before = client.post("/api/health/score", json=payload)
        assert before.status_code == 200, before.text

        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)
        client.post("/api/predict/calibrate")
        after = client.post("/api/health/score", json=payload)

        assert before.json()["calibrated"] is False
        assert after.json()["calibrated"] is True

    def test_model_info_agrees_with_the_calibration_endpoint(self, client) -> None:
        _record_n_failures(client, MIN_FAILURES_TO_CALIBRATE, interval_hours=4000.0)
        client.post("/api/predict/calibrate")

        info = client.get("/api/predict/model-info").json()
        status = client.get("/api/predict/calibration").json()

        assert info["calibrated"] is status["calibrated"]
        assert info["base_hazard_per_hour"] == pytest.approx(
            status["base_hazard_per_hour"]
        )
