"""Write docs/lab/course_map_v1.csv (UTF-8 with BOM so spreadsheet apps open the Chinese text).

The file is the proposed course -> knowledge point -> experiment mapping with
empty columns for teacher feedback (plan v1.0 section 11.3). Teachers fill in a
copy; tests/test_lab_schemas.py keeps the checked-in baseline in sync with the
catalog, so rerun this script after catalog changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab.catalog import load_catalog  # noqa: E402
from lab.export import course_map_csv  # noqa: E402

TARGET = ROOT / "docs/lab/course_map_v1.csv"

if __name__ == "__main__":
    TARGET.write_text(course_map_csv(load_catalog()), encoding="utf-8-sig", newline="")
    print(TARGET)
