-- models/marts/fct_ai_performance.sql
-- Gold Layer: Lina AI assistant performance metrics
-- Measures summary quality, coverage and potential revenue impact
-- Source: stg_ai_summaries + stg_contacts
--
-- Fix: explicit DOUBLE casts on DECIMAL(13,2) financial columns before
-- passing to round(). DuckDB's round() returns DOUBLE, causing type binding
-- errors when summing/averaging DECIMAL values. Casting to DOUBLE first
-- ensures consistent types throughout the aggregation.
{{ config(materialized='table') }}

with summaries as (
    select * from {{ ref('stg_ai_summaries') }}
),
contacts as (
    select * from {{ ref('stg_contacts') }}
),
performance as (
    select
        -- summary quality distribution
        s.summary_quality,
        count(*)                                            as total_contacts,
        round(avg(s.summary_length), 0)                    as avg_summary_length,
        -- financial impact of AI-assisted contacts
        round(sum(cast(c.lifetime_value as double)), 2)    as total_ltv,
        round(avg(cast(c.lifetime_value as double)), 2)    as avg_ltv,
        round(sum(cast(c.potencial_venda as double)), 2)   as total_potencial,
        round(avg(cast(c.potencial_venda as double)), 2)   as avg_potencial,
        -- engagement metrics
        round(avg(c.qtd_consultas), 1)                     as avg_consultas,
        -- coverage
        round(
            count(*) * 100.0 / nullif(
                (select count(*) from contacts), 0
            ), 2
        )                                                  as coverage_pct
    from summaries s
    inner join contacts c
        on s.contato_hash = c.contato_hash
    group by s.summary_quality
)
select * from performance
