"""Turn results/*.csv into the Markdown tables used in README.md.

    python3 -I report.py                 # print the tables
    python3 -I report.py --write-readme  # replace the block between the RESULTS markers in README.md

Every number printed here is computed from results/, so the README cannot drift from the data.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

here = Path(__file__).resolve().parent
res = here / "results"


def pct(a: int, b: int) -> str:
    return "n/a" if b == 0 else f"{100 * a / b:.1f}%"


def read(name: str):
    return list(csv.DictReader(open(res / name, newline="")))


def build() -> str:
    manifest = {r["project"]: r for r in csv.DictReader(open(here / "manifest.csv", newline=""))}
    metrics = {r["project"]: {k: int(v) for k, v in r.items() if k != "project"} for r in read("metrics.csv")}
    allm = metrics["ALL"]
    env = json.loads((res / "environment.json").read_text())
    files = read("files.csv")
    out: list[str] = []
    w = out.append

    n_proj = len(manifest)
    w(f"Corpus: **{n_proj} projects, {allm['files']:,} migration files "
      f"({allm['files_parsed']:,} parsed, {allm['files_unparsed']} not), {allm['statements']:,} statements**, "
      f"each project pinned to one commit (see `manifest.csv`). Parsed with pglast {env['pglast']} "
      f"(PostgreSQL {env['postgres_grammar']} grammar).\n")

    w("### Headline counts\n")
    w("| Pattern (on a table that is not new and empty) | Statements | Online-safe form | Share |")
    w("|---|---:|---:|---:|")
    w(f"| `CREATE INDEX` | {allm['index_existing']:,} | `CONCURRENTLY`: {allm['index_existing_concurrent']:,} | "
      f"{pct(allm['index_existing_concurrent'], allm['index_existing'])} |")
    w(f"| `ADD FOREIGN KEY` | {allm['fk_existing']:,} | `NOT VALID`: {allm['fk_not_valid']:,} | "
      f"{pct(allm['fk_not_valid'], allm['fk_existing'])} |")
    w(f"| `ADD CHECK` | {allm['check_existing']:,} | `NOT VALID`: {allm['check_not_valid']:,} | "
      f"{pct(allm['check_not_valid'], allm['check_existing'])} |")
    w("")
    w("Other statements that rewrite a table or fail on a non-empty one (no online-safe one-liner exists, "
      "so no share is given):\n")
    w(f"- `ALTER COLUMN TYPE` on an existing table: {allm['alter_col_type']:,}")
    w(f"- `ADD COLUMN ... NOT NULL` with no default, on an existing table (fails on any non-empty table): "
      f"{allm['add_col_notnull_nodefault']:,}")
    w(f"- `DROP COLUMN`: {allm['drop_column']:,}; `DROP TABLE`: {allm['drop_table']:,}")
    w(f"- `DO $$ ... $$` blocks, which this count cannot see into: {allm['do_blocks']:,}\n")

    w("### By project\n")
    w("| Project | Commit | Files | Statements | `CREATE INDEX` (existing) | `CONCURRENTLY` | FK added | FK `NOT VALID` |")
    w("|---|---|---:|---:|---:|---:|---:|---:|")
    for p, m in manifest.items():
        x = metrics[p]
        w(f"| {p} | `{m['commit'][:10]}` | {x['files_parsed']:,}/{x['files']:,} | {x['statements']:,} | "
          f"{x['index_existing']:,} | {x['index_existing_concurrent']:,} ({pct(x['index_existing_concurrent'], x['index_existing'])}) | "
          f"{x['fk_existing']:,} | {x['fk_not_valid']:,} |")
    w("")
    w("Do not read this as a league table: the projects differ in age, size, migration runner and "
      "deployment style. Windmill's row is a known undercount (see Limits).\n")

    # Where CONCURRENTLY lives
    conc_files = [r for r in files if r["parsed"] == "1" and int(r["index_existing_concurrent"] or 0) > 0]
    single = [r for r in conc_files if int(r["statements"]) == 1]
    projs_conc = sorted({r["project"] for r in conc_files})
    projs_all_single = sorted(p for p in projs_conc
                              if all(int(r["statements"]) == 1 for r in conc_files if r["project"] == p))
    w("### Where `CONCURRENTLY` appears\n")
    w(f"{len(conc_files)} files contain a `CREATE INDEX CONCURRENTLY` on an existing table, in "
      f"{len(projs_conc)} of the {n_proj} projects. **{len(single)} of those {len(conc_files)} files "
      f"({pct(len(single), len(conc_files))}) contain exactly one statement.** In "
      f"{len(projs_all_single)} projects every such file does ({', '.join(projs_all_single)}). "
      f"This is consistent with the Postgres rule that `CONCURRENTLY` cannot run inside a transaction block "
      f"and with runners that send a file as one multi-statement query. It means a low `CONCURRENTLY` share can "
      f"reflect the runner as much as the authors' awareness.\n")

    # Mattermost: the one project whose repo enforces CONCURRENTLY with a CI rule (verified by hand in the Makefile)
    mm = [r for r in files if r["project"] == "mattermost/mattermost" and r["parsed"] == "1"
          and int(r["index_existing"] or 0) > 0 and re.match(r"\d+_", r["path"])]
    if mm:
        def split(sel):
            tot = sum(int(r["index_existing"]) for r in sel)
            con = sum(int(r["index_existing_concurrent"]) for r in sel)
            return tot, con
        hi = split([r for r in mm if int(re.match(r"(\d+)_", r["path"]).group(1)) >= 166])
        lo = split([r for r in mm if int(re.match(r"(\d+)_", r["path"]).group(1)) < 166])
        w("### One project that enforces it\n")
        w("Mattermost runs a vet rule over its migrations (`server/Makefile`, lines 980 to 982 at the pinned "
          "commit, with `-concurrentIndex.minMigration=166`; read by hand, not produced by these scripts). "
          f"In migrations numbered 166 and above there are {hi[0]} index creations on existing tables and "
          f"{hi[1]} use `CONCURRENTLY`; below 166 there are {lo[0]}, and {lo[1]} do.\n")

    # Cross-check
    cc = res / "crosscheck.csv"
    if cc.exists():
        rows = [r for r in csv.DictReader(open(cc, newline="")) if r["project"] == "ALL"]
        allrows = list(csv.DictReader(open(cc, newline="")))
        ndiff = sum(1 for r in allrows if r["project"] != "ALL" and r["result"] != "match")
        ver = json.loads((res / "crosscheck_environment.json").read_text()).get("safemigrate_lint", "unknown")
        w("### Cross-check against a second implementation\n")
        w(f"`crosscheck_linter.py` runs safemigrate-lint {ver} (the author's own Postgres migration linter, "
          f"written separately) over the same files. Per-project differences: **{ndiff}**.\n")
        w("| Category | analyze.py | safemigrate-lint |")
        w("|---|---:|---:|")
        for r in rows:
            w(f"| {r['category'].replace('_', ' ')} | {int(r['analyze_py']):,} | {int(r['safemigrate_lint']):,} |")
        w("")

    # Windmill adjustment (optional)
    wm = res / "windmill_overrides.csv"
    if wm.exists():
        wrows = list(csv.DictReader(open(wm, newline="")))
        creates = [r for r in wrows if r["action"].startswith("CREATE")]
        base = metrics.get("windmill-labs/windmill")
        if creates and base:
            n = len(creates)
            w("### Windmill, with its startup rewrite applied\n")
            w(f"Windmill rewrites {len({r['version'] for r in wrows})} migrations at startup so that they run "
              f"`CONCURRENTLY` (`extras/windmill/`). They contain {n} `CREATE INDEX` statements, all "
              f"`CONCURRENTLY` once rewritten, so at least {n} of Windmill's {base['index_existing']} "
              f"({pct(n, base['index_existing'])}) are online-safe in practice, not 0.\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-readme", action="store_true")
    a = ap.parse_args()
    block = build()
    if not a.write_readme:
        print(block)
        return
    readme = here / "README.md"
    text = readme.read_text()
    pat = re.compile(r"<!-- RESULTS:START -->.*?<!-- RESULTS:END -->", re.S)
    if not pat.search(text):
        raise SystemExit("README.md has no RESULTS markers")
    replacement = "<!-- RESULTS:START -->\n" + block + "\n<!-- RESULTS:END -->"
    readme.write_text(pat.sub(lambda m: replacement, text))
    print("README.md updated")


if __name__ == "__main__":
    main()
