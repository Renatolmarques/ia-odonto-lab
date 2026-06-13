"""
DAG: pipeline_weekly_email
Schedule: toda segunda às 8h (0 8 * * 1)
Purpose: Gera e envia e-mail semanal de reativação para a dentista.

Lê dados do Gold (PostgreSQL) e monta e-mail com 5 blocos:
  Bloco 1 — R$ total parado em orcamento_enviado + consulta_agendada
  Bloco 2 — Top 10 pacientes para reativar (at_risk + churned, ordenado por LTV)
  Bloco 3 — Comparativo semana atual vs anterior e mês atual vs anterior
  Bloco 4 — Origem dos leads mês atual vs anterior por canal
  Bloco 5 — Oportunidades sem atualização há 7+ dias com valor potencial

Envio: via n8n webhook → Gmail OAuth2.
Destinatário em teste: renato_marques_17@hotmail.com
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta

import psycopg2
from airflow import DAG
from airflow.operators.python import PythonOperator
from jinja2 import Template

DEFAULT_ARGS = {
    "owner": "renato",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
    "email_on_retry": False,
}

DESTINATARIO = "renato_marques_17@hotmail.com"
N8N_WEBHOOK = "http://ia_n8n:5678/webhook/email-reativacao"


def _on_failure_callback(context):
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    payload = json.dumps(
        {
            "dag_id": dag_id,
            "task_id": task_id,
            "execution_date": str(context["execution_date"]),
            "log_url": context["task_instance"].log_url,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            "http://ia_n8n:5678/webhook/airflow-alert",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "ia-odonto-db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ia_odonto"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
    )


# ---------------------------------------------------------------------------
# Template HTML do e-mail
# ---------------------------------------------------------------------------
EMAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; margin: 0; padding: 20px; color: #1a1a1a; }
  .container { max-width: 640px; margin: 0 auto; }
  .header { background: #1a1a2e; color: white; padding: 28px 32px;
            border-radius: 12px 12px 0 0; }
  .header h1 { margin: 0; font-size: 22px; font-weight: 600; }
  .header p { margin: 6px 0 0; color: #a0a8c0; font-size: 14px; }
  .bloco { background: white; padding: 28px 32px; border-bottom: 1px solid #eee; }
  .bloco:last-child { border-radius: 0 0 12px 12px; border-bottom: none; }
  .bloco-titulo { font-size: 13px; font-weight: 700; text-transform: uppercase;
                  letter-spacing: 0.08em; color: #6b7280; margin: 0 0 16px; }
  .valor-destaque { font-size: 38px; font-weight: 700; color: #1a1a2e;
                    letter-spacing: -0.02em; line-height: 1; }
  .valor-sub { font-size: 14px; color: #6b7280; margin-top: 6px; }
  .valor-linha { display: flex; justify-content: space-between;
                 padding: 10px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  .valor-linha:last-child { border-bottom: none; }
  .tag { display: inline-block; padding: 3px 10px; border-radius: 20px;
         font-size: 12px; font-weight: 600; }
  .tag-risco { background: #fef3c7; color: #92400e; }
  .tag-perdido { background: #fee2e2; color: #991b1b; }
  .paciente-card { padding: 14px 0; border-bottom: 1px solid #f0f0f0; }
  .paciente-card:last-child { border-bottom: none; }
  .paciente-nome { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
  .paciente-info { font-size: 13px; color: #6b7280; margin-bottom: 10px; }
  .btn-wa { display: inline-block; background: #25d366; color: white;
            padding: 8px 18px; border-radius: 8px; text-decoration: none;
            font-size: 13px; font-weight: 600; }
  .btn-crm { display: inline-block; background: #1a1a2e; color: white;
             padding: 8px 18px; border-radius: 8px; text-decoration: none;
             font-size: 13px; font-weight: 600; margin-left: 8px; }
  .comparativo { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .comp-card { background: #f9fafb; border-radius: 8px; padding: 16px; }
  .comp-label { font-size: 12px; color: #6b7280; font-weight: 600;
                text-transform: uppercase; letter-spacing: 0.06em; }
  .comp-valor { font-size: 28px; font-weight: 700; color: #1a1a2e;
                margin: 4px 0; line-height: 1; }
  .comp-delta { font-size: 13px; }
  .delta-up { color: #059669; }
  .delta-down { color: #dc2626; }
  .delta-flat { color: #6b7280; }
  .canal-linha { display: flex; justify-content: space-between; align-items: center;
                 padding: 10px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  .canal-linha:last-child { border-bottom: none; }
  .canal-badge { background: #ede9fe; color: #5b21b6; padding: 3px 10px;
                 border-radius: 20px; font-size: 12px; font-weight: 600; }
  .alerta-box { background: #fef3c7; border-left: 4px solid #f59e0b;
                padding: 14px 18px; border-radius: 0 8px 8px 0; margin-bottom: 16px; }
  .alerta-box p { margin: 0; font-size: 14px; font-weight: 600; color: #92400e; }
  .opp-linha { padding: 12px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  .opp-linha:last-child { border-bottom: none; }
  .footer { text-align: center; padding: 20px; font-size: 12px; color: #9ca3af; }
</style>
</head>
<body>
<div class="container">

  <!-- HEADER -->
  <div class="header">
    <h1>🦷 Relatório Semanal</h1>
    <p>{{ data_hoje }} · IA Odonto Lab</p>
  </div>

  <!-- BLOCO 1: R$ PARADO -->
  <div class="bloco">
    <p class="bloco-titulo">💰 Receita parada no funil</p>
    <div class="valor-destaque">R$ {{ "{:,.0f}".format(bloco1.total_parado).replace(",", ".") }}</div>
    <div class="valor-sub">{{ bloco1.total_oportunidades }} oportunidades aguardando ação</div>
    <div style="margin-top: 20px;">
      <div class="valor-linha">
        <span>Orçamentos enviados</span>
        <strong>R$ {{ "{:,.0f}".format(bloco1.total_orcamentos).replace(",", ".") }}</strong>
      </div>
      <div class="valor-linha">
        <span>Consultas agendadas</span>
        <strong>R$ {{ "{:,.0f}".format(bloco1.total_consultas).replace(",", ".") }}</strong>
      </div>
    </div>
  </div>

  <!-- BLOCO 2: TOP PACIENTES PARA REATIVAR -->
  <div class="bloco">
    <p class="bloco-titulo">🔥 Pacientes para reativar esta semana</p>
    {% if bloco2 %}
      {% for p in bloco2 %}
      <div class="paciente-card">
        <div class="paciente-nome">
          {{ p.nome }}
          {% if p.pipeline_segment == 'churned' %}
            <span class="tag tag-perdido">Perdido</span>
          {% else %}
            <span class="tag tag-risco">Em risco</span>
          {% endif %}
        </div>
        <div class="paciente-info">
          {{ p.dias_ultima_interacao or '?' }} dias sem contato
          {% if p.procedimento_interesse %} · {{ p.procedimento_interesse.replace('_', ' ').title() }}{% endif %}
          {% if p.ltv_crm %} · LTV R$ {{ "{:,.0f}".format(p.ltv_crm).replace(",", ".") }}{% endif %}
        </div>
        {% if p.telefone %}
        <a class="btn-wa"
           href="https://wa.me/55{{ p.telefone | replace(' ','') | replace('(','') | replace(')','') | replace('-','') }}?text=Ol%C3%A1%20{{ p.nome | urlencode }}!%20Faz%20um%20tempo%20que%20n%C3%A3o%20te%20vemos%20por%20aqui.%20Que%20tal%20agendarmos%20sua%20avalia%C3%A7%C3%A3o%3F%20%F0%9F%98%8A">
          💬 WhatsApp
        </a>
        {% else %}
        <span style="font-size:12px;color:#9ca3af;">Telefone não cadastrado</span>
        {% endif %}
      </div>
      {% endfor %}
    {% else %}
      <p style="color:#6b7280;font-size:14px;">Nenhum paciente para reativar esta semana. 🎉</p>
    {% endif %}
  </div>

  <!-- BLOCO 3: COMPARATIVO -->
  <div class="bloco">
    <p class="bloco-titulo">📊 Comparativo de desempenho</p>
    <p style="font-size:12px;color:#9ca3af;margin:0 0 16px;">Semana atual vs anterior</p>
    <div class="comparativo">
      <div class="comp-card">
        <div class="comp-label">Leads semana</div>
        <div class="comp-valor">{{ bloco3.leads_semana_atual }}</div>
        <div class="comp-delta {% if bloco3.delta_leads_semana > 0 %}delta-up{% elif bloco3.delta_leads_semana < 0 %}delta-down{% else %}delta-flat{% endif %}">
          {% if bloco3.delta_leads_semana > 0 %}▲{% elif bloco3.delta_leads_semana < 0 %}▼{% else %}—{% endif %}
          {{ bloco3.delta_leads_semana | abs }} vs semana anterior
        </div>
      </div>
      <div class="comp-card">
        <div class="comp-label">Leads mês</div>
        <div class="comp-valor">{{ bloco3.leads_mes_atual }}</div>
        <div class="comp-delta {% if bloco3.delta_leads_mes > 0 %}delta-up{% elif bloco3.delta_leads_mes < 0 %}delta-down{% else %}delta-flat{% endif %}">
          {% if bloco3.delta_leads_mes > 0 %}▲{% elif bloco3.delta_leads_mes < 0 %}▼{% else %}—{% endif %}
          {{ bloco3.delta_leads_mes | abs }} vs mês anterior
        </div>
      </div>
    </div>
  </div>

  <!-- BLOCO 4: ORIGEM DOS LEADS -->
  <div class="bloco">
    <p class="bloco-titulo">📣 Origem dos leads — mês atual</p>
    {% for canal in bloco4 %}
    <div class="canal-linha">
      <span>
        <span class="canal-badge">{{ canal.canal.replace('_', ' ').title() }}</span>
      </span>
      <span>
        <strong>{{ canal.leads_mes_atual }}</strong>
        <span style="color:#9ca3af;font-size:12px;">
          {% if canal.leads_mes_anterior > 0 %}
            ({{ "%.0f"|format(((canal.leads_mes_atual - canal.leads_mes_anterior) / canal.leads_mes_anterior) * 100) }}% vs mês ant.)
          {% elif canal.leads_mes_atual > 0 %}
            (novo canal)
          {% endif %}
        </span>
      </span>
    </div>
    {% endfor %}
  </div>

  <!-- BLOCO 5: OPORTUNIDADES SEM UPDATE -->
  <div class="bloco">
    <p class="bloco-titulo">⚠️ Oportunidades sem atualização</p>
    {% if bloco5.oportunidades %}
    <div class="alerta-box">
      <p>{{ bloco5.oportunidades | length }} oportunidades paradas · Valor potencial: R$ {{ "{:,.0f}".format(bloco5.valor_total).replace(",", ".") }}</p>
    </div>
    {% for o in bloco5.oportunidades %}
    <div class="opp-linha">
      <strong>{{ o.nome_oportunidade }}</strong>
      {% if o.paciente %} · {{ o.paciente }}{% endif %}
      <br>
      <span style="color:#6b7280;">
        {{ o.stage.replace('_', ' ').title() }} ·
        R$ {{ "{:,.0f}".format(o.valor).replace(",", ".") }} ·
        {{ o.dias_sem_atualizacao }} dias sem update
      </span>
    </div>
    {% endfor %}
    <a class="btn-crm" href="https://crm.odonto-integracao.com" style="margin-top:16px;display:inline-block;">
      Ver no CRM →
    </a>
    {% else %}
    <p style="color:#6b7280;font-size:14px;">Todas as oportunidades estão atualizadas. ✅</p>
    {% endif %}
  </div>

  <div class="footer">
    IA Odonto Lab · Gerado automaticamente em {{ data_hoje }}<br>
    <a href="https://bi.odonto-integracao.com" style="color:#6b7280;">Ver dashboard completo</a>
  </div>

</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Task: gera e envia o e-mail
# ---------------------------------------------------------------------------
def gerar_e_enviar_email(**context):
    import logging
    from datetime import date

    log = logging.getLogger(__name__)
    hoje = date.today().strftime("%d/%m/%Y")

    conn = _get_pg_conn()
    cur = conn.cursor()

    # --- Bloco 1 ---
    cur.execute(
        """
        SELECT
            COUNT(*)                                            AS total_oportunidades,
            COALESCE(SUM(valor), 0)                            AS total_parado,
            COALESCE(SUM(CASE WHEN stage = 'orcamento_enviado'
                THEN valor ELSE 0 END), 0)                     AS total_orcamentos,
            COALESCE(SUM(CASE WHEN stage = 'consulta_agendada'
                THEN valor ELSE 0 END), 0)                     AS total_consultas
        FROM gold.dim_opportunities
        WHERE stage IN ('orcamento_enviado', 'consulta_agendada')
    """
    )
    row = cur.fetchone()

    class Bloco1:
        total_oportunidades = row[0]
        total_parado = float(row[1])
        total_orcamentos = float(row[2])
        total_consultas = float(row[3])

    # --- Bloco 2 ---
    cur.execute(
        """
        SELECT
            p.nome,
            p.telefone,
            p.pipeline_segment,
            p.dias_ultima_interacao,
            COALESCE(p.ltv_crm, 0)          AS ltv_crm,
            p.procedimento_interesse
        FROM gold.dim_patients p
        WHERE p.pipeline_segment IN ('at_risk', 'churned')
          AND p.nome != ''
        ORDER BY p.ltv_crm DESC NULLS LAST
        LIMIT 10
    """
    )
    cols2 = [d[0] for d in cur.description]
    bloco2 = [dict(zip(cols2, r)) for r in cur.fetchall()]

    # --- Bloco 3 ---
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE first_contact >= CURRENT_DATE - INTERVAL '7 days')
                AS leads_semana_atual,
            COUNT(*) FILTER (WHERE first_contact >= CURRENT_DATE - INTERVAL '14 days'
                              AND first_contact <  CURRENT_DATE - INTERVAL '7 days')
                AS leads_semana_anterior,
            COUNT(*) FILTER (WHERE first_contact >= DATE_TRUNC('month', CURRENT_DATE))
                AS leads_mes_atual,
            COUNT(*) FILTER (WHERE first_contact >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
                              AND first_contact <  DATE_TRUNC('month', CURRENT_DATE))
                AS leads_mes_anterior
        FROM gold.dim_patients
        WHERE nome != ''
    """
    )
    r3 = cur.fetchone()

    class Bloco3:
        leads_semana_atual = r3[0]
        leads_semana_anterior = r3[1]
        leads_mes_atual = r3[2]
        leads_mes_anterior = r3[3]
        delta_leads_semana = r3[0] - r3[1]
        delta_leads_mes = r3[2] - r3[3]

    # --- Bloco 4 ---
    cur.execute(
        """
        SELECT
            COALESCE(origem_lead, 'nao_identificada')          AS canal,
            COUNT(*) FILTER (WHERE first_contact >= DATE_TRUNC('month', CURRENT_DATE))
                AS leads_mes_atual,
            COUNT(*) FILTER (WHERE first_contact >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
                              AND first_contact <  DATE_TRUNC('month', CURRENT_DATE))
                AS leads_mes_anterior
        FROM gold.dim_patients
        WHERE nome != ''
        GROUP BY COALESCE(origem_lead, 'nao_identificada')
        ORDER BY leads_mes_atual DESC
    """
    )
    cols4 = [d[0] for d in cur.description]
    bloco4 = [dict(zip(cols4, r)) for r in cur.fetchall()]

    # --- Bloco 5 ---
    cur.execute(
        """
        SELECT
            o.nome_oportunidade,
            p.nome                                              AS paciente,
            o.stage,
            COALESCE(o.valor, 0)                               AS valor,
            CURRENT_DATE - o.modified_at                       AS dias_sem_atualizacao
        FROM gold.dim_opportunities o
        LEFT JOIN gold.dim_patients p ON o.patient_key = p.patient_key
        WHERE o.stage NOT IN ('tratamento_concluido', 'perdido', 'tratamento_andamento')
          AND o.modified_at < CURRENT_DATE - INTERVAL '7 days'
        ORDER BY dias_sem_atualizacao DESC
    """
    )
    cols5 = [d[0] for d in cur.description]
    opps = [dict(zip(cols5, r)) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT COALESCE(SUM(valor), 0)
        FROM gold.dim_opportunities
        WHERE stage NOT IN ('tratamento_concluido', 'perdido', 'tratamento_andamento')
          AND modified_at < CURRENT_DATE - INTERVAL '7 days'
    """
    )
    valor_total_bloco5 = float(cur.fetchone()[0])

    class Bloco5:
        oportunidades = opps
        valor_total = valor_total_bloco5

    conn.close()

    # --- Renderiza HTML ---
    tmpl = Template(EMAIL_TEMPLATE)
    html = tmpl.render(
        data_hoje=hoje,
        bloco1=Bloco1,
        bloco2=bloco2,
        bloco3=Bloco3,
        bloco4=bloco4,
        bloco5=Bloco5,
    )

    log.info("E-mail gerado: %d chars", len(html))

    # --- Envia via n8n webhook ---
    payload = json.dumps(
        {
            "destinatario": DESTINATARIO,
            "assunto": f"🦷 Relatório Semanal — {hoje}",
            "html": html,
        }
    ).encode()

    req = urllib.request.Request(
        N8N_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    log.info("n8n response: %s", resp.status)


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_weekly_email",
    description="E-mail semanal de reativação — toda segunda 8h",
    schedule_interval="0 8 * * 1",
    start_date=datetime(2026, 6, 16),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["email", "reativacao", "production"],
) as dag:

    t_email = PythonOperator(
        task_id="gerar_e_enviar_email",
        python_callable=gerar_e_enviar_email,
        on_failure_callback=_on_failure_callback,
    )
