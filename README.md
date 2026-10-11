# pg-migration-census

A small, reproducible count of how real open-source projects change their Postgres schemas:
how often a migration uses the online-safe form of a statement (`CREATE INDEX CONCURRENTLY`,
`ADD FOREIGN KEY ... NOT VALID`) and how often it does not.

This counts statements in migration files at pinned commits. It is not a study of outages, it
says nothing about table size, and 17 projects is a convenience sample (popular, Postgres-backed,
public migrations), not a random one. Snapshot taken 2026-10-10.

## Reproduce

```bash
pip install -r requirements.txt
bash fetch.sh                    # 17 sparse checkouts at pinned commits, about 20 seconds
python3 -I analyze.py            # writes results/metrics.csv and results/files.csv
python3 -I report.py             # prints the tables below
```

Optional cross-check against a second implementation (safemigrate-lint, a Postgres migration
linter by the same author, written separately):

```bash
pip install -r requirements-crosscheck.txt
python3 -I crosscheck_linter.py
```

Every number in the Results section is produced by `report.py` from `results/`, so the README
cannot drift from the data. `results/files.csv` has one row per migration file, so any count can
be traced to the files that produced it.

## What is counted

A table is **new and empty** when an earlier statement in the *same file* created it (`CREATE
TABLE`, or `CREATE TABLE AS ... WITH NO DATA`) and nothing in that file has written rows into it
since (`INSERT`, `COPY`, `CREATE TABLE AS` with data). Locks on such a table cost nothing, so
statements on it are not counted as risky. Every other table is **existing**, including a table
created by an earlier migration file (which may have been empty when that migration first ran).

| Counter | Meaning |
|---|---|
| `index_existing`, `index_existing_concurrent` | `CREATE INDEX` on an existing table, and how many use `CONCURRENTLY` |
| `fk_existing`, `fk_not_valid` | `ADD FOREIGN KEY` on an existing table, and how many use `NOT VALID` |
| `check_existing`, `check_not_valid` | the same for `ADD CHECK` |
| `alter_col_type` | `ALTER COLUMN TYPE` on an existing table (usually rewrites the table) |
| `add_col_notnull_nodefault` | `ADD COLUMN ... NOT NULL` with no default, identity or generated expression, on an existing table (fails on any non-empty table) |
| `drop_column`, `drop_table` | every such statement, new table or not |
| `do_blocks` | `DO $$ ... $$` blocks, which a static parse cannot see into |

## Results

<!-- RESULTS:START -->
Corpus: **17 projects, 4,365 migration files (4,301 parsed, 64 not), 15,217 statements**, each project pinned to one commit (see `manifest.csv`). Parsed with pglast v8.5 (PostgreSQL 18.6 grammar).

### Headline counts

| Pattern (on a table that is not new and empty) | Statements | Online-safe form | Share |
|---|---:|---:|---:|
| `CREATE INDEX` | 1,450 | `CONCURRENTLY`: 243 | 16.8% |
| `ADD FOREIGN KEY` | 425 | `NOT VALID`: 2 | 0.5% |
| `ADD CHECK` | 56 | `NOT VALID`: 6 | 10.7% |

Other statements that rewrite a table or fail on a non-empty one (no online-safe one-liner exists, so no share is given):

- `ALTER COLUMN TYPE` on an existing table: 283
- `ADD COLUMN ... NOT NULL` with no default, on an existing table (fails on any non-empty table): 182
- `DROP COLUMN`: 574; `DROP TABLE`: 285
- `DO $$ ... $$` blocks, which this count cannot see into: 266

### By project

