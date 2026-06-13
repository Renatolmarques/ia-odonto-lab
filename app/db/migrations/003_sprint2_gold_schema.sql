-- app/db/migrations/003_sprint2_gold_schema.sql
-- Sprint 2 (2026-05-28): extend dim_patients + create dim_opportunities
--
-- Run BEFORE load_gold_vps.py:
--   docker exec ia-odonto-db psql -U postgres -d ia_odonto -f /tmp/003_sprint2_gold_schema.sql

-- ============================================================
-- Part 1: Extend gold.dim_patients with Sprint 1 CRM fields
-- ============================================================
-- These columns receive data written by Lina to EspoCRM,
-- flowing through Bronze → Silver → Gold pipeline.
-- c_c_fobias_dentarias excluded (LGPD Art. 11 — health data).

ALTER TABLE gold.dim_patients
    ADD COLUMN IF NOT EXISTS etapa_funil            VARCHAR(50),
    ADD COLUMN IF NOT EXISTS status_risco           VARCHAR(20),
    ADD COLUMN IF NOT EXISTS ltv_crm                NUMERIC(13,2),
    ADD COLUMN IF NOT EXISTS dias_ultima_interacao  INTEGER,
    ADD COLUMN IF NOT EXISTS origem_lead            VARCHAR(50),
    ADD COLUMN IF NOT EXISTS intencao_principal     VARCHAR(100),
    ADD COLUMN IF NOT EXISTS procedimento_interesse VARCHAR(100),
    ADD COLUMN IF NOT EXISTS ctwa_clid              VARCHAR(255),
    ADD COLUMN IF NOT EXISTS anuncio_origem         VARCHAR(255);

-- ============================================================
-- Part 2: Create gold.dim_opportunities
-- ============================================================
-- New table for EspoCRM Opportunity entity.
-- opportunity_key: SHA-256 hash of EspoCRM opportunity id + salt.
-- patient_key: SHA-256 hash of contact_id + salt — joins to dim_patients.
-- All monetary values in BRL (or as specified in moeda/valor_realizado_moeda).

CREATE TABLE IF NOT EXISTS gold.dim_opportunities (
    opportunity_key         VARCHAR(64)     NOT NULL,
    patient_key             VARCHAR(64),                    -- FK to dim_patients (nullable: orphan opportunities)
    nome_oportunidade       TEXT,
    stage                   VARCHAR(50),
    last_stage              VARCHAR(50),
    valor                   NUMERIC(13,2)   DEFAULT 0,
    moeda                   VARCHAR(3),
    probabilidade           INTEGER         DEFAULT 0,
    origem_lead             VARCHAR(50),
    data_fechamento_prevista DATE,
    -- Sprint 1 custom fields
    procedimento            VARCHAR(100),
    capi_enviado            BOOLEAN         DEFAULT FALSE,
    capi_enviado_em         TIMESTAMP,
    valor_realizado         NUMERIC(13,2)   DEFAULT 0,
    valor_realizado_moeda   VARCHAR(3),
    capi_evento_tipo        VARCHAR(50),
    ctwa_clid               VARCHAR(255),
    origem_lead_custom      VARCHAR(50),
    -- audit
    created_at              DATE,
    modified_at             DATE,
    loaded_at               TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT dim_opportunities_pkey PRIMARY KEY (opportunity_key)
);

-- Index for joining with dim_patients
CREATE INDEX IF NOT EXISTS idx_dim_opportunities_patient_key
    ON gold.dim_opportunities (patient_key);

-- Index for stage filtering (used in email blocks 1 and 5)
CREATE INDEX IF NOT EXISTS idx_dim_opportunities_stage
    ON gold.dim_opportunities (stage);

-- Index for modified_at (used in email block 5: sem atualização há 7+ dias)
CREATE INDEX IF NOT EXISTS idx_dim_opportunities_modified_at
    ON gold.dim_opportunities (modified_at);

-- Verify
SELECT
    table_name,
    COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'gold'
  AND table_name IN ('dim_patients', 'dim_opportunities')
GROUP BY table_name
ORDER BY table_name;