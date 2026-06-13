-- models/marts/fct_pipeline.sql
-- Silver Layer: patient care pipeline combining contact profile + billing
-- Joins stg_contacts, stg_recebimentos, and stg_opportunities via contato_hash
--
-- Sprint 2 (2026-06-13): Opportunity.stage now takes priority over
-- Contact.etapa_funil as source of truth for pipeline_segment.
-- Rationale: dentist manually updates Opportunity.stage — it reflects
-- what actually happened. Lina's etapa_funil only sees WhatsApp and
-- is used as fallback when no Opportunity exists for the contact.
--
-- status_risco is now calculated from last_contact dates (objective)
-- rather than relying on Lina's classification (subjective).
-- Thresholds based on dental industry benchmarks:
--   active:          last contact within 6 months
--   em_risco:        6-12 months without contact
--   perdido:         12-18 months without contact
--   inativo_critico: 18+ months without contact
-- Exception: tratamento_andamento always = ativo regardless of date.
-- Exception: loyal expires after 12 months without return.
--
-- Fix: explicit DECIMAL(13,2) casts on all financial columns to prevent
-- DuckDB type binding errors.
{{ config(materialized='table') }}

with contacts as (
    select * from {{ ref('stg_contacts') }}
),
recebimentos as (
    select * from {{ ref('stg_recebimentos') }}
),
opportunities as (
    -- Most recent opportunity per contact
    select
        contato_hash,
        stage                   as opp_stage,
        valor                   as opp_valor,
        probabilidade           as opp_probabilidade,
        data_fechamento_prevista as opp_data_fechamento,
        modified_at             as opp_modified_at,
        row_number() over (
            partition by contato_hash
            order by modified_at desc
        ) as _opp_rank
    from {{ ref('stg_opportunities') }}
    where contato_hash is not null
),
latest_opportunity as (
    select * from opportunities where _opp_rank = 1
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
        -- care status (legacy, kept for compatibility)
        c.status_atendimento,
        -- Sprint 1 CRM fields from Lina (informational)
        c.etapa_funil           as lina_etapa_funil,
        c.status_risco          as lina_status_risco,
        c.ltv_crm,
        c.dias_ultima_interacao,
        c.origem_lead,
        c.intencao_principal,
        c.procedimento_interesse,
        c.ctwa_clid,
        c.anuncio_origem,
        -- Opportunity data (source of truth when available)
        o.opp_stage,
        o.opp_valor,
        o.opp_probabilidade,
        o.opp_data_fechamento,
        o.opp_modified_at,
        -- financial profile
        c.lifetime_value,
        c.lifetime_value_moeda,
        c.potencial_venda,
        c.potencial_venda_moeda,
        coalesce(b.total_pago, cast(0 as decimal(13,2)))  as total_pago_recebimentos,
        coalesce(b.total_pagamentos, 0)                    as total_pagamentos,
        -- engagement
        c.qtd_consultas,
        c.ultima_visita,
        b.primeiro_pagamento,
        b.ultimo_pagamento,
        -- recency in days
        case
            when c.ultima_visita is not null
                then cast(current_date as date) - c.ultima_visita
            else null
        end                                                as dias_desde_ultima_visita,

        -- ----------------------------------------------------------------
        -- status_risco: calculated from dates (objective)
        -- Priority: tratamento_andamento always active
        -- Then: date-based thresholds (dental industry benchmarks)
        -- ----------------------------------------------------------------
        case
            when o.opp_stage = 'tratamento_andamento'
                then 'ativo'
            -- Use dias_ultima_interacao (from Lina) when available
            when c.dias_ultima_interacao is not null
                and c.dias_ultima_interacao <= 180
                then 'ativo'
            when c.dias_ultima_interacao is not null
                and c.dias_ultima_interacao <= 365
                then 'em_risco'
            when c.dias_ultima_interacao is not null
                and c.dias_ultima_interacao <= 548
                then 'perdido'
            when c.dias_ultima_interacao is not null
                then 'inativo_critico'
            -- Fallback: use ultima_visita or created_at dates
            when c.ultima_visita is null and c.created_at is null
                then 'em_risco'
            when cast(current_date as date) - coalesce(c.ultima_visita, c.created_at)
                 <= 180
                then 'ativo'
            when cast(current_date as date) - coalesce(c.ultima_visita, c.created_at)
                 <= 365
                then 'em_risco'
            when cast(current_date as date) - coalesce(c.ultima_visita, c.created_at)
                 <= 548
                then 'perdido'
            else 'inativo_critico'
        end                                                as status_risco,

        -- ----------------------------------------------------------------
        -- etapa_funil: Opportunity.stage wins when Opportunity exists
        -- Falls back to Lina's Contact.etapa_funil otherwise
        -- ----------------------------------------------------------------
        coalesce(o.opp_stage, c.etapa_funil)              as etapa_funil,

        -- ----------------------------------------------------------------
        -- pipeline_segment: uses etapa_funil (already resolved above)
        -- loyal expires after 12 months without return
        -- ----------------------------------------------------------------
        case
            -- Active treatment: always active
            when o.opp_stage = 'tratamento_andamento'
                then 'active'
            -- Lost / no response
            when coalesce(o.opp_stage, c.etapa_funil) in ('perdido', 'sem_resposta')
                then 'churned'
            -- At risk by date
            when cast(current_date as date) - coalesce(c.ultima_visita, c.created_at)
                 > 365
                then 'at_risk'
            -- Upsell: has open opportunity with value
            when coalesce(o.opp_stage, c.etapa_funil) in ('orcamento_enviado', 'consulta_agendada')
                 and coalesce(o.opp_valor, cast(0 as decimal(13,2)))
                   > cast(0 as decimal(13,2))
                then 'upsell_opportunity'
            -- Loyal: completed treatment AND returned within 12 months
            when coalesce(o.opp_stage, c.etapa_funil) = 'tratamento_concluido'
                 and (
                     c.ultima_visita is null
                     or cast(current_date as date) - c.ultima_visita <= 365
                 )
                then 'loyal'
            -- Loyal expired: completed but not returned in 12 months
            when coalesce(o.opp_stage, c.etapa_funil) = 'tratamento_concluido'
                 and cast(current_date as date) - c.ultima_visita > 365
                then 'at_risk'
            -- New lead
            when coalesce(o.opp_stage, c.etapa_funil) = 'lead_novo'
                then 'active'
            else 'active'
        end                                                as pipeline_segment,

        -- audit
        c.created_at,
        c.modified_at
    from contacts c
    left join latest_opportunity o
        on c.contato_hash = o.contato_hash
    left join billing_agg b
        on c.contato_hash = b.contato_hash
)
select * from pipeline