| Project | Commit | Files | Statements | `CREATE INDEX` (existing) | `CONCURRENTLY` | FK added | FK `NOT VALID` |
|---|---|---:|---:|---:|---:|---:|---:|
| mattermost/mattermost | `47eebd5530` | 232/232 | 601 | 58 | 37 (63.8%) | 0 | 0 |
| calcom/cal.com | `54343aa685` | 595/595 | 1,864 | 180 | 2 (1.1%) | 113 | 0 |
| supabase/postgres | `142c6a2c6c` | 54/54 | 161 | 0 | 0 (n/a) | 0 | 0 |
| supabase/auth | `ce9a8eee0c` | 12/76 | 13 | 0 | 0 (n/a) | 0 | 0 |
| supabase/storage | `f31599e188` | 105/105 | 299 | 11 | 8 (72.7%) | 0 | 0 |
| hasura/graphql-engine | `e77418e3f6` | 82/82 | 305 | 9 | 0 (0.0%) | 4 | 0 |
| windmill-labs/windmill | `ed7a859e8c` | 675/675 | 2,285 | 136 | 0 (0.0%) | 4 | 0 |
| LemmyNet/lemmy | `040171d37a` | 344/344 | 2,666 | 455 | 0 (0.0%) | 34 | 0 |
| documenso/documenso | `38ecb217ef` | 165/165 | 770 | 71 | 0 (0.0%) | 58 | 0 |
| formbricks/formbricks | `2c5c3b104b` | 183/183 | 1,009 | 84 | 3 (3.6%) | 20 | 0 |
| triggerdotdev/trigger.dev | `93465d8549` | 843/843 | 2,348 | 155 | 62 (40.0%) | 112 | 0 |
| langfuse/langfuse | `35d16971a3` | 450/450 | 1,203 | 139 | 64 (46.0%) | 42 | 0 |
| hatchet-dev/hatchet | `82fce9703e` | 245/245 | 1,295 | 100 | 66 (66.0%) | 18 | 2 |
| riverqueue/river | `2f6b27e69c` | 8/8 | 46 | 2 | 0 (0.0%) | 0 | 0 |
| temporalio/temporal | `f6279f9c03` | 25/25 | 68 | 0 | 0 (n/a) | 0 | 0 |
| openfga/openfga | `526995eb20` | 6/6 | 15 | 2 | 1 (50.0%) | 0 | 0 |
| ory/kratos | `b86338da04` | 277/277 | 269 | 48 | 0 (0.0%) | 20 | 0 |

Do not read this as a league table: the projects differ in age, size, migration runner and deployment style. Windmill's row is a known undercount (see Limits).

### Where `CONCURRENTLY` appears

207 files contain a `CREATE INDEX CONCURRENTLY` on an existing table, in 8 of the 17 projects. **188 of those 207 files (90.8%) contain exactly one statement.** In 5 projects every such file does (calcom/cal.com, langfuse/langfuse, mattermost/mattermost, supabase/storage, triggerdotdev/trigger.dev). This is consistent with the Postgres rule that `CONCURRENTLY` cannot run inside a transaction block and with runners that send a file as one multi-statement query. It means a low `CONCURRENTLY` share can reflect the runner as much as the authors' awareness.

### One project that enforces it

Mattermost runs a vet rule over its migrations (`server/Makefile`, lines 980 to 982 at the pinned commit, with `-concurrentIndex.minMigration=166`; read by hand, not produced by these scripts). In migrations numbered 166 and above there are 28 index creations on existing tables and 28 use `CONCURRENTLY`; below 166 there are 30, and 9 do.

### Cross-check against a second implementation

`crosscheck_linter.py` runs safemigrate-lint 1.3.0 (the author's own Postgres migration linter, written separately) over the same files. Per-project differences: **0**.

| Category | analyze.py | safemigrate-lint |
|---|---:|---:|
| index without concurrently | 1,207 | 1,207 |
| fk check without not valid | 473 | 473 |
| alter column type | 283 | 283 |
| add column notnull nodefault | 182 | 182 |
| drop column | 574 | 574 |
| drop table | 285 | 285 |

### Windmill, with its startup rewrite applied

Windmill rewrites 13 migrations at startup so that they run `CONCURRENTLY` (`extras/windmill/`). They contain 35 `CREATE INDEX` statements, all `CONCURRENTLY` once rewritten, so at least 35 of Windmill's 136 (25.7%) are online-safe in practice, not 0.

<!-- RESULTS:END -->

## Limits: read before quoting a number

1. **Statements, not outages.** "Existing" does not mean "large". A non-concurrent index on a
   small table is fine, and old migrations were written under the norms of their day.
2. **Static view.** Only top-level statements are parsed. Dynamic SQL and `DO` blocks are
   invisible. Some projects also change migrations at runtime: Windmill rewrites 13 of its
   migrations at startup, so its 0 `CONCURRENTLY` here understates what actually runs
   (see `extras/windmill/`).
3. **The runner shapes the result.** `CONCURRENTLY` cannot run in a transaction block, so a low
   share can reflect the migration runner, not the authors' awareness. This is why the single-
   statement-file figure above is reported.
4. **64 files are not parsed.** All are in `supabase/auth` and are Go templates (`{{ ... }}`).
   They are counted in `files_unparsed`, not silently dropped.
5. **`CREATE TABLE IF NOT EXISTS` counts as creating the table**, which can make a statement
   look new when the table already existed. The safemigrate-lint cross-check makes the same
   assumption, so agreement between the two does not test it.
6. **Not a ranking.** Projects differ in age, size, runner and deployment style.

## Extras

- `extras/windmill/`: an audit of Windmill's startup rewrite of migrations, with a reproduction
  of what happens when a concurrent index build is interrupted.

## Corrections

If a number looks wrong, open an issue with the project and file name from `results/files.csv`.

Written by Krishna Harshith ([github.com/Harshith029](https://github.com/Harshith029)).
MIT licensed (see `LICENSE`). Only counts are published here; the SQL belongs to the projects.
