"""Documentation accuracy tests.

Documentation drifts silently: nobody notices a stale endpoint count or an
example response captured before the risk model was replaced. These assert the
claims the documents make against the code they describe, so an incorrect
figure fails CI rather than misleading a reader.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import AlertType  # noqa: E402


def read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def read_flat(name: str) -> str:
    """Document text with whitespace collapsed.

    Prose is hard-wrapped, so a phrase like "relative index" can straddle a
    line break. Matching the raw text would produce a false failure and
    encourage weakening the assertion instead of fixing the document.
    """
    return re.sub(r"\s+", " ", read(name)).lower()


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


def operation_count(spec: dict) -> int:
    """Count operations, not paths: one path can serve several methods."""
    methods = {"get", "post", "put", "patch", "delete"}
    return sum(len(methods & set(ops)) for ops in spec["paths"].values())


# --- Completeness ---

def test_every_endpoint_is_documented(spec: dict) -> None:
    """Paths are documented relative to the /api prefix."""
    doc = read("API_DOCUMENTATION.md")
    missing = [
        path for path in spec["paths"] if path.removeprefix("/api") not in doc
    ]
    assert not missing, f"Undocumented endpoints: {sorted(missing)}"


def test_every_configuration_setting_is_documented() -> None:
    doc = read("API_DOCUMENTATION.md")
    missing = [
        f"APP_{name.upper()}" for name in Settings.model_fields
        if f"APP_{name.upper()}" not in doc
    ]
    assert not missing, f"Undocumented settings: {missing}"


def test_every_alert_type_is_documented() -> None:
    doc = read("API_DOCUMENTATION.md")
    missing = [t.value for t in AlertType if t.value not in doc]
    assert not missing, f"Undocumented alert types: {missing}"


def test_every_setting_appears_in_the_env_example() -> None:
    """`.env.example` is what a new operator actually reads.

    It covered only 15 of 47 settings during the final audit, and omitted the two
    safety-critical ones (`risk_model_calibrated`, `base_hazard_per_hour`) — a
    reader configuring the system from that file alone would never learn the risk
    model is uncalibrated.
    """
    example = read(".env.example")
    missing = [
        f"APP_{name.upper()}" for name in Settings.model_fields
        if f"APP_{name.upper()}" not in example
    ]
    assert not missing, f"Settings absent from .env.example: {missing}"


def test_env_example_warns_that_the_base_hazard_is_a_placeholder() -> None:
    """The two settings that must never be changed blindly."""
    example = read(".env.example")
    assert "APP_BASE_HAZARD_PER_HOUR" in example
    assert "APP_RISK_MODEL_CALIBRATED" in example
    assert "PLACEHOLDER" in example, (
        "The example must flag the base hazard as a placeholder, not a fitted value"
    )
    assert "relative index" in re.sub(r"\s+", " ", example).lower() or "RELATIVE INDEX" in example


def test_env_example_does_not_ship_a_relative_sqlite_path() -> None:
    """Every Makefile backend target runs from `backend/`.

    An active relative `APP_DATABASE_URL` would therefore resolve to
    `backend/data/...`, silently creating a second empty database so the
    dashboard shows no equipment. The default must stay unanchored so the
    project-root-anchored built-in applies.
    """
    example = read(".env.example")
    for line in example.splitlines():
        if line.strip().startswith("APP_DATABASE_URL"):
            assert "sqlite:///./" not in line, (
                f"An active relative sqlite path would break the quick start: {line!r}"
            )


# Every "### `METHOD /path`" heading must name a real route. This is stricter
# than a substring search: the original test stripped the /api prefix, which let
# "GET /health" pass for the real route "/api/health" -- a path that 404s.
HEADING = re.compile(r"^###\s+`([A-Z]+)\s+(\S+)`", re.MULTILINE)


@pytest.fixture(scope="module")
def documented_routes() -> list[tuple[str, str]]:
    return HEADING.findall(read("API_DOCUMENTATION.md"))


def test_documented_route_headings_are_real_routes(
    spec: dict, documented_routes: list[tuple[str, str]]
) -> None:
    """Each documented heading must resolve to a real method + path."""
    invalid = []
    for method, path in documented_routes:
        if not path.startswith("/api/"):
            # Paths are documented relative to the /api prefix, except the
            # system health probe, which is spelled out in full.
            full = f"/api{path}"
        else:
            full = path
        ops = spec["paths"].get(full)
        if ops is None or method.lower() not in ops:
            invalid.append(f"{method} {path} -> {full}")
    assert not invalid, f"Documented but non-existent routes: {invalid}"


def test_no_documented_route_is_missing_from_the_api(
    spec: dict, documented_routes: list[tuple[str, str]]
) -> None:
    """The reverse direction: no real route may lack a documented heading."""
    documented = set()
    for method, path in documented_routes:
        documented.add((method.lower(), path if path.startswith("/api/") else f"/api{path}"))

    absent = [
        f"{method.upper()} {path}"
        for path, ops in spec["paths"].items()
        for method in ops
        if method in {"get", "post", "put", "patch", "delete"}
        and (method, path) not in documented
    ]
    assert not absent, f"Routes with no documented heading: {sorted(absent)}"



def test_all_four_documents_exist() -> None:
    for name in (
        "README.md",
        "ARCHITECTURE.md",
        "ML_DOCUMENTATION.md",
        "API_DOCUMENTATION.md",
    ):
        assert (PROJECT_ROOT / name).is_file(), f"{name} is missing"
        assert read(name).strip(), f"{name} is empty"


# --- Quoted figures must be true ---

def test_endpoint_count_is_accurate(spec: dict) -> None:
    actual = operation_count(spec)
    readme = read("README.md")
    assert f"{actual} operations" in readme, (
        f"README claims a different operation count; actual is {actual}"
    )
    assert f"{len(spec['paths'])} paths" in readme


def test_setting_count_is_accurate() -> None:
    actual = len(Settings.model_fields)
    for name in ("README.md", "API_DOCUMENTATION.md"):
        doc = read(name)
        assert f"{actual} settings" in doc, (
            f"{name} should state that there are {actual} settings"
        )


def test_documented_thresholds_match_the_defaults() -> None:
    """A changed default must not leave the documentation asserting the old one."""
    doc = read("API_DOCUMENTATION.md")
    expectations = {
        "APP_EXCELLENT_MIN": 90.0,
        "APP_HEALTHY_MIN": 75.0,
        "APP_WARNING_MIN": 50.0,
        "APP_POOR_MIN": 25.0,
        "APP_SENSOR_POINTS_BUDGET": 70.0,
        "APP_ANOMALY_POINTS_BUDGET": 18.0,
        "APP_RISK_POINTS_BUDGET": 12.0,
        "APP_VIBRATION_WEIGHT": 0.28,
        "APP_TEMPERATURE_WEIGHT": 0.22,
        "APP_RISK_MODERATE_MIN": 0.10,
    }
    settings = Settings().model_dump()
    for name, expected in expectations.items():
        field = name.removeprefix("APP_").lower()
        assert settings[field] == expected, (
            f"{name} default changed to {settings[field]}; update the documentation"
        )
        # The documented value must sit on the same line as the variable name,
        # otherwise the number belongs to a different setting entirely.
        line = next((l for l in doc.splitlines() if name in l), None)
        assert line is not None, f"{name} is not documented"
        assert str(expected) in line or f"{expected:g}" in line, (
            f"{name} is documented as {line.strip()!r}, expected {expected}"
        )


def test_point_budgets_are_documented_as_summing_to_100() -> None:
    """The documented budgets must partition exactly the 100 available points."""
    total = (
        Settings().sensor_points_budget
        + Settings().anomaly_points_budget
        + Settings().risk_points_budget
    )
    assert total == 100.0, f"Budgets total {total}, not 100"
    ml = read("ML_DOCUMENTATION.md")
    assert "sensor | 70" in ml and "anomaly | 18" in ml and "risk | 12" in ml


# --- Safety caveats must survive ---

def test_readme_leads_with_the_data_and_calibration_warning() -> None:
    readme = read("README.md")
    head = readme[:2000]
    assert "synthetic" in head.lower()
    assert "uncalibrated" in head.lower()
    assert "calibrated" in head


def test_risk_documentation_warns_against_presented_probabilities() -> None:
    ml = read("ML_DOCUMENTATION.md")
    flat = read_flat("ML_DOCUMENTATION.md")
    assert "not calibrated" in flat or "uncalibrated" in flat
    assert "1 / mtbf" in flat or "1/mtbf" in flat
    assert "relative index" in flat


def test_api_doc_flags_uncalibrated_risk() -> None:
    doc = read("API_DOCUMENTATION.md")
    predict_section = doc[doc.index("## Failure prediction"):]
    assert "calibrated: false" in predict_section
    assert "relative index" in re.sub(r"\s+", " ", predict_section).lower()


def test_every_response_reporting_a_probability_also_reports_calibration() -> None:
    """A probability without its calibration flag is the exact failure mode the
    flag exists to prevent.

    The README asserts that every response carries `calibrated: false`. That was
    false until now: `/health/score` and `/equipment/{id}/health` both returned
    `failure_probability` with no indication it was a relative index.
    """
    from app.schemas.health import HealthHistoryResponse, HealthScoreResponse
    from app.schemas.risk import EquipmentRiskResponse, PredictionResponse

    for schema in (
        HealthScoreResponse, HealthHistoryResponse, PredictionResponse, EquipmentRiskResponse
    ):
        fields = set(schema.model_fields)
        if "failure_probability" in fields:
            assert "calibrated" in fields, (
                f"{schema.__name__} reports failure_probability without a "
                "calibration flag, so a client cannot tell a real frequency "
                "from a relative index."
            )
        assert "calibrated" in fields, (
            f"{schema.__name__} omits the calibration flag entirely"
        )


def test_readme_claim_about_every_response_is_actually_true() -> None:
    """The README's safety claim must be enforced, not merely asserted.

    "Every API response carries calibrated: false" was a stronger claim than the
    code supported. This checks the claim holds for the schemas that report risk.
    """
    assert "calibrated: false" in read("README.md")
    test_every_response_reporting_a_probability_also_reports_calibration()


def test_synthetic_data_caveat_is_referenced() -> None:
    """Both the overview and the ML document must point at the data notice."""
    for name in ("README.md", "ML_DOCUMENTATION.md"):
        assert "SYNTHETIC_DATA.md" in read(name), f"{name} must reference the data notice"


def test_known_limitations_are_disclosed() -> None:
    readme = read("README.md").lower()
    for limitation in ("uncalibrated", "no authentication", "synthetic"):
        assert limitation in readme, f"'{limitation}' should be a disclosed limitation"
