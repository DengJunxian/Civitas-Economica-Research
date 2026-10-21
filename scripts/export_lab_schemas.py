"""Write the Civitas Lab protocol v0.1 JSON Schemas to docs/lab/schemas/.

tests/test_lab_schemas.py fails when the checked-in files differ from the
models, so rerun this script after changing lab/specs.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab.specs import json_schemas  # noqa: E402

SCHEMA_DIR = ROOT / "docs/lab/schemas"


def render(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        (SCHEMA_DIR / f"{name}.schema.json").write_text(render(schema), encoding="utf-8")
        print(SCHEMA_DIR / f"{name}.schema.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
