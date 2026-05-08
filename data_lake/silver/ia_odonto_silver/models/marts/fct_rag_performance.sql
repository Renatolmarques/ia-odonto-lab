-- Silver mart: fct_rag_performance
-- Daily aggregated metrics for AI observability.
-- Answers: How often is the RAG used? Is retrieval quality improving?
-- Which collection is hit most? Are there hours with more AI activity?
--
-- Recruiter note: This model demonstrates end-to-end observability of a
-- production RAG system — from raw query logs to actionable daily metrics.

{{ config(materialized='table') }}

with base as (

    select * from {{ ref('stg_rag_audit') }}

),

daily as (

    select
        query_date,
        query_type,
        collection,

        -- Volume
        count(*)                                            as total_queries,
        count(distinct patient_id)                          as unique_patients,

        -- Quality
        round(avg(avg_score)::numeric, 3)                  as mean_relevance_score,
        round(avg(results_returned)::numeric, 2)           as avg_results_returned,
        sum(case when is_low_quality then 1 else 0 end)    as low_quality_queries,
        round(
            100.0 * sum(case when is_low_quality then 1 else 0 end)
            / nullif(count(*), 0), 1
        )                                                   as low_quality_pct,

        -- Peak usage hour (mode)
        mode() within group (order by query_hour)          as peak_hour

    from base
    group by 1, 2, 3

)

select
    query_date,
    query_type,
    collection,
    total_queries,
    unique_patients,
    mean_relevance_score,
    avg_results_returned,
    low_quality_queries,
    low_quality_pct,
    peak_hour,

    -- Running 7-day average for trend analysis
    round(
        avg(mean_relevance_score) over (
            partition by query_type
            order by query_date
            rows between 6 preceding and current row
        )::numeric, 3
    )                                                       as score_7d_avg

from daily
order by query_date desc, query_type