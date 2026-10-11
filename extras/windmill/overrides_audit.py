"""Audit of Windmill's OVERRIDDEN_MIGRATIONS (backend/windmill-api/src/db.rs).

Windmill rewrites some migration files at startup (CREATE INDEX -> CREATE INDEX CONCURRENTLY, etc.)
and runs them statement by statement outside a transaction. Linting the .sql files as committed
therefore shows something different from what actually runs. This script reproduces the rewrite
and looks at the statements that really execute.

What it reports, per rewritten migration that contains CONCURRENTLY after the rewrite:
  * every statement, in order, exactly as db.rs would split it (naive split on ';');
  * for each CREATE INDEX CONCURRENTLY IF NOT EXISTS: is there a DROP INDEX of the same name
    earlier in the same migration? Without one, a build interrupted part-way leaves an invalid
    index that a replay skips (see repro_invalid_index.sql);
  * any CREATE INDEX form the string replace misses (it is case-sensitive and does not match
    CREATE UNIQUE INDEX);
  * any chunk of the naive split that does not parse on its own.

Usage:
    python3 -I overrides_audit.py <windmill-checkout-root> <out.csv>

Reads files only. Needs pglast.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from pglast import parse_sql
from pglast.ast import DropStmt, IndexStmt
from pglast.enums import ObjectType

root = Path(sys.argv[1]).resolve()
out_csv = Path(sys.argv[2])
backend = root / "backend"
db_rs = (backend / "windmill-api/src/db.rs").read_text(encoding="utf-8")

# Entries look like:  (20260207000001, include_str!("../../migrations/x.up.sql").replace("A","B").replace(...)),
ENTRY = re.compile(
    r'\((\d{14}),\s*include_str!\(\s*"([^"]+)"\s*\)((?:\s*\.replace\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*\))*)',
    re.S,
)
REPLACE = re.compile(r'\.replace\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', re.S)


def rust_str(s: str) -> str:
    # Only the escapes that appear in db.rs patterns.
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def is_comment_only(stmt: str) -> bool:
    return not any(l.strip() and not l.strip().startswith("--") for l in stmt.splitlines())


rows = []
problems = []
for version, rel, chain in ENTRY.findall(db_rs):
    path = (backend / "windmill-api/src" / rel).resolve()
    sql = path.read_text(encoding="utf-8")
    for a, b in REPLACE.findall(chain):
        sql = sql.replace(rust_str(a), rust_str(b))
    if "CONCURRENTLY" not in sql:
        continue  # db.rs runs these as one multi-statement execute; not part of this audit
    dropped: set[str] = set()
    pos = 0
    for chunk in sql.split(";"):
        stmt = chunk.strip()
        if not stmt or is_comment_only(stmt):
            continue
        pos += 1
        try:
            parsed = parse_sql(stmt)
        except Exception as e:  # a chunk that cannot stand alone is what db.rs would send to Postgres
            problems.append((version, path.name, pos, f"chunk does not parse on its own: {e}"))
            continue
        for raw in parsed:
            s = raw.stmt
            if isinstance(s, DropStmt) and s.removeType == ObjectType.OBJECT_INDEX:
                for o in s.objects or []:
                    name = o[-1].sval.lower()
                    dropped.add(name)
                    rows.append([version, path.name, pos, "DROP INDEX", name, "", s.concurrent, s.missing_ok, "", ""])
            elif isinstance(s, IndexStmt):
                name = (s.idxname or "").lower()
                table = s.relation.relname
                protected = name in dropped
                note = ""
                if not s.concurrent:
                    note = "NOT concurrent after the rewrite (missed by the string replace)"
                    problems.append((version, path.name, pos, f"{name}: {note}"))
                rows.append([version, path.name, pos, "CREATE UNIQUE INDEX" if s.unique else "CREATE INDEX",
                             name, table, s.concurrent, s.if_not_exists, protected, note])

with out_csv.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["version", "file", "stmt_no", "action", "index", "table", "concurrent", "if_exists_or_not_exists",
                "dropped_earlier_in_same_migration", "note"])
    w.writerows(rows)

creates = [r for r in rows if r[3].startswith("CREATE")]
ine = [r for r in creates if r[7] is True]
unprotected = [r for r in ine if r[8] is False]
migs = sorted({r[0] for r in creates})
print(f"rewritten migrations containing CONCURRENTLY: {len({r[0] for r in rows})}")
print(f"CREATE INDEX statements that execute: {len(creates)} (CONCURRENTLY after rewrite: {sum(1 for r in creates if r[6])})")
print(f"  with IF NOT EXISTS: {len(ine)}")
print(f"  of those, a DROP INDEX of the same name earlier in the same migration: {len(ine) - len(unprotected)}")
print(f"  of those, no such DROP (a replay after an interrupted build would skip an invalid index): {len(unprotected)}")
print(f"  spread over migrations: {sorted({r[0] for r in unprotected})}")
print(f"problems found: {len(problems)}")
for p in problems:
    print("  ", p)
