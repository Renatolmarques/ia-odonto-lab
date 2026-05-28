-- models/staging/stg_contacts.sql
-- Silver Layer: clean and normalize contact data from Bronze
-- LGPD: contact id is irreversibly hashed with SHA-256 + salt
--
-- PII policy:
--   first_name, phone, address_street, address_city, c_delivery_street
--   are intentionally passed through — exposed to BI under LGPD legitimate
--   interest (clinical identification). They are NOT hashed or scrubbed here.
--   c_aisummary was already scrubbed at Bronze (regex + Presidio NER).
--
-- Sprint 1 fields (9 of 10 custom fields added 2026-05-27):
--   c_c_etapa_funil, c_c_status_risco, c_c_ltv_total,
--   c_c_dias_ultima_interacao, c_c_origem_lead, c_c_intencao_principal,
--   c_c_procedimento_interesse, c_c_ctwa_clid, c_c_anuncio_origem.
--   EXCLUDED: c_c_fobias_dentarias — health data, LGPD Art. 11.
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
        -- Identity (hashed) — never expose raw id downstream
        sha256(
            cast(id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                        as contato_hash,

        -- PII allowed fields — passed through intact for BI (LGPD: legítimo interesse clínico)
        coalesce(first_name, '')                 as nome,
        coalesce(phone, '')                      as telefone,
        coalesce(address_street, '')             as bairro,
        coalesce(address_city, '')               as cidade,
        coalesce(c_delivery_street, '')          as endereco_entrega,

        -- Analytical fields (pre-Sprint 1)
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

        -- Sprint 1 analytical fields — funnel and engagement intelligence
        -- All nullable: contacts created before Sprint 1 will have NULLs here.
        lower(trim(c_c_etapa_funil))             as etapa_funil,
        lower(trim(c_c_status_risco))            as status_risco,
        cast(c_c_ltv_total as decimal(13,2))     as ltv_total,
        cast(c_c_dias_ultima_interacao as integer) as dias_ultima_interacao,
        lower(trim(c_c_origem_lead))             as origem_lead,
        lower(trim(c_c_intencao_principal))      as intencao_principal,
        lower(trim(c_c_procedimento_interesse))  as procedimento_interesse,
        c_c_ctwa_clid                            as ctwa_clid,
        lower(trim(c_c_anuncio_origem))          as anuncio_origem,

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
    nome,
    telefone,
    bairro,
    cidade,
    endereco_entrega,
    status_atendimento,
    lifetime_value,
    lifetime_value_moeda,
    potencial_venda,
    potencial_venda_moeda,
    qtd_consultas,
    ultima_visita,
    ai_summary,
    created_at,
    modified_at,
    etapa_funil,
    status_risco,
    ltv_total,
    dias_ultima_interacao,
    origem_lead,
    intencao_principal,
    procedimento_interesse,
    ctwa_clid,
    anuncio_origem
from deduplicated
