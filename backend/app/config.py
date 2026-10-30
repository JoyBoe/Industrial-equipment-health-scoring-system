"""Application configuration loaded from environment variables.

All tunables live here so behaviour can be changed without touching logic.
Nothing in this module contains a secret; sensitive values come from the
environment via a .env file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Feature order is part of the ML contract: it is used to fit the scaler and
# must match the order the detector expects at inference time.
SENSOR_FEATURES: tuple[str, ...] = (
    "temperature",
    "vibration",
    "pressure",
    "rpm",
    "current",
    "voltage",
    "power",
    "load",
)


class Settings(BaseSettings):
    """Runtime settings.

    Values are read from the environment first, then from a .env file, then
    from the defaults below. Prefix every variable with ``APP_``.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="APP_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Industrial Equipment Health Scoring System"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- CORS ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Database ---
    # Relative paths are resolved against the project root so the app behaves
    # the same regardless of the directory it is launched from.
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'health_scoring.db'}"

    # --- ML artifacts ---
    models_dir: Path = PROJECT_ROOT / "models"
    data_dir: Path = PROJECT_ROOT / "data"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Scoring weights ---
    # Relative importance of each parameter *within* the sensor budget. These
    # are normalised against their own sum, so they express priority rather
    # than an absolute point cost, and adding a parameter cannot silently
    # change the meaning of the others.
    temperature_weight: float = Field(default=0.22, ge=0.0, le=1.0)
    vibration_weight: float = Field(default=0.28, ge=0.0, le=1.0)
    pressure_weight: float = Field(default=0.14, ge=0.0, le=1.0)
    power_weight: float = Field(default=0.18, ge=0.0, le=1.0)
    current_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    rpm_weight: float = Field(default=0.08, ge=0.0, le=1.0)
    voltage_weight: float = Field(default=0.06, ge=0.0, le=1.0)
    load_weight: float = Field(default=0.08, ge=0.0, le=1.0)
    anomaly_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    risk_weight: float = Field(default=0.18, ge=0.0, le=1.0)

    # --- Point budgets ---
    #
    # The 100 available points are partitioned explicitly, which is what makes
    # the score decomposable: every point deducted is attributable to exactly
    # one of these three budgets. The budgets must total 100.
    sensor_points_budget: float = Field(default=70.0, ge=0.0, le=100.0)
    anomaly_points_budget: float = Field(default=18.0, ge=0.0, le=100.0)
    risk_points_budget: float = Field(default=12.0, ge=0.0, le=100.0)

    # --- Health status thresholds (configurable per the brief) ---
    excellent_min: float = Field(default=90.0, ge=0.0, le=100.0)
    healthy_min: float = Field(default=75.0, ge=0.0, le=100.0)
    warning_min: float = Field(default=50.0, ge=0.0, le=100.0)
    poor_min: float = Field(default=25.0, ge=0.0, le=100.0)

    # --- Risk level thresholds ---
    #
    # These are applied to the PRIMARY horizon probability (30 days), not to an
    # arbitrary one. A probability must be judged against the decision it
    # informs, and at a realistic industrial MTBF a 24-hour probability is
    # always a fraction of a percent no matter how sick the machine is, so
    # thresholds calibrated on that horizon report "Low" for everything.
    #
    # A 30-day failure probability above 10 % is a genuine maintenance trigger;
    # above 25 % the machine is likely to fail within the planning cycle; above
    # 50 % it should be treated as failing until proven otherwise.
    risk_moderate_min: float = Field(default=0.10, ge=0.0, le=1.0)
    risk_high_min: float = Field(default=0.25, ge=0.0, le=1.0)
    risk_severe_min: float = Field(default=0.50, ge=0.0, le=1.0)

    # --- Status caps from the model-derived terms ---
    #
    # The anomaly and risk budgets are deliberately small, so a machine can
    # have near-perfect sensors and still be about to fail. Without these caps
    # such a machine is reported "Healthy" with a Severe risk level, and the
    # recommendation tells the operator to carry on. The status is therefore
    # never allowed to be better than the models support.
    anomaly_status_cap_min: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Anomaly score forcing at most Warning"
    )
    high_risk_status_cap: str = "Warning"
    severe_risk_status_cap: str = "Poor"

    # --- Risk model ---
    #
    # Failure hazard of a healthy machine, per hour. A **placeholder**: it must
    # be replaced with 1/MTBF measured from this fleet's own failure history
    # before any absolute probability is trustworthy.
    #
    # The default of 2.0e-5 implies an MTBF of ~50,000 h (about 5.7 years),
    # which is a realistic order of magnitude for industrial rotating equipment
    # and is unlikely to overstate risk. Note that a fully degraded channel
    # multiplies the hazard by e^3 (~20x), so a machine at its alarm limits is
    # modelled as failing roughly every 2,500 h.
    base_hazard_per_hour: float = Field(default=2.0e-5, gt=0.0, le=1.0)

    #: Set True only once the base hazard has been fitted to real failure data.
    #: Responses report this flag, so a consumer can never mistake an
    #: uncalibrated index for a measured failure frequency.
    risk_model_calibrated: bool = False

    #: Acceleration exponent for the wear-out hazard curve.
    risk_hazard_sensitivity: float = Field(default=4.0, gt=0.0, le=10.0)

    #: Extra hazard scaling applied in proportion to the anomaly score.
    risk_anomaly_multiplier: float = Field(default=2.0, ge=0.0, le=20.0)

    # --- Alert rules ---
    #
    # Thresholds are expressed as band degradation (0..1) rather than raw
    # sensor values, so a single setting is meaningful across equipment types
    # with completely different limits. A vibration degradation of 0.5 means
    # "half way from normal to the alarm limit" whether that is 3 mm/s or
    # 9 mm/s.
    alert_temperature_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    alert_vibration_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    alert_pressure_threshold: float = Field(default=0.60, ge=0.0, le=1.0)

    #: Anomaly score at which an anomaly alert is raised.
    alert_anomaly_threshold: float = Field(default=0.50, ge=0.0, le=1.0)

    #: 30-day failure probability at which a risk alert is raised.
    alert_risk_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    #: Health score below which a health alert is raised.
    alert_health_threshold: float = Field(default=50.0, ge=0.0, le=100.0)

    #: Automatically resolve alerts whose condition has cleared. Without this,
    #: a machine that has been repaired keeps a wall of stale Critical alerts
    #: and the real ones stop being visible.
    auto_resolve_alerts: bool = True

    #: Maximum alerts returned by the list endpoint, newest first.
    alert_page_size: int = Field(default=100, ge=1, le=1000)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def parameter_weights(self) -> dict[str, float]:
        """Sensor parameter weights, excluding anomaly and risk.

        Every channel in the ML feature contract is present. Omitting one would
        leave it detectable as out-of-band yet unable to cost a single point,
        so it would show up in the factor list with a zero penalty.
        """
        return {
            "temperature": self.temperature_weight,
            "vibration": self.vibration_weight,
            "pressure": self.pressure_weight,
            "power": self.power_weight,
            "current": self.current_weight,
            "rpm": self.rpm_weight,
            "voltage": self.voltage_weight,
            "load": self.load_weight,
        }

    def validate_budgets(self) -> None:
        """Assert the point budgets form a usable partition.

        Called at scoring time rather than at import, so a misconfigured
        deployment fails with a clear message instead of producing scores that
        silently never drop below some floor.
        """
        total = (
            self.sensor_points_budget
            + self.anomaly_points_budget
            + self.risk_points_budget
        )
        if total <= 0:
            raise ValueError("At least one scoring budget must be positive")
        if total > 100.0:
            raise ValueError(
                f"Scoring budgets total {total}, which exceeds the 100 available "
                "points; a healthy machine could not score 100"
            )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance.

    Cached so the environment and ``.env`` are parsed once per process rather
    than on every injection.
    """
    return Settings()
