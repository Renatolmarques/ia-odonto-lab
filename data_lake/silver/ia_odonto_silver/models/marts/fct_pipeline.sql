-- models/marts/fct_pipeline.sql
-- Gold Layer: patient care pipeline combining contact profile + billing
-- Joins stg_contacts and stg_recebimentos via contato_hash
-- Key metric for clinic funnel and re-engagement analysis
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
        -- care status
        c.status_atendimento,
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
        case
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
