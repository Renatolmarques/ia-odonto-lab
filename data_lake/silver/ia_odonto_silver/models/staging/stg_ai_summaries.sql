-- models/staging/stg_ai_summaries.sql
-- Silver Layer: extracts and validates AI-generated clinical summaries
-- Source: stg_contacts (never Bronze directly — always via ref())
-- Purpose: isolates AI summary analysis from contact metrics
{{ config(materialized='view') }}

with contacts as (
    select * from {{ ref('stg_contacts') }}
),
summaries as (
    select
        contato_hash,
        ai_summary,
        -- quality signals
        case
            when ai_summary is null
                then 'missing'
            when length(trim(ai_summary)) = 0
                then 'empty'
            when length(ai_summary) < 50
                then 'too_short'
            else 'ok'
        end                                     as summary_quality,
        length(ai_summary)                      as summary_length,
        -- audit
        created_at,
        modified_at
    from contacts
    where ai_summary is not null
      and length(trim(ai_summary)) > 0
)
select * from summaries
