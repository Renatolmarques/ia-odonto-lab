-- models/staging/stg_recebimentos.sql
-- Silver Layer: clean and anonymize billing data from Bronze
-- LGPD: contato_id is irreversibly hashed with SHA-256

{{ config(materialized='view') }}

with source as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/c_recebimento/*/data.parquet',
        union_by_name=true
    )
),

renamed as (
    select
        -- LGPD: hash patient identifier
        sha256(cast(contato_id as varchar) || '{{ env_var("DBT_SALT") }}') as contato_hash,

        -- financials
        cast(valor as decimal(10, 2))       as valor,
        lower(valor_currency)               as moeda,

        -- dates
        cast(data_recebimento as date)      as data_recebimento,

        -- status
        lower(status)                       as status,

        -- audit
        cast(created_at as date)       as created_at,
        cast(modified_at as date)      as modified_at

    from source
    where valor > 0
    and contato_id is not null
)

select * from renamed