-- models/marts/fct_ltv.sql
-- Silver Layer: LTV and visit metrics per anonymized patient
-- Reads from stg_recebimentos via ref() — never from Bronze directly

{{ config(materialized='table') }}

with recebimentos as (
    select * from {{ ref('stg_recebimentos') }}
),

metrics as (
    select
        contato_hash,

        -- financial metrics
        round(sum(valor), 2)                    as ltv_acumulado,
        round(avg(valor), 2)                    as ticket_medio,
        round(max(valor), 2)                    as maior_pagamento,

        -- visit metrics
        count(*)                                as frequencia_visitas,
        min(data_recebimento)                   as primeira_visita,
        max(data_recebimento)                   as ultima_visita,

        -- recency (days since last visit)
        cast(current_date as date)
            - max(data_recebimento)             as dias_desde_ultima_visita

    from recebimentos
    group by contato_hash
)

select * from metrics