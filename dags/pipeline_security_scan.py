"""
DAG: pipeline_security_scan
Schedule: daily at 06:30 UTC (03:30 Recife, UTC-3) — runs after pipeline_backups
Purpose: Daily VPS security scan to detect intrusions, malware, and network risks.

Background:
  The VPS was previously compromised by a cryptominer injected via an exposed
  PostgreSQL port (0.0.0.0:5432). The malware persisted through container restarts
  by injecting scripts into /docker-entrypoint-initdb.d/. This DAG automates the
  manual security checks used during incident response.

What it checks:
  1. Suspicious executables in /tmp, /var/tmp, /dev/shm (host + containers)
  2. PostgreSQL container entrypoint hooks (/docker-entrypoint-initdb.d/)
  3. Known malware process names (xmrig, kdevtmpfsi, kinsing, etc.)
  4. Outbound connections to known mining pool ports (3333, 4444, 5555, 14444)
  5. Docker ports exposed on 0.0.0.0 instead of 127.0.0.1
  6. Crontab integrity — unknown jobs trigger AVISO
  7. SSH logins in the last 24h + Fail2Ban status
  8. CPU / memory / disk anomalies

Alert strategy:
  All tasks run sequentially. The final PythonOperator reads XCom output from
  every task, classifies findings as CRITICO or AVISO, and POSTs to the
  n8n /cpu-alert webhook (Gmail) only if at least one issue is found.
  A clean scan produces no email — zero noise.

Note: This DAG requires the Airflow container to have access to the Docker socket
so BashOperator commands can call `docker exec` on sibling containers.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# ---------------------------------------------------------------------------
# Alert endpoint — same n8n webhook used by CPU alert workflow
# ---------------------------------------------------------------------------
N8N_ALERT_WEBHOOK = "http://ia_n8n:5678/webhook/cpu-alert"

# ---------------------------------------------------------------------------
# Default arguments — matches pipeline_backups.py pattern
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "renato",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}


# ---------------------------------------------------------------------------
# Failure callback — mirrors pipeline_backups.py pattern
# ---------------------------------------------------------------------------
def _on_failure_callback(context):
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = str(context["execution_date"])
    log_url = context["task_instance"].log_url

    payload = json.dumps(
        {
            "dag_id": dag_id,
            "task_id": task_id,
            "execution_date": execution_date,
            "log_url": log_url,
            "cpu_percent": "DAG failure",
            "timestamp": execution_date,
            "top_processes": f"Task {task_id} falhou na DAG {dag_id}. Ver logs: {log_url}",
        }
    ).encode()

    try:
        req = urllib.request.Request(
            N8N_ALERT_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TASK 1 — Suspicious executables: host /tmp + container entrypoint hooks
# The cryptominer previously used /docker-entrypoint-initdb.d/ for persistence.
# ---------------------------------------------------------------------------
CHECK_EXECUTABLES_CMD = """
echo "=== EXECUTÁVEIS SUSPEITOS ==="

HOST_RESULT=$(find /tmp /var/tmp /dev/shm -type f -executable 2>/dev/null || true)
if [ -n "$HOST_RESULT" ]; then
    echo "CRITICO: Executáveis encontrados no host:"
    echo "$HOST_RESULT"
else
    echo "OK: Nenhum executável suspeito no host"
fi

echo "--- Hooks em containers PostgreSQL (vetor original do ataque) ---"
for CONTAINER in ia_postgres ia-odonto-db; do
    RESULT=$(docker exec $CONTAINER find /docker-entrypoint-initdb.d/ -type f 2>/dev/null || true)
    if [ -n "$RESULT" ]; then
        echo "CRITICO: Arquivos em entrypoint-initdb.d do $CONTAINER:"
        echo "$RESULT"
    else
        echo "OK: $CONTAINER entrypoint-initdb.d limpo"
    fi
done

echo "--- Executáveis em /tmp dentro dos containers ativos ---"
for CONTAINER in ia_postgres ia-odonto-db ia-odonto-api ia_n8n ia_mariadb; do
    RESULT=$(docker exec $CONTAINER find /tmp -type f -executable 2>/dev/null || true)
    if [ -n "$RESULT" ]; then
        echo "AVISO: Executáveis em /tmp do container $CONTAINER:"
        echo "$RESULT"
    fi
