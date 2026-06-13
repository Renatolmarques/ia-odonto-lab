-- models/staging/stg_opportunities.sql
-- Silver Layer: clean and normalize Opportunity data from Bronze
-- LGPD: opportunity id hashed; contact_id hashed to match stg_contacts.contato_hash
--
-- Schema confirmed via SHOW COLUMNS 2026-05-28:
--   stage varchar(255), custom fields use c_c_ prefix
--
-- All fields are analytical — no PII in opportunity table.
-- contact_id is hashed to enable JOIN with stg_contacts/dim_patients in Gold.
-- name field contains dentist-set titles (e.g. "Implante"), not patient PII.
-- description excluded by policy (free text, not needed for analytics).
--
-- Deduplication: same strategy as stg_contacts — ROW_NUMBER on modified_at desc.
{{ config(materialized='table') }}
with source as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/opportunity/*/data.parquet',
        union_by_name=true
    )
),
renamed as (
    select
        -- Identity (hashed)
        sha256(
            cast(id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                           as opportunity_hash,

        -- contact_id hashed to match stg_contacts.contato_hash for Gold joins
        case
            when contact_id is not null
            then sha256(cast(contact_id as varchar) || '{{ env_var("DBT_SALT") }}')
            else null
        end                                         as contato_hash,

        -- Core fields
        name                                        as nome_oportunidade,
        lower(trim(stage))                          as stage,
        lower(trim(last_stage))                     as last_stage,
        cast(amount as decimal(13,2))               as valor,
        upper(amount_currency)                      as moeda,
        cast(probability as integer)                as probabilidade,
        lower(trim(lead_source))                    as origem_lead,
        cast(close_date as date)                    as data_fechamento_prevista,
        cast(created_at as date)                    as created_at,
        cast(modified_at as date)                   as modified_at,

        -- Sprint 1 custom fields
        lower(trim(c_c_procedimento))               as procedimento,
        cast(c_c_capi_enviado as boolean)           as capi_enviado,
        cast(c_c_capi_enviado_em as timestamp)      as capi_enviado_em,
        cast(c_c_valor_realizado as decimal(13,2))  as valor_realizado,
        upper(c_c_valor_realizado_currency)         as valor_realizado_moeda,
        lower(trim(c_c_capi_evento_tipo))           as capi_evento_tipo,
        c_c_ctwa_clid                               as ctwa_clid,
        lower(trim(c_c_origem_lead))                as origem_lead_custom,

        row_number() over (
            partition by id
            order by cast(modified_at as date) desc
        )                                           as _row_rank
    from source
    where id is not null
),
deduplicated as (
    select * from renamed
    where _row_rank = 1
)
select
    opportunity_hash,
    contato_hash,
    nome_oportunidade,
    stage,
    last_stage,
    valor,
    moeda,
    probabilidade,
    origem_lead,
    data_fechamento_prevista,
    created_at,
    modified_at,
    procedimento,
    capi_enviado,
    capi_enviado_em,
    valor_realizado,
    valor_realizado_moeda,
    capi_evento_tipo,
    ctwa_clid,
    origem_lead_custom
from deduplicated