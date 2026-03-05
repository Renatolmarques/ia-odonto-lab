from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

# Configurações
KNOWLEDGE_FILE = Path(__file__).parent.parent / "context" / "clinica_knowledge.md"
CONNECTION_STRING = "postgresql+psycopg://postgres:postgres@localhost:5433/ia_odonto"
COLLECTION_NAME = "clinica_docs"
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def main():
    load_dotenv()
    print("=== Iniciando ingestão da base de conhecimento ===\n")

    # 1. Carregamento do arquivo
    print(f"[1/4] Carregando arquivo: {KNOWLEDGE_FILE}")
    loader = TextLoader(str(KNOWLEDGE_FILE), encoding="utf-8")
    documents = loader.load()
    print(f"      Arquivo carregado com sucesso. {len(documents)} documento(s) encontrado(s).\n")

    # 2. Divisão em chunks
    print(f"[2/4] Dividindo texto em chunks (tamanho={CHUNK_SIZE}, sobreposição={CHUNK_OVERLAP})...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    print(f"      Texto dividido em {len(chunks)} chunk(s).\n")

    # 3. Inicialização do modelo de embeddings
    print(f"[3/4] Inicializando modelo de embeddings: '{EMBEDDING_MODEL}'...")
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    print("      Modelo de embeddings pronto.\n")

    # 4. Conexão ao banco e persistência dos vetores
    print(f"[4/4] Conectando ao banco Postgres e salvando vetores na collection '{COLLECTION_NAME}'...")
    PGVector.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        pre_delete_collection=True,
    )
    print(f"      {len(chunks)} chunk(s) indexado(s) com sucesso na collection '{COLLECTION_NAME}'.\n")

    print("=== Ingestão concluída com sucesso! ===")


if __name__ == "__main__":
    main()
