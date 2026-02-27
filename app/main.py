from typing import Any, Dict

from fastapi import FastAPI, Body


app = FastAPI(title="IA Odonto Lab API", version="0.1.0")


@app.post("/webhook/n8n_handoff")
async def n8n_handoff(payload: Dict[str, Any] = Body(...)) -> Dict[str, str]:
    """
    Endpoint para receber handoff do n8n.

    Neste primeiro momento apenas confirma o recebimento
    do JSON no ambiente local.
    """
    # A carga é recebida mas ainda não é processada.
    _ = payload
    return {"status": "recebido no ambiente local"}


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    """Endpoint simples de healthcheck."""
    return {"status": "ok"}

