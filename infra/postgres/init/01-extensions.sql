-- Extensions the platform depends on.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Factory Memory retrieval starts on Postgres full-text search. Memory with
-- three weeks of history is worthless anyway (§3 Layer 4), so semantic recall
-- via pgvector is enabled here but not depended on until the plant has depth.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
