-- models/staging/stg_opportunities.sql
-- Silver Layer: clean and normalize opportunity data from Bronze
-- LGPD: opportunity id and contact_id are hashed with SHA-256 + salt
--
-- Each Opportunity represents one treatment plan in the clinic.
-- Architectural decision (2026-05-28): no Consulta entity in EspoCRM.
-- Each treatment = 1 Opportunity. Individual sessions are Stream notes.
-- This model powers dim_opportunities in the Gold layer.
--
-- contact_id is hashed to contato_hash so it can join with stg_contacts
-- and dim_patients downstream without exposing the raw EspoCRM UUID.
--
-- Deduplication: same ROW_NUMBER() strategy as stg_contacts — Bronze
-- accumulates one partition per day; we keep the most recent per opportunity.
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
        -- Identity (hashed) — never expose raw ids downstream
        sha256(
            cast(id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                           as opportunity_hash,

        -- contact_id hashed to match stg_contacts.contato_hash for joins
        sha256(
            cast(contact_id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                           as contato_hash,

        -- Opportunity fields — analytical, no PII
        coalesce(name, '')                          as nome_oportunidade,
        lower(trim(stage))                          as stage,
        cast(amount as decimal(13,2))               as valor,
        upper(amount_currency)                      as valor_moeda,
        cast(probability as integer)                as probabilidade,
        cast(close_date as date)                    as data_fechamento_prevista,
        cast(created_at as date)                    as created_at,
        cast(modified_at as date)                   as modified_at,

        row_number() over (
            partition by id
            order by cast(modified_at as date) desc
        )                                           as _row_rank
    from source
    where id is not null
      and contact_id is not null
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
    valor,
    valor_moeda,
    probabilidade,
    data_fechamento_prevista,
    created_at,
    modified_at
from deduplicated
