"""
Skill de Busca - Retriever Tool para RAG (Retrieval-Augmented Generation)

Esta ferramenta conecta ao banco vetorial PGVector e recupera trechos da base
de conhecimento clínica que são semanticamente similares à pergunta do usuário.
"""

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector


# ==========================================
# CONFIGURAÇÕES
# ==========================================

# Carrega variáveis de ambiente
load_dotenv()

# Parâmetros de conexão ao Postgres com pgvector
CONNECTION_STRING = "postgresql+psycopg://postgres:postgres@localhost:5433/ia_odonto"
COLLECTION_NAME = "clinica_docs"
EMBEDDING_MODEL = "text-embedding-3-small"


# ==========================================
# INICIALIZAÇÃO
# ==========================================

def _inicializar_pgvector():
    """Inicializa a conexão com o PGVector."""
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
    )

    return vectorstore


# ==========================================
# FUNÇÃO PRINCIPAL DE BUSCA
# ==========================================

def buscar_contexto(pergunta: str, k: int = 3) -> list[dict]:
    """
    Busca no banco vetorial os k trechos mais similares à pergunta.

    Args:
        pergunta (str): A pergunta ou query do usuário
        k (int): Número de resultados a retornar (padrão: 3)

    Returns:
        list[dict]: Lista com os trechos encontrados e suas distâncias.
                   Exemplo:
                   [
                       {
                           "texto": "A clínica funciona de segunda a sexta das 8h às 18h...",
                           "relevancia": 0.95
                       },
                       ...
                   ]
    """
    print(f"\n[BUSCA] Consultando base de conhecimento com pergunta: '{pergunta}'")

    try:
        # Inicializa o PGVector
        vectorstore = _inicializar_pgvector()

        # Busca os k documentos mais similares com similarity score
        resultados = vectorstore.similarity_search_with_score(pergunta, k=k)

        # Formata os resultados
        contexto = []
        for doc, score in resultados:
            contexto.append({
                "texto": doc.page_content,
                "relevancia": round(1 - score, 2)  # Converte distância em relevância (0-1)
            })

        print(f"[BUSCA] {len(contexto)} resultado(s) encontrado(s).\n")

        return contexto

    except Exception as e:
        print(f"[ERRO] Falha ao buscar no banco vetorial: {str(e)}")
        return []


# ==========================================
# TESTE DE VALIDAÇÃO
# ==========================================

def test_retriever():
    """
    Testa a ferramenta de busca com uma pergunta real sobre horários.
    """
    print("\n" + "="*60)
    print("TESTE DO RETRIEVER TOOL")
    print("="*60)

    pergunta_teste = "Quais os horários de atendimento no sábado?"

    resultados = buscar_contexto(pergunta_teste, k=3)

    if resultados:
        print(f"✓ Pergunta: '{pergunta_teste}'\n")
        for i, resultado in enumerate(resultados, 1):
            print(f"Resultado {i} (Relevância: {resultado['relevancia']})")
            print(f"Texto: {resultado['texto'][:150]}...")
            print()
    else:
        print(f"✗ Nenhum resultado encontrado para: '{pergunta_teste}'")
        print("  Verifique se:")
        print("  1. O Postgres está rodando na porta 5433")
        print("  2. Os dados foram ingeridos (rode ingest_knowledge.py)")
        print("  3. A OPENAI_API_KEY está correta no .env")

    print("="*60 + "\n")


if __name__ == "__main__":
    test_retriever()
