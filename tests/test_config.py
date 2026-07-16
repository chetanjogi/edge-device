import sys, os, json, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from config import load_config, ConfigError

GOOD = {
    "device_id": "edge-01",
    "sample_hz": 4,
    "rules": {"T": {"warn": [15, 32], "crit": [5, 40]}},
}


def write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data) if isinstance(data, dict) else data)
    return str(p)


def test_valid_config_loads(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg["device_id"] == "edge-01"
    assert cfg["rules"]["T"]["warn"] == [15, 32]


def test_defaults_are_applied(tmp_path):
    data = {k: v for k, v in GOOD.items() if k != "sample_hz"}
    cfg = load_config(write(tmp_path, data))
    assert cfg["sample_hz"] == 4                      # came from DEFAULTS
    assert cfg["db_path"].endswith("device.db")       # default name applied
    assert os.path.isabs(cfg["db_path"])              # and resolved to absolute


def test_file_values_override_defaults(tmp_path):
    data = dict(GOOD, sample_hz=10)
    cfg = load_config(write(tmp_path, data))
    assert cfg["sample_hz"] == 10


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "nope.json"))


def test_malformed_json_raises(tmp_path):
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(write(tmp_path, "{ not json"))


def test_missing_required_key_raises(tmp_path):
    data = {k: v for k, v in GOOD.items() if k != "rules"}
    with pytest.raises(ConfigError, match="rules"):
        load_config(write(tmp_path, data))


def test_inverted_band_raises(tmp_path):
    data = dict(GOOD, rules={"T": {"warn": [32, 15], "crit": [5, 40]}})
    with pytest.raises(ConfigError, match="less than"):
        load_config(write(tmp_path, data))


def test_crit_must_contain_warn(tmp_path):
    # The dangerous one: bands inverted, no runtime error would catch it.
    data = dict(GOOD, rules={"T": {"warn": [5, 40], "crit": [15, 32]}})
    with pytest.raises(ConfigError, match="must contain"):
        load_config(write(tmp_path, data))


def test_missing_crit_band_raises(tmp_path):
    data = dict(GOOD, rules={"T": {"warn": [15, 32]}})
    with pytest.raises(ConfigError, match="crit"):
        load_config(write(tmp_path, data))


def test_bad_sample_hz_raises(tmp_path):
    data = dict(GOOD, sample_hz=0)
    with pytest.raises(ConfigError, match="positive"):
        load_config(write(tmp_path, data))
        
        
def test_db_path_resolves_next_to_config(tmp_path):
    """db_path must not depend on the current working directory."""
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg["db_path"] == str(tmp_path / "device.db")


def test_absolute_db_path_is_left_alone(tmp_path):
    data = dict(GOOD, db_path="/var/lib/edge/device.db")
    cfg = load_config(write(tmp_path, data))
    assert cfg["db_path"] == "/var/lib/edge/device.db"