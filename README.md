# IA Odonto Lab

> An AI-powered CRM intelligence layer for dental clinics, built with modern data engineering stack.

[![CI](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/Renatolmarques/ia-odonto-lab/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![LangChain](https://img.shields.io/badge/LangChain-0.3-orange)
![Docker](https://img.shields.io/badge/Docker-Compose%20v2-blue)
![pgvector](https://img.shields.io/badge/pgvector-PostgreSQL-336791?logo=postgresql)
![Airflow](https://img.shields.io/badge/Apache%20Airflow-Orchestration-017CEE?logo=apacheairflow)
![dbt](https://img.shields.io/badge/dbt-Silver%20Layer-FF694B?logo=dbt)
![Azure Databricks](https://img.shields.io/badge/Azure%20Databricks-Portfolio%20Showcase-FF3621?logo=databricks)
![Snowflake](https://img.shields.io/badge/Snowflake-Portfolio%20Showcase-29B5E8?logo=snowflake)
![Cloudflare](https://img.shields.io/badge/Cloudflare-Tunnel-F38020?logo=cloudflare)

---

## The Problem

Dental clinics lose revenue and patient relationships because conversation history lives in WhatsApp and never reaches the CRM in a structured way. Receptionists forget critical details. Leads go cold. High-ticket aesthetic treatments (whitening, veneers, implants) are missed because patients express interest casually in chat and never get followed up. Concerns about price, allergies and fears mentioned months ago remain invisible at the next visit, weakening trust and conversion.

The bottleneck is not intention. It is memory.

## The Solution

Lina is a silent AI agent that listens to every WhatsApp Business conversation and writes structured patient summaries directly into the CRM — automatically, with no manual input — and learns through each interaction.

What Lina does:

- Registers every new lead with LTV estimate and conversion potential
- Highlights in CRM: client's potential for high-ticket upsell, payment impediments, needle phobia and allergies
- Enriches the CRM with intent classification and visit history
- Remembers every patient across conversations via episodic memory (pgvector)
- Runs 24/7 on a private cloud VPS — no laptop or manual work required

What makes Lina different: she uses RAG (Retrieval-Augmented Generation) to consult the clinic's knowledge base, episodic memory to recall previous patient interactions, and structured Pydantic output to guarantee the CRM always receives valid and typed data — no regex, no hallucinated fields.

---

## Production vs Portfolio

This project runs two parallel tracks deliberately:

**Production (VPS, 24/7)** — a real system serving a real dental clinic. The full pipeline from WhatsApp to CRM to data lake runs continuously on a self-hosted VPS with Airflow orchestration, dbt Silver transformations, and Cloudflare security.

**Portfolio showcase** — the same pipeline demonstrated at enterprise scale. The Azure Databricks notebook (PySpark, Delta Lake, SHA-256 LGPD masking, time travel queries) and the Snowflake Star Schema DDL are committed to this repository as verifiable evidence of the implementation. These were built during trial periods specifically to demonstrate the architecture at scale — the same data engineering patterns used in production, applied to a cloud-native warehouse environment.

This separation is intentional: production uses lightweight, zero-cost tooling (Parquet, DuckDB, dbt) that runs sustainably on a $7/month VPS. The portfolio layer demonstrates the same decisions scaled to Spark and Snowflake — the kind of tooling found in data engineering roles this project is designed to target.

---

## Architecture

```
WhatsApp Message
      ↓
Evolution API (WhatsApp gateway)
      ↓
n8n Workflow Orchestration
  ├── Debounce buffer (PostgreSQL, 2-min wait)
  ├── GPT-4o-mini (conversation summarization → EspoCRM notes)
  ├── POST /conversations/save  (episodic memory → pgvector)
  └── POST /webhook/n8n_handoff (Lina agent → CRM enrichment)
      ↓
FastAPI — ia-odonto-api (Docker, 127.0.0.1:8000)
      ↓
┌──────────────────────────────────────────────┐
│                 Lina Agent                   │
│  LangChain 0.3 + GPT-4o-mini                │
│  RAG: pgvector clinica_docs collection       │
│  Episodic Memory: pgvector patient_history   │
│  Pydantic structured output (no free text)   │
└──────────────────────────────────────────────┘
      ↓
EspoCRM REST API (upsert contact + AI fields)
      ↓
┌──────────────────────────────────────────────┐
│           Medallion Data Lake                │
│                                              │
│  Bronze (Production — VPS)                  │
│  Parquet, partitioned by date               │
│  Airflow DAG every 2h                       │
│         ↓                                   │
│  Silver (Production — VPS)                  │
│  dbt + DuckDB (6 models, 37 tests)          │
│  PySpark + Delta Lake (Databricks showcase) │
│         ↓                                   │
│  Gold (Planned)                             │
│  PostgreSQL Star Schema (VPS)               │
│  Snowflake Star Schema (portfolio showcase) │
│         ↓                                   │
│  Metabase Dashboard (Planned)               │
└──────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Environment |
|-------|-----------|-------------|
| API | FastAPI + Pydantic v2 | Production (VPS) |
| AI Agent | LangChain 0.3 + GPT-4o-mini | Production (VPS) |
| Vector DB + RAG | pgvector (PostgreSQL 16), HNSW index | Production (VPS) |
| Episodic Memory | pgvector `patient_history` collection, SHA-256 keyed | Production (VPS) |
| Embeddings | OpenAI text-embedding-3-small | Production (VPS) |
| CRM | EspoCRM REST API | Production (VPS) |
| WhatsApp Gateway | Evolution API v2 | Production (VPS) |
| Workflow Automation | n8n (self-hosted) | Production (VPS) |
| Pipeline Orchestration | Apache Airflow (self-hosted) | Production (VPS) |
| Bronze Layer | Parquet, partitioned by date | Production (VPS) |
| Silver Layer — SQL | dbt + DuckDB (6 models, 37 tests) | Production (VPS) |
| Silver Layer — Spark | PySpark + Delta Lake (ACID, time travel) | Portfolio showcase (Databricks) |
| Gold Layer — SQL | Star Schema DDL + analytical views | Portfolio showcase (Snowflake) |
| Gold Layer — DB | PostgreSQL Star Schema | Planned (VPS) |
| BI Dashboard | Metabase (self-hosted) | Planned (VPS) |
| Reverse Proxy | Nginx Proxy Manager + Cloudflare Tunnel | Production (VPS) |
| Infrastructure | Docker Compose v2 | Production (VPS) |
| CI/CD | GitHub Actions | Production |
| Testing | pytest + pytest-asyncio, 66 tests | Production |

---

## Key Engineering Decisions

**Structured output over prompt engineering**
Rather than parsing free-text LLM responses, Lina uses `llm.with_structured_output(ResumoClinico)` — a Pydantic model that guarantees the CRM always receives valid, typed data. No regex. No hallucinated fields.

**Episodic memory via pgvector — LGPD-safe patient context**
Each conversation is embedded and stored in a dedicated `patient_history` collection in pgvector, keyed by SHA-256 hash of the patient's phone number. On the next conversation, the retriever searches both `clinica_docs` (institutional knowledge) and `patient_history` (that patient's history) simultaneously. The LLM receives the full context before generating any summary. The raw phone number is never stored — only its hash. This allows Lina to recall that a patient mentioned needle phobia three weeks ago without ever persisting identifying information.

**Debounce pattern for WhatsApp message batching**
Patients rarely send a single message — they send three or four in quick succession. Each incoming message is written to a PostgreSQL buffer. A 2-minute wait node in n8n then checks whether newer messages arrived from the same number. If so, the earlier execution stops — only the latest one continues, reading the full accumulated batch. This prevents the LLM from generating partial summaries on message fragments.

**Apache Airflow over cron — observable, retriable pipelines**
The Bronze → Silver pipeline runs every 2 hours via a DAG with six sequential tasks: Bronze export, Parquet validation, dbt run, dbt test, 90-day Parquet rotation, and LGPD-compliant message buffer cleanup. Each task has independent retry logic, failure email alerts, and a full execution log in the Airflow UI — replacing fragile cron scripts with a production-grade orchestrator.

**n8n webhook routing — one entry point, multiple independent workflows**
Evolution API supports only one webhook URL per instance. A lightweight router workflow receives every WhatsApp event and fans out in parallel, keeping each downstream workflow focused on a single responsibility.

**Two AI layers, complementary not competing**
n8n runs GPT-4o-mini for fast conversation summarization (`nota_timeline`, `cAisummary` text). Lina runs independently for semantic enrichment (`cPotencialVenda`, `cQtdConsultas`, intent classification). Field ownership is enforced: neither system overwrites the other's CRM fields.

**dbt for Silver layer — SQL as code**
Transformations are dbt SQL models versioned in Git with automated tests (`not_null`, `accepted_values`) and auto-generated lineage documentation — the same tooling used across SQL-heavy data engineering roles.

**Production tooling vs portfolio tooling — a deliberate choice**
The VPS runs Parquet + DuckDB + dbt: zero licensing cost, no Spark dependency, sustainable on a $7/month server. The Databricks notebook and Snowflake schema demonstrate the same pipeline decisions applied at enterprise scale — Delta Lake for ACID guarantees and time travel, Snowflake for a columnar warehouse with Star Schema. Both tracks are in this repository.

**Production-grade security**
All services run behind a Cloudflare Tunnel — the VPS origin IP is never exposed. Docker ports are bound to `127.0.0.1` only (Docker bypasses UFW iptables rules; loopback binding is the correct mitigation). Fail2Ban active with escalating bans up to 720h. Cloudflare Access (email OTP) gates the n8n, EspoCRM, and Evolution API panels. All external traffic reaches the stack exclusively through Cloudflare-proxied domains with HTTPS.

---

## Project Structure

```
ia-odonto-lab/
├── app/
│   ├── main.py                      # FastAPI entry point — /webhook/n8n_handoff orchestration
│   ├── schemas.py                   # Pydantic models (WebhookPayload, ResumoClinico)
│   ├── dependencies.py              # X-API-Key authentication guard
│   ├── agents/
│   │   └── clinical_agent.py        # Lina: LangChain + RAG + episodic memory + structured output
│   ├── routers/
│   │   └── conversations.py         # POST /conversations/save — episodic memory persistence
│   ├── services/
│   │   └── crm_service.py           # EspoCRM REST API integration
│   ├── db/
│   │   └── db_client.py             # PostgreSQL connection management
│   └── tools/
│       ├── retriever_tool.py        # pgvector HNSW similarity search (clinica_docs + patient_history)
│       ├── episodic_memory.py       # SHA-256 hashing, PII scrubbing, pgvector write/read
│       └── ingest_knowledge.py      # Knowledge base ingestion pipeline
├── dags/
│   ├── pipeline_bronze_silver.py    # Airflow DAG: Bronze export → validate → dbt → rotate → cleanup
│   └── pipeline_backups.py          # Airflow DAG: n8n workflow + infrastructure backup to GitHub
├── data_lake/
│   ├── bronze/
│   │   └── export_bronze.py         # MariaDB + PostgreSQL → Parquet (partitioned by date, PII scrubbed)
│   ├── silver/
│   │   ├── ia_odonto_silver/        # dbt project (6 models, 37 tests, DuckDB)
│   │   └── databricks_notebook.ipynb  # PySpark: Bronze → Silver Delta Lake, SHA-256, time travel
│   └── gold/
│       └── gold_schema.sql          # Snowflake Star Schema DDL + analytical views
├── scripts/
│   ├── dbt_run.sh                   # dbt runner for local development
│   └── dbt_run_vps.sh               # dbt runner for VPS execution
├── tests/
│   ├── test_clinical_output.py      # Pydantic rules, LGPD field validation (10 tests)
│   ├── test_episodic_memory.py      # SHA-256 hashing, PII scrubbing, pgvector save/search (11 tests)
│   ├── test_retriever.py            # RAG retrieval, empty results, PII guardrails (4 tests)
│   ├── test_silver_models.py        # dbt model logic, scrubber patterns, segmentation (35 tests)
│   └── test_webhook.py              # Endpoint auth, payload validation, async flow (6 tests)
├── docs/
│   └── screenshots/                 # Airflow UI, dbt docs, pipeline evidence
├── Dockerfile                       # Multi-stage build (builder + runner, non-root user)
├── Dockerfile.dbt                   # Isolated dbt runner image
├── docker-compose.yml               # FastAPI + PostgreSQL 16 + pgvector (127.0.0.1 bindings)
├── docker-compose-airflow.yml       # Airflow scheduler + webserver
└── .github/workflows/ci.yml         # GitHub Actions: black → isort → flake8 → pytest → docker build
```

---

## Data Pipeline — Medallion Architecture

```
WhatsApp Conversations        MariaDB (EspoCRM billing)      PostgreSQL (RAG audit)
         ↓                             ↓                              ↓
    Bronze Layer ──────────────── Bronze Layer ──────────────── Bronze Layer
  Parquet, partitioned           Parquet, partitioned           Parquet, partitioned
  dt=YYYY-MM-DD/                 dt=YYYY-MM-DD/                 dt=YYYY-MM-DD/
         ↓                             ↓                              ↓
         └─────────────────────────────┴──────────────────────────────┘
                                       ↓
                              Silver Layer (Production)
                         dbt + DuckDB — SQL models in Git
                         6 models: stg_contacts, stg_recebimentos,
                         stg_ai_summaries, fct_ltv, fct_pipeline,
                         fct_ai_performance
                         37 automated data quality tests
                         LGPD: SHA-256 PII masking
                                       ↓
                         Silver Layer (Portfolio Showcase)
                         PySpark on Azure Databricks
                         Delta Lake: ACID + time travel
                         Same transformations, enterprise scale
                                       ↓
                              Gold Layer (Planned)
                         PostgreSQL Star Schema (VPS production)
                         Snowflake Star Schema (portfolio showcase)
                         FACT_INTERACTIONS, DIM_PATIENTS (anonymized)
                         DIM_SERVICES, DIM_DATE
                                       ↓
                         Metabase Dashboard (Planned)
                    LTV by period · Conversion by intent
                    Re-engagement opportunities · AI ROI
```

**Airflow orchestrates the full pipeline every 2 hours:**
```
export_bronze → validate_parquet → dbt_run → dbt_test → rotate_parquet → cleanup_buffer
```
Each task: independent retry (×2), failure email alert, full execution log in Airflow UI.

---

## Episodic Memory — How It Works

Without episodic memory, Lina starts from zero on every conversation. A patient who mentioned needle phobia three weeks ago is a stranger today.

With episodic memory, every completed conversation is embedded and persisted in pgvector under a SHA-256 hash of the patient's phone number. On the next interaction, the retriever searches two collections simultaneously — `clinica_docs` for institutional knowledge and `patient_history` for that specific patient's history — and injects both into the LLM context before any summary is generated.

```
New conversation arrives
        ↓
hash = SHA256(phone_number)          # phone never stored raw
        ↓
search patient_history WHERE key = hash   # pgvector cosine similarity
        ↓
inject retrieved history into LLM prompt
        ↓
Lina generates summary with full patient context
        ↓
POST /conversations/save → embed + persist to patient_history
```

LGPD compliance: the vector store contains zero identifying information. Only the SHA-256 hash links records to a patient — the original phone number cannot be recovered from it.

---

## Quickstart

**Prerequisites:** Docker, Python 3.12, OpenAI API key

```bash
git clone https://github.com/Renatolmarques/ia-odonto-lab.git
cd ia-odonto-lab && cp .env.example .env
# Add your OPENAI_API_KEY to .env
docker compose up -d
python init_db.py
python app/tools/ingest_knowledge.py
```

**Verify:**
```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.4.0"}

curl -X POST http://localhost:8000/webhook/n8n_handoff \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"phone":"+5511999999999","patient_name":"Test Patient","message_text":"I need an implant but I am afraid of needles. How much does it cost?"}'
```

**Run tests:**
```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
# → 66 passed
```

---

## What Lina Writes to the CRM

Every conversation produces two outputs. A structured clinical profile captures what matters about the patient — known allergies, fears, procedures of interest, budget signals — updated automatically as new information emerges across conversations. A dated note attached to the contact record summarizes what was discussed. Both are written with no manual input.

```
Cumulative AI Profile (cAisummary):
"Renato Marques. Allergic to domperidone. Fear of needles.
Interested in teeth whitening (budget around R$ 1,000).
Most recent contact: asked about cleaning price and clinic address."

Per-conversation note (nota_timeline):
"Client: Renato Marques — Intent: Scheduling
Request: Cleaning price and clinic address
Notes: Known allergy to domperidone, needle anxiety on record."
```

**CRM field ownership — enforced, never overlapping:**
- Lina writes: `cAisummary`, `cPotencialVenda`, `cQtdConsultas`, `cDisplayPotencial`
- n8n writes: `cLifetimeValue`, `cCKanbanCard`, `cCUltimoRecebimento`, `cDisplayLTV`

---

## LGPD Compliance

- Vector database contains **zero patient data** — only institutional knowledge (`clinica_docs`)
- Episodic memory keyed by SHA-256 hash of phone number — original never stored
- Patient PII masked at Silver layer via SHA-256 hashing before any persistence
- 8-pattern regex scrubber (CPF, CNPJ, credit cards, phones, emails, PIX keys, RG, postal codes)
- Message buffer cleaned automatically every 30 days (Airflow `cleanup_buffer` task)
- Bronze Parquet rotated after 90 days (Airflow `rotate_parquet` task)
- Lina operates on conversation text delivered by n8n — never queries patient databases directly

---

## Infrastructure

```
Mac (development)              VPS Contabo (production, 24/7)
─────────────────              ──────────────────────────────
git push → CI/CD               Docker Compose v2:
.venv + Cursor                   ia-odonto-api  (FastAPI + Lina, 127.0.0.1:8000)
pre-commit hooks                 ia-odonto-db   (pgvector, 127.0.0.1:5433)
black, isort, flake8             ia_mariadb     (EspoCRM billing, 127.0.0.1:3306)
                                 ia_espocrm     (CRM, 127.0.0.1:8080)
                                 ia_n8n         (workflow orchestration, 127.0.0.1:5678)
                                 ia_evolution   (WhatsApp gateway, 127.0.0.1:8081)
                                 ia_airflow     (pipeline orchestration, 127.0.0.1:8082)
                                 ia_postgres    (Evolution + buffer DB)
                                 ia_redis       (cache)
                                 nginx-proxy-manager

                               Network security:
                                 Cloudflare Tunnel (VPS IP never exposed)
                                 All Docker ports → 127.0.0.1 only
                                 Docker bypasses UFW — loopback binding is the mitigation
                                 Fail2Ban (4 jails, escalating bans up to 720h)
                                 Cloudflare Access (email OTP) on admin panels
```

---

## Roadmap

| Sprint | Status | Description |
|--------|--------|-------------|
| 1 | ✅ | CRM integration + n8n webhook pipeline |
| 2 | ✅ | FastAPI + Docker + multi-stage build |
| 3 | ✅ | LangChain agent + RAG with pgvector |
| 4 | ✅ | Structured output + CRM upsert + CI/CD + 66 tests |
| 5A | ✅ | VPS deploy + n8n routing + patient_name handoff |
| 5B | ✅ | Bronze Layer — Parquet export + Airflow DAG |
| 5C | ✅ | Silver Layer — dbt (6 models, 37 tests) + Delta Lake on Azure Databricks |
| 5D | ✅ | Security — Cloudflare Tunnel + Fail2Ban + Docker network isolation |
| 6 | ✅ | Airflow orchestration — full pipeline DAG (6 tasks, retry, alerts) |
| 7 | ✅ | Episodic Memory — pgvector patient_history, SHA-256 keyed, validated in production |
| 8 | ⬜ | Gold Layer — PostgreSQL Star Schema (VPS) + Metabase dashboard |
| 9 | ⬜ | NER — Microsoft Presidio for name/address PII detection |
| 10 | ⬜ | Fine-tuning showcase — synthetic JSONL + gpt-4o-mini |

---

*Built as a real production system for a dental clinic, then abstracted into a reusable architecture showcase.*