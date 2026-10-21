"""Protocol v0.1 artefacts stay in sync: JSON Schemas and accept/reject examples (plan 11.3)."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from lab.capability import check_scenario
from lab.specs import ScenarioSpec, json_schemas

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/lab/spec-examples"


def test_checked_in_schemas_match_models():
    spec = importlib.util.spec_from_file_location("export_lab_schemas", ROOT / "scripts/export_lab_schemas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, schema in json_schemas().items():
        path = ROOT / "docs/lab/schemas" / f"{name}.schema.json"
        assert path.read_text(encoding="utf-8") == module.render(schema), f"{path.name} is stale; run scripts/export_lab_schemas.py"
    assert json.loads((ROOT / "docs/lab/schemas/scenario_spec.schema.json").read_text())["additionalProperties"] is False


EXPECTATIONS = yaml.safe_load((EXAMPLES / "expectations.yaml").read_text(encoding="utf-8"))


def test_every_example_has_an_expectation():
    assert set(EXPECTATIONS) == {p.name for p in EXAMPLES.glob("*.yaml")} - {"expectations.yaml"}


@pytest.mark.parametrize("name", sorted(EXPECTATIONS))
def test_examples_behave_as_declared(name):
    expected = EXPECTATIONS[name]
    payload = yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))
    if expected["status"] == "schema_error":
        with pytest.raises(ValueError):
            ScenarioSpec.model_validate(payload)
        return
    report = check_scenario(ScenarioSpec.model_validate(payload))
    assert report.status == expected["status"]
    assert set(expected["codes"]) <= {i.code for i in report.issues}


def test_course_map_baseline_matches_catalog():
    from lab.catalog import load_catalog
    from lab.export import course_map_csv
    path = ROOT / "docs/lab/course_map_v1.csv"
    assert path.read_text(encoding="utf-8-sig") == course_map_csv(load_catalog()).replace("\r\n", "\n"), \
        "course_map_v1.csv is stale; run scripts/export_course_map.py"
