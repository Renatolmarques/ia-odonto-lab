-- models/staging/stg_contacts.sql
-- Silver Layer: clean and anonymize contact data from Bronze
-- LGPD: contact id is irreversibly hashed with SHA-256 + salt
-- No PII fields were extracted at Bronze — only analytical fields
{{ config(materialized='view') }}

with source as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/contact/*/data.parquet',
        union_by_name=true
    )
),
renamed as (
    select
        -- LGPD: hash contact identifier (same salt as stg_recebimentos
        -- so joins between models work via contato_hash)
        sha256(
            cast(id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                       as contato_hash,
        -- care pipeline
        lower(trim(c_status_atendimento))       as status_atendimento,
        -- financials
        cast(c_lifetime_value as decimal(13,2)) as lifetime_value,
        upper(c_lifetime_value_currency)        as lifetime_value_moeda,
        cast(c_potencial_venda as decimal(13,2)) as potencial_venda,
        upper(c_potencial_venda_currency)       as potencial_venda_moeda,
        -- engagement
        cast(c_qtd_consultas as integer)        as qtd_consultas,
        cast(c_ultima_visita as date)           as ultima_visita,
        -- ai
        c_aisummary                             as ai_summary,
        -- audit
        cast(created_at as date)                as created_at,
        cast(modified_at as date)               as modified_at
    from source
    where id is not null
)
select * from renamed