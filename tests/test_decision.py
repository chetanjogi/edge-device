import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from decision import decide, Status

RULES = {"T": {"warn": [15, 32], "crit": [5, 40]}}


def test_normal_reading():
    result = decide({"T": 22}, RULES)
    assert result.status == Status.NORMAL


def test_warning_when_outside_warn_band():
    result = decide({"T": 35}, RULES)
    assert result.status == Status.WARNING
    assert "warning band" in result.reasons[0]


def test_critical_when_outside_crit_band():
    result = decide({"T": 45}, RULES)
    assert result.status == Status.CRITICAL


def test_invalid_when_value_missing():
    result = decide({}, RULES)
    assert result.status == Status.INVALID


def test_boundaries_are_inclusive():
    assert decide({"T": 15}, RULES).status == Status.NORMAL
    assert decide({"T": 32}, RULES).status == Status.NORMAL


def test_worst_status_wins():
    rules = {"T": {"warn": [15, 32], "crit": [5, 40]},
             "H": {"warn": [25, 60], "crit": [15, 75]}}
    # T is merely warning, H is critical → overall must be CRITICAL
    result = decide({"T": 35, "H": 90}, rules)
    assert result.status == Status.CRITICAL