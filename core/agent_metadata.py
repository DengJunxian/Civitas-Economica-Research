"""Dependency-light metadata helpers shared by agent implementations."""
import os
import json
import hashlib
from typing import Any, Dict, Mapping, Optional

def env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag."""
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def stable_json_hash(payload: Any) -> str:
    """Return a deterministic SHA-256 hash for a JSON-serializable payload."""
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    except TypeError:
        encoded = json.dumps(str(payload), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float coercion for noisy market/account payloads."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def resolve_feature_flags(overrides: Optional[Mapping[str, bool]] = None) -> Dict[str, bool]:
    """Resolve feature flags from environment defaults plus explicit overrides."""
    defaults = {
        "manager_orchestration_v1": True,
        "manager_external_analysts_v1": False,
        "manager_debate_v1": False,
        "manager_metadata_v1": True,
        "population_protocol_v1": True,
    }
    flags = {name: env_flag(f"CIVITAS_{name.upper()}", default) for name, default in defaults.items()}
    if overrides:
        for name, value in overrides.items():
            flags[str(name)] = bool(value)
    return flags


def build_runtime_metadata(
    *,
    seed: int,
    config: Mapping[str, Any],
    snapshot: Optional[Mapping[str, Any]] = None,
    feature_flags: Optional[Mapping[str, bool]] = None,
) -> Dict[str, Any]:
    """Create reproducible metadata for logs, reports, and tests."""
    snapshot_payload = dict(snapshot or {})
    feature_payload = dict(feature_flags or {})
    config_payload = dict(config)
    config_hash = stable_json_hash(
        {
            "seed": int(seed),
            "config": config_payload,
            "feature_flags": feature_payload,
            "snapshot": snapshot_payload,
        }
    )
    return {
        "seed": int(seed),
        "config_hash": config_hash,
        "config": config_payload,
        "snapshot": snapshot_payload,
        "feature_flags": feature_payload,
    }

