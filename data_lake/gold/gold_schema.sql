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
--
-- Views:
--   VW_PIPELINE_BY_INTENT          — estimated potential vs actual revenue by intent
--   VW_FEAR_VS_CONVERSION          — fear segment vs conversion rate
--   VW_LTV_BY_PERIOD               — LTV evolution month over month
--   VW_REENGAGEMENT_OPPORTUNITIES  — patients inactive 90+ days with prior revenue
--   VW_AI_ROI                      — Lina's lead scoring accuracy over time
--   VW_SERVICE_CONVERSION          — conversion rate by dental procedure
--
-- Future views (Sprint 7 — Episodic Memory):
--   VW_AESTHETIC_CONVERSION        — requires service_interest field from Lina
--   VW_FEAR_TREATMENT_CORRELATION  — requires fear_tag field from Lina

CREATE DATABASE IF NOT EXISTS IA_ODONTO_DW;
CREATE SCHEMA IF NOT EXISTS IA_ODONTO_DW.GOLD;
USE DATABASE IA_ODONTO_DW;
USE SCHEMA GOLD;

-- ── Dimension: Anonymized Patients (LGPD compliant) ──────────────────────────
CREATE TABLE IF NOT EXISTS DIM_PATIENTS (
    patient_key     VARCHAR(64)  PRIMARY KEY,  -- SHA-256 hash of original ID
    intent_segment  VARCHAR(50),               -- Inquiry | Scheduling | Complaint | Other
    fear_segment    VARCHAR(100),              -- e.g. "needle_phobia", "none"
    first_contact   DATE,
    last_contact    DATE,
    total_visits    INTEGER      DEFAULT 0
);

-- ── Dimension: Dental Services ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DIM_SERVICES (
    service_key     INTEGER      AUTOINCREMENT PRIMARY KEY,
    service_name    VARCHAR(100) NOT NULL,     -- e.g. "Dental Implant"
    category        VARCHAR(50),               -- e.g. "Implantology", "Aesthetics"
    avg_price_brl   DECIMAL(10,2)
);

-- ── Dimension: Date ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DIM_DATE (
    date_key        DATE         PRIMARY KEY,
    year            INTEGER,
    month           INTEGER,
    quarter         INTEGER,
    day_of_week     INTEGER,
    is_weekend      BOOLEAN
);

-- ── Fact Table: Patient Interactions ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS FACT_INTERACTIONS (
    interaction_id       VARCHAR(36)   DEFAULT UUID_STRING() PRIMARY KEY,
    patient_key          VARCHAR(64)   REFERENCES DIM_PATIENTS(patient_key),
    service_key          INTEGER       REFERENCES DIM_SERVICES(service_key),
    date_key             DATE          REFERENCES DIM_DATE(date_key),
    estimated_potential  DECIMAL(12,2) DEFAULT 0,
    ai_intent            VARCHAR(50),
    visit_count          INTEGER       DEFAULT 0,
    valor_pago           DECIMAL(12,2) DEFAULT 0,
    ltv_acumulado        DECIMAL(12,2) DEFAULT 0,
    created_at           TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ── View: Revenue pipeline by intent ─────────────────────────────────────────
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

-- ── View: Fear segment vs conversion correlation ──────────────────────────────
CREATE OR REPLACE VIEW VW_FEAR_VS_CONVERSION AS
SELECT
    p.fear_segment,
    COUNT(f.interaction_id)             AS total_patients,
    ROUND(AVG(f.estimated_potential),2) AS avg_potential,
    ROUND(AVG(f.valor_pago),2)          AS avg_revenue,
    ROUND(AVG(f.valor_pago) / NULLIF(AVG(f.estimated_potential),0) * 100, 1) AS conversion_pct
FROM FACT_INTERACTIONS f
JOIN DIM_PATIENTS p ON f.patient_key = p.patient_key
GROUP BY p.fear_segment
ORDER BY conversion_pct DESC;

-- ── View: LTV evolution over time ─────────────────────────────────────────────
CREATE OR REPLACE VIEW VW_LTV_BY_PERIOD AS
SELECT
    d.year,
    d.month,
    COUNT(DISTINCT f.patient_key)       AS active_patients,
    ROUND(AVG(f.ltv_acumulado), 2)      AS avg_ltv_brl,
    ROUND(SUM(f.valor_pago), 2)         AS total_revenue_brl,
    ROUND(AVG(f.valor_pago), 2)         AS avg_ticket_brl
FROM FACT_INTERACTIONS f
JOIN DIM_DATE d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;

-- ── View: Re-engagement opportunities ────────────────────────────────────────
CREATE OR REPLACE VIEW VW_REENGAGEMENT_OPPORTUNITIES AS
SELECT
    p.patient_key,
    p.intent_segment,
    p.fear_segment,
    p.last_contact,
    DATEDIFF('day', p.last_contact, CURRENT_DATE())  AS days_since_last_visit,
    ROUND(MAX(f.ltv_acumulado), 2)                   AS ltv_acumulado,
    MAX(f.visit_count)                               AS total_visits
FROM DIM_PATIENTS p
JOIN FACT_INTERACTIONS f ON p.patient_key = f.patient_key
WHERE DATEDIFF('day', p.last_contact, CURRENT_DATE()) > 90
  AND f.ltv_acumulado > 0
GROUP BY p.patient_key, p.intent_segment, p.fear_segment, p.last_contact
ORDER BY days_since_last_visit DESC;

-- ── View: AI ROI — estimated potential vs actual revenue ──────────────────────
CREATE OR REPLACE VIEW VW_AI_ROI AS
SELECT
    d.year,
    d.month,
    ROUND(SUM(f.estimated_potential), 2)             AS total_estimated_brl,
    ROUND(SUM(f.valor_pago), 2)                      AS total_actual_brl,
    ROUND(SUM(f.valor_pago) / NULLIF(SUM(f.estimated_potential), 0) * 100, 1) AS roi_pct,
    COUNT(DISTINCT f.patient_key)                    AS patients_scored
FROM FACT_INTERACTIONS f
JOIN DIM_DATE d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;

-- ── View: Service conversion rate ─────────────────────────────────────────────
CREATE OR REPLACE VIEW VW_SERVICE_CONVERSION AS
SELECT
    s.service_name,
    s.category,
    s.avg_price_brl,
    COUNT(f.interaction_id)                          AS total_interactions,
    ROUND(SUM(f.valor_pago), 2)                      AS total_revenue_brl,
    ROUND(AVG(f.estimated_potential), 2)             AS avg_potential_brl,
    ROUND(SUM(f.valor_pago) / NULLIF(SUM(f.estimated_potential), 0) * 100, 1) AS conversion_pct
FROM FACT_INTERACTIONS f
JOIN DIM_SERVICES s ON f.service_key = s.service_key
GROUP BY s.service_name, s.category, s.avg_price_brl
ORDER BY conversion_pct DESC;