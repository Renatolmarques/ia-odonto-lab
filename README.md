# IA Odonto Lab

> An AI-powered CRM intelligence layer for dental clinics, built with modern data engineering stack.

[![CI](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![LangChain](https://img.shields.io/badge/LangChain-0.3-orange)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![pgvector](https://img.shields.io/badge/pgvector-PostgreSQL-336791?logo=postgresql)

<!-- In progress — Sprint 5B onwards -->
![dbt](https://img.shields.io/badge/dbt-Silver%20Layer-FF694B?logo=dbt)
![Databricks](https://img.shields.io/badge/Databricks-Delta%20Lake-FF3621?logo=databricks)
![Snowflake](https://img.shields.io/badge/Snowflake-Gold%20Layer-29B5E8?logo=snowflake)

---

## The Problem

Dental clinics lose revenue and patient relationships because conversation history lives in WhatsApp and never reaches the CRM in a structured way. Receptionists forget critical details. Leads go cold. High-ticket aesthetic treatments (whitening, veneers, implants) are missed because patients express interest casually in chat and never get followed up. Concerns about price, allergies and fears mentioned months ago remain invisible at the next visit, weakening trust and conversion.

The bottleneck is not intention. It is memory.

## The Solution

Lina is a silent AI agent that listens to every WhatsApp Business conversation and writes valuable structured patient's summary directly into the CRM — automatically, with no manual input.

What Lina does:

 - Registers every new lead with LTV estimate and conversion potential
 - Highlights in CRM: client's potential for high-ticket upsell, payment impediments, and needle phobia/allergies
 - Enriches the CRM with intent classification and visit history
 - Runs 24/7 on a private VPS — no laptop or manual work required

What makes Lina different from a simple webhook:
She uses RAG (Retrieval-Augmented Generation) to consult the clinic's knowledge base before generating any AI summary, and structured Pydantic output to guarantee the CRM always receives valid and typed data, with no regex and hallucinated fields.
A PostgreSQL database stores per-patient profiles with full summaries of each interaction


## Architecture

```
WhatsApp Message
      ↓
Evolution API (WhatsApp gateway)
      ↓
n8n Workflow Orchestration
      ↓
POST /webhook/n8n_handoff
(all services run on a private cloud VPS — no local machine required)
      ↓
FastAPI — ia-odonto-api (Docker, port 8000)
      ↓
┌─────────────────────────────────────────┐
│             Lina Agent                  │
│  LangChain 0.3 + GPT-4o-mini           │
│  RAG via pgvector (clinica_docs)        │
│  Pydantic structured output             │
│  patient_name context injection         │
└─────────────────────────────────────────┘
      ↓
CRM REST API (upsert contact + fields)
      ↓
┌─────────────────────────────────────────┐
│       Medallion Data Lake               │
│  Bronze  →  Silver  →  Gold            │
│  Parquet    dbt + Delta Lake  Snowflake │
│  (VPS)      (Databricks)     Star Schema│
└─────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| API | FastAPI + Pydantic v2 | Async, type-safe, auto-documented |
| AI Agent | LangChain 0.3 + GPT-4o-mini | Structured output with `with_structured_output()` |
| Vector DB | pgvector (PostgreSQL 16) | RAG with cosine similarity, HNSW index |
| Embeddings | OpenAI text-embedding-3-small | Cost-efficient, high quality |
| CRM | EspoCRM REST API | Upsert contacts with AI-generated clinical summaries |
| Orchestration | n8n (self-hosted) | Visual workflow: buffer → GPT → CRM → Lina |
| Reverse Proxy | Nginx Proxy Manager | HTTPS termination, domain routing |
| Infrastructure | VPS + Docker Compose | Cloud-hosted, production-grade, zero downtime |
| CI/CD | GitHub Actions | Lint → Test → Docker build on every push |
| Bronze Layer | Parquet (partitioned by date) | Columnar, compressed, market standard |
| Silver Layer | dbt + Delta Lake (Databricks) | SQL models versioned in Git, ACID, time travel |
| Data Warehouse | Snowflake | Star Schema for business analytics |
| BI Dashboard | Metabase (self-hosted) | Free, simple for non-technical users |
| Testing | pytest + pytest-asyncio | 18 tests, async coverage |

---

## Key Engineering Decisions

**Structured output over prompt engineering**
Rather than parsing free-text LLM responses, Lina uses `llm.with_structured_output(ResumoClinico)` — a Pydantic model that guarantees the CRM always receives valid, typed data. No regex. No hallucinated fields.

**n8n webhook routing — one entry point, two independent workflows**
Evolution API supports only one webhook URL per instance. Rather than merging two unrelated workflows into one, we built a lightweight router workflow that receives every WhatsApp event and forwards it in parallel, keeping each workflow focused on a single responsibility.

**Two AI layers, complementary not competing**
n8n runs GPT-4o-mini for fast conversation summarization (nota_timeline, cAisummary text). Lina runs in parallel for semantic enrichment (cPotencialVenda, cQtdConsultas, intent classification). Neither overwrites the other.

**dbt for Silver layer — SQL as code**
Instead of a raw `silver_transform.py`, transformations are dbt SQL models versioned in Git. Each model has automated tests (`not_null`, `accepted_values`) and auto-generated documentation. This is the same tooling applicable to any SQL-heavy data project — a deliberate, transferable choice.

**Delta Lake for portfolio, Parquet for production (small Clinic)**
The VPS runs Parquet (lightweight, no Spark dependency). Databricks runs Delta Lake (ACID, time travel, schema enforcement) — demonstrating the same pipeline at both scales.

**LGPD-compliant RAG**
The vector database contains only institutional knowledge (clinic FAQs, services, pricing). Patient's confidencial never enters pgvector. PII masking (SHA-256) happens at the Silver layer before any persistence.

**CRM responsibility boundaries**
Lina writes: `cAisummary`, `cPotencialVenda`, `cQtdConsultas`, `cDisplayPotencial`.
n8n writes: `cLifetimeValue`, `cCKanbanCard`, `cCUltimoRecebimento`, `cDisplayLTV`.
Clean separation — neither system overwrites the other.

**Production-grade webhook routing**
All external webhooks are served over HTTPS via a dedicated domain with DNS and reverse proxy configuration. The domain has no public-facing pages; it exists solely to give the internal services a stable, secure entry point accessible from the internet.

---

## Project Structure

```
ia-odonto-lab/
├── app/
│   ├── main.py                      # FastAPI orchestration + /webhook/n8n_handoff
│   ├── schemas.py                   # Pydantic models (WebhookPayload, ResumoClinico)
│   ├── agents/
│   │   └── clinical_agent.py        # Lina: LangChain + RAG + structured output
│   ├── services/
│   │   └── crm_service.py           # CRM REST API integration
│   ├── tools/
│   │   ├── retriever_tool.py        # pgvector HNSW similarity search
│   │   ├── ingest_knowledge.py      # Knowledge base ingestion pipeline
│   │   └── db_client.py             # PostgreSQL connection management
│   └── context/
│       └── clinica_knowledge.md     # Clinic knowledge base (RAG source)
├── data_lake/
│   ├── bronze/
│   │   └── export_bronze.py         # MariaDB + PostgreSQL → Parquet (partitioned by date)
│   ├── silver/
│   │   ├── models/                  # dbt SQL models (stg_contacts, recebimentos_limpos)
│   │   ├── silver_transform.py      # PySpark + LGPD masking + feature engineering
│   │   └── databricks_notebook.ipynb
│   └── gold/
│       └── gold_schema.sql          # Snowflake Star Schema DDL
├── tests/                           # 18 tests — webhook, RAG, Pydantic rules
├── docs/
│   └── sprint_notes.md              # Engineering decisions log per sprint
├── .github/workflows/ci.yml         # GitHub Actions: lint → test → docker build
├── Dockerfile                       # Multi-stage build (builder + runner)
└── docker-compose.yml               # FastAPI + PostgreSQL 16 + pgvector
```

---

## Data Pipeline — Medallion Architecture

```
WhatsApp Conversations        MariaDB (CRM billing)
         ↓                             ↓
    Bronze Layer                 Bronze Layer
  Parquet, partitioned         Parquet, partitioned
  /bronze/interactions/        /bronze/c_recebimento/
  dt=YYYY-MM-DD/               dt=YYYY-MM-DD/
         ↓                             ↓
         └──────────┬──────────────────┘
                    ↓
             Silver Layer
         dbt models (SQL versioned in Git)
         PySpark on Databricks Community
         LGPD: SHA-256 PII masking
         Features:
           ltv_acumulado
           frequencia_visitas
           dias_desde_ultima_visita
         Delta Lake: ACID + time travel
                    ↓
              Gold Layer
           Snowflake Star Schema
           FACT_INTERACTIONS
           DIM_PATIENTS (anonymized)
           DIM_SERVICES / DIM_DATE
                    ↓
             Metabase Dashboard
           LTV by period · Conversion by intent
           Re-engagement opportunities
```

---

## Quickstart

**Prerequisites:** Docker, Python 3.12, OpenAI API key

```bash
git clone https://github.com/Renatolmarques/ia-odonto-lab.git
cd ia-odonto-lab && cp .env.example .env
# Add your OPENAI_API_KEY to .env
docker-compose up -d
python init_db.py
python app/tools/ingest_knowledge.py
```

**Verify:**
```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.4.0"}

curl -X POST http://localhost:8000/webhook/n8n_handoff \
  -H "Content-Type: application/json" \
  -d '{"phone":"+5511999999999","patient_name":"Test Patient","message_text":"I need an implant but I am afraid of needles. How much does it cost?"}'
```

**Run tests:**
```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

---

## What Lina Writes to the CRM

```
Every conversation produces two outputs: a structured clinical profile that captures what matters about the patient — known allergies, fears, procedures of interest, budget signals — updated automatically as new information emerges across conversations. The second output is a dated note attached to the contact record summarizing what was discussed. 
Both are written with no manual input.

**Cumulative AI Profile (example):**
"Renato Marques. Allergic to domperidone. Fear of needles.
Interested in teeth whitening (budget around R$ 1,000).
Most recent contact: asked about cleaning price and clinic address."

**Per-conversation note (example):**
"Client: Renato Marques — Intent: Scheduling
Request: Cleaning price and clinic address
Notes: Known allergy to domperidone, needle anxiety on record."
```

---

## Roadmap

| Sprint | Status | Description |
|--------|--------|-------------|
| 1 | ✅ | CRM integration + n8n webhook pipeline |
| 2 | ✅ | FastAPI + Docker + multi-stage build |
| 3 | ✅ | LangChain agent + RAG with pgvector |
| 4 | ✅ | Structured output + CRM upsert + CI/CD + 18 tests |
| 5A | ✅ | VPS deploy + n8n routing fix + patient_name handoff |
| 5B | 🔄 | Bronze Layer — Parquet export + daily cron job |
| 5C | ⬜ | Silver Layer — dbt models + Delta Lake on Databricks |
| 6 | ⬜ | Gold Layer — Snowflake Star Schema + Metabase dashboard |
| 7 | ⬜ | Episodic Memory — pgvector patient_history collection |
| 8 | ⬜ | Security — UFW, Fail2ban, SSH keys, API auth |
| 9 | ⬜ | Fine-tuning showcase — synthetic JSONL + gpt-4o-mini |

---

## LGPD Compliance

- Vector database contains **zero patient data** — only institutional knowledge
- Patient PII masked at Silver layer via SHA-256 hashing before any persistence
- Lina operates on conversation text delivered by n8n — never queries patient databases directly
- Audit trails maintained for all CRM write operations
- Episodic memory (Sprint 7) stores only hashed `contato_id` — never name, CPF, or health data

---

## Infrastructure

```
Mac (development)              VPS Contabo (production, 24/7)
─────────────────              ──────────────────────────────
git push → CI/CD               Docker Compose (stack-ia):
.venv + Cursor                   ia-odonto-api  (FastAPI + Lina)
                                 ia-odonto-db   (pgvector)
                                 ia_mariadb     (CRM billing)
                                 ia_CRM
                                 ia_n8n
                                 ia_evolution   (WhatsApp gateway)
                                 nginx-proxy-manager
```

---

*Built as a real production system for a dental clinic, then abstracted into a reusable architecture showcase.*
