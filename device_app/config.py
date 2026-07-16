import json
import os

DEFAULTS = {
    "sample_hz": 4,
    "log_level": "INFO",
    "db_path": "device.db",
    "run_duration_s": 15,
}

REQUIRED = ["device_id", "rules"]


class ConfigError(Exception):
    """Configuration is invalid — the device must not start."""


def _validate_band(key, name, band):
    if not isinstance(band, list) or len(band) != 2:
        raise ConfigError(f"rules.{key}.{name} must be [low, high]")
    lo, hi = band
    if not all(isinstance(v, (int, float)) for v in (lo, hi)):
        raise ConfigError(f"rules.{key}.{name} values must be numbers")
    if lo >= hi:
        raise ConfigError(f"rules.{key}.{name}: low ({lo}) must be less than high ({hi})")
    return lo, hi


def validate_rules(rules):
    if not isinstance(rules, dict) or not rules:
        raise ConfigError("rules must be a non-empty object")

    for key, band in rules.items():
        if not isinstance(band, dict):
            raise ConfigError(f"rules.{key} must be an object with 'warn' and 'crit'")
        for name in ("warn", "crit"):
            if name not in band:
                raise ConfigError(f"rules.{key} is missing the '{name}' band")

        warn_lo, warn_hi = _validate_band(key, "warn", band["warn"])
        crit_lo, crit_hi = _validate_band(key, "crit", band["crit"])

        # The critical band must be wider than the warning band, or the
        # decision logic is meaningless.
        if crit_lo > warn_lo or crit_hi < warn_hi:
            raise ConfigError(
                f"rules.{key}: crit band [{crit_lo}, {crit_hi}] must contain "
                f"warn band [{warn_lo}, {warn_hi}]")


def load_config(path="config.json"):
    """Load and validate config. Raises ConfigError — never returns bad config."""
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"config file is not valid JSON: {e}")

    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")

    for key in REQUIRED:
        if key not in raw:
            raise ConfigError(f"missing required key: '{key}'")

    validate_rules(raw["rules"])

    cfg = dict(DEFAULTS)      # start from defaults
    cfg.update(raw)           # file values win

    hz = cfg["sample_hz"]
    if not isinstance(hz, (int, float)) or hz <= 0:
        raise ConfigError(f"sample_hz must be a positive number, got {hz!r}")
    
    dur = cfg["run_duration_s"]
    if not isinstance(dur, (int, float)) or dur <= 0:
        raise ConfigError(f"run_duration_s must be a positive number, got {dur!r}")

    # Resolve db_path relative to the config file, not the working directory.
    if not os.path.isabs(cfg["db_path"]):
        cfg["db_path"] = os.path.join(os.path.dirname(os.path.abspath(path)),
                                      cfg["db_path"])
    
    return cfg