-- data_lake/gold/gold_schema.sql
-- IA Odonto Lab — Gold Layer: Snowflake Star Schema
--
-- Run this in a Snowflake Worksheet before loading data.
-- Designed for analytical queries on treatment pipeline and revenue.
--
-- Star schema:
--   FACT_INTERACTIONS (center)
--   DIM_PATIENTS      (anonymized — LGPD compliant)
--   DIM_SERVICES
--   DIM_DATE

CREATE DATABASE IF NOT EXISTS IA_ODONTO_DW;
CREATE SCHEMA IF NOT EXISTS IA_ODONTO_DW.GOLD;
USE SCHEMA IA_ODONTO_DW.GOLD;

-- ── Dimension: Anonymized Patients (LGPD compliant) ─────────────────────────
CREATE TABLE IF NOT EXISTS DIM_PATIENTS (
    patient_key     VARCHAR(64)  PRIMARY KEY,  -- SHA-256 hash of original ID
    intent_segment  VARCHAR(50),               -- Inquiry | Scheduling | Complaint | Other
    fear_segment    VARCHAR(100),              -- e.g. "needle_phobia", "none"
    first_contact   DATE,
    last_contact    DATE,
    total_visits    INTEGER      DEFAULT 0
);

-- ── Dimension: Dental Services ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DIM_SERVICES (
    service_key     INTEGER      AUTOINCREMENT PRIMARY KEY,
    service_name    VARCHAR(100) NOT NULL,     -- e.g. "Dental Implant"
    category        VARCHAR(50),               -- e.g. "Implantology", "Aesthetics"
    avg_price_brl   DECIMAL(10,2)
);

-- ── Dimension: Date ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DIM_DATE (
    date_key        DATE         PRIMARY KEY,
    year            INTEGER,
    month           INTEGER,
    quarter         INTEGER,
    day_of_week     INTEGER,
    is_weekend      BOOLEAN
);

-- ── Fact Table: Patient Interactions ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS FACT_INTERACTIONS (
    interaction_id   VARCHAR(36)  DEFAULT UUID_STRING() PRIMARY KEY,
    patient_key      VARCHAR(64)  REFERENCES DIM_PATIENTS(patient_key),
    service_key      INTEGER      REFERENCES DIM_SERVICES(service_key),
    date_key         DATE         REFERENCES DIM_DATE(date_key),

    -- AI-generated intelligence
    estimated_potential  DECIMAL(12,2) DEFAULT 0,
    ai_intent            VARCHAR(50),
    visit_count          INTEGER       DEFAULT 0,

    -- Financial actuals (from billing DB via Silver layer)
    valor_pago           DECIMAL(12,2) DEFAULT 0,
    ltv_acumulado        DECIMAL(12,2) DEFAULT 0,

    -- Metadata
    created_at           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ── Analytical Views ─────────────────────────────────────────────────────────

-- Revenue pipeline: estimated potential vs actual LTV
CREATE OR REPLACE VIEW VW_PIPELINE_BY_INTENT AS
SELECT
    ai_intent,
    COUNT(*)                          AS total_interactions,
    ROUND(SUM(estimated_potential),2) AS total_potential_brl,
    ROUND(SUM(valor_pago),2)          AS total_revenue_brl,
    ROUND(AVG(ltv_acumulado),2)       AS avg_ltv_brl,
    ROUND(SUM(valor_pago) / NULLIF(SUM(estimated_potential),0) * 100, 1) AS conversion_rate_pct
FROM FACT_INTERACTIONS
GROUP BY ai_intent
ORDER BY total_potential_brl DESC;

-- Fear segment vs conversion correlation
CREATE OR REPLACE VIEW VW_FEAR_VS_CONVERSION AS
SELECT
    p.fear_segment,
    COUNT(f.interaction_id)           AS total_patients,
    ROUND(AVG(f.estimated_potential),2) AS avg_potential,
    ROUND(AVG(f.valor_pago),2)        AS avg_revenue,
    ROUND(AVG(f.valor_pago) / NULLIF(AVG(f.estimated_potential),0) * 100, 1) AS conversion_pct
FROM FACT_INTERACTIONS f
JOIN DIM_PATIENTS p ON f.patient_key = p.patient_key
GROUP BY p.fear_segment
ORDER BY conversion_pct DESC;
