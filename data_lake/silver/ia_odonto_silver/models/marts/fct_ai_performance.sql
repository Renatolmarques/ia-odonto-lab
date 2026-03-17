-- models/marts/fct_ai_performance.sql
-- Gold Layer: Lina AI assistant performance metrics
-- Measures summary quality, coverage and potential revenue impact
-- Source: stg_ai_summaries + stg_contacts
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
        count(*)                                as total_contacts,
        round(avg(s.summary_length), 0)         as avg_summary_length,
        -- financial impact of AI-assisted contacts
        round(sum(c.lifetime_value), 2)         as total_ltv,
        round(avg(c.lifetime_value), 2)         as avg_ltv,
        round(sum(c.potencial_venda), 2)        as total_potencial,
        round(avg(c.potencial_venda), 2)        as avg_potencial,
        -- engagement metrics
        round(avg(c.qtd_consultas), 1)          as avg_consultas,
        -- coverage
        round(
            count(*) * 100.0 / nullif(
                (select count(*) from contacts), 0
            ), 2
        )                                       as coverage_pct
    from summaries s
    inner join contacts c
        on s.contato_hash = c.contato_hash
    group by s.summary_quality
)
select * from performance
