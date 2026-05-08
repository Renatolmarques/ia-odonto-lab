-- Silver staging: rag_audit
-- Cleans and standardises the Bronze RAG query log.
-- Source: Bronze Parquet exported daily from rag_audit (PostgreSQL ia_odonto).
--
-- Business context:
--   Every call to the RAG (clinic knowledge base or patient history) produces
--   one row here. This model powers fct_rag_performance for AI observability.

{{ config(materialized='view') }}

with source as (

    select *
    from read_parquet(
        '{{ env_var("BRONZE_PATH") }}/rag_audit/*/data.parquet',
        union_by_name=true
    )

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

        -- Truncate to 200 chars for safe display in BI tools
        left(query_text, 200)                               as query_text,

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

    from source
    where created_at is not null

)

select * from cleaned