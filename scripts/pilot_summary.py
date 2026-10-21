"""Summarise anonymous classroom pilot data into a descriptive Markdown report.

    python scripts/pilot_summary.py --template > pilot_template.csv
    python scripts/pilot_summary.py responses.csv --output pilot_report.md

Rows: anon_id (course-internal anonymous number), lesson_id, phase (pre/post),
concept_score, transfer_score (0-100) and likert_1..likert_6 (1-5, post only).
Cells under 5 respondents are suppressed; identifiers that look like student
numbers, national IDs or e-mail addresses are refused. See lab/pilot.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.pilot import load_responses, report_markdown, summarize, template_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("responses", nargs="?", help="CSV of anonymous responses")
    parser.add_argument("--output", help="Markdown report path (default: stdout)")
    parser.add_argument("--template", action="store_true", help="Print an empty CSV template and exit")
    args = parser.parse_args()
    if args.template:
        sys.stdout.write(template_csv())
        return 0
    if not args.responses:
        parser.error("responses CSV is required unless --template is given")
    try:
        report = report_markdown(summarize(load_responses(Path(args.responses).read_text(encoding="utf-8-sig"))))
    except ValueError as exc:
        print(f"数据未通过检查：{exc}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