done

echo "OK: Varredura de executáveis concluída"
"""

# ---------------------------------------------------------------------------
# TASK 2 — Known malware process names
# xmrig = Monero miner used in the original attack
# kdevtmpfsi, kinsing = common Linux malware names designed to look legitimate
# ---------------------------------------------------------------------------
CHECK_PROCESSES_CMD = """
echo "=== PROCESSOS SUSPEITOS ==="

RESULT=$(ps aux | grep -E "xmrig|kdevtmpfsi|kinsing|ld-linux|minerd|cgminer|bfgminer" | grep -v grep || true)
RESULT_TMP=$(ps aux | grep -E "/tmp/[a-zA-Z]|/dev/shm/[a-zA-Z]" | grep -v grep || true)

if [ -n "$RESULT" ]; then
    echo "CRITICO: Processos maliciosos conhecidos detectados:"
    echo "$RESULT"
else
    echo "OK: Nenhum processo malicioso conhecido"
fi

if [ -n "$RESULT_TMP" ]; then
    echo "CRITICO: Processos rodando a partir de /tmp ou /dev/shm:"
    echo "$RESULT_TMP"
fi

echo "--- Top 10 processos por CPU ---"
ps aux --sort=-%cpu | head -11

echo "OK: Varredura de processos concluída"
"""

# ---------------------------------------------------------------------------
# TASK 3 — Outbound connections to mining pool ports
# Mining pools use ports 3333, 4444, 5555, 14444 by convention.
# Any ESTABLISHED connection to these ports is a critical indicator.
# ---------------------------------------------------------------------------
CHECK_CONNECTIONS_CMD = """
echo "=== CONEXÕES DE SAÍDA SUSPEITAS ==="

ALL_CONN=$(ss -tnp 2>/dev/null | grep ESTABLISHED || true)
echo "Todas as conexões ESTABLISHED:"
echo "$ALL_CONN"
echo "---"

MINING_FOUND=""
for PORT in 3333 4444 5555 14444 45560 3256; do
    RESULT=$(echo "$ALL_CONN" | grep ":$PORT " || true)
    if [ -n "$RESULT" ]; then
        echo "CRITICO: Conexão com porta de mineração $PORT detectada:"
        echo "$RESULT"
        MINING_FOUND="sim"
    fi
done

if [ -z "$MINING_FOUND" ]; then
    echo "OK: Nenhuma conexão com portas de mineração conhecidas"
fi

echo "OK: Varredura de conexões concluída"
"""

# ---------------------------------------------------------------------------
# TASK 4 — Docker ports exposed on 0.0.0.0
# After the cryptominer incident all ports were moved to 127.0.0.1.
# This task ensures no container drifted back to 0.0.0.0.
# ---------------------------------------------------------------------------
CHECK_PORTS_CMD = """
echo "=== PORTAS DOCKER EXPOSTAS ==="

EXPOSED=$(docker inspect $(docker ps -q) 2>/dev/null | grep -o '"'"'"HostIp": "0.0.0.0"'"'"' || true)

if [ -n "$EXPOSED" ]; then
    echo "CRITICO: Containers com portas expostas em 0.0.0.0 (vulnerabilidade critica):"
    docker inspect $(docker ps -q) 2>/dev/null | grep -B5 \"HostIp\" | grep -E \"Name|HostIp\" || true
else
    echo "OK: Nenhuma porta exposta em 0.0.0.0"
fi

echo "--- Containers ativos ---"
docker ps --no-trunc 2>/dev/null | head -20

echo "OK: Varredura de portas concluida"
"""

# ---------------------------------------------------------------------------
# TASK 5 — Crontab integrity
# Known jobs are whitelisted. Any unknown line triggers AVISO.
# ---------------------------------------------------------------------------
CHECK_CRONTAB_CMD = """
echo "=== INTEGRIDADE DO CRONTAB ==="

CRONTAB=$(crontab -l 2>/dev/null || echo "")
echo "Crontab atual:"
echo "$CRONTAB"
echo "---"

