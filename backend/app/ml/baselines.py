"""Per-equipment-type operating profiles.

These profiles are the engineering knowledge base of the system. They encode
what "normal" means for a given machine type, which is essential because a
hydraulic pump and a boiler have completely different acceptable temperature
and vibration bands. A single global threshold table would be wrong for almost
every machine.

A profile provides, per parameter:
  `nominal`   the expected steady-state value
  `ok_*`      the range where the machine is healthy (zero penalty)
  `alarm_*`   the value at which the parameter is fully degraded (max penalty)
  `sigma`     typical measurement noise, used by the synthetic generator

Values are engineering defaults, not measured from a specific asset. They are
meant to be tuned per site; see data/SYNTHETIC_DATA.md.
"""

from dataclasses import dataclass, field

from app.config import SENSOR_FEATURES


@dataclass(frozen=True)
class ParamBand:
    """Acceptance band for one parameter on one equipment type.

    `ok_low`/`ok_high` bound the healthy range. `alarm_low`/`alarm_high` are
    the extremes at which the parameter is considered fully degraded; the
    scoring engine interpolates linearly between the two, with a steepened
    curve past `alarm_high` for overtemperature-style failure modes.

    `low_relevant` records whether drifting *below* the acceptable range is
    itself a failure. It matters: a low temperature is normal for a chiller
    and benign for a motor, whereas falling pressure or collapsing voltage
    indicate real faults. When False, only the high side is penalised and
    `alarm_low` is ignored by the scorer.
    """

    nominal: float
    ok_low: float
    ok_high: float
    alarm_low: float
    alarm_high: float
    sigma: float
    low_relevant: bool = False

    def clamp_span(self) -> float:
        """Distance from `ok_high` to `alarm_high`.

        Zero-span guards would make the scorer's denominator zero.
        """
        return max(self.alarm_high - self.ok_high, 1e-6)

    def low_span(self) -> float:
        """Distance from `alarm_low` to `ok_low`, used only if `low_relevant`."""
        return max(self.ok_low - self.alarm_low, 1e-6)


@dataclass(frozen=True)
class BaselineProfile:
    """Complete operating envelope for one equipment type."""

    equipment_type: str
    bands: dict[str, ParamBand]
    rated_current: float
    rated_power_kw: float
    nominal_rpm: float
    power_factor: float = 0.88
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def band(self, parameter: str) -> ParamBand:
        """Return the band for a parameter, raising a clear error if absent."""
        if parameter not in self.bands:
            raise KeyError(
                f"No baseline band for '{parameter}' on profile '{self.equipment_type}'. "
                f"Available: {sorted(self.bands)}"
            )
        return self.bands[parameter]

    def expected_power_kw(self, load_percent: float, voltage: float, current: float) -> float:
        """Three-phase electrical power in kW.

        P = sqrt(3) * V * I * PF / 1000. Used to keep generated power consistent
        with the current and voltage drawn for a given load, rather than
        sampling power independently.
        """
        import math

        return math.sqrt(3) * voltage * current * self.power_factor / 1000.0


def _band(
    nominal: float,
    ok_low: float,
    ok_high: float,
    alarm_low: float,
    alarm_high: float,
    sigma: float,
    low_relevant: bool = False,
) -> ParamBand:
    return ParamBand(
        nominal=nominal,
        ok_low=ok_low,
        ok_high=ok_high,
        alarm_low=alarm_low,
        alarm_high=alarm_high,
        sigma=sigma,
        low_relevant=low_relevant,
    )


# --- Profile definitions -------------------------------------------------
# Electrical and speed bands are derived from each machine's own rating rather
# than from a shared default table. A fixed table is wrong by construction: an
# early version allowed 95 A on a 40 A motor, which made a genuine 130 %
# overload read as perfectly normal and left that fault class undetectable.


