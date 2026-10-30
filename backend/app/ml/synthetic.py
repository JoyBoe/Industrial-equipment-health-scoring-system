"""Synthetic industrial telemetry generator.

**This is development and test data only.** No real plant data is available to
this project, so telemetry is simulated from a physics-informed model. It is
correlated and regime-structured, not independent random draws: an
independent sampler produces a distribution the detector cannot learn from and
would make the system look better than it is.

The generator models:

* a duty cycle driving load, which in turn drives current and power
* electrical power derived from voltage and current, never sampled freely
* first-order thermal lag, so temperature follows load with a time constant
* vibration that scales with speed and load (bearing/alignment signature)
* pressure at its setpoint with load-dependent droop

Four regimes are produced, matching real failure progression:

``nominal``   healthy operation
``degraded``  gradual wear: vibration and temperature creep up, pressure
              variance widens, current and power rise with load
``bearing``   a discrete spall: sharp vibration rise plus periodic impact
``overheat``  thermal runaway: temperature climbs well past the alarm band
``cavitation``pressure collapse with vibration and current ripple
``overload``  current and power spike against a stalled speed

`regime` and `fault_type` are recorded as ground truth for *evaluating* the
detector. The unsupervised detector and the health scorer never read them.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import math

import numpy as np

from app.ml.baselines import BaselineProfile, get_profile

#: How the machine is behaving. Ground truth for evaluation only.
REGIME_NOMINAL = "nominal"
REGIME_DEGRADED = "degraded"
REGIME_BEARING = "bearing_fault"
REGIME_OVERHEAT = "overheat"
REGIME_CAVITATION = "cavitation"
REGIME_OVERLOAD = "overload"

REGIMES: tuple[str, ...] = (
    REGIME_NOMINAL,
    REGIME_DEGRADED,
    REGIME_BEARING,
    REGIME_OVERHEAT,
    REGIME_CAVITATION,
    REGIME_OVERLOAD,
)

FAULT_REGIMES: frozenset[str] = frozenset(
    r for r in REGIMES if r != REGIME_NOMINAL
)

#: Typical fault duration in samples, as (min, max).
#:
#: Durations are mode-specific. Overheating and cavitation are sustained events
#: that persist until an operator intervenes; a bearing impact or an overload
#: episode is comparatively brief. Using one duration for all of them made
#: slow thermal signatures disappear inside the thermal time constant.
FAULT_DURATION_SAMPLES: dict[str, tuple[int, int]] = {
    REGIME_BEARING: (20, 45),
    REGIME_OVERHEAT: (90, 240),
    REGIME_CAVITATION: (40, 90),
    REGIME_OVERLOAD: (15, 40),
}

#: Samples per full duty-cycle revolution, i.e. 8 h at 1-minute cadence.
DUTY_CYCLE_SAMPLES = 480

#: Nominal samples required between two fault events.
#:
#: Must comfortably exceed the thermal time constant so the machine cools back
#: down; without it, one overheat event contaminates the next segment and each
#: fault stops being independently identifiable.
RECOVERY_GAP_SAMPLES = 120

#: Minutes between samples. Matches a 1 Hz-ish SCADA scan averaged to 1 min,
#: which is realistic for a supervisory health system.
DEFAULT_INTERVAL_MINUTES = 1


@dataclass
class Reading:
    """One generated sample.

    Carries the ground-truth `regime`/`fault_type` alongside the sensor
    channels so detector accuracy can be scored offline.
    """

    timestamp: datetime
    temperature: float
    vibration: float
    pressure: float
    rpm: float
    current: float
    voltage: float
    power: float
    load: float
    regime: str = REGIME_NOMINAL
    fault_type: str | None = None

    def as_dict(self) -> dict[str, float | str | datetime]:
        """Flatten to the sensor channels plus ground truth."""
        return {
            "timestamp": self.timestamp,
            "temperature": self.temperature,
            "vibration": self.vibration,
            "pressure": self.pressure,
            "rpm": self.rpm,
            "current": self.current,
            "voltage": self.voltage,
            "power": self.power,
            "load": self.load,
            "regime": self.regime,
            "fault_type": self.fault_type,
        }


@dataclass
class SeriesConfig:
    """How to generate one machine's history."""

    equipment_type: str = "Hydraulic Pump"
    samples: int = 2880  # 48 h at 1-minute cadence
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    start_time: datetime | None = None
    base_load: float = 70.0
    seed: int | None = None
    # Proportion of the series spent in each regime. `degraded` ramps in
    # gradually so slow wear is represented, not just step changes.
    regime_mix: dict[str, float] = field(
        default_factory=lambda: {
            REGIME_NOMINAL: 0.70,
            REGIME_DEGRADED: 0.15,
            REGIME_BEARING: 0.05,
            REGIME_OVERHEAT: 0.04,
            REGIME_CAVITATION: 0.03,
            REGIME_OVERLOAD: 0.03,
        }
    )