UNKNOWN=""
while IFS= read -r line; do
    [[ -z "$line" || "$line" == \\#* ]] && continue
    KNOWN=false
    for PATTERN in "export_bronze" "mariadb-dump" "pg_dump" "backup-infra" "find /opt/ia-odonto-lab/backups"; do
        if echo "$line" | grep -q "$PATTERN"; then
            KNOWN=true
            break
        fi
    done
    if [ "$KNOWN" = false ]; then
        echo "AVISO: Job desconhecido no crontab: $line"
        UNKNOWN="sim"
    fi
done <<< "$CRONTAB"

if [ -z "$UNKNOWN" ]; then
    echo "OK: Todos os jobs do crontab são conhecidos"
fi

echo "OK: Varredura de crontab concluída"
"""

# ---------------------------------------------------------------------------
# TASK 6 — SSH logins in the last 24h + Fail2Ban status
# ---------------------------------------------------------------------------
CHECK_SSH_CMD = """
echo "=== LOGINS SSH ÚLTIMAS 24H ==="

AUTH_LOG=""
for LOG_FILE in /var/log/auth.log /var/log/secure; do
    if [ -f "$LOG_FILE" ]; then
        AUTH_LOG="$LOG_FILE"
        break
    fi
done

if [ -z "$AUTH_LOG" ]; then
    echo "AVISO: Arquivo de log SSH não encontrado"
else
    ACCEPTED=$(grep "Accepted" "$AUTH_LOG" | tail -20 || true)
    FAILED_COUNT=$(grep -c "Failed password" "$AUTH_LOG" || echo "0")
    INVALID_COUNT=$(grep -c "Invalid user" "$AUTH_LOG" || echo "0")

    echo "Logins bem-sucedidos (últimos 20):"
    if [ -n "$ACCEPTED" ]; then
        echo "$ACCEPTED"
    else
        echo "Nenhum login bem-sucedido nas últimas entradas"
    fi

    echo "--- Contadores de tentativas ---"
    echo "Senhas erradas: $FAILED_COUNT"
    echo "Usuários inválidos: $INVALID_COUNT"

    echo "--- IPs banidos pelo Fail2Ban ---"
    fail2ban-client status sshd 2>/dev/null | grep -E "Banned|Currently" || echo "Fail2Ban indisponível"
fi

echo "OK: Varredura SSH concluída"
"""

# ---------------------------------------------------------------------------
# TASK 7 — CPU, memory, disk usage anomalies
# ---------------------------------------------------------------------------
CHECK_RESOURCES_CMD = """
echo "=== USO DE RECURSOS ==="

CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | tr -d '%us,' || echo "0")
MEM_PERCENT=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100}')
SWAP_PERCENT=$(free | grep Swap | awk '{if ($2 > 0) printf "%.0f", $3/$2 * 100; else print "0"}')

echo "CPU atual: ${CPU}%"
echo "RAM em uso: ${MEM_PERCENT}%"
echo "Swap em uso: ${SWAP_PERCENT}%"

CPU_INT=$(printf "%.0f" "${CPU:-0}" 2>/dev/null || echo "0")
if [ "$CPU_INT" -gt 80 ] 2>/dev/null; then
    echo "AVISO: CPU acima de 80% no momento da varredura"
    ps aux --sort=-%cpu | head -6
fi

if [ "$SWAP_PERCENT" -gt 80 ] 2>/dev/null; then
    echo "AVISO: Swap acima de 80% — pressão de memória crítica"
fi

echo "--- Uso de disco ---"
df -h / /opt 2>/dev/null || df -h /

DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_PCT" -gt 85 ] 2>/dev/null; then
    echo "AVISO: Disco raiz acima de 85% ($DISK_PCT%)"
fi

