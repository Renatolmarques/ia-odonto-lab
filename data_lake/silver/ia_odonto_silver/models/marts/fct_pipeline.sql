-- models/marts/fct_pipeline.sql
-- Gold Layer: patient care pipeline combining contact profile + billing
-- Joins stg_contacts and stg_recebimentos via contato_hash
-- Key metric for clinic funnel and re-engagement analysis
--
-- Sprint 2 enrichment: etapa_funil, status_risco, origem_lead from stg_contacts
-- are now surfaced here to power the weekly email and Gold dim_patients.
--
-- Fix: explicit DECIMAL(13,2) casts on all financial columns to prevent
-- DuckDB type binding errors when mixing DOUBLE (from sum/round) with
-- DECIMAL(13,2) (from stg_contacts). The QUALIFY clause in stg_contacts
-- changed type inference, exposing this latent type mismatch.
{{ config(materialized='table') }}

with contacts as (
    select * from {{ ref('stg_contacts') }}
),
recebimentos as (
    select * from {{ ref('stg_recebimentos') }}
),
billing_agg as (
    select
        contato_hash,
        cast(round(sum(valor), 2) as decimal(13,2))  as total_pago,
        count(*)                                      as total_pagamentos,
        min(data_recebimento)                         as primeiro_pagamento,
        max(data_recebimento)                         as ultimo_pagamento
    from recebimentos
    group by contato_hash
),
pipeline as (
    select
        c.contato_hash,
        -- care status (pre-Sprint 1 field, kept for backwards compatibility)
        c.status_atendimento,
        -- Sprint 1 funnel fields — NULL for contacts created before Sprint 1
        c.etapa_funil,
        c.status_risco,
        c.origem_lead,
        c.intencao_principal,
        c.procedimento_interesse,
        c.ctwa_clid,
        c.anuncio_origem,
        c.dias_ultima_interacao,
        c.ltv_total                                    as ltv_total_lina,
        -- financial profile
        c.lifetime_value,
        c.lifetime_value_moeda,
        c.potencial_venda,
        c.potencial_venda_moeda,
        coalesce(b.total_pago, cast(0 as decimal(13,2)))      as total_pago_recebimentos,
        coalesce(b.total_pagamentos, 0)                        as total_pagamentos,
        -- engagement
        c.qtd_consultas,
        c.ultima_visita,
        b.primeiro_pagamento,
        b.ultimo_pagamento,
        -- recency
        case
            when c.ultima_visita is not null
                then cast(current_date as date) - c.ultima_visita
            else null
        end                                                    as dias_desde_ultima_visita,
        -- pipeline classification
        -- Uses status_risco (Sprint 1) when available; falls back to legacy logic.
        case
            when c.status_risco is not null then c.status_risco
            when c.status_atendimento = 'finalizado'
                and coalesce(b.total_pago, cast(0 as decimal(13,2))) = cast(0 as decimal(13,2))
                then 'churned'
            when coalesce(c.potencial_venda, cast(0 as decimal(13,2)))
               > coalesce(c.lifetime_value, cast(0 as decimal(13,2)))
                then 'upsell_opportunity'
            when c.qtd_consultas >= 3
                then 'loyal'
            else 'active'
        end                                                    as pipeline_segment,
        -- audit
        c.created_at,
        c.modified_at
    from contacts c
    left join billing_agg b
        on c.contato_hash = b.contato_hash
)
select * from pipeline
