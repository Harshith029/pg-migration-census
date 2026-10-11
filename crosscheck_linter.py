"""Cross-check analyze.py against a second, independently written implementation.

    pip install -r requirements-crosscheck.txt
    python3 -I crosscheck_linter.py [--repos DIR] [--manifest FILE] [--out DIR]

analyze.py counts statement patterns with a short pglast walk. safemigrate-lint (the author's
Postgres migration linter) reaches the same categories through its own rule engine and state
machine. This script runs the linter over the same files and compares the totals per project.
Where they agree, a bug would have to be in both implementations. Where they differ, the
difference is printed, not hidden.

Reads files only; executes nothing from them. Run analyze.py first.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_string
from safemigrate_lint.core.state import StateBuilder

# category -> (linter rule, how to compute the same number from analyze.py's metrics.csv)
CATEGORIES = {
    "index_without_concurrently": ("concurrent-index-create-required",
                                   lambda m: m["index_existing"] - m["index_existing_concurrent"]),
    "fk_check_without_not_valid": ("constraint-not-valid-required",
                                   lambda m: (m["fk_existing"] - m["fk_not_valid"]) + (m["check_existing"] - m["check_not_valid"])),
    "alter_column_type": ("column-type-change-rewrites-table", lambda m: m["alter_col_type"]),
    "add_column_notnull_nodefault": ("add-non-nullable-without-default", lambda m: m["add_col_notnull_nodefault"]),
    "drop_column": ("drop-column-restricted", lambda m: m["drop_column"]),
    "drop_table": ("drop-table-restricted", lambda m: m["drop_table"]),
}


def goose_up(text: str) -> str:
    parts = text.split("-- +goose Up", 1)
    body = parts[1] if len(parts) == 2 else text
    return body.split("-- +goose Down", 1)[0]


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default=str(here / "repos"))
    ap.add_argument("--manifest", default=str(here / "manifest.csv"))
    ap.add_argument("--out", default=str(here / "results"))
    a = ap.parse_args()
    repos, out = Path(a.repos), Path(a.out)

    lint: dict[str, Counter] = defaultdict(Counter)
    for m in csv.DictReader(open(a.manifest, newline="")):
        base = repos / m["project"].replace("/", "__") / m["migrations_dir"]
        for p in sorted(base.glob(m["glob"])):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            if m["kind"] == "goose":
                text = goose_up(text)
            res = parse_string(text, file=str(p))
            if res.statements is None:
                continue
            state = StateBuilder.build(res.statements)
            for f in analyze(res, state):
                lint[m["project"]][f.rule_id] += 1

    metrics = {r["project"]: {k: int(v) for k, v in r.items() if k != "project"}
               for r in csv.DictReader(open(out / "metrics.csv", newline=""))}
    rows = []
    tot = defaultdict(lambda: [0, 0])
    for proj, m in metrics.items():
        if proj == "ALL":
            continue
        for cat, (rule, fn) in CATEGORIES.items():
            mine, theirs = fn(m), lint[proj][rule]
            rows.append([proj, cat, mine, theirs, "match" if mine == theirs else "DIFFERS"])
            tot[cat][0] += mine
            tot[cat][1] += theirs
    for cat, (mine, theirs) in tot.items():
        rows.append(["ALL", cat, mine, theirs, "match" if mine == theirs else "DIFFERS"])

    with (out / "crosscheck.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["project", "category", "analyze_py", "safemigrate_lint", "result"])
        w.writerows(rows)

    import safemigrate_lint
    ver = getattr(safemigrate_lint, "__version__", "unknown")
    (out / "crosscheck_environment.json").write_text(json.dumps({"safemigrate_lint": ver}) + "\n")
    print(f"safemigrate-lint {ver}")
    for cat, (mine, theirs) in tot.items():
        print(f"  {cat:32} analyze.py={mine:5}  linter={theirs:5}  {'match' if mine == theirs else 'DIFFERS'}")
    diffs = [r for r in rows if r[0] != "ALL" and r[4] != "match"]
    print(f"project-level differences: {len(diffs)}")
    for r in diffs:
        print("  ", r)


if __name__ == "__main__":
    main()
