"""KNX side: group addresses, datapoint types and the value behaviour of group objects.

Only what the gateway sees is modelled: a sequence of group-value telegrams (time, value)
per group address. Bus timing is not modelled here (see the sibling project on TP1).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

# --------------------------------------------------------------- group addresses
MAIN_MAX, MIDDLE_MAX, SUB_MAX = 31, 7, 255           # 3-level address: 5 / 3 / 8 bits


def parse_ga(text: str) -> tuple[int, int, int]:
    """'1/2/3' -> (1, 2, 3), range checked for the 3-level form."""
    parts = text.split("/")
    if len(parts) != 3:
        raise ValueError(f"not a 3-level group address: {text!r}")
    x, y, z = (int(p) for p in parts)
    if not (0 <= x <= MAIN_MAX and 0 <= y <= MIDDLE_MAX and 0 <= z <= SUB_MAX):
        raise ValueError(f"group address out of range: {text!r}")
    return x, y, z


def format_ga(x: int, y: int, z: int) -> str:
    return f"{x}/{y}/{z}"


def ga_to_instance(ga: str) -> int:
    """BACnet object instance of an exported group object.

    The platform documentation (docs/SOURCES.md, B1) states: X/Y/Z => X * 2048 + Y * 256 + Z,
    which is the 16-bit group address read as an unsigned number.
    """
    x, y, z = parse_ga(ga)
    return x * 2048 + y * 256 + z


def instance_to_ga(instance: int) -> str:
    if not 0 <= instance <= 0xFFFF:
        raise ValueError("instance outside the 16-bit group address range")
    return format_ga(instance >> 11, (instance >> 8) & 0x7, instance & 0xFF)


# --------------------------------------------------------------- datapoint types
@dataclass(frozen=True)
class Dpt:
    id: str
    name: str
    kind: str          # "binary", "numeric" or "other" (decides BACnet export)
    unit: str


DPTS = {
    "1.001": Dpt("1.001", "switch", "binary", ""),
    "5.001": Dpt("5.001", "percentage 0..100", "numeric", "%"),
    "9.001": Dpt("9.001", "temperature", "numeric", "degC"),
    "13.010": Dpt("13.010", "active energy", "numeric", "Wh"),
    "14.056": Dpt("14.056", "power", "numeric", "W"),
    "10.001": Dpt("10.001", "time of day", "other", ""),
    "11.001": Dpt("11.001", "date", "other", ""),
    "16.000": Dpt("16.000", "character string", "other", ""),
}

# signal kind -> datapoint type of its group object
SIGNAL_DPT = {"temperature": "9.001", "setpoint": "9.001", "power": "14.056", "energy": "13.010",
              "switch": "1.001", "blind": "5.001", "date": "11.001", "text": "16.000"}


# --------------------------------------------------------------- value behaviour
@dataclass
class SignalParams:
    """Parameters of the generated KNX value behaviour. All are assumptions (DOSSIER A3)."""
    temp_mean: float = 21.5
    temp_std: float = 0.25          # K, stationary standard deviation of the room temperature
    temp_tau: float = 7200.0        # s, correlation time
    temp_trend: float = 0.3         # K/h, peak slow trend (heating or cooling period)
    temp_send_delta: float = 0.1    # K, sensor sends on change of this much
    power_mean_gap: float = 180.0   # s, mean time between load steps
    power_step: float = 400.0       # W, standard deviation of a load step
    power_send_delta: float = 10.0  # W, meter sends on change of this much
    power_sample: float = 10.0      # s, meter checks its value this often
    switch_mean_gap: float = 900.0  # s, mean time between switch changes
    switch_short_share: float = 0.3 # share of "on" periods that are short pulses (30..120 s)
    setpoint_mean_gap: float = 5400.0
    blind_mean_gap: float = 2400.0
    energy_step: float = 10.0       # Wh per telegram
    dt: float = 10.0                # s, integration step of the continuous signals


def temperature(rng: random.Random, t_end: float, p: SignalParams, start: float | None = None) -> list:
    """Room temperature: Ornstein-Uhlenbeck process plus a slow trend; send-on-delta sensor."""
    x = p.temp_mean + rng.gauss(0, p.temp_std) if start is None else start
    trend = rng.uniform(-1, 1) * p.temp_trend / 3600.0
    sigma = p.temp_std * math.sqrt(2.0 / p.temp_tau)
    sent = round(x, 2)
    out = [(0.0, sent)]
    t, mu = 0.0, x
    while t < t_end:
        t += p.dt
        mu += trend * p.dt
        x += -(x - mu) / p.temp_tau * p.dt + sigma * math.sqrt(p.dt) * rng.gauss(0, 1)
        if abs(x - sent) >= p.temp_send_delta:
            sent = round(x, 2)
            out.append((t, sent))
    return out


def power(rng: random.Random, t_end: float, p: SignalParams) -> list:
    """Electrical power of a sub-distribution: random load steps, meter sends on 10 W change."""
    level = rng.uniform(300, 3000)
    nxt = rng.expovariate(1 / p.power_mean_gap)
    sent = round(level)
    out = [(0.0, float(sent))]
    t = 0.0
    while t < t_end:
        t += p.power_sample
        while nxt <= t:
            level = min(8000.0, max(50.0, level + rng.gauss(0, p.power_step)))
            nxt += rng.expovariate(1 / p.power_mean_gap)
        v = level + rng.gauss(0, 3.0)
        if abs(v - sent) >= p.power_send_delta:
            sent = round(v)
            out.append((t, float(sent)))
    return out


def switch(rng: random.Random, t_end: float, p: SignalParams) -> list:
    state = rng.random() < 0.4
    out = [(0.0, 1.0 if state else 0.0)]
    t = 0.0
    while True:
        if state and rng.random() < p.switch_short_share:
            t += rng.uniform(30, 120)
        else:
            t += rng.expovariate(1 / p.switch_mean_gap)
        if t >= t_end:
            return out
        state = not state
        out.append((t, 1.0 if state else 0.0))


def setpoint(rng: random.Random, t_end: float, p: SignalParams) -> list:
    v = rng.choice([20.0, 20.5, 21.0, 21.5, 22.0])
    out = [(0.0, v)]
    t = rng.expovariate(1 / p.setpoint_mean_gap)
    while t < t_end:
        v = min(24.0, max(18.0, v + rng.choice([-1.0, -0.5, 0.5, 1.0])))
        out.append((t, v))
        t += rng.expovariate(1 / p.setpoint_mean_gap)
    return out


def blind(rng: random.Random, t_end: float, p: SignalParams) -> list:
    v = rng.choice([0.0, 0.0, 50.0, 100.0])
    out = [(0.0, v)]
    t = rng.expovariate(1 / p.blind_mean_gap)
    while t < t_end:
        v = rng.choice([0.0, 25.0, 50.0, 75.0, 100.0])
        if v != out[-1][1]:
            out.append((t, v))
        t += rng.expovariate(1 / p.blind_mean_gap)
    return out


def energy(rng: random.Random, t_end: float, p: SignalParams) -> list:
    v = float(rng.randrange(100_000, 900_000))
    mean_w = rng.uniform(300, 3000)
    out = [(0.0, v)]
    t = 0.0
    while True:
        t += p.energy_step * 3600.0 / mean_w * rng.uniform(0.7, 1.3)
        if t >= t_end:
            return out
        v += p.energy_step
        out.append((t, v))


def date(rng: random.Random, t_end: float, p: SignalParams) -> list:
    return [(0.0, 0.0)]              # a date telegram once a day; value not used


GENERATORS = {"temperature": temperature, "power": power, "switch": switch, "setpoint": setpoint,
              "blind": blind, "energy": energy, "date": date, "text": date}


def generate(kind: str, rng: random.Random, t_end: float, p: SignalParams | None = None) -> list:
    return GENERATORS[kind](rng, t_end, p or SignalParams())
