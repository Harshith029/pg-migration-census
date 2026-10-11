# Windmill: what the startup rewrite actually runs

Windmill's `backend/windmill-api/src/db.rs` holds a map, `OVERRIDDEN_MIGRATIONS`. For some
migration versions it loads the `.sql` file, applies string replacements (mostly
`CREATE INDEX` to `CREATE INDEX CONCURRENTLY`), splits the result on `;` and runs each statement
outside a transaction. A linter reading the committed `.sql` files therefore sees something other
than what runs.

`overrides_audit.py` reproduces that rewrite and looks at the statements that really execute:

```bash
bash ../../fetch.sh                      # includes the Windmill extras
python3 -I overrides_audit.py ../../repos/windmill-labs__windmill ../../results/windmill_overrides.csv
```

Result at commit `ed7a859e8c` (2026-10-10):

- 13 migrations are rewritten to run `CONCURRENTLY`; they contain 35 `CREATE INDEX` statements,
  all `CONCURRENTLY` after the rewrite. Nothing slips past the string replace (no `CREATE UNIQUE
  INDEX` or lower-case `create index` left over), and every chunk of the `;` split parses on its
  own.
- 31 of the 35 use `IF NOT EXISTS`. Only 1 of those 31 has a `DROP INDEX` of the same name earlier
  in the same migration (`queue_suspended_v2`, 20260826202939). The other 30 are spread over ten
  migrations dated 2026-02-07 to 2026-07-27.
- The four indexes in the newest rewritten migration (20260909163047) have no `IF NOT EXISTS`
  and are each dropped first. That migration, and 20260826202939, explain why in their comments:
  an interrupted concurrent build leaves an invalid index that `IF NOT EXISTS` skips on replay.

## Why the missing drop can matter

`repro_invalid_index.sql` shows the mechanism on PostgreSQL 16 in about five seconds:

1. a `CREATE INDEX CONCURRENTLY IF NOT EXISTS` that is interrupted leaves an index with
   `indisvalid = f`;
2. replaying the same statement prints `relation "repro_ix" already exists, skipping` and the
   index stays invalid;
3. the planner does not use an invalid index (the plan stays a sequential scan);
4. dropping first, then building, gives a valid index that the planner uses.

It only matters if a build was ever interrupted (for example a process killed mid-build during a
rolling restart). Nothing here says that happened on any deployment. This query lists any invalid
index on a live database:

```sql
SELECT t.relname AS "table", c.relname AS "index", i.indisvalid, i.indisready
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
WHERE NOT i.indisvalid;
```

## Not claimed

That this is a bug, that any deployment is affected, or that the maintainers are unaware of it:
their own comments describe the failure mode, and `live_migrations.rs` drops before building for
the audit-table index. This only lists which earlier rewritten migrations predate that practice.
