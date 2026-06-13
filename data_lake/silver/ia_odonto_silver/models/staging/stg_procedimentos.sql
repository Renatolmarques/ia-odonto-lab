-- models/staging/stg_procedimentos.sql
-- Silver Layer: normalize Procedimento data from Bronze
-- Joins c_procedimento with c_opportunity_procedimento to produce
-- one row per opportunity-procedure pair.
--
-- LGPD: opportunity_id and c_procedimento_id are hashed to match
-- stg_opportunities.opportunity_hash and enable Gold joins.
-- No PII in this entity — all fields are analytical.
--
-- Categoria is mapped from nome when null (fallback mapping).
-- Deduplication: latest modified_at per procedimento id.
{{ config(materialized='table') }}
with procedimentos as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/c_procedimento/*/data.parquet',
        union_by_name=true
    )
    where deleted = 0 or deleted is null
),
relacionamentos as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/c_opportunity_procedimento/*/data.parquet',
        union_by_name=true
    )
    where deleted = 0 or deleted is null
),
joined as (
    select
        -- hashed keys
        sha256(
            cast(r.opportunity_id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                           as opportunity_hash,
        sha256(
            cast(p.id as varchar) || '{{ env_var("DBT_SALT") }}'
        )                                           as procedimento_hash,

        -- procedure fields
        lower(trim(p.nome))                         as procedimento,
        -- categoria: use stored value or derive from nome as fallback
        case
            when p.categoria is not null and trim(p.categoria) != ''
                then lower(trim(p.categoria))
            when p.nome in ('limpeza_profilaxia', 'aplicacao_fluor',
                            'consulta_avaliacao', 'retorno_preventivo')
                then 'preventivo'
            when p.nome in ('clareamento', 'faceta', 'faceta_porcelana', 'faceta_resina')
                then 'estetico'
            when p.nome in ('implante_dentario', 'protese', 'protese_total',
                            'protese_parcial', 'coroa', 'coroa_porcelana',
                            'canal_endodontia')
                then 'reabilitador'
            when p.nome in ('extracao_simples', 'extracao_siso', 'enxerto_osseo')
                then 'cirurgico'
            when p.nome in ('ortodontia_fixa', 'ortodontia_alinhador', 'contencao')
                then 'ortodontico'
            else 'outro'
        end                                         as categoria,

        coalesce(p.observacao, '')                  as observacao,
        cast(p.created_at as date)                  as created_at,
        cast(p.modified_at as date)                 as modified_at,

        row_number() over (
            partition by r.opportunity_id, p.id
            order by cast(p.modified_at as date) desc
        )                                           as _row_rank
    from relacionamentos r
    inner join procedimentos p
        on p.id = r.c_procedimento_id
    where r.opportunity_id is not null
      and p.id is not null
),
deduplicated as (
    select * from joined
    where _row_rank = 1
)
select
    opportunity_hash,
    procedimento_hash,
    procedimento,
    categoria,
    observacao,
    created_at,
    modified_at
from deduplicated