#!/usr/bin/env python3
"""Keep the handoff docs lean (run by check.sh).

Each file loaded into every Claude session, or read before every change, has a line budget,
and none talks about the people behind the project in the third person: write the decision
and its reason, not who made it. Over budget means prune (the release skill says how), not
raise the number.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET = {"CLAUDE.md": 80, "PROGRESS.md": 110, "ROADMAP.md": 50}
NO_PEOPLE = re.compile(r"\b(Albert|the owner|owner's)\b")
CHECKED = [*BUDGET, "docs/SPEC.md", *(str(p.relative_to(ROOT)) for p in ROOT.glob(".claude/**/*.md"))]

errors = []
for rel, limit in BUDGET.items():
    lines = len((ROOT / rel).read_text(encoding="utf-8").splitlines())
    if lines > limit:
        errors.append(f"{rel}: {lines} lines, budget {limit}")
for rel in CHECKED:
    for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
        if NO_PEOPLE.search(line):
            errors.append(f"{rel}:{n}: names a person; state the decision and its reason instead")
for e in errors:
    print(f"    {e}", file=sys.stderr)
sys.exit(1 if errors else 0)
