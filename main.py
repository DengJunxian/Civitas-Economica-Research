"""Civitas application entry: headless experiments or optional UI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Civitas reproducible artificial market experiments")
    commands = parser.add_subparsers(dest="command")
    for command in ("run", "validate", "batch"):
        p = commands.add_parser(command)
        p.add_argument("--config", required=True, action="append" if command == "batch" else "store")
        if command != "validate":
            p.add_argument("--output", default="outputs/experiments")
        if command == "batch":
            p.add_argument("--seeds", required=True, help="Comma-separated integer seeds")
    p = commands.add_parser("verify")
    p.add_argument("run_dir")
    p = commands.add_parser("reproduce")
    p.add_argument("run_dir")
    p.add_argument("--output", default="outputs/reproductions")
    commands.add_parser("ui")
    lab = commands.add_parser("lab", help="Civitas Lab teaching scenarios").add_subparsers(dest="lab_command", required=True)
    p = lab.add_parser("catalog")
    p.add_argument("--course", help="Course code, e.g. 107349")
    for name in ("check", "compile", "run"):
        p = lab.add_parser(name)
        p.add_argument("scenario", help="Scenario id from the catalog (EXP-05) or a ScenarioSpec YAML path")
        if name == "run":
            p.add_argument("--confirm", default="", help="Name of the teacher confirming this run (required)")
            p.add_argument("--output", default="outputs/lab")
    p = lab.add_parser("compare")
    p.add_argument("comparison_dir")
    p.add_argument("--metric", help="Outcome id; defaults to the scenario's primary outcome")
    p = lab.add_parser("verify")
    p.add_argument("comparison_dir")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "ui":
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("app.py"))])
    if args.command == "lab":
        return lab_main(args)
    from core.experiments.config import ExperimentConfig, load_config
    from core.experiments.runner import ExperimentRunner, write_json
    from core.experiments.verify import verify_run, reproduce_run
    try:
        if args.command == "validate":
            print(load_config(args.config).model_dump_json(indent=2))
        elif args.command == "run":
            print(ExperimentRunner(load_config(args.config)).run(args.output))
        elif args.command == "verify":
            print(json.dumps(verify_run(args.run_dir), ensure_ascii=False, indent=2))
        elif args.command == "reproduce":
            print(json.dumps(reproduce_run(args.run_dir, args.output), ensure_ascii=False, indent=2))
        else:
            configs = [load_config(p) for p in args.config]
            seeds = [int(s) for s in args.seeds.split(",")]
            batch_dir = Path(args.output).resolve() / f"batch-{uuid.uuid4().hex[:12]}"
            batch_dir.mkdir(parents=True, exist_ok=False)
            rows = []
            for cfg in configs:
                for seed in seeds:
                    payload = cfg.model_dump(mode="json"); payload["seed"] = seed
                    runner = None
                    try:
                        runner = ExperimentRunner(ExperimentConfig.model_validate(payload))
                        path = runner.run(batch_dir)
                        rows.append({"name": cfg.name, "seed": seed, "status": "completed", "path": str(path)})
                    except Exception as exc:
                        rows.append({"name": cfg.name, "seed": seed, "status": "failed", "error_type": type(exc).__name__,
                                     "path": str(runner.output_dir) if runner and hasattr(runner, "output_dir") else None})
                    write_json(batch_dir / "batch.json", rows)
            print(batch_dir)
            return int(any(r["status"] != "completed" for r in rows))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def lab_main(args) -> int:
    from lab.capability import check_scenario
    from lab.catalog import load_catalog
    from lab.compare import evidence_card
    from lab.compiler import compile_scenario
    from lab.service import run_scenario, verify_comparison
    from lab.specs import load_scenario

    def dump(payload) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    def scenario(ref: str):
        return load_scenario(ref) if Path(ref).is_file() else load_catalog().scenarios[ref]

    try:
        if args.lab_command == "catalog":
            catalog = load_catalog()
            if args.course:
                dump({"course": catalog.course(args.course), "scenarios": catalog.scenarios_for_course(args.course),
                      "lessons": [l.lesson_id for l in catalog.lessons_for_course(args.course)]})
            else:
                dump([{"id": e["id"], "title": e["title"], "phase": e["phase"], "runnable": e["id"] in catalog.scenarios}
                      for e in catalog.index])
        elif args.lab_command == "check":
            report = check_scenario(scenario(args.scenario))
            dump(report.to_dict())
            return int(not report.ok)
        elif args.lab_command == "compile":
            dump([{"group_id": c.group_id, "seed": c.seed, "config": c.config.model_dump(mode="json")}
                  for c in compile_scenario(scenario(args.scenario))])
        elif args.lab_command == "run":
            print(run_scenario(scenario(args.scenario), confirmed_by=args.confirm, output_root=args.output,
                               on_progress=lambda i, n, e: print(f"[{i}/{n}] {e['group_id']} seed={e['seed']} {e['status']}", file=sys.stderr)))
        elif args.lab_command == "compare":
            dump(evidence_card(args.comparison_dir, args.metric))
        else:
            result = verify_comparison(args.comparison_dir)
            dump(result)
            return int(result["status"] != "verified")
    except KeyError as exc:
        print(f"Unknown catalog entry: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
