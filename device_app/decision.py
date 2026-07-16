from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    INVALID = "invalid"


# Ranked worst-to-best so we can compare severity.
SEVERITY = {Status.NORMAL: 0, Status.WARNING: 1, Status.CRITICAL: 2, Status.INVALID: 3}


@dataclass
class Result:
    status: Status
    reasons: list = field(default_factory=list)
    values: dict = field(default_factory=dict)


def decide(reading: dict, rules: dict) -> Result:
    """
    Pure function: same reading + rules always gives the same Result.

    reading: {"T": 25.7, "P": 1013.2, "H": 41.5}
    rules:   {"T": {"warn": [15, 32], "crit": [5, 40]}, ...}
    """
    missing = [k for k in rules if reading.get(k) is None]
    if missing:
        return Result(Status.INVALID,
                      [f"missing values: {', '.join(missing)}"],
                      reading)

    worst = Status.NORMAL
    reasons = []

    for key, band in rules.items():
        value = reading[key]
        crit_lo, crit_hi = band["crit"]
        warn_lo, warn_hi = band["warn"]

        if not (crit_lo <= value <= crit_hi):
            worst = Status.CRITICAL
            reasons.append(f"{key}={value} outside critical band [{crit_lo}, {crit_hi}]")
        elif not (warn_lo <= value <= warn_hi):
            if SEVERITY[Status.WARNING] > SEVERITY[worst]:
                worst = Status.WARNING
            reasons.append(f"{key}={value} outside warning band [{warn_lo}, {warn_hi}]")

    if not reasons:
        reasons.append("all values nominal")

    return Result(worst, reasons, reading)