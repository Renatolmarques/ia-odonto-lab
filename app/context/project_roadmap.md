# IA Odonto Lab - Project Roadmap & Sprints

Este documento contém a visão arquitetural e o passo a passo de todas as Sprints do projeto. O assistente de IA deve consultar este arquivo para entender o contexto global antes de sugerir ou implementar novos códigos.

INSTRUÇÃO PARA A IA: Você é o Arquiteto e Engenheiro Líder deste projeto. Sua missão é garantir a integridade da visão técnica descrita neste documento.

Antes de qualquer implementação, leia o @project_roadmap.md que contém a visão arquiteturl e o passo a passo de todas as Sprints do projeto. O assistente de IA deve consultar este arquivo para entender o contexto global antes de sugerir ou implementar novos códigos.

Ao finalizar uma tarefa, atualize o status para [CONCLUÍDA] (se tiver permissão) ou informe ao usuário que o passo foi vencido.

Proatividade: Imediatamente após concluir um passo, analise os itens pendentes e sugira ao usuário o próximo passo lógico, explicando brevemente o porquê daquela escolha para manter o momentum do desenvolvimento.

## Sprint 1: O Fechamento do Negócio (CRM + n8n) [CONCLUÍDA]
* **Conceito:** Finalizar a base operacional atual. Integração via API REST.
* **Passos:**
  - [x] Criar o fluxo de "Upsert" (Update/Insert) no n8n para cadastrar pacientes no EspoCRM automaticamente.
  - [x] Fazer o botão "Resolvido" mover o Lead no funil do CRM.
* **Habilidades no CV:** REST APIs, Automação de Processos (BPM), Integração de Sistemas.

## Sprint 2: Migração para Código Puro (FastAPI no Docker) [CONCLUÍDA]
* **Objetivo:** Subir um container Docker com uma API Python (FastAPI) que vai assumir o controle da inteligência (IA e EspoCRM), deixando o n8n apenas como o "carteiro" do WhatsApp.
* **Passos:**
  - [x] Instalar Cursor e a extensão Cline/Cursor.
  - [x] Criar um container Docker com Python e FastAPI.
  - [x] Fazer o Webhook do WhatsApp bater nessa API Python (em vez do n8n), e a API processar a regra.
* **Habilidades no CV:** Python, FastAPI, Docker, Microserviços, Software Architecture (WAT Framework), Clean Code.

## Sprint 3: O Cérebro Agêntico e Memória Técnica (RAG) [EM ANDAMENTO]
* **Conceito:** Transformar a API em um agente especialista utilizando as camadas de Contexto (`/context`) e Ferramentas (`/tools`), conectadas ao seu banco `pgvector`.
* **Passos Práticos:**
  - [x] Configuração Vetorial: Ativar a extensão `pgvector` no Postgres do seu Docker.
  - [x] Script de Ingestão (`app/tools/ingest_knowledge.py`): Criar a ferramenta que lê o `clinica_knowledge.md`, divide em blocos e salva matematicamente no banco.
  - [ ] A Skill de Busca (`app/tools/retriever_tool.py`): Criar a ferramenta que o agente LangChain vai usar para "pesquisar" no banco sempre que não souber a resposta.
  - [ ] Atualização do Agente (`app/agents/clinical_agent.py`): Plugar a Skill de Busca no cérebro do GPT-4o mini.
  - [ ] Protocolo de Erro: Qualquer erro de arquitetura deve ser documentado imediatamente no `.cursorrules`.
* **Habilidades no CV:** LangChain Agents, LLMs (GPT-4), Prompt Engineering, RAG Architectures, Vector Databases.

## Sprint 4: Orquestração Profissional e CI/CD [A FAZER]
* **Conceito:** Trabalhar em equipe e colocar código em produção de forma segura.
* **Passos:**
  - [ ] Usar o GitHub para guardar o código (Controle de Versão).
  - [ ] Criar testes automáticos com a biblioteca Pytest.
  - [ ] Configurar o GitHub Actions para fazer o deploy automático no servidor.
* **Habilidades no CV:** Pytest, Git, GitHub Actions, CI/CD, Teste de Dados.

## Sprint 5: Big Data "O Guindaste" (PySpark + Databricks + Medallion Architecture) [A FAZER]
* **Conceito:** O projeto "Showcase" para o currículo. Processamento em lote de dados e criação de um mini Data Lake da clínica.
* **Passos:**
  - [ ] Camada Bronze (Raw): Script Python para exportar as conversas brutas do Postgres (JSON/Parquet).
  - [ ] Configurar Databricks Community (Gratuito).
  - [ ] Camada Silver (Cleansed): Código em PySpark dentro de um notebook do Databricks para limpar dados (remover emojis, mascarar CPFs/telefones para LGPD, classificar pacientes).
* **Habilidades no CV:** PySpark, Notebook-based development, ETL/ELT, Big Data Processing.

## Sprint 6: Cloud e Armazém de Dados (Snowflake) [A FAZER]
* **Conceito:** Mostrar domínio de nuvem e modelagem analítica.
* **Passos:**
  - [ ] Abrir conta de teste no Snowflake.
  - [ ] Camada Gold (Business): Conectar o resultado do Databricks (Sprint 5) para dentro do Snowflake, criando tabelas de valor (ex: LTV por Paciente, Reclamações Mensais).
  - [ ] Fazer consultas SQL avançadas simulando um Analista de Dados.
* **Habilidades no CV:** Snowflake, Cloud (AWS/Azure basics), Data Warehousing, SQL.