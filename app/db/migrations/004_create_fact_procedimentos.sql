-- app/db/migrations/004_create_fact_procedimentos.sql
-- Sprint 2: create gold.fact_procedimentos
--
-- Run BEFORE load_gold_vps.py:
--   docker cp /tmp/004_create_fact_procedimentos.sql ia-odonto-db:/tmp/
--   docker exec ia-odonto-db psql -U postgres -d ia_odonto -f /tmp/004_create_fact_procedimentos.sql

CREATE TABLE IF NOT EXISTS gold.fact_procedimentos (
    opportunity_key     VARCHAR(64)     NOT NULL,
    procedimento_key    VARCHAR(64)     NOT NULL,
    procedimento        VARCHAR(100),
    categoria           VARCHAR(50),
    observacao          TEXT,
    created_at          DATE,
    modified_at         DATE,
    loaded_at           TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT fact_procedimentos_pkey PRIMARY KEY (opportunity_key, procedimento_key)
);

-- Index para joins com dim_opportunities
CREATE INDEX IF NOT EXISTS idx_fact_procedimentos_opportunity_key
    ON gold.fact_procedimentos (opportunity_key);

-- Index para agrupamento por procedimento (ticket médio, frequência)
CREATE INDEX IF NOT EXISTS idx_fact_procedimentos_procedimento
    ON gold.fact_procedimentos (procedimento);

-- Index para agrupamento por categoria
CREATE INDEX IF NOT EXISTS idx_fact_procedimentos_categoria
    ON gold.fact_procedimentos (categoria);

-- Verificação
SELECT 'fact_procedimentos criada' AS status, COUNT(*) AS total_rows
FROM gold.fact_procedimentos;