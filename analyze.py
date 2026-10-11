"""Count online-schema-change patterns in Postgres migration files, with plain pglast.

    python3 -I analyze.py [--repos DIR] [--manifest FILE] [--out DIR]

Reads the files fetched by fetch.sh; executes nothing from them. Output (in --out):
    metrics.csv       one row per project plus ALL
    files.csv         one row per migration file (so any number can be audited)
    environment.json  tool versions and the manifest used

Definitions (all counts are statements, not outages, and say nothing about table size):
  * A table is "fresh" when an earlier statement in the SAME FILE created it (CREATE TABLE, or
    CREATE TABLE AS ... WITH NO DATA) and nothing in that file has written rows into it since
    (INSERT, COPY, CREATE TABLE AS with data). Locks on a fresh table cost nothing.
  * "existing" means: not fresh. Tables created by an earlier migration FILE count as existing.
  * index_existing            CREATE INDEX on an existing table
    index_existing_concurrent ... of which CONCURRENTLY
  * fk_existing / fk_not_valid       ALTER TABLE ... ADD FOREIGN KEY on an existing table / with NOT VALID
  * check_existing / check_not_valid ALTER TABLE ... ADD CHECK on an existing table / with NOT VALID
  * alter_col_type              ALTER COLUMN TYPE on an existing table
  * add_col_notnull_nodefault   ADD COLUMN ... NOT NULL with no default, identity or generated
                                expression, on an existing table (fails on any non-empty table)
  * drop_column, drop_table     every DROP COLUMN / DROP TABLE, fresh or not
  * do_blocks                   DO $$ ... $$ blocks, which a static parse cannot see into
Files that do not parse (Go templates, for example) are counted in files_unparsed and skipped.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from collections import defaultdict
from pathlib import Path

import pglast
from pglast import parse_sql
from pglast.ast import (
    AlterTableCmd,
    AlterTableStmt,
    ColumnDef,
    Constraint,
    CopyStmt,
    CreateStmt,
    CreateTableAsStmt,
    DoStmt,
    DropStmt,
    IndexStmt,
    InsertStmt,
)
from pglast.enums import AlterTableType, ConstrType, ObjectType

COUNTERS = [
    "statements", "do_blocks",
    "index_existing", "index_existing_concurrent", "index_fresh",
    "fk_existing", "fk_not_valid", "check_existing", "check_not_valid",
    "alter_col_type", "add_col_notnull_nodefault", "drop_column", "drop_table",
]


def goose_up(text: str) -> str:
    parts = text.split("-- +goose Up", 1)
    body = parts[1] if len(parts) == 2 else text
    return body.split("-- +goose Down", 1)[0]


def rel_key(rv) -> tuple[str, str]:
    return (rv.schemaname or "public", rv.relname or "")


def analyze_text(text: str) -> dict[str, int]:
    """Counters for one migration file's SQL. Raises on a parse error."""
    stmts = parse_sql(text)
    c = dict.fromkeys(COUNTERS, 0)
    c["statements"] = len(stmts)
    created: set[tuple[str, str]] = set()
    populated: set[tuple[str, str]] = set()

    def fresh(k) -> bool:
        return k in created and k not in populated

    for raw in stmts:
        s = raw.stmt
        if isinstance(s, CreateStmt):
            created.add(rel_key(s.relation))
        elif isinstance(s, CreateTableAsStmt):
            rel = s.into.rel if s.into is not None else None
            if rel is not None:
                k = rel_key(rel)
                created.add(k)
                if not s.into.skipData:
                    populated.add(k)
        elif isinstance(s, (InsertStmt, CopyStmt)):
            if s.relation is not None:
                populated.add(rel_key(s.relation))
        elif isinstance(s, DoStmt):
            c["do_blocks"] += 1
        elif isinstance(s, IndexStmt):
            if fresh(rel_key(s.relation)):
                c["index_fresh"] += 1
            else:
                c["index_existing"] += 1
                if s.concurrent:
                    c["index_existing_concurrent"] += 1
        elif isinstance(s, AlterTableStmt) and s.cmds:
            on_fresh = fresh(rel_key(s.relation))
            for cmd in s.cmds:
                if not isinstance(cmd, AlterTableCmd):
                    continue
                st, d = cmd.subtype, cmd.def_
                if st == AlterTableType.AT_DropColumn:
                    c["drop_column"] += 1
                if on_fresh:
                    continue
                if st == AlterTableType.AT_AddConstraint and isinstance(d, Constraint):
                    if d.contype == ConstrType.CONSTR_FOREIGN:
                        c["fk_existing"] += 1
                        c["fk_not_valid"] += bool(d.skip_validation)
                    elif d.contype == ConstrType.CONSTR_CHECK:
                        c["check_existing"] += 1
                        c["check_not_valid"] += bool(d.skip_validation)
                elif st == AlterTableType.AT_AlterColumnType:
                    c["alter_col_type"] += 1
                elif st == AlterTableType.AT_AddColumn and isinstance(d, ColumnDef):
                    cons = [x for x in (d.constraints or []) if isinstance(x, Constraint)]
                    notnull = any(x.contype == ConstrType.CONSTR_NOTNULL for x in cons)
                    has_default = any(
                        x.contype in (ConstrType.CONSTR_DEFAULT, ConstrType.CONSTR_IDENTITY,
                                      ConstrType.CONSTR_GENERATED) for x in cons
                    ) or d.raw_default is not None
                    if notnull and not has_default:
                        c["add_col_notnull_nodefault"] += 1
        elif isinstance(s, DropStmt) and s.removeType == ObjectType.OBJECT_TABLE:
            c["drop_table"] += len(s.objects or [])
        # Order matters: every statement above was judged before being folded into the state.
    return c


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default=str(here / "repos"))
    ap.add_argument("--manifest", default=str(here / "manifest.csv"))
    ap.add_argument("--out", default=str(here / "results"))
    a = ap.parse_args()
    repos, out = Path(a.repos), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = list(csv.DictReader(open(a.manifest, newline="")))
    per_project = {r["project"]: defaultdict(int) for r in manifest}
    file_rows = []
    for m in manifest:
        proj = m["project"]
        base = repos / proj.replace("/", "__") / m["migrations_dir"]
        files = sorted(p for p in base.glob(m["glob"]) if p.is_file())
        if not files:
            sys.exit(f"no files for {proj} under {base}: run fetch.sh first")
        agg = per_project[proj]
        agg["files"] = len(files)
        for p in files:
            rel = str(p.relative_to(base))
            row = {"project": proj, "path": rel, "parsed": 0, "reason": ""}
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                row["reason"] = "not-utf8"
                agg["files_unparsed"] += 1
                file_rows.append(row)
                continue
            if m["kind"] == "goose":
                text = goose_up(text)
            try:
                counts = analyze_text(text)
            except Exception:
                row["reason"] = "template" if "{{" in text else "syntax-error"
                agg["files_unparsed"] += 1
                file_rows.append(row)
                continue
            row["parsed"] = 1
            row.update(counts)
            file_rows.append(row)
            agg["files_parsed"] += 1
            for k, v in counts.items():
                agg[k] += v

    cols = ["files", "files_parsed", "files_unparsed"] + COUNTERS
    with (out / "metrics.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["project"] + cols)
        total = defaultdict(int)
        for m in manifest:
            agg = per_project[m["project"]]
            w.writerow([m["project"]] + [agg[c] for c in cols])
            for c in cols:
                total[c] += agg[c]
        w.writerow(["ALL"] + [total[c] for c in cols])

    with (out / "files.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["project", "path", "parsed", "reason"] + COUNTERS, restval="")
        w.writeheader()
        w.writerows(file_rows)

    env = {
        "python": platform.python_version(),
        "pglast": pglast.__version__,
        "postgres_grammar": ".".join(str(x) for x in pglast.parser.get_postgresql_version()),
        "manifest": manifest,
    }
    (out / "environment.json").write_text(json.dumps(env, indent=1) + "\n")
    print(f"analysed {len(file_rows)} files -> {out}")


if __name__ == "__main__":
    main()