echo "OK: Varredura de recursos concluída"
"""


# ---------------------------------------------------------------------------
# TASK 8 — Aggregate results and send alert only if issues found
# ---------------------------------------------------------------------------
def _avaliar_e_notificar(**context):
    """
    Reads XCom output from all scan tasks.
    Classifies lines containing CRITICO or AVISO.
    Sends a single consolidated alert via n8n webhook only if issues exist.
    A clean scan produces no email — zero noise by design.
    """
    ti = context["ti"]

    task_ids = [
        "check_suspicious_executables",
        "check_malware_processes",
        "check_outbound_connections",
        "check_exposed_docker_ports",
        "check_crontab_integrity",
        "check_ssh_logins",
        "check_resource_usage",
    ]

    criticos = []
    avisos = []
    resumo_completo = []

    for task_id in task_ids:
        output = ti.xcom_pull(task_ids=task_id) or ""
        resumo_completo.append(f"\n{'=' * 40}\n{task_id}\n{'=' * 40}\n{output}")
        for line in output.split("\n"):
            if "CRITICO:" in line:
                criticos.append(f"[{task_id}] {line}")
            elif "AVISO:" in line:
                avisos.append(f"[{task_id}] {line}")

    if not criticos and not avisos:
        print("SEGURANÇA OK — Nenhum problema encontrado. Nenhum email enviado.")
        return

    nivel = "CRÍTICO" if criticos else "AVISO"
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    problemas = "\n".join(criticos + avisos)
    relatorio = "".join(resumo_completo)

    payload = json.dumps(
        {
            "cpu_percent": f"{len(criticos)} críticos, {len(avisos)} avisos",
            "timestamp": timestamp,
            "top_processes": (
                f"🔴 SEGURANÇA VPS — {nivel}\n\n"
                f"Problemas encontrados:\n{problemas}\n\n"
                f"--- RELATÓRIO COMPLETO ---\n{relatorio}"
            ),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        N8N_ALERT_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"Alerta enviado para n8n — HTTP {resp.status}")

    print(f"\nResumo: {len(criticos)} críticos, {len(avisos)} avisos encontrados.")


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_security_scan",
    description="Daily VPS security scan — malware, ports, processes, SSH logins",
    schedule_interval="30 6 * * *",  # 06:30 UTC = 03:30 Recife (UTC-3)
    start_date=datetime(2026, 5, 19),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["security", "monitoring"],
) as dag:

    t_executaveis = BashOperator(
        task_id="check_suspicious_executables",
        bash_command=CHECK_EXECUTABLES_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    t_processos = BashOperator(
        task_id="check_malware_processes",
        bash_command=CHECK_PROCESSES_CMD,
        execution_timeout=timedelta(minutes=3),
        on_failure_callback=_on_failure_callback,
    )

    t_conexoes = BashOperator(
        task_id="check_outbound_connections",
        bash_command=CHECK_CONNECTIONS_CMD,
        execution_timeout=timedelta(minutes=3),
        on_failure_callback=_on_failure_callback,
    )

    t_portas = BashOperator(
        task_id="check_exposed_docker_ports",
        bash_command=CHECK_PORTS_CMD,
        execution_timeout=timedelta(minutes=3),
        on_failure_callback=_on_failure_callback,
    )

    t_crontab = BashOperator(
        task_id="check_crontab_integrity",
        bash_command=CHECK_CRONTAB_CMD,
        execution_timeout=timedelta(minutes=2),
        on_failure_callback=_on_failure_callback,
    )

    t_ssh = BashOperator(
        task_id="check_ssh_logins",
        bash_command=CHECK_SSH_CMD,
        execution_timeout=timedelta(minutes=3),
        on_failure_callback=_on_failure_callback,
    )

    t_recursos = BashOperator(
        task_id="check_resource_usage",
        bash_command=CHECK_RESOURCES_CMD,
        execution_timeout=timedelta(minutes=3),
        on_failure_callback=_on_failure_callback,
    )

    t_avaliar = PythonOperator(
        task_id="avaliar_e_notificar",
        python_callable=_avaliar_e_notificar,
        provide_context=True,
        execution_timeout=timedelta(minutes=2),
        on_failure_callback=_on_failure_callback,
    )

    # Sequential pipeline — each scan feeds into the next, then evaluate
    (
        t_executaveis
        >> t_processos
        >> t_conexoes
        >> t_portas
        >> t_crontab
        >> t_ssh
        >> t_recursos
        >> t_avaliar
    )
