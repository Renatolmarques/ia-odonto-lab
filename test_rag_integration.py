#!/usr/bin/env python
"""
Script de teste para validar a integração do RAG (Retrieval-Augmented Generation)
com o agente clínico Lina.
"""

import sys
from app.agents.clinical_agent import testar_agente_langchain

def main():
    """Teste do agente com RAG"""
    print("\n" + "="*70)
    print("TESTE: AGENTE CLÍNICO LINA COM RAG (Retrieval-Augmented Generation)")
    print("="*70)

    # Testes com diferentes tipos de perguntas
    testes = [
        "Qual o horário de atendimento?",
        "Quanto custa um implante?",
        "Vocês fazem clareamento?",
    ]

    for i, pergunta in enumerate(testes, 1):
        print(f"\n--- TESTE {i}/{len(testes)} ---")
        try:
            resposta = testar_agente_langchain(pergunta)
            print(f"✓ Teste {i} passou!")
        except Exception as e:
            print(f"✗ Teste {i} FALHOU: {str(e)}")
            return 1

    print("\n" + "="*70)
    print("✓ TODOS OS TESTES PASSARAM!")
    print("="*70 + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
