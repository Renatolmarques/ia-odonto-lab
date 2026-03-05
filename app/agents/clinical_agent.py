# Aqui fica a personalidade do Agent com RAG (Retrieval-Augmented Generation).

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
import os
from app.tools.retriever_tool import buscar_contexto

# Carrega a OPENAI_API_KEY do nosso cofre .env
load_dotenv()

def _construir_prompt_com_rag(contexto_recuperado: list[dict]) -> str:
    """
    Constrói um bloco de contexto a ser inserido no System Prompt
    baseado nos resultados do retriever.
    """
    if not contexto_recuperado:
        return ""

    contexto_texto = "\n".join([
        f"- {doc['texto']}"
        for doc in contexto_recuperado
    ])

    return f"""
    CONTEXTO DA BASE DE CONHECIMENTO (obtido automaticamente):
    {contexto_texto}

    Use este contexto para responder com precisão. Se o contexto for relevante, use-o.
    """

def testar_agente_langchain(mensagem_paciente: str):
    """
    Função para testar a comunicação da LangChain com a OpenAI
    usando a identidade da Lina e o modelo gpt-4o-mini.

    Agora enriquecida com RAG (Retrieval-Augmented Generation).
    """
    print("\n[SISTEMA] Iniciando a personalidade da Lina...")

    # STEP 1: Recupera contexto relevante da base de conhecimento
    print("[SISTEMA] Consultando base de conhecimento...")
    contexto_recuperado = buscar_contexto(mensagem_paciente, k=3)

    # Inicializa a IA
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3 # Um pouco de criatividade para ser empática, mas sem alucinar
    )

    # O System Prompt: A Alma e as Regras de Comportamento
    prompt_sistema = """
    IDENTIDADE E OBJETIVO
    Você é a Lina, a assistente virtual inteligente de uma clínica odontológica de excelência.
    Seu tom de voz é: Acolhedor, Profissional e Empático. Você transmite autoridade técnica, mas com o carinho de quem entende que o paciente pode ter medo de dentista.
    Seu objetivo principal: Tirar dúvidas básicas e CONVERTER a conversa em um AGENDAMENTO DE AVALIAÇÃO.

    REGRAS DE OURO (GUARDRAILS)
    1. NUNCA invente informações. Se não souber, diga: "Por favor verificar este ponto com a Doutora."
    2. NUNCA dê diagnósticos médicos. (Ex: "Isso parece ser uma cárie"). Diga apenas que "A Doutora precisa avaliar clinicamente".
    3. BLOQUEIO DE ASSUNTO: Você é uma especialista em Odontologia. Se perguntarem sobre política, receitas ou fora do contexto, responda: "Desculpe, sou a assistente da clínica e só consigo ajudar com o seu sorriso."
    4. MENSAGENS CURTAS: Não envie textos longos. Use emojis moderadamente (🦷, ✨, 📅). Responda de forma breve, ideal para o WhatsApp.

    SCRIPT DE VENDAS (COMO AGIR)
    Sempre termine suas respostas com uma PERGUNTA para incentivar o agendamento.
    - CASO 1 (Preço): "Entendo que o valor é importante. Como cada caso é único, a Dra. precisa examinar você para passar um valor exato. Vamos agendar uma avaliação para tirar essa dúvida?"
    - CASO 2 (Agendar): Pergunte o Nome Completo. Depois, pergunte o melhor dia/período. Confirme e diga que a secretária humana validará o horário.
    - CASO 3 (Medo/Dúvida): Use empatia. "Fique tranquilo(a). Nossa equipe tem atendimento humanizado e a Dra. tem a mão super leve. Vamos marcar uma avaliação para você conversar com ela?"
    """ + _construir_prompt_com_rag(contexto_recuperado)

    # Define a 'Personalidade' e junta com a mensagem do usuário
    mensagens = [
        SystemMessage(content=prompt_sistema),
        HumanMessage(content=mensagem_paciente)
    ]

    print(f"[PACIENTE]: '{mensagem_paciente}'\n")

    # STEP 2: Bate na porta da OpenAI com contexto enriquecido
    resposta = llm.invoke(mensagens)

    print("=== RESPOSTA DA LINA ===")
    print(resposta.content)
    print("========================\n")

    return resposta.content

if __name__ == "__main__":
    # Teste manual simulando um paciente com medo perguntando de implante
    teste_msg = "Oi, eu morro de medo de dentista, mas acho que preciso colocar um implante. Quanto custa mais ou menos?"
    testar_agente_langchain(teste_msg)