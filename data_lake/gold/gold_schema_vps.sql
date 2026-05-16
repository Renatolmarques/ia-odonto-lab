-- data_lake/gold/gold_schema_vps.sql
-- IA Odonto Lab — Gold Layer: Star Schema (PostgreSQL / VPS)
--
-- Run once on ia-odonto-db to create the Gold schema.
-- Equivalent to gold_schema.sql (Snowflake) but using PostgreSQL syntax.
--
-- Star schema:
--   fact_interactions (center)
--   dim_patients      (anonymized — LGPD compliant)
--   dim_services
--   dim_date

CREATE SCHEMA IF NOT EXISTS gold;

-- ── Dimension: Anonymized Patients (LGPD compliant) ──────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_patients (
    patient_key         VARCHAR(64)     PRIMARY KEY,
    status_atendimento  VARCHAR(50),
    pipeline_segment    VARCHAR(50),
    first_contact       DATE,
    last_contact        DATE,
    total_visits        INTEGER         DEFAULT 0
);

-- ── Dimension: Date ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key        DATE        PRIMARY KEY,
    year            INTEGER,
    month           INTEGER,
    quarter         INTEGER,
    day_of_week     INTEGER,
    is_weekend      BOOLEAN
);

-- ── Dimension: Services ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_services (
    service_key     SERIAL          PRIMARY KEY,
    service_name    VARCHAR(100)    NOT NULL,
    category        VARCHAR(50),
    avg_price_brl   NUMERIC(10,2)
);

-- ── Fact Table: Patient Interactions ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_interactions (
    interaction_id      UUID            DEFAULT gen_random_uuid() PRIMARY KEY,
    patient_key         VARCHAR(64)     REFERENCES gold.dim_patients(patient_key),
    service_key         INTEGER,
    date_key            DATE            REFERENCES gold.dim_date(date_key),
    estimated_potential NUMERIC(12,2)   DEFAULT 0,
    ltv_acumulado       NUMERIC(12,2)   DEFAULT 0,
    ticket_medio        NUMERIC(12,2)   DEFAULT 0,
    maior_pagamento     NUMERIC(12,2)   DEFAULT 0,
    visit_count         INTEGER         DEFAULT 0,
    dias_ultima_visita  INTEGER         DEFAULT 0,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

-- ── View: Pipeline by treatment status ───────────────────────────────────────
CREATE OR REPLACE VIEW gold.vw_pipeline_by_status AS
SELECT
    p.status_atendimento,
    p.pipeline_segment,
    COUNT(*)                                                        AS total_patients,
    ROUND(SUM(f.estimated_potential)::NUMERIC, 2)                  AS total_potential_brl,
    ROUND(SUM(f.ltv_acumulado)::NUMERIC, 2)                        AS total_ltv_brl,
    ROUND(AVG(f.ticket_medio)::NUMERIC, 2)                         AS avg_ticket_brl,
    ROUND(
        SUM(f.ltv_acumulado) / NULLIF(SUM(f.estimated_potential), 0) * 100
    , 1)                                                            AS conversion_pct
FROM gold.fact_interactions f
JOIN gold.dim_patients p ON f.patient_key = p.patient_key
GROUP BY p.status_atendimento, p.pipeline_segment
ORDER BY total_potential_brl DESC;

-- ── View: LTV evolution over time ─────────────────────────────────────────────
CREATE OR REPLACE VIEW gold.vw_ltv_by_period AS
SELECT
    d.year,
    d.month,
    COUNT(DISTINCT f.patient_key)                   AS active_patients,
    ROUND(AVG(f.ltv_acumulado)::NUMERIC, 2)         AS avg_ltv_brl,
    ROUND(SUM(f.ltv_acumulado)::NUMERIC, 2)         AS total_ltv_brl,
    ROUND(AVG(f.ticket_medio)::NUMERIC, 2)          AS avg_ticket_brl
FROM gold.fact_interactions f
JOIN gold.dim_date d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;

-- ── View: Re-engagement opportunities ────────────────────────────────────────
CREATE OR REPLACE VIEW gold.vw_reengagement_opps AS
SELECT
    p.patient_key,
    p.status_atendimento,
    p.pipeline_segment,
    p.last_contact,
    f.dias_ultima_visita,
    ROUND(MAX(f.ltv_acumulado)::NUMERIC, 2)         AS ltv_acumulado,
    MAX(f.visit_count)                              AS total_visits
FROM gold.dim_patients p
JOIN gold.fact_interactions f ON p.patient_key = f.patient_key
WHERE f.dias_ultima_visita > 90
  AND f.ltv_acumulado > 0
GROUP BY p.patient_key, p.status_atendimento, p.pipeline_segment,
         p.last_contact, f.dias_ultima_visita
ORDER BY f.dias_ultima_visita DESC;

-- ── View: AI ROI — estimated potential vs actual LTV ─────────────────────────
CREATE OR REPLACE VIEW gold.vw_ai_roi AS
SELECT
    d.year,
    d.month,
    ROUND(SUM(f.estimated_potential)::NUMERIC, 2)   AS total_estimated_brl,
    ROUND(SUM(f.ltv_acumulado)::NUMERIC, 2)         AS total_actual_brl,
    ROUND(
        SUM(f.ltv_acumulado) / NULLIF(SUM(f.estimated_potential), 0) * 100
    , 1)                                            AS roi_pct,
    COUNT(DISTINCT f.patient_key)                   AS patients_scored
FROM gold.fact_interactions f
JOIN gold.dim_date d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;

-- ── View: RAG performance over time ──────────────────────────────────────────
CREATE OR REPLACE VIEW gold.vw_rag_performance AS
SELECT
    date_trunc('day', created_at)::DATE             AS query_date,
    collection,
    COUNT(*)                                        AS total_queries,
    ROUND(AVG(avg_score)::NUMERIC, 3)               AS mean_relevance,
    COUNT(*) FILTER (WHERE avg_score < 0.5)         AS low_quality_queries,
    ROUND(
        COUNT(*) FILTER (WHERE avg_score < 0.5)::NUMERIC / NULLIF(COUNT(*), 0) * 100
    , 1)                                            AS low_quality_pct
FROM public.rag_audit
GROUP BY date_trunc('day', created_at)::DATE, collection
ORDER BY query_date DESC;