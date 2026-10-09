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
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "ui":
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("app.py"))])
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


if __name__ == "__main__":
    raise SystemExit(main())
