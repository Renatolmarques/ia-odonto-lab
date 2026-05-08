-- Migration 001 — Create rag_audit table
-- Tracks every RAG query for observability and analytics.
-- Populated automatically by retriever_tool.py and episodic_memory.py.
-- Exported daily to Bronze layer by export_bronze.py.

CREATE TABLE IF NOT EXISTS rag_audit (
    id               SERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    collection       VARCHAR(50)  NOT NULL,   -- 'clinica_docs' | 'patient_history'
    query_text       TEXT         NOT NULL,   -- scrubbed query (max 500 chars stored)
    k                INTEGER      NOT NULL,   -- results requested
    results_returned INTEGER      NOT NULL,   -- results actually found
    avg_score        FLOAT,                   -- mean relevance score (0.0-1.0)
    patient_id       VARCHAR(64)              -- SHA-256 hash or NULL for clinica_docs
);

CREATE INDEX IF NOT EXISTS idx_rag_audit_created_at   ON rag_audit (created_at);
CREATE INDEX IF NOT EXISTS idx_rag_audit_collection   ON rag_audit (collection);
CREATE INDEX IF NOT EXISTS idx_rag_audit_patient_id   ON rag_audit (patient_id);