class SyntheticGenerator:
    """Stateful telemetry generator for a single machine.

    Kept stateful across calls so a continuous stream can be produced without
    respawning the thermal state, which would produce an implausible
    temperature jump at every boundary.
    """

    def __init__(self, config: SeriesConfig) -> None:
        self.config = config
        self.profile: BaselineProfile = get_profile(config.equipment_type)
        self.rng = np.random.default_rng(config.seed)

        # Physical state carried between samples.
        self._temperature = self.profile.bands["temperature"].nominal
        self._load = config.base_load
        self._vibration_phase = 0.0
        # Peak deviation of the duty cycle from the base load.
        self.duty_amplitude = 12.0

        # Per-sample severity for gradual regimes, ramping 0 -> 1 across the
        # degraded segment so wear progresses rather than jumping.
        self._severity = 0.0
        self._in_degraded = False

        # Pre-planned regime for each sample, so realised proportions match the
        # requested mix and the thermal state has time to recover between
        # events.
        self._timeline = self._build_timeline()
        self._degraded_start = next(
            (i for i, r in enumerate(self._timeline) if r == REGIME_DEGRADED), 0
        )
        self._degraded_end = next(
            (i for i in range(len(self._timeline) - 1, -1, -1)
             if self._timeline[i] == REGIME_DEGRADED),
            self._degraded_start,
        )

        self._cursor = config.start_time or datetime.now(UTC).replace(microsecond=0)
        # Explicit sample counter. Deriving the index from the timestamp
        # silently collapses to 0 when `start_time` is unset, which would
        # disable the time-segmented regimes entirely.
        self._index = 0

    # -- internals ---------------------------------------------------------

    def _next_load(self, index: int) -> float:
        """Advance the duty cycle.

        Load is dominated by a slow sinusoidal shift/duty pattern, with a small
        mean-reverting perturbation on top.

        The smooth component is essential, not cosmetic. Thermal state is a
        ~25-minute first-order lag, which strongly attenuates high-frequency
        input. If load were driven by a large per-step random innovation it
        would be effectively white noise, the thermal lag would average it
        away, and the load-temperature correlation would collapse to ~0.05 --
        which is not how a real machine behaves, where load and temperature
        track each other closely.
        """
        base = self.config.base_load
        band = self.profile.bands["load"]

        # Shift duty cycle: one full cycle per 8 hours at 1-minute cadence.
        duty = self.duty_amplitude * np.sin(2 * np.pi * index / DUTY_CYCLE_SAMPLES)

        # Small mean-reverting perturbation.
        self._load += 0.08 * (base - self._load) + self.rng.normal(0, band.sigma * 0.12)

        return float(np.clip(base + duty + (self._load - base), 5.0, 110.0))

    def _build_timeline(self) -> list[str]:
        """Pre-plan the regime for every sample in the series.

        The timeline is planned once, up front, rather than rolled per sample.
        Rolling per sample with a fixed probability per regime is incoherent
        once durations are long: a 4% per-sample chance of a 150-sample
        overheat event requests ~170 events from a 4320-sample series, so the
        machine never returns to nominal and mean temperature under *nominal*
        ends up higher than under *degraded*. Planning the events explicitly
        makes the realised regime mix match the requested mix.

        Guarantees:
          * a contiguous wear block, since gradual degradation is one process;
          * a bounded number of fault events, sized from the requested share;
          * a recovery gap between events, so thermal state settles back
            toward nominal and each event is independently diagnosable.
        """
        n = self.config.samples
        mix = self.config.regime_mix
        timeline = [REGIME_NOMINAL] * n
        if n <= 0:
            return timeline

        # --- gradual wear: one contiguous block in the second half ---------
        # Block length equals the requested share exactly, so the realised mix
        # matches `regime_mix` rather than drifting to a fixed proportion.
        degraded_share = mix.get(REGIME_DEGRADED, 0.0)
        if degraded_share > 0:
            span = max(int(degraded_share * n), 1)
            start = min(int(n * 0.55), max(n - span, 0))
            for i in range(start, min(start + span, n)):
                timeline[i] = REGIME_DEGRADED

        # --- fault events ---------------------------------------------------
        # Indices already spoken for. The recovery gap around an occupied run
        # is enforced by `_find_free_window`, which requires a free run of
        # duration + gap, so it must not also be pre-marked here.
        blocked = {i for i, regime in enumerate(timeline) if regime != REGIME_NOMINAL}

        total_fault_budget = sum(
            mix.get(regime, 0.0) for regime in FAULT_DURATION_SAMPLES
        ) * n

        for regime, (low, high) in FAULT_DURATION_SAMPLES.items():
            share = mix.get(regime, 0.0)
            if share <= 0:
                continue
            avg_duration = (low + high) / 2.0
            # Events needed so this regime occupies its requested share of the
            # series. Rounded down so the total never overshoots the budget.
            event_count = int(round((share * n) / avg_duration))
            for _ in range(event_count):
                duration = int(self.rng.integers(low, high + 1))
                start = self._find_free_window(blocked, duration, n)
                if start is None:
                    break
                for i in range(start, start + duration):
                    timeline[i] = regime
                # Recovery gap plus a small buffer between events.
                for i in range(start, start + duration + RECOVERY_GAP_SAMPLES):
                    blocked.add(i)

        return timeline

    def _find_free_window(
        self, blocked: set[int], duration: int, n: int
    ) -> int | None:
        """Find a run of `duration + RECOVERY_GAP` consecutive free samples.

        All candidate windows are enumerated and one is chosen at random,
        rather than sampling a single random start. A single miss used to abort
        the whole regime, which silently dropped fault types from the series
        depending on the seed.

        Returns None only when no gap anywhere is large enough.
        """
        needed = duration + RECOVERY_GAP_SAMPLES
        if n < needed:
            return None

        candidates: list[int] = []
        run_start: int | None = None
        for i in range(n):
            if i in blocked:
                if run_start is not None and i - run_start >= needed:
                    candidates.append(run_start)
                run_start = None
            elif run_start is None:
                run_start = i
        # Trailing free run.
        if run_start is not None and n - run_start >= needed:
            candidates.append(run_start)

        if not candidates:
            return None
        return candidates[int(self.rng.integers(0, len(candidates)))]

    def _intensity_for(self, index: int) -> float:
        """Severity of the regime at `index`, in [0, 1].

        Gradual wear ramps across the whole wear block because wear accumulates
        over hours. A fault ramps over its first few samples because a spall or
        a cavitation event hits hard and immediately, then holds.

        Indices past the end of the planned timeline return 0: a generator
        asked for more samples than configured continues in nominal operation
        rather than raising.
        """
        if index >= len(self._timeline):
            return 0.0

        if self._timeline[index] == REGIME_DEGRADED:
            span = max(self._degraded_end - self._degraded_start, 1)
            return min(1.0, (index - self._degraded_start) / span)
        if self._timeline[index] == REGIME_NOMINAL:
            return 0.0

        # Distance from the start of this fault's contiguous run.
        run_start = index
        while run_start > 0 and self._timeline[run_start - 1] == self._timeline[index]:
            run_start -= 1
        run_length = sum(1 for r in self._timeline if r == self._timeline[index])
        ramp = max(min(run_length, 10), 1)
        return min(1.0, (index - run_start + 1) / ramp)

    def _update_temperature(self, target: float, interval_minutes: float) -> float:
        """First-order thermal lag toward `target`.

        Real machines heat and cool gradually. A first-order lag with a ~25 min
        time constant reproduces that and is what makes rolling-window features
        meaningful.
        """
        tau_minutes = 25.0
        alpha = 1.0 - np.exp(-interval_minutes / tau_minutes)
        self._temperature += alpha * (target - self._temperature)
        return self._temperature

    # -- public API --------------------------------------------------------

    def next_reading(self) -> Reading:
        """Generate the next sample, advancing the machine state."""
        cfg = self.config
        profile = self.profile
        bands = profile.bands
        interval = max(cfg.interval_minutes, 1)

        # Regimes are chosen from an explicit sample counter so behaviour is
        # identical whether the series is generated in one call or streamed.
        index = self._index
        regime = self._timeline[index] if index < len(self._timeline) else REGIME_NOMINAL
        severity = self._intensity_for(index)

        load = self._next_load(index)
        severity = severity

        # --- electrical: current from load, power from V and I -------------
        # Current tracks load, with power factor degrading slightly under load.
        load_fraction = load / 100.0
        current = profile.rated_current * (0.35 + 0.75 * load_fraction)
        pf = profile.power_factor * (1.0 - 0.06 * load_fraction)

        if regime == REGIME_OVERLOAD:
            current *= 1.35 + 0.1 * severity
            pf *= 0.86

        current += self.rng.normal(0, bands["current"].sigma * 0.5)
        current = max(current, 0.0)

        voltage = bands["voltage"].nominal + self.rng.normal(0, bands["voltage"].sigma)
        # Mild brownout under heavy load, as seen on real feeds.
        voltage -= 6.0 * max(load_fraction - 0.85, 0.0) * 4

        # The power factor here is the *effective* one (degraded by load and by
        # regime), so the shared helper is given it directly rather than the
        # profile's nominal value. Delegating keeps a single copy of the
        # equation, which the power_residual feature depends on.
        power = math.sqrt(3) * voltage * current * pf / 1000.0
        power = max(power, 0.0)

        # --- speed: tracks load, sags when overloaded -----------------------
        rpm = profile.nominal_rpm * (0.85 + 0.18 * load_fraction)
        if regime == REGIME_OVERLOAD:
            rpm *= 0.72  # stalled against a heavy load
        if regime == REGIME_CAVITATION:
            rpm *= 0.93 + self.rng.normal(0, 0.02)
        rpm += self.rng.normal(0, bands["rpm"].sigma)
        rpm = max(rpm, 0.0)

        # --- vibration: speed and load dependent, with bearing impacts -----
        self._vibration_phase += 0.9 + 0.5 * load_fraction
        base_vib = bands["vibration"].nominal * (0.75 + 0.45 * load_fraction)
        vibration = base_vib + self.rng.normal(0, bands["vibration"].sigma * 0.4)

        if regime == REGIME_DEGRADED:
            # Wear scales with speed, which is what makes it diagnosable.
            vibration *= 1.0 + 0.55 * severity
        if regime == REGIME_BEARING:
            # Broadband rise plus a periodic impact component.
            vibration *= 1.9 + 0.5 * severity
            vibration += abs(np.sin(self._vibration_phase * 3.1)) * 3.4
        if regime == REGIME_CAVITATION:
            vibration *= 1.5
            vibration += abs(np.sin(self._vibration_phase * 5.7)) * 1.8

        vibration = max(vibration, 0.0)

        # --- pressure: setpoint with load droop, variance widens with wear -
        pressure = bands["pressure"].nominal * (1.0 - 0.10 * load_fraction)
        if regime == REGIME_DEGRADED:
            # Degrading seals show as instability before they show as drift.
            pressure += self.rng.normal(0, bands["pressure"].sigma * (1.0 + 3.0 * severity))
        if regime == REGIME_CAVITATION:
            # Head collapses and ripples violently.
            pressure *= 0.55
            pressure += abs(np.sin(self._vibration_phase * 4.3)) * 4.0
        pressure += self.rng.normal(0, bands["pressure"].sigma * 0.5)

        # --- temperature: thermal target from load and power ---------------
        thermal_target = bands["temperature"].nominal * (0.72 + 0.42 * load_fraction)
        if regime == REGIME_DEGRADED:
            thermal_target *= 1.0 + 0.16 * severity
        if regime == REGIME_OVERHEAT:
            thermal_target *= 1.0 + 0.55 * severity
        if regime == REGIME_BEARING:
            thermal_target *= 1.0 + 0.20 * severity
        if regime == REGIME_OVERLOAD:
            thermal_target *= 1.12

        temperature = self._update_temperature(thermal_target, interval)
        temperature += self.rng.normal(0, bands["temperature"].sigma * 0.35)

        self._cursor += timedelta(minutes=interval)
        self._index += 1

        return Reading(
            timestamp=self._cursor,
            temperature=round(temperature, 3),
            vibration=round(vibration, 4),
            pressure=round(pressure, 3),
            rpm=round(rpm, 1),
            current=round(current, 3),
            voltage=round(voltage, 2),
            power=round(float(power), 4),
            load=round(load, 2),
            regime=regime,
            fault_type=None if regime == REGIME_NOMINAL else regime,
        )

    def generate(self, count: int | None = None) -> list[Reading]:
        """Generate a contiguous series of readings."""
        total = self.config.samples if count is None else count
        return [self.next_reading() for _ in range(total)]


