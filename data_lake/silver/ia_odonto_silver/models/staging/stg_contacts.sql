-- models/staging/stg_contacts.sql
-- Silver Layer: clean and anonymize contact data from Bronze
-- LGPD: contact id is irreversibly hashed with SHA-256 + salt
-- No PII fields were extracted at Bronze — only analytical fields
--
-- Deduplication strategy: Bronze accumulates one partition per day (dt=YYYY-MM-DD).
-- The wildcard read_parquet loads ALL partitions, producing one row per contact per day.
-- ROW_NUMBER() in a subquery keeps only the most recent partition per contact,
-- ensuring Silver has exactly one row per contato_hash.
-- Note: QUALIFY syntax was avoided due to a DuckDB internal error with DECIMAL types.
{{ config(materialized='table') }}
with source as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/contact/*/data.parquet',
        union_by_name=true
    )
),
renamed as (
    select
        sha256(
            cast(id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                        as contato_hash,
        lower(trim(c_status_atendimento))        as status_atendimento,
        cast(c_lifetime_value as decimal(13,2))  as lifetime_value,
        upper(c_lifetime_value_currency)         as lifetime_value_moeda,
        cast(c_potencial_venda as decimal(13,2)) as potencial_venda,
        upper(c_potencial_venda_currency)        as potencial_venda_moeda,
        cast(c_qtd_consultas as integer)         as qtd_consultas,
        cast(c_ultima_visita as date)            as ultima_visita,
        c_aisummary                              as ai_summary,
        cast(created_at as date)                 as created_at,
        cast(modified_at as date)                as modified_at,
        row_number() over (
            partition by id
            order by cast(modified_at as date) desc
        )                                        as _row_rank
    from source
    where id is not null
),
deduplicated as (
    select * from renamed
    where _row_rank = 1
)
select
    contato_hash,
    status_atendimento,
    lifetime_value,
    lifetime_value_moeda,
    potencial_venda,
    potencial_venda_moeda,
    qtd_consultas,
    ultima_visita,
    ai_summary,
    created_at,
    modified_at
from deduplicated