def _electrical_bands(
    rated_current: float, rated_power_kw: float, nominal_rpm: float
) -> dict[str, ParamBand]:
    """Derive current, power, and speed limits from the machine's rating.

    A machine may legitimately draw somewhat more than its nameplate current
    for short periods, so the healthy ceiling sits above rated and the alarm
    ceiling well above that. Speed is bidirectional: a machine that will not
    reach its rated speed is stalling, which is a real fault and must be
    penalised on the low side.
    """
    bands = {
        "current": _band(
            nominal=rated_current,
            ok_low=0.0,
            ok_high=rated_current * 1.15,
            alarm_low=0.0,
            alarm_high=rated_current * 1.50,
            sigma=max(rated_current * 0.05, 0.5),
        ),
        "power": _band(
            nominal=rated_power_kw * 0.75,
            ok_low=0.0,
            ok_high=rated_power_kw * 1.10,
            alarm_low=0.0,
            alarm_high=rated_power_kw * 1.40,
            sigma=max(rated_power_kw * 0.04, 0.2),
        ),
        "load": _band(
            nominal=70.0,
            ok_low=0.0,
            ok_high=95.0,
            alarm_low=0.0,
            alarm_high=115.0,
            sigma=4.0,
        ),
    }
    if nominal_rpm > 0:
        # ok_low must sit *below* the minimum speed a healthy machine produces
        # under its lightest load, with margin. Placing it exactly at that
        # minimum makes ordinary speed noise at low load look like a stall and
        # pushes the false-positive rate to ~4 %.
        bands["rpm"] = _band(
            nominal=nominal_rpm,
            ok_low=nominal_rpm * 0.80,
            ok_high=nominal_rpm * 1.08,
            alarm_low=nominal_rpm * 0.68,
            alarm_high=nominal_rpm * 1.20,
            sigma=max(nominal_rpm * 0.012, 3.0),
            low_relevant=True,
        )
    return bands


def _profile(
    equipment_type: str,
    *,
    rated_current: float,
    rated_power_kw: float,
    nominal_rpm: float,
    temperature: ParamBand,
    vibration: ParamBand,
    pressure: ParamBand,
    overrides: dict[str, ParamBand] | None = None,
    power_factor: float = 0.88,
    notes: str = "",
    tags: tuple[str, ...] = (),
) -> BaselineProfile:
    """Assemble a profile from a thermal/vibration/pressure core plus rating-derived bands."""
    bands: dict[str, ParamBand] = {
        "temperature": temperature,
        "vibration": vibration,
        "pressure": pressure,
        "voltage": _band(
            nominal=400.0,
            ok_low=370.0,
            ok_high=440.0,
            alarm_low=340.0,
            alarm_high=465.0,
            sigma=4.0,
            low_relevant=True,
        ),
        **_electrical_bands(rated_current, rated_power_kw, nominal_rpm),
    }
    if overrides:
        bands.update(overrides)
    return BaselineProfile(
        equipment_type=equipment_type,
        bands=bands,
        rated_current=rated_current,
        rated_power_kw=rated_power_kw,
        nominal_rpm=nominal_rpm,
        power_factor=power_factor,
        notes=notes,
        tags=tags,
    )


