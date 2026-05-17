-- Silver staging: rag_audit
-- Cleans and standardises the Bronze RAG query log.
-- Source: Bronze Parquet exported daily from rag_audit (PostgreSQL ia_odonto).
--
-- Business context:
--   Every call to the RAG (clinic knowledge base or patient history) produces
--   one row here. This model powers fct_rag_performance for AI observability.
--
-- Deduplication: Bronze accumulates Parquet files daily; rows with the same
--   id may appear across multiple partitions. ROW_NUMBER() keeps the most
--   recent version of each record before any downstream aggregation.
--
-- Privacy: query_text is stored as sha256 hash in the source table (LGPD).
--   query_category is inferred at write time by retriever_tool.py.
{{ config(materialized='view') }}
with source as (
    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/rag_audit/*/data.parquet',
        union_by_name=true
    )
),
deduplicated as (
    select *
    from (
        select
            *,
            row_number() over (
                partition by id
                order by created_at desc
            ) as rn
        from source
        where created_at is not null
    )
    where rn = 1
),
cleaned as (
    select
        id,
        created_at                                          as queried_at,
        date_trunc('day', created_at)                       as query_date,
        date_part('hour', created_at)                       as query_hour,
        collection,
        -- Classify the type of RAG call
        case
            when collection = 'clinica_docs'    then 'knowledge_base'
            when collection = 'patient_history' then 'episodic_memory'
            else 'unknown'
        end                                                 as query_type,
        -- SHA-256 hash of original query (LGPD — raw text never stored in Silver)
        query_text                                          as query_hash,
        -- Inferred intent category written by retriever_tool.py at query time
        query_category,
        k,
        results_returned,
        avg_score,
        -- Flag low-quality retrievals (score below 0.5 or no results)
        case
            when results_returned = 0              then true
            when avg_score is not null
             and avg_score < 0.5                   then true
            else false
        end                                                 as is_low_quality,
        -- Patient context (only for episodic memory calls)
        patient_id
    from deduplicated
)
select * from cleaned