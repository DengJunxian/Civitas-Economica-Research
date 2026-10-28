"""Read-only reference-data validator for market stylized-fact comparisons.

This is a *data ingestion/evaluation* entrypoint, not historical replay and
not a claim that any uploaded CSV is authentic. Only a verified provenance
manifest and independently sourced snapshots can establish real-world origin.
No network access and no synthetic fallback are permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReferenceSpec:
    snapshot_path: Path
    expected_sha256: str
    source_name: str
    source_url: str
    origin: str
    asset: str
    market: str
    frequency: str
    price_adjustment: str
    timezone: str
    development_end: str
    validation_end: str

    @classmethod
    def from_json(cls, path: Path) -> "ReferenceSpec":
        raw = json.loads(path.read_text(encoding="utf-8"))
        mandatory = {field for field in cls.__dataclass_fields__}
        if set(raw) != mandatory:
            raise ValueError(f"Reference spec requires exactly: {sorted(mandatory)}")
        snapshot = Path(raw["snapshot_path"])
        if not snapshot.is_absolute():
            snapshot = (path.parent / snapshot).resolve()
        raw["snapshot_path"] = snapshot
        return cls(**raw)

    def validate(self) -> None:
        if not self.snapshot_path.is_file():
            raise ValueError(f"Reference snapshot missing: {self.snapshot_path}")
        if (len(self.expected_sha256) != 64 or
                any(c not in "0123456789abcdef" for c in self.expected_sha256)):
            raise ValueError("Reference data requires a lowercase SHA256 digest")
        if hashlib.sha256(self.snapshot_path.read_bytes()).hexdigest() != self.expected_sha256:
            raise ValueError("Reference snapshot SHA256 mismatch")
        if self.origin not in {"historical", "synthetic_test"}:
            raise ValueError("Reference origin must be historical or synthetic_test")
        if self.frequency not in {"daily", "intraday"}:
            raise ValueError("Reference sample frequency must be explicitly declared")
        if self.price_adjustment not in {"adjusted", "unadjusted"}:
            raise ValueError("Declare whether close prices are adjusted")
        for value in (self.source_name, self.asset, self.market, self.timezone):
            if not str(value).strip():
                raise ValueError("Reference source, asset, market and timezone are required")
        if self.origin == "historical" and not self.source_url.strip():
            raise ValueError("Claimed historical snapshots require a source URL")
        try:
            dev = date.fromisoformat(self.development_end)
            val = date.fromisoformat(self.validation_end)
        except ValueError as exc:
            raise ValueError("Use YYYY-MM-DD for development/validation end") from exc
        if not dev < val:
            raise ValueError("Require chronological development_end < validation_end")


def load_reference(spec: ReferenceSpec) -> pd.DataFrame:
    spec.validate()
    data = pd.read_csv(spec.snapshot_path)
    required = {"date", "close", "volume"}
    if not required.issubset(data.columns):
        raise ValueError(f"Reference CSV requires columns: {sorted(required)}")
    if data.empty or len(data) < 30:
        raise ValueError("At least 30 ordered reference records are required")
    parsed = pd.to_datetime(data["date"], errors="raise", utc=True)
    if parsed.isna().any():
        raise ValueError("Reference dates must not be missing")
    dates = pd.Series(parsed.dt.date, index=data.index)
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise ValueError("Reference dates must be strictly increasing and unique")
    close = pd.to_numeric(data["close"], errors="raise")
    volume = pd.to_numeric(data["volume"], errors="raise")
    values = pd.DataFrame({"date": dates, "close": close, "volume": volume})
    if (not np.isfinite(values[["close", "volume"]].to_numpy(dtype=float)).all()
            or (values["close"] <= 0).any() or (values["volume"] < 0).any()):
        raise ValueError("Close and volume must be finite, close >0 and volume >=0")
    if not (
        (values.date <= date.fromisoformat(spec.development_end)).any()
        and (values.date.between(
            date.fromisoformat(spec.development_end),
            date.fromisoformat(spec.validation_end),
            inclusive="right"
        )).any()
        and (values.date > date.fromisoformat(spec.validation_end)).any()
    ):
        raise ValueError("Development, validation and held-out slices must all be nonempty")
    return values


def _summary(data: pd.DataFrame) -> dict[str, Any]:
    closes = data["close"].to_numpy(dtype=float)
    volume = data["volume"].to_numpy(dtype=float)
    r = np.diff(np.log(closes))
    if len(r) == 0:
        raise ValueError("A reference partition needs multiple prices")
    mean = float(np.mean(r))
    var = float(np.var(r))
    vol = math.sqrt(var)
    centered = r - mean
    kurt = float(np.mean(centered ** 4) / var ** 2 - 3) if var > 1e-16 else None
    absolutes = np.abs(r)
    acf = (
        float(np.corrcoef(absolutes[1:], absolutes[:-1])[0, 1])
        if len(absolutes) >= 4 and np.std(absolutes) > 1e-12
        and np.std(absolutes[1:]) > 1e-12
        and np.std(absolutes[:-1]) > 1e-12 else None
    )
    return {
        "observation_count": int(len(data)),
        "return_observation_count": int(len(r)),
        "mean_log_return": mean,
        "log_return_std": vol,
        "excess_kurtosis": kurt,
        "acf_abs_log_return_lag1": acf,
        "zero_return_share": float(np.mean(np.abs(r) <= 1e-12)),
        "positive_volume_share": float(np.mean(volume > 0)),
        "mean_volume": float(np.mean(volume)),
        "first_date": str(data.date.iloc[0]),
        "last_date": str(data.date.iloc[-1]),
    }


def inspect_reference(
    spec: ReferenceSpec, output: Path, *,
    unlock_heldout: bool = False, min_partition_size: int = 20,
) -> dict[str, Any]:
    values = load_reference(spec)
    if min_partition_size < 5:
        raise ValueError("Minimum partition size must be >=5")
    dev_date = date.fromisoformat(spec.development_end)
    val_date = date.fromisoformat(spec.validation_end)
    slices = {
        "development": values.loc[values.date <= dev_date],
        "validation": values.loc[(values.date > dev_date) & (values.date <= val_date)],
        "heldout": values.loc[values.date > val_date],
    }
    if any(len(data) < min_partition_size for data in slices.values()):
        raise ValueError("A reference partition is too short for the requested threshold")
    report = {
        "schema_version": 1,
        "source_declared_origin": spec.origin,
        "source_name": spec.source_name,
        "source_url": spec.source_url,
        "asset": spec.asset,
        "market": spec.market,
        "frequency": spec.frequency,
        "price_adjustment": spec.price_adjustment,
        "timezone": spec.timezone,
        "snapshot_sha256": spec.expected_sha256,
        "historical_origin_independently_verified": False,
        "simulator_step_equals_reference_frequency": False,
        "partition_boundaries": {
            "development_end": spec.development_end,
            "validation_end": spec.validation_end,
        },
        "metrics": {},
        "limitations": [
            "Snapshot hashes establish file identity, not independent data authenticity",
            "Daily/intraday reference sampling is not automatically aligned to one simulator step",
            "Close/volume snapshots cannot validate order-book spread/depth",
            "Development and validation metrics are not out-of-sample trading predictions",
        ],
    }
    for name, frame in slices.items():
        if name == "heldout" and not unlock_heldout:
            report["metrics"][name] = {
                "locked": True, "observation_count": len(frame),
                "returns_not_inspected": True,
            }
        else:
            report["metrics"][name] = {
                "locked": False,
                **_summary(frame),
            }
    output.mkdir(parents=True, exist_ok=False)
    (output / "reference_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--unlock-heldout", action="store_true")
    parser.add_argument("--min-partition-size", default=20, type=int)
    args = parser.parse_args()
    report = inspect_reference(
        ReferenceSpec.from_json(args.spec), args.output,
        unlock_heldout=args.unlock_heldout,
        min_partition_size=args.min_partition_size,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