PROFILES: dict[str, BaselineProfile] = {
    "Hydraulic Pump": _profile(
        "Hydraulic Pump",
        rated_current=26.0,
        rated_power_kw=15.0,
        nominal_rpm=1450.0,
        temperature=_band(62.0, 0.0, 78.0, -20.0, 100.0, 1.2),
        vibration=_band(3.1, 0.0, 5.5, 0.0, 12.0, 0.25),
        pressure=_band(105.0, 88.0, 118.0, 62.0, 140.0, 1.8, low_relevant=True),
        notes="Hydraulic oil degrades above ~85 C; cavitation shows as pressure collapse.",
        tags=("thermal", "pressure", "cavitation"),
    ),
    "Industrial Motor": _profile(
        "Industrial Motor",
        rated_current=40.0,
        rated_power_kw=22.0,
        nominal_rpm=1450.0,
        temperature=_band(68.0, 0.0, 82.0, -20.0, 105.0, 1.5),
        vibration=_band(2.8, 0.0, 6.0, 0.0, 12.5, 0.3),
        pressure=_band(4.2, 2.0, 5.5, 0.5, 7.0, 0.25, low_relevant=True),
        notes="Bearing faults dominate vibration; winding damage drives current imbalance.",
        tags=("thermal", "bearing"),
    ),
    "Air Compressor": _profile(
        "Air Compressor",
        rated_current=130.0,
        rated_power_kw=75.0,
        nominal_rpm=2900.0,
        temperature=_band(78.0, 0.0, 95.0, -20.0, 115.0, 1.8),
        vibration=_band(3.8, 0.0, 6.5, 0.0, 13.0, 0.35),
        # Discharge pressure is a setpoint with a firm compliance window.
        pressure=_band(8.5, 7.2, 9.5, 5.5, 11.0, 0.2, low_relevant=True),
        notes="Discharge temperature is the primary health indicator; high head pressure raises it.",
        tags=("thermal", "pressure"),
    ),
    "CNC Machine": _profile(
        "CNC Machine",
        rated_current=45.0,
        rated_power_kw=25.0,
        nominal_rpm=2400.0,
        temperature=_band(48.0, 0.0, 62.0, -20.0, 80.0, 1.0),
        vibration=_band(2.2, 0.0, 4.0, 0.0, 8.0, 0.2),
        pressure=_band(6.0, 4.5, 7.5, 3.0, 9.5, 0.3, low_relevant=True),
        notes="Coolant and lube condition drive temperature; spindle runout drives vibration.",
        tags=("spindle",),
    ),
    "Conveyor Drive": _profile(
        "Conveyor Drive",
        rated_current=10.0,
        rated_power_kw=5.5,
        nominal_rpm=960.0,
        temperature=_band(52.0, 0.0, 70.0, -20.0, 90.0, 1.1),
        vibration=_band(3.4, 0.0, 6.2, 0.0, 12.0, 0.3),
        pressure=_band(3.0, 2.0, 4.5, 1.0, 6.0, 0.2, low_relevant=True),
        notes="Belt misalignment is the usual cause of rising vibration.",
        tags=("bearing", "alignment"),
    ),
    "Gearbox": _profile(
        "Gearbox",
        rated_current=54.0,
        rated_power_kw=30.0,
        nominal_rpm=1200.0,
        temperature=_band(64.0, 0.0, 80.0, -20.0, 100.0, 1.4),
        vibration=_band(3.0, 0.0, 6.5, 0.0, 13.0, 0.28),
        pressure=_band(3.5, 2.2, 5.0, 1.2, 6.5, 0.25, low_relevant=True),
        notes="Oil condition governs both temperature and vibration; watch for rising trend.",
        tags=("thermal", "bearing"),
    ),
    "Boiler": _profile(
        "Boiler",
        rated_current=310.0,
        rated_power_kw=180.0,
        nominal_rpm=1800.0,
        temperature=_band(165.0, 0.0, 195.0, -30.0, 240.0, 3.0),
        vibration=_band(2.6, 0.0, 5.0, 0.0, 10.0, 0.25),
        pressure=_band(11.0, 9.0, 13.5, 6.0, 16.0, 0.25, low_relevant=True),
        notes="Stack and drum temperature are the leading indicators; scaling raises both.",
        tags=("thermal", "pressure"),
    ),
    "Chiller": _profile(
        "Chiller",
        rated_current=190.0,
        rated_power_kw=110.0,
        nominal_rpm=1450.0,
        temperature=_band(9.0, -10.0, 16.0, -25.0, 30.0, 0.9),
        vibration=_band(2.4, 0.0, 4.8, 0.0, 9.5, 0.22),
        pressure=_band(12.0, 9.5, 14.5, 7.0, 18.0, 0.3, low_relevant=True),
        notes="Refrigerant pressure and approach temperature indicate charge level.",
        tags=("pressure", "refrigerant"),
    ),
    "Transformer": _profile(
        "Transformer",
        rated_current=260.0,
        rated_power_kw=180.0,
        nominal_rpm=0.0,  # Stationary asset.
        temperature=_band(72.0, 0.0, 90.0, -20.0, 115.0, 1.6),
        vibration=_band(1.2, 0.0, 4.0, 0.0, 9.0, 0.15),
        # Oil-immersed: pressure loss indicates a leak or blocked gauge.
        pressure=_band(1.0, 0.4, 1.6, 0.1, 2.4, 0.05, low_relevant=True),
        power_factor=0.95,
        overrides={
            # Stationary asset: it should not be turning. Any measurable speed
            # implies unintended rotation, so the band is "near zero".
            "rpm": _band(0.0, 0.0, 5.0, 0.0, 60.0, 1.0),
        },
        notes="Cooling oil temperature and load factor dominate; a fan failure is visible in vibration.",
        tags=("thermal",),
    ),
    "Turbine": _profile(
        "Turbine",
        rated_current=120.0,
        rated_power_kw=75.0,
        nominal_rpm=3600.0,
        temperature=_band(88.0, 0.0, 105.0, -20.0, 135.0, 2.2),
        vibration=_band(2.0, 0.0, 4.5, 0.0, 11.0, 0.2),
        pressure=_band(18.0, 14.0, 22.0, 9.0, 27.0, 0.4, low_relevant=True),
        notes="Bearing and blade vibration are tightly coupled to load; surge is a pressure event.",
        tags=("thermal", "pressure", "bearing"),
    ),
}

