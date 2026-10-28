"""Reference snapshot data-quality gate tests (fixtures are synthetic_test only)."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from research.civitas_agent.reference import (
    ReferenceSpec, inspect_reference, load_reference,
)


def reference_fixture(tmp_path, origin="synthetic_test"):
    file = tmp_path / "reference.csv"
    dates = pd.date_range("2024-01-01", periods=90, freq="D")
    rng = np.random.default_rng(42)
    prices = np.exp(np.log(100) + np.cumsum(rng.normal(0, 0.01, 90)))
    data = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": prices,
        "volume": 1000 + rng.integers(0, 100, 90),
    })
    data.to_csv(file, index=False)
    spec = {
        "snapshot_path": str(file),
        "expected_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "source_name": "controlled synthetic validation fixture",
        "source_url": "",
        "origin": origin,
        "asset": "TEST",
        "market": "ARTIFICIAL",
        "frequency": "daily",
        "price_adjustment": "unadjusted",
        "timezone": "UTC",
        "development_end": "2024-01-30",
        "validation_end": "2024-02-29",
    }
    metadata = tmp_path / "reference_spec.json"
    metadata.write_text(json.dumps(spec))
    return metadata, file


def test_three_chronological_slices_and_locked_heldout(tmp_path):
    spec_path, _ = reference_fixture(tmp_path)
    parsed = ReferenceSpec.from_json(spec_path)
    result = inspect_reference(parsed, tmp_path / "report")
    assert result["source_declared_origin"] == "synthetic_test"
    assert result["historical_origin_independently_verified"] is False
    assert result["simulator_step_equals_reference_frequency"] is False
    assert result["metrics"]["development"]["observation_count"] == 30
    assert result["metrics"]["validation"]["observation_count"] == 30
    assert result["metrics"]["heldout"]["locked"] is True
    assert "mean_log_return" not in result["metrics"]["heldout"]
    assert (tmp_path / "report" / "reference_report.json").is_file()


def test_explicit_heldout_unlock_produces_separate_metrics(tmp_path):
    path, _ = reference_fixture(tmp_path)
    r = inspect_reference(
        ReferenceSpec.from_json(path), tmp_path / "opened",
        unlock_heldout=True,
    )
    assert r["metrics"]["heldout"]["observation_count"] == 30
    assert r["metrics"]["heldout"]["return_observation_count"] == 29
    assert r["metrics"]["heldout"]["first_date"] == "2024-03-01"
    assert r["metrics"]["development"]["return_observation_count"] == 29
    assert r["metrics"]["validation"]["return_observation_count"] == 29


def test_hash_mismatch_fails_closed(tmp_path):
    path, file = reference_fixture(tmp_path)
    file.write_text(file.read_text() + "\n")
    with pytest.raises(ValueError, match="SHA256"):
        load_reference(ReferenceSpec.from_json(path))


@pytest.mark.parametrize("mutation", [
    lambda df: df.assign(close=-2),
    lambda df: df.assign(volume=-1),
    lambda df: df.iloc[::-1],
    lambda df: pd.concat([df.iloc[:10], df.iloc[[9]], df.iloc[10:]]),
    lambda df: df.drop(columns=["volume"]),
])
def test_invalid_market_snapshots_rejected(tmp_path, mutation):
    spec_path, file = reference_fixture(tmp_path)
    frame = pd.read_csv(file)
    mutation(frame).to_csv(file, index=False)
    raw = json.loads(spec_path.read_text())
    raw["expected_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    spec_path.write_text(json.dumps(raw))
    with pytest.raises((ValueError, KeyError)):
        load_reference(ReferenceSpec.from_json(spec_path))


def test_declared_historical_without_source_url_is_rejected(tmp_path):
    path, _ = reference_fixture(tmp_path, origin="historical")
    with pytest.raises(ValueError, match="source URL"):
        load_reference(ReferenceSpec.from_json(path))
