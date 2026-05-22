# ISLA Chatbot 🤖

Este é um projeto de chatbot com Inteligência Artificial para o ISLA, que utiliza um modelo de linguagem local (Ollama) ou APIs na cloud, bem como um banco de dados de vectores (ChromaDB) para responder a questões com base nos documentos e disciplinas fornecidas.

## 🚀 Como instalar e correr (Windows)

1. **Pré-requisitos:**
   - Instalar o [Python 3.10+](https://www.python.org/downloads/) (marca a opção "Add Python to PATH" durante a instalação).
   - Instalar o [Ollama](https://ollama.com/) (para correr a IA localmente sem custos).

2. **Iniciar o Chatbot:**
   - Clica duas vezes no ficheiro **`start.bat`** (ou corre-o no terminal).
   - Este script fará o download do modelo da IA automaticamente, instalará as dependências, configurará o banco de dados e iniciará o servidor.

3. **Aceder:**
   - Abre o teu browser em: [http://localhost:8080](http://localhost:8080)
   - **Login Admin:** `admin@islasantarem.pt` / `admin1234`

## 🐧 Como instalar e correr (Linux / macOS)

1. **Pré-requisitos:**
   - Python 3.10+
   - Ollama instalado.

2. **Iniciar o Chatbot:**
   - Abre o terminal na pasta do projeto e corre:
     ```bash
     chmod +x start.sh
     ./start.sh
     ```

## 🛠 Configuração (.env)

Se quiseres usar uma API externa (como OpenAI, Groq, OpenRouter) em vez do Ollama local, podes criar um ficheiro `backend/.env` e colocar as tuas chaves de API:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=tua_chave_aqui
```

Para usar o Ollama local (padrão), não precisas de mexer em nada. O modelo padrão é o `qwen2.5:3b`.