def generate_series(
    equipment_type: str = "Hydraulic Pump",
    samples: int = 2880,
    seed: int | None = 42,
    base_load: float = 70.0,
    start_time: datetime | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> list[Reading]:
    """Convenience wrapper to generate one machine's history."""
    config = SeriesConfig(
        equipment_type=equipment_type,
        samples=samples,
        seed=seed,
        base_load=base_load,
        start_time=start_time,
        interval_minutes=interval_minutes,
    )
    return SyntheticGenerator(config).generate()


def generate_healthy_series(
    equipment_type: str = "Hydraulic Pump",
    samples: int = 200,
    seed: int | None = 7,
    start_time: datetime | None = None,
) -> list[Reading]:
    """Generate a fault-free series, for baseline fitting and demo data.

    Detection thresholds must be learned from healthy operation, so training
    data deliberately contains no injected faults.
    """
    config = SeriesConfig(
        equipment_type=equipment_type,
        samples=samples,
        seed=seed,
        start_time=start_time,
        base_load=65.0,
        regime_mix={REGIME_NOMINAL: 1.0},
    )
    return SyntheticGenerator(config).generate()


def generate_multi_equipment(
    equipment: list[dict],
    samples: int = 2880,
    seed_base: int = 1000,
    start_time: datetime | None = None,
) -> dict[str, list[Reading]]:
    """Generate a distinct series per unit.

    Each machine gets its own seed, base load, and equipment type, so no two
    units share an identical distribution. Identical series would make fleet
    statistics meaningless and let the detector memorise one machine.
    """
    series: dict[str, list[Reading]] = {}
    for index, unit in enumerate(equipment):
        equipment_id = unit.get("equipment_id", f"EQ-{index + 1:03d}")
        equipment_type = unit.get("equipment_type", "Hydraulic Pump")
        # Deterministic per-unit base load spread across a realistic duty band.
        base_load = 55.0 + (index * 7.0) % 35.0
        series[equipment_id] = generate_series(
            equipment_type=equipment_type,
            samples=samples,
            seed=seed_base + index,
            base_load=base_load,
            start_time=start_time,
        )
    return series
