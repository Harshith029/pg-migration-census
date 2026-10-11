-- Does an interrupted CREATE INDEX CONCURRENTLY ... IF NOT EXISTS get rebuilt when the
-- migration is replayed?  Run against a scratch database (PostgreSQL 16 was used):
--   psql -v ON_ERROR_STOP=0 -f repro_invalid_index.sql
-- Each statement runs as its own transaction (psql autocommit), which CONCURRENTLY requires.

DROP TABLE IF EXISTS repro_t;
CREATE TABLE repro_t AS
  SELECT g AS a, md5(g::text) AS b FROM generate_series(1, 2000000) AS g;
ANALYZE repro_t;

\echo '--- 1. interrupt the build (statement_timeout stands in for a killed pod or a cancelled deploy)'
SET statement_timeout = '100ms';
CREATE INDEX CONCURRENTLY IF NOT EXISTS repro_ix ON repro_t (b);
RESET statement_timeout;

\echo '--- the leftover index is invalid (indisvalid = f):'
SELECT c.relname AS index, i.indisvalid
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'repro_ix';

\echo '--- 2. replay the same statement, as a re-run migration would:'
CREATE INDEX CONCURRENTLY IF NOT EXISTS repro_ix ON repro_t (b);

\echo '--- still invalid after the replay:'
SELECT c.relname AS index, i.indisvalid
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'repro_ix';

\echo '--- 3. the planner does not use it:'
EXPLAIN (COSTS OFF) SELECT * FROM repro_t WHERE b = 'abc';

\echo '--- 4. drop-before-build recovers:'
DROP INDEX CONCURRENTLY IF EXISTS repro_ix;
CREATE INDEX CONCURRENTLY IF NOT EXISTS repro_ix ON repro_t (b);
SELECT c.relname AS index, i.indisvalid
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'repro_ix';
EXPLAIN (COSTS OFF) SELECT * FROM repro_t WHERE b = 'abc';

\echo '--- the check to run on a live database (lists every stranded index):'
SELECT t.relname AS "table", c.relname AS "index", i.indisvalid, i.indisready
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
WHERE NOT i.indisvalid;

DROP TABLE repro_t;
