"""Course catalog: courses -> knowledge points -> scenarios -> lessons.

Loads the versioned YAML under lab/content and cross-checks references so that
a course never links to a scenario that does not exist, and a lesson never
references a scenario without a runnable spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from lab.specs import LessonSpec, ScenarioSpec, load_lesson, load_scenario

CATALOG_DIR = Path(__file__).resolve().parent / "content"


@dataclass(frozen=True)
class Catalog:
    courses: tuple[dict, ...]
    index: tuple[dict, ...]
    scenarios: dict[str, ScenarioSpec]
    lessons: dict[str, LessonSpec]

    def course(self, code: str) -> dict:
        return next(c for c in self.courses if c["code"] == code)

    def entry(self, scenario_id: str) -> dict:
        return next(e for e in self.index if e["id"] == scenario_id)

    def scenarios_for_course(self, code: str) -> list[dict]:
        """Index entries linked to a course, each marked runnable or not."""
        ids = self.course(code)["scenarios"]
        return [{**self.entry(i), "runnable": i in self.scenarios} for i in ids]

    def lessons_for_course(self, code: str) -> list[LessonSpec]:
        return [l for l in self.lessons.values()
                if code in {c.code for c in (*l.primary_courses, *l.related_courses)}]

    def search(self, text: str) -> list[dict]:
        text = text.strip().lower()
        return [c for c in self.courses if not text or text in c["code"] or text in c["name"].lower()
                or any(text in k.lower() for k in c["knowledge_points"])]


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_catalog(catalog: Catalog) -> list[str]:
    problems = []
    index_ids = {e["id"] for e in catalog.index}
    course_codes = {c["code"] for c in catalog.courses}
    if len(index_ids) != len(catalog.index):
        problems.append("duplicate scenario ids in scenario_index.yaml")
    if len(course_codes) != len(catalog.courses):
        problems.append("duplicate course codes in courses.yaml")
    for course in catalog.courses:
        for sid in set(course["scenarios"]) - index_ids:
            problems.append(f"course {course['code']} links unknown scenario {sid}")
    for entry in catalog.index:
        for code in set(entry["courses"]) - course_codes:
            problems.append(f"{entry['id']} references unknown course {code}")
    for sid, spec in catalog.scenarios.items():
        if sid not in index_ids:
            problems.append(f"scenario spec {sid} missing from index")
        for code in set(spec.course_codes) - course_codes:
            problems.append(f"{sid} references unknown course {code}")
    for lid, lesson in catalog.lessons.items():
        for sid in set(lesson.scenarios) - set(catalog.scenarios):
            problems.append(f"lesson {lid} references scenario without spec: {sid}")
        for stage in lesson.stages:
            if stage.scenario_id in catalog.scenarios:
                known = {g.id for g in catalog.scenarios[stage.scenario_id].comparison.groups}
                for gid in set(stage.groups) - known:
                    problems.append(f"lesson {lid} stage '{stage.name}' references unknown group {gid}")
    return problems


@lru_cache(maxsize=4)
def load_catalog(root: str | Path = CATALOG_DIR) -> Catalog:
    root = Path(root)
    scenarios = {s.scenario_id: s for s in map(load_scenario, sorted((root / "scenarios").glob("*.yaml")))}
    lessons = {l.lesson_id: l for l in map(load_lesson, sorted((root / "lessons").glob("*.yaml")))}
    catalog = Catalog(tuple(_yaml(root / "courses.yaml")), tuple(_yaml(root / "scenario_index.yaml")), scenarios, lessons)
    problems = validate_catalog(catalog)
    if problems:
        raise ValueError("Invalid Civitas Lab catalog: " + "; ".join(problems))
    return catalog
