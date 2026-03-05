import psycopg2
import os
from dotenv import load_dotenv

# Carrega as variáveis de ambiente (como senhas, se houver)
load_dotenv()

def configurar_banco_vetorial():
    """
    Ferramenta de infraestrutura: Conecta no banco PostgreSQL e 
    ativa a extensão pgvector necessária para a Memória da IA.
    """
    print("\n[SISTEMA] Iniciando configuração do banco de dados vetorial...")
    
    # Credenciais padrão que configuramos no docker-compose.yml
    db_config = {
        "dbname": "ia_odonto",
        "user": "postgres",
        "password": "postgres",
        "host": "db", 
        "port": "5432"
    }

    try:
        # 1. Conecta ao banco de dados
        conn = psycopg2.connect(**db_config)
        conn.autocommit = True # Necessário para rodar comandos de criação de extensão
        cursor = conn.cursor()

        # 2. Ativa a extensão matemática da IA (pgvector)
        print("[SISTEMA] Executando ativação do pgvector...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        # 3. Cria a tabela que vai guardar os fragmentos do nosso PDF
        print("[SISTEMA] Criando tabela de documentos e embeddings...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documentos_conhecimento (
                id SERIAL PRIMARY KEY,
                conteudo TEXT NOT NULL,
                embedding vector(1536) -- 1536 é o padrão de tamanho da OpenAI
            );
        """)

        print("✅ SUCESSO! O Postgres agora é um Vector Database e a tabela está pronta.\n")

    except Exception as e:
        print(f"❌ ERRO ao configurar o banco: {e}\n")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    configurar_banco_vetorial()