#: Applied to any equipment type without a specific profile, so an
#: unregistered machine is still scoreable rather than rejected.
DEFAULT_PROFILE = _profile(
    "Generic",
    rated_current=45.0,
    rated_power_kw=22.0,
    nominal_rpm=1450.0,
    temperature=_band(60.0, 0.0, 80.0, -20.0, 100.0, 1.5),
    vibration=_band(3.0, 0.0, 6.0, 0.0, 12.0, 0.3),
    pressure=_band(6.0, 3.0, 10.0, 1.0, 14.0, 0.3, low_relevant=True),
    notes="Fallback envelope. Configure a specific profile for meaningful limits.",
    tags=("generic",),
)


def get_profile(equipment_type: str) -> BaselineProfile:
    """Look up a profile, falling back to the generic envelope.

    Case-insensitive so a type stored as "hydraulic pump" still matches.
    """
    if not equipment_type:
        return DEFAULT_PROFILE
    if equipment_type in PROFILES:
        return PROFILES[equipment_type]
    lowered = equipment_type.strip().lower()
    for name, profile in PROFILES.items():
        if name.lower() == lowered:
            return profile
    return DEFAULT_PROFILE


def has_specific_profile(equipment_type: str) -> bool:
    """Whether a dedicated profile exists, as opposed to the fallback."""
    return get_profile(equipment_type) is not DEFAULT_PROFILE


def all_profiles() -> dict[str, BaselineProfile]:
    """Every known profile, keyed by equipment type."""
    return dict(PROFILES)


def validate_profiles() -> None:
    """Assert every profile defines a band for each ML feature.

    Called at import by the tests. A missing band would otherwise surface as a
    confusing KeyError deep inside the scorer.
    """
    for profile in all_profiles().values():
        missing = [feature for feature in SENSOR_FEATURES if feature not in profile.bands]
        if missing:
            raise ValueError(f"Profile '{profile.equipment_type}' missing bands: {missing}")
        for name, band in profile.bands.items():
            if band.ok_low > band.ok_high or band.alarm_low > band.alarm_high:
                raise ValueError(f"Profile '{profile.equipment_type}' band '{name}' is inverted")
            if band.alarm_high <= band.ok_high:
                raise ValueError(f"Profile '{profile.equipment_type}' band '{name}' has no headroom")
            # Low-side headroom only matters where falling below the band is
            # itself a failure mode (pressure collapse, voltage sag). A cold
            # chiller or a low vibration reading is perfectly healthy.
            if band.low_relevant and band.ok_low <= band.alarm_low:
                raise ValueError(
                    f"Profile '{profile.equipment_type}' band '{name}' has no low headroom"
                )
