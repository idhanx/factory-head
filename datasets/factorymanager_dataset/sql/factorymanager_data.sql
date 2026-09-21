-- FactoryManager AI — full-product test dataset
-- 419,557 rows, 9 plants, data up to 2026-09-12T11:20:00+05:30
-- Load into an EMPTY schema at Alembic head (9f3c2d6e8a41):
--   cd backend && alembic upgrade head
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f factorymanager_data.sql
-- Money is integer paise. Timestamps are IST (+05:30).

-- The data itself is in data/*.sql, split so that no single file
-- exceeds GitHub's file-size limit. Load this file, not the parts:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f factorymanager_data.sql
-- psql resolves \i relative to the file it is reading, so run it from
-- anywhere.

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET search_path = public;

BEGIN;
\i data/000_data.sql
\i data/001_data.sql
\i data/002_data.sql
\i data/003_data.sql
\i data/004_data.sql

COMMIT;
