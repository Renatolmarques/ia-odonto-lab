-- data_lake/gold/migrations/002_sprint2_gold_migration.sql
--
-- Migration: Sprint 2 — add Sprint 1 analytical fields to dim_patients
--            and create dim_opportunities table.
--
-- Run once on ia-odonto-db (PostgreSQL) before running load_gold_vps.py.
-- Safe to run multiple times: ADD COLUMN IF NOT EXISTS + CREATE TABLE IF NOT EXISTS.
--
-- VPS execution:
--   docker exec ia-odonto-db psql -U postgres -d ia_odonto -f /tmp/002_sprint2_gold_migration.sql

-- ── 1. Add Sprint 1 fields to dim_patients ───────────────────────────────────
-- Nine analytical fields. All nullable — contacts created before Sprint 1
-- will have NULLs until the pipeline re-processes them.

ALTER TABLE gold.dim_patients
    ADD COLUMN IF NOT EXISTS etapa_funil            VARCHAR(50),
    ADD COLUMN IF NOT EXISTS status_risco           VARCHAR(20),
    ADD COLUMN IF NOT EXISTS ltv_total_lina         NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS dias_ultima_interacao  INTEGER,
    ADD COLUMN IF NOT EXISTS origem_lead            VARCHAR(50),
    ADD COLUMN IF NOT EXISTS intencao_principal     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS procedimento_interesse VARCHAR(100),
    ADD COLUMN IF NOT EXISTS ctwa_clid              VARCHAR(255),
    ADD COLUMN IF NOT EXISTS anuncio_origem         VARCHAR(100);

-- ── 2. Create dim_opportunities ──────────────────────────────────────────────
-- Each row = one treatment plan. FK to dim_patients via contato_hash.
-- Architectural decision (2026-05-28): no Consulta entity.
-- Each treatment = 1 Opportunity. Sessions = Stream notes in EspoCRM.

CREATE TABLE IF NOT EXISTS gold.dim_opportunities (
    opportunity_hash        VARCHAR(64)     PRIMARY KEY,
    contato_hash            VARCHAR(64)     REFERENCES gold.dim_patients(patient_key),
    nome_oportunidade       VARCHAR(255),
    stage                   VARCHAR(50),
    valor                   NUMERIC(12,2)   DEFAULT 0,
    valor_moeda             VARCHAR(10)     DEFAULT 'BRL',
    probabilidade           INTEGER         DEFAULT 0,
    data_fechamento_prevista DATE,
    created_at              DATE,
    modified_at             DATE
);

CREATE INDEX IF NOT EXISTS idx_dim_opportunities_contato_hash
    ON gold.dim_opportunities (contato_hash);

CREATE INDEX IF NOT EXISTS idx_dim_opportunities_stage
    ON gold.dim_opportunities (stage);
