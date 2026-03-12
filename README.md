# IA Odonto Lab

> An AI-powered CRM intelligence layer for dental clinics, built with production-grade Python engineering and a modern data stack.

[![CI](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![LangChain](https://img.shields.io/badge/LangChain-0.3-1C3C3C)
![pgvector](https://img.shields.io/badge/pgvector-PostgreSQL-336791?logo=postgresql)

---

## The Problem

Dental clinics lose revenue and patient relationships because conversation history lives in WhatsApp and never reaches the CRM in a structured way. Receptionists forget critical details. Leads go cold. High-ticket aesthetic treatments (whitening, veneers, implants) are missed because patients express interest casually in chat and never get followed up. Concerns about price, allergies and fears mentioned months ago remain invisible at the next visit, weakening trust and conversion.

## The Solution

**Lina** — Is a silent AI listener agent built to increase clinic revenue by ensuring no patient opportunity ever goes unnoticed. Running 24/7 on a private cloud server (meaning it operates independently without needing anyone's computer to be on).

Lina listens to every WhatsApp Business conversation and writes structured intelligence directly into the CRM with zero manual input. Every new lead is automatically registered with LTV, interaction history, and conversion potential. A PostgreSQL database stores per-patient profiles with full summaries of each interaction, including flagged mentions of allergies, needle anxiety, and payment concerns. The RAG pipeline gives Lina long-term memory by retrieving past patient context before generating any recommendation in the CRM, making her sharper with every conversation. When a patient signals interest in any procedure, Lina alerts the dentist before the lead goes cold. It also tracks patients who have not returned for routine cleanings in months, flagging them as re-engagement opportunities and reminding the dentist to reconnect — often the ideal moment to revisit a previously mentioned aesthetic treatment they once showed interest in.

---

## Architecture

```
WhatsApp → Evolution API → Chatwoot → n8n
                                        ↓
                           POST /webhook/n8n_handoff
                                        ↓
                              FastAPI (Docker)
                                        ↓
                    ┌───────────────────────────────┐
                    │         Lina Agent            │
                    │   LangChain + GPT-4o-mini     │
                    │   RAG via pgvector            │
                    │   Pydantic structured output  │
                    └───────────────────────────────┘
                                        ↓
                           EspoCRM REST API (Upsert)
                                        ↓
                    ┌───────────────────────────────┐
                    │      Medallion Data Lake      │
                    │  Bronze → Silver → Gold       │
                    │  PySpark + Databricks         │
                    │  Snowflake Star Schema        │
                    └───────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| API | FastAPI + Pydantic v2 | Async, type-safe, auto-documented |
| AI Agent | LangChain 0.3 + GPT-4o-mini | Structured output with `with_structured_output()` |
| Vector DB | pgvector (PostgreSQL 16) | RAG with cosine similarity — LGPD compliant |
| Embeddings | OpenAI text-embedding-3-small | Cost-efficient, high quality |
| CRM | EspoCRM REST API | Upsert contacts with AI-generated clinical summaries |
| Containerization | Docker + Compose | Multi-stage build, non-root user |
| CI/CD | GitHub Actions | Lint → Test → Docker build on every push |
| Big Data | PySpark + Databricks | Medallion Architecture (Bronze/Silver/Gold) |
| Data Warehouse | Snowflake | Star Schema for business analytics |
| Testing | pytest + pytest-asyncio | 18 tests, async coverage |

---

## Key Engineering Decisions

**Structured output over prompt engineering**
Rather than parsing free-text LLM responses, Lina uses `llm.with_structured_output(ResumoClinico)` — a Pydantic model that guarantees the CRM always receives valid, typed data. No regex. No hallucinated fields.

**LGPD-compliant RAG**
The vector database contains only institutional knowledge (clinic FAQs, services, pricing policy). Patient data never enters pgvector. PII masking happens at the Silver layer via PySpark UDFs before any persistence.

**Automatic environment detection**
`retriever_tool.py` detects whether it is running inside Docker (connects to `db:5432`) or locally (connects to `localhost:5433`) without any manual configuration switch.

**CRM responsibility boundaries**
Lina writes: `cAisummary`, `cPotencialVenda`, `cQtdConsultas`.
The existing n8n workflow writes: `cLifetimeValue`, `cCKanbanCard` (calculated from the billing database).
Neither overwrites the other — clean separation of concerns across two independent systems.

---

## Project Structure

```
ia-odonto-lab/
├── app/
│   ├── main.py                    # FastAPI orchestration
│   ├── schemas.py                 # Pydantic models (WebhookPayload, ResumoClinico)
│   ├── agents/clinical_agent.py   # Lina: LangChain + RAG + structured output
│   ├── services/crm_service.py    # EspoCRM REST API integration
│   ├── tools/retriever_tool.py    # pgvector similarity search
│   ├── tools/ingest_knowledge.py  # Knowledge base ingestion pipeline
│   └── context/clinica_knowledge.md
├── data_lake/
│   ├── bronze/export_bronze.py    # Raw data extraction → Parquet
│   ├── silver/silver_transform.py # PySpark + LGPD masking + feature engineering
│   └── gold/gold_schema.sql       # Snowflake Star Schema DDL
├── tests/                         # 18 tests — webhook, RAG, Pydantic rules
├── .github/workflows/ci.yml       # GitHub Actions: lint → test → docker build
├── Dockerfile                     # Multi-stage build (builder + runner)
└── docker-compose.yml             # FastAPI + PostgreSQL 16 + pgvector
```

---

## Data Pipeline — Medallion Architecture

```
MariaDB (billing)     PostgreSQL (interactions)
        ↓                       ↓
    Bronze Layer          Bronze Layer
  (Parquet, raw)        (Parquet, raw)
        ↓                       ↓
        └──────────┬────────────┘
                   ↓
            Silver Layer
        PySpark on Databricks
        LGPD: PII masking (SHA-256)
        Features: LTV, visit frequency,
                  days since last visit
                   ↓
             Gold Layer
          Snowflake Star Schema
          FACT_INTERACTIONS
          DIM_PATIENTS (anonymized)
          DIM_SERVICES

```

---

## Running Locally

**Prerequisites:** Docker, Python 3.12, OpenAI API key

```bash
# Clone and configure
git clone https://github.com/Renatolmarques/ia-odonto-lab.git
cd ia-odonto-lab
cp .env.example .env
# Add your OPENAI_API_KEY to .env

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Start services
docker-compose up -d

# Initialize vector database
python init_db.py
python app/tools/ingest_knowledge.py

# Run tests
pytest tests/ -v

# Test the full pipeline
curl http://localhost:8000/health
curl -X POST http://localhost:8000/webhook/n8n_handoff \
  -H "Content-Type: application/json" \
  -d '{"phone":"+5511999999999","patient_name":"Test Patient","message_text":"I need a dental implant but I am afraid of needles. How much does it cost?"}'
```

---

## What Lina Produces

Every conversation is analyzed and written to the CRM in this format:

```
Clinical AI Summary:
Patient name. Known allergies. Fears/phobias. Chronological history.

Technical Note:
- Client:       [Full name]
- Intent:       [Inquiry | Scheduling | Complaint | Other]
- Request:      [What the patient wants]
- Notes:        [Fears, objections, timeline]
- Potential:    R$ [estimated by Lina based on RAG context]
- Visits:       [identified in conversation]
```

---

## Roadmap

- [x] Sprint 1 — CRM integration + n8n webhook pipeline
- [x] Sprint 2 — FastAPI + Docker + multi-stage build
- [x] Sprint 3 — LangChain agent + RAG with pgvector
- [x] Sprint 4 — Structured output + EspoCRM upsert + CI/CD + 18 tests
- [ ] Sprint 5 — PySpark Bronze/Silver + Databricks Delta Lake
- [ ] Sprint 6 — Snowflake Gold layer

---

## LGPD Compliance

This project is designed with Brazilian data privacy law (LGPD) in mind:

- The vector database contains **zero patient data** — only institutional knowledge
- Patient PII is masked at the Silver layer (SHA-256 hashing, CPF redaction)
- The AI agent operates on conversation text delivered by the orchestration layer — it never queries patient databases directly
- Audit trails are maintained for all CRM write operations

---

*Built as a real-world production system for a dental clinic, then abstracted into a reusable architecture showcase.*
