# Assistente Académico com Inteligência Artificial para o ISLA Santarém
## Relatório Técnico — Sistema de Chatbot Académico com Base de Conhecimento Local

---

**Instituição:** ISLA Santarém — Instituto Superior de Gestão e Administração  
**Curso:** CTeSP em Inteligência Artificial  
**Versão:** 2.3  
**Data:** Abril de 2026  

---

## Resumo

O presente relatório descreve o desenvolvimento do **ISLA Chatbot**, um assistente académico inteligente para o ISLA Santarém. O sistema combina modelos de linguagem de grande escala (LLM) com uma base de conhecimento local estruturada por unidade curricular, gerida através de painéis web específicos por papel de utilizador.

A versão 2.0 representou uma revisão arquitetural significativa: substituição da base de dados vetorial ChromaDB por ficheiros de texto plano com recuperação por palavras-chave, e criação de um painel de administração web com upload drag-and-drop e organização LLM. A versão 2.1 reorganizou o painel administrativo em secções funcionais com grupos de navegação, grelha de cursos e pesquisa de UCs. A versão 2.2 introduziu o Portal do Docente, organização por tópicos, web search e animações de UI. A versão 2.3 — descrita neste relatório — acrescenta:

- **Portal do Docente**: interface dedicada onde docentes gerem os conteúdos das suas UCs de forma autónoma, sem acesso às ferramentas de administração global;
- **Organização hierárquica por tópicos**: a IA divide automaticamente cada documento em tópicos (Avaliação, Conteúdo Programático, Objectivos, etc.), cada um guardado como bloco independente para melhor recuperação;
- **Atribuição ao docente**: cada bloco identifica o docente que o carregou; o chatbot cita-o na resposta ("De acordo com o/a Prof. X...");
- **Web search contextual**: se os documentos não contêm a resposta, o sistema pesquisa automaticamente o site da ISLA Santarém;
- **Entrada por voz**: o estudante pode ditar a sua pergunta usando o microfone do dispositivo (Web Speech API, sem servidor);
- **Anexos PDF e imagem no chat**: o estudante pode carregar um PDF ou uma fotografia directamente no input — o sistema extrai o texto (PDFs via pdfplumber; imagens via LLM multimodal: Gemini, OpenRouter ou Ollama com modelo vision) e pré-preenche o campo de texto para revisão;
- **Input redesenhado**: área de texto com botões de microfone e anexo integrados, barra de preview de anexos animada com nome, estado e remoção;
- **Interface renovada**: animações de entrada de mensagens, navbar reestruturada em grupos funcionais com menu overflow, e suporte a múltiplos fornecedores LLM (Ollama, Groq, OpenRouter, Gemini).

**Palavras-chave:** Inteligência Artificial, LLM, Chatbot Académico, RAG, Base de Conhecimento, Ollama, Gemma3, FastAPI, pdfplumber, Portal do Docente, Web Speech API, OCR multimodal.

---

## Índice

1. [Introdução](#1-introdução)
2. [Contexto e Motivação](#2-contexto-e-motivação)
3. [Arquitetura do Sistema](#3-arquitetura-do-sistema)
4. [Sistema de Gestão de Conhecimento](#4-sistema-de-gestão-de-conhecimento)
5. [Organização Hierárquica por Tópicos](#5-organização-hierárquica-por-tópicos)
6. [Pipeline de Resposta e Web Search](#6-pipeline-de-resposta-e-web-search)
7. [Painel de Administração](#7-painel-de-administração)
8. [Portal do Docente](#8-portal-do-docente)
9. [Atribuição de Docente e Citação](#9-atribuição-de-docente-e-citação)
10. [Modelo de Dados](#10-modelo-de-dados)
11. [Autenticação e Controlo de Acesso](#11-autenticação-e-controlo-de-acesso)
12. [Stack Tecnológico](#12-stack-tecnológico)
13. [Interface de Utilizador](#13-interface-de-utilizador)
14. [Entrada por Voz e Anexos com OCR](#14-entrada-por-voz-e-anexos-com-ocr)
15. [Fluxos de Interação](#15-fluxos-de-interação)
16. [Evolução Arquitetural — v1 para v2.3](#16-evolução-arquitetural--v1-para-v23)
17. [Segurança e Privacidade](#17-segurança-e-privacidade)
18. [Infraestrutura LLM Local — Simulação e Planeamento](#18-infraestrutura-llm-local--simulação-e-planeamento)
19. [Limitações e Trabalho Futuro](#19-limitações-e-trabalho-futuro)
20. [Conclusão](#20-conclusão)
21. [Referências](#21-referências)

---

## 1. Introdução

A proliferação de modelos de linguagem de grande escala (LLMs) abriu novas possibilidades no domínio da educação assistida por IA. Sistemas como o ChatGPT demonstraram que a interação em linguagem natural com sistemas de informação é não só viável como altamente eficaz para a recuperação e síntese de conhecimento [1]. Contudo, os LLMs genéricos apresentam uma limitação estrutural crítica: o seu conhecimento está confinado aos dados de treino, não tendo acesso a informação institucional específica como fichas de unidades curriculares, datas de avaliação ou materiais pedagógicos internos.

O **ISLA Chatbot** resolve este problema através de uma base de conhecimento local estruturada por unidade curricular. Documentos académicos são extraídos de PDFs, organizados automaticamente pela IA em tópicos hierárquicos, e armazenados em ficheiros de texto estruturado. Quando um estudante faz uma pergunta, o sistema recupera os blocos mais relevantes e fornece-os como contexto ao LLM antes de gerar a resposta — garantindo que as respostas são fundamentadas nos documentos oficiais da instituição. Quando os documentos locais não têm a informação, o sistema recorre automaticamente ao site da ISLA Santarém.

A versão 2.2 introduz ainda um **Portal do Docente** — uma interface separada do painel de administração onde cada docente gere autonomamente os conteúdos das suas UCs, com o seu nome identificado como fonte em cada bloco de conhecimento.

---

## 2. Contexto e Motivação

### 2.1 Problema

Os estudantes do ensino superior necessitam frequentemente de informação dispersa por múltiplos documentos: fichas de unidades curriculares, cronogramas de avaliação, materiais de aula e regulamentos académicos. Consultar estes documentos manualmente é moroso e, frequentemente, os estudantes não sabem onde procurar.

Os docentes, por sua vez, enfrentam dificuldades em manter os materiais actualizados num sistema centralizado sem depender de intervenção técnica. O sistema de administração genérico, embora funcional, não é adequado para uso diário por docentes sem perfil técnico.

### 2.2 Solução Proposta

Um assistente conversacional que:

- Permite aos **administradores** gerir toda a operação do sistema via painel web;
- Permite aos **docentes** carregar e organizar os materiais das suas UCs via portal dedicado, sem acesso a configurações globais;
- Extrai e organiza automaticamente o conteúdo com IA, dividido em tópicos hierárquicos;
- Responde a questões em linguagem natural, com base nos documentos carregados e, em fallback, no site institucional;
- Garante que cada estudante acede apenas à informação das suas UCs;
- Opera inteiramente em infraestrutura local, sem envio de dados para serviços externos (modo Ollama).

### 2.3 Casos de Uso Principais

| Utilizador | Caso de Uso | Exemplo |
|---|---|---|
| Estudante | Consulta de avaliação | *"Quando é o exame de IIA?"* |
| Estudante | Conteúdo programático | *"Quais os temas de FCSI?"* |
| Estudante | Regulamentos institucionais | *"Como me candidato a uma bolsa?"* |
| Estudante | Notícias ISLA | *"Há algum evento próximo na ISLA?"* |
| Docente | Carregar material por tópico | Upload de FUC → IA divide em Avaliação, Conteúdo, etc. |
| Docente | Rever e editar tópicos | Ajustar nomes e conteúdo antes de guardar |
| Administrador | Gestão global | Utilizadores, cursos, semestres, configurações IA |

---

## 3. Arquitetura do Sistema

O sistema é composto por quatro camadas funcionais: **apresentação**, **API**, **conhecimento** e **persistência**.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          CAMADA DE APRESENTAÇÃO                              │
│                                                                              │
│  ┌───────────────┐  ┌──────────────────────┐  ┌──────────┐  ┌────────────┐  │
│  │  index.html   │  │     chat.html        │  │admin.html│  │instructor  │  │
│  │  (Login/Auth) │─▶│  (Chat estudante)    │  │(Admin)   │  │.html       │  │
│  └───────────────┘  └──────────────────────┘  └──────────┘  └────────────┘  │
│                                                               Portal Docente │
│              Vanilla HTML · CSS3 · JavaScript ES2022                         │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │ HTTP / JSON + JWT
┌──────────────────────────────────▼───────────────────────────────────────────┐
│                               CAMADA DE API                                  │
│                                                                              │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────────────────────┐   │
│  │  /api/auth   │  │   /api/chat    │  │    /api/admin                  │   │
│  │  login       │  │   message(SSE) │  │    extract / extract-topics    │   │
│  │  register    │  │   sessions     │  │    save / save-topics          │   │
│  └──────────────┘  └────────────────┘  │    knowledge / documents       │   │
│                                        │    users / courses / semesters │   │
│               FastAPI · Pydantic v2 · Python 3.13                       │   │
└─────────────────────────────┬────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────────────┐
│                        CAMADA DE CONHECIMENTO                                │
│                                                                              │
│  ┌─────────────────────┐  ┌───────────────────┐  ┌────────────────────────┐ │
│  │  Extração de Texto  │  │  Organização LLM  │  │  Recuperação           │ │
│  │  pdfplumber         │─▶│  Flat (admin)     │─▶│  por Keywords          │ │
│  │  (todas as páginas) │  │  Por Tópicos      │  │  + Scoring TF          │ │
│  │                     │  │  (instructor)     │  │  + Sinónimos PT        │ │
│  └─────────────────────┘  └───────────────────┘  └────────────────────────┘ │
│         Ficheiros .txt por UC  ·  data/knowledge/                            │
└─────────────────────────────┬────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────────────┐
│                          CAMADA DE PERSISTÊNCIA                              │
│                                                                              │
│   ┌──────────────────────────────┐       ┌──────────────────────────────┐   │
│   │   SQLite                     │       │   Ficheiros de texto         │   │
│   │   data/isla_chatbot.db       │       │   data/knowledge/            │   │
│   │   (utilizadores, UCs,        │       │   course_1_sem_1.txt         │   │
│   │    sessões, mensagens,       │       │   course_2_sem_1.txt         │   │
│   │    configurações, teachings) │       │   general.txt                │   │
│   └──────────────────────────────┘       └──────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Sistema de Gestão de Conhecimento

O conhecimento do sistema reside em ficheiros de texto simples, um por unidade curricular (mais um para documentos gerais). Esta abordagem substitui a base de dados vetorial ChromaDB utilizada na v1.

### 4.1 Estrutura dos Ficheiros de Conhecimento

```
data/knowledge/
├── course_1_sem_1.txt      ← IIA — Introdução à Inteligência Artificial
├── course_2_sem_1.txt      ← FCSI — Fundamentos de CS
├── course_3_sem_2.txt      ← ESIA — Eng. de Software para IA
├── course_4_sem_2.txt      ← EST — Estatística
└── general.txt             ← Documentos institucionais ISLA
```

Cada ficheiro acumula blocos de conteúdo separados por um cabeçalho identificador:

```
============================================================
# FUC IIA — Avaliação [Docente: João Silva]  [2026-01-15 14:30]
============================================================

=== AVALIAÇÃO ===
- Trabalhos Práticos: 40%
- Exame Final: 60% (mínimo 8 valores)
- Época Normal: Janeiro 2026

============================================================
# FUC IIA — Conteúdo Programático [Docente: João Silva]  [2026-01-15 14:30]
============================================================

=== CONTEÚDO PROGRAMÁTICO ===
Tópico 1: Introdução à IA e Agentes Inteligentes
Tópico 2: Procura Não Informada (BFS, DFS, UCS)
...
```

### 4.2 Dois Modos de Ingestão

#### Modo por Tópicos — Portal do Docente

```
PDF carregado
      │
      ▼
┌─────────────────┐   pdfplumber.extract_text(layout=True)
│  Extração de    │   Preserva colunas, tabelas, estrutura
│  Texto          │   Todas as páginas concatenadas
└────────┬────────┘
         │  texto_bruto
         ▼
┌─────────────────┐   Prompt específico → estrutura em tópicos
│  Organização    │   === TÓPICO: Avaliação ===
│  por Tópicos    │   === TÓPICO: Conteúdo Programático ===
│  (LLM)          │   === TÓPICO: Objectivos ===
└────────┬────────┘
         │  [{name, content}, ...]
         ▼
┌─────────────────┐   Docente vê cada tópico como card
│  Revisão por    │   Pode renomear, editar, remover, adicionar
│  Tópicos        │
└────────┬────────┘
         │
         ▼
  Cada tópico → bloco separado com [Docente: Nome]
```

**Vantagem:** A recuperação por palavras-chave fica muito mais precisa. Um estudante que pergunta "como é a avaliação" vai ao bloco "FUC IIA — Avaliação" directamente, sem receber o documento completo.

#### Modo Flat — Painel de Administração (ficheiro único com revisão)

```
PDF carregado
      │
      ▼
┌─────────────────┐   pdfplumber.extract_text(layout=True)
│  Extração de    │
│  Texto          │
└────────┬────────┘
         │  texto_bruto
         ▼
┌─────────────────┐   Organiza com === SECÇÃO === headers
│  Organização    │   Admin pode editar antes de guardar
│  LLM (flat)     │
└────────┬────────┘
         │
         ▼
  Um bloco no ficheiro .txt
```

#### Modo Bulk — Extração Directa (múltiplos ficheiros)

```
Múltiplos PDFs
      │
      ▼  Para cada ficheiro:
┌─────────────────┐   pdfplumber.extract_text()
│  Extração rápida│   SEM chamada ao LLM
│  de Texto       │   ~1–3 segundos por ficheiro
└────────┬────────┘   Barra de progresso: N / total
         │
         ▼
  Ficheiro .txt atualizado
```

**Utilização recomendada:** 73+ regulamentos institucionais ISLA, documentos gerais, carga inicial em massa.

### 4.3 Recuperação de Secções Relevantes

```python
def _score_block(block: str, query_words: set, query_lower: str) -> float:
    # TF (term frequency) — blocos com mais ocorrências da keyword sobem
    freq   = Counter(re.findall(r'\w+', block.lower()))
    tf     = sum(freq[w] for w in query_words)
    # Phrase bonus — query completa no bloco vale +3
    phrase = 3.0 if query_lower in block.lower() else 0.0
    return tf + phrase
```

Os resultados são também expandidos com sinónimos portugueses para evitar falsos negativos por diferença de vocabulário entre estudante e documento:

```python
_PT_SYNONYMS = {
    "avaliacao": {"avaliação", "exame", "nota", "classificacao", "frequencia"},
    "data":      {"datas", "prazo", "calendário", "época"},
    "docente":   {"professor", "responsavel", "instrutor"},
    ...
}
```

---

## 5. Organização Hierárquica por Tópicos

A versão 2.2 introduz um novo modo de organização LLM orientado a tópicos, utilizado pelo Portal do Docente. Em vez de produzir um documento flat com cabeçalhos genéricos (`=== AVALIAÇÃO ===`), o LLM é instruído a dividir o conteúdo em tópicos nomeados que se tornam blocos independentes na base de conhecimento.

### 5.1 Prompt de Organização por Tópicos

```
Extrai e organiza toda a informação do seguinte documento académico,
dividindo-a em TÓPICOS claros.

Formato de saída OBRIGATÓRIO:
=== TÓPICO: [Nome do Tópico] ===
[Conteúdo detalhado do tópico aqui]

Tópicos típicos:
- Informações Gerais (UC, ECTS, regime, horário)
- Docente Responsável
- Objectivos e Competências
- Conteúdo Programático
- Avaliação (componentes, percentagens, datas)
- Metodologia e Funcionamento das Aulas
- Recursos e Bibliografia
```

### 5.2 Interface de Revisão de Tópicos

Após o LLM processar o PDF, o portal do docente apresenta cada tópico como um card expansível e editável:

```
┌─────────────────────────────────────────────────────────────────┐
│ Tópicos identificados pela IA  [4 tópicos]                      │
├─────────────────────────────────────────────────────────────────┤
│ ▼  1. [Informações Gerais         ]                    [×]      │
│    ┌──────────────────────────────────────────────────────────┐ │
│    │ Nome: Introdução à Inteligência Artificial               │ │
│    │ ECTS: 6  ·  Regime: Semestral  ·  Semestre: 1.º         │ │
│    └──────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│ ▶  2. [Avaliação                  ]                    [×]      │
├─────────────────────────────────────────────────────────────────┤
│ ▶  3. [Conteúdo Programático      ]                    [×]      │
├─────────────────────────────────────────────────────────────────┤
│ ▶  4. [Metodologia                ]                    [×]      │
└─────────────────────────────────────────────────────────────────┘
[ + Adicionar tópico manualmente ]

  4 tópicos prontos a guardar     [Cancelar]  [Guardar tópicos]
```

O docente pode:
- **Renomear** qualquer tópico clicando no campo de nome;
- **Expandir/recolher** o conteúdo de cada tópico;
- **Editar** o conteúdo directamente no card;
- **Remover** tópicos irrelevantes;
- **Adicionar** tópicos manualmente (para conteúdo criado de raiz).

### 5.3 Resultado na Base de Conhecimento

Cada tópico é guardado como um bloco independente com label estruturado:

```
============================================================
# FUC IIA — Avaliação [Docente: João Silva]  [2026-04-26 10:30]
============================================================
- Trabalhos Práticos: 40%
- Exame Final: 60% (mínimo 8 valores)
- Época Normal: Janeiro 2026

============================================================
# FUC IIA — Conteúdo Programático [Docente: João Silva]  [2026-04-26 10:30]
============================================================
Módulo 1: Fundamentos de IA
Módulo 2: Algoritmos de Procura
...
```

**Impacto na recuperação:** Um estudante que pergunta "quais os temas de IIA?" vai receber exactamente o bloco "FUC IIA — Conteúdo Programático" com score elevado, sem ser inundado com informação de avaliação ou bibliografia.

---

## 6. Pipeline de Resposta e Web Search

### 6.1 Fluxo Completo de Resposta

```
         Pergunta do Estudante
                │
                ▼
   ┌────────────────────────┐
   │  Detecção de tipo      │  Saudação → resposta directa
   │                        │  Confirmação (ok/obrigado) → resposta curta
   │                        │  Pedido de clarificação → usa só histórico
   └────────────┬───────────┘
                │ pergunta substantiva
                ▼
   ┌────────────────────────┐
   │  Ler ficheiros .txt    │   course_X_sem_Y.txt  +  general.txt
   └────────────┬───────────┘
                │
                ▼
   ┌────────────────────────┐
   │  Extração por keywords │   max 18 000 chars da UC (TF + sinónimos)
   │  (UC + Geral)          │   max 8 000 chars do Geral
   └────────────┬───────────┘
                │
    ┌───────────┴────────────────────────────────────────────────┐
    │                                                            │
    │ Sem contexto OU query sobre ISLA OU                       │
    │ notícias/eventos OU follow-up a resposta de notícia?      │
    │ → search_isla_website()                                   │
    │   Scrape news + events do islasantarem.pt                 │
    │   Adiciona ao contexto                                    │
    └───────────┬────────────────────────────────────────────────┘
                │
                ▼
   ┌────────────────────────┐
   │  Construção do Prompt  │   System prompt (RBAC + regras PT)
   │                        │   + Documentos UC (se relevantes)
   │                        │   + Documentos Gerais (se relevantes)
   │                        │   + Notícias web (se aplicável)
   │                        │   + Histórico (6 mensagens)
   │                        │   + Pergunta
   └────────────┬───────────┘
                │
                ▼
   ┌────────────────────────┐
   │  LLM (SSE streaming)   │   temperature configurável (default 0.3)
   │  Geração de resposta   │   Tokens enviados em tempo real ao browser
   └────────────┬───────────┘
                │
                ▼
   ┌────────────────────────┐
   │  Limpeza da resposta   │   Remove "você/sua", frases de encerramento
   │                        │   Garante Português Europeu
   │                        │   Adiciona marcador [[MODE:CURSO|GERAL]]
   └────────────────────────┘
```

### 6.2 Detecção de Web Search

O sistema usa duas funções de detecção complementares:

**`_wants_live_info()`** — activa quando o estudante pede explicitamente notícias recentes:
```
"há novidades na isla?", "o que aconteceu esta semana?"
```

**`_wants_isla_search()`** — activa para tópicos institucionais não cobertos por documentos de UC:
```
"quais as propinas do curso?"  →  activa (propinas ∈ _ISLA_SEARCH_KEYWORDS)
"como me candidatar?"          →  activa (candidatura ∈ _ISLA_SEARCH_KEYWORDS)
"pesquisa o site da isla"      →  activa (padrão regex explícito)
"a avaliação é 40% exame?"     →  NÃO activa (excluído: contém "avaliacao", "exame")
```

O segundo filtro evita falsos positivos em questões de avaliação que contenham "isla" ou "propina" no contexto de documentos de UC.

### 6.3 System Prompt e Língua

O sistema prompt inclui regras estritas para garantir respostas em Português Europeu:
- `"tu/teu/tua"` em vez de `"você/seu/sua"`
- `"aprendizagem automática"` em vez de `"aprendizado de máquina"`
- `"ficheiro"` em vez de `"arquivo"`
- Proibição de frases de encerramento genéricas ("estou aqui para ajudar", etc.)
- Detecção automática de inglês → resposta em inglês se pergunta for em inglês

---

## 7. Painel de Administração

O painel de administração (`admin.html`) é acessível exclusivamente a utilizadores com papel `admin`. A sidebar está organizada em cinco secções funcionais:

```
┌─────────────────┬────────────────────────────────────────────────┐
│ [ISLA logo]     │  Painel de Administração                        │
│ ─────────────── │  ─────────────────────────────────────────────  │
│ ■ Dashboard     │                                                 │
│                 │  [Dashboard, Utilizadores, Cursos, Semestres,   │
│ GESTÃO          │   Utilização, Histórico, Insights, Feed,        │
│ 👥 Utilizadores │   Config IA, Base de Conhecimento]              │
│ 📋 Cursos &     │                                                 │
│    Inscrições   │  A secção activa é renderizada no painel        │
│ 📅 Semestres    │  principal à direita.                           │
│ 📊 Utilização   │                                                 │
│                 │                                                 │
│ ANÁLISE         │                                                 │
│ 💬 Histórico    │                                                 │
│ 🔍 Insights     │                                                 │
│ 📡 Feed Direto  │                                                 │
│                 │                                                 │
│ SISTEMA         │                                                 │
│ ⚙️ Config. IA  │                                                 │
│                 │                                                 │
│ BASE DE CONH.   │                                                 │
│ [🔍 filtrar…]  │                                                 │
│ 2024/25 S1      │                                                 │
│  > IIA          │                                                 │
│  > FCSI         │                                                 │
│ 2024/25 S2      │                                                 │
│  > ESIA  > EST  │                                                 │
│ 🏠 Docs Gerais  │                                                 │
└─────────────────┴────────────────────────────────────────────────┘
```

### 7.1 Funcionalidades do Painel

| Funcionalidade | Descrição |
|---|---|
| **Dashboard** | Estatísticas: alunos, mensagens dia/semana, satisfação, gráfico 14 dias |
| **Gestão de utilizadores** | Tabela com pesquisa, filtro por papel, criar/editar/desativar |
| **Gestão de cursos** | Grelha de cards responsiva com filtros por semestre e pesquisa |
| **Gestão de semestres** | Criar, editar, activar semestres académicos |
| **Utilização & Limites** | Utilização por aluno, rate limit configurável por hora |
| **Histórico de chats** | Listagem e visualização de todas as conversas |
| **Insights & Qualidade** | Perguntas sem resposta e avaliações negativas |
| **Feed em Direto** | Sincronização de notícias e eventos do site ISLA |
| **Configurações IA** | Modelo, temperatura, system prompt personalizado |
| **Base de Conhecimento** | Upload, organização LLM, edição, limpeza por UC |

### 7.2 Atribuição de Docentes

O administrador pode atribuir docentes a UCs via endpoint `POST /api/admin/courses/{id}/instructors`. Após a atribuição, o docente passa a ver essa UC no seu Portal do Docente. As atribuições são armazenadas na tabela `teachings` (instructor_id, course_id).

---

## 8. Portal do Docente

O Portal do Docente (`instructor.html`) é uma interface dedicada para utilizadores com papel `instructor`. É completamente separada do painel de administração e expõe apenas as funcionalidades necessárias para gerir os conteúdos das UCs atribuídas.

### 8.1 Controlo de Acesso

```javascript
// Auth guard no frontend
if (USER.role !== 'instructor' && USER.role !== 'admin') {
  window.location.href = 'chat.html';  // redireciona não-autorizados
}

// Auth guard no backend — todos os endpoints /api/admin/* exigem:
require_role(UserRole.admin, UserRole.instructor)

// Filtragem de UCs — instrutores só veem as suas
if current.role == UserRole.instructor:
    teaching_ids = {t.course_id for t in db.query(Teaching)
                    .filter(Teaching.instructor_id == current.id).all()}
    courses = db.query(Course).filter(Course.id.in_(teaching_ids)).all()
```

### 8.2 Fluxo de Trabalho do Docente

```
1. Docente entra no Portal do Docente
2. Sidebar mostra apenas "As Minhas UCs" (filtradas por Teaching)
3. Docente selecciona uma UC → painel de upload aparece
4. Docente arrasta/selecciona PDF
5. Clica "Carregar e Organizar"
   → POST /api/admin/extract-topics
   → pdfplumber extrai texto
   → LLM organiza em tópicos (=== TÓPICO: X ===)
   → parse_topic_blocks() → [{name, content}]
6. Portal mostra cada tópico como card expansível
7. Docente revê, renomeia, edita conteúdo se necessário
8. Clica "Guardar tópicos"
   → POST /api/admin/save-topics
   → Cada tópico guardado como bloco separado com [Docente: Nome]
9. Lista "Documentos já carregados" actualiza
   → Blocos agrupados por documento base com contagem de tópicos
```

### 8.3 Endpoint `/api/admin/extract-topics`

```python
@router.post("/extract-topics")
async def extract_topics(doc_label, file, current):
    # 1. Extrai texto do PDF com pdfplumber
    raw_text = extract_text_from_file(tmp_path)
    # 2. LLM organiza em tópicos (prompt específico)
    organized = await loop.run_in_executor(None, organize_with_topics, raw_text, label)
    # 3. Parse dos tópicos
    topics = parse_topic_blocks(organized)
    return {"topics": topics, "filename": ..., "raw_chars": ...}
```

### 8.4 Endpoint `/api/admin/save-topics`

```python
@router.post("/save-topics")
def save_document_topics(body: SaveTopicsIn, current):
    path    = course_knowledge_path(body.course_id, body.semester_id)
    teacher = body.teacher_name or current.name
    saved   = 0
    for topic in body.topics:
        base  = f"{body.doc_label} — {topic.name}"
        label = f"{base} [Docente: {teacher}]"
        if not is_duplicate(path, label):
            append_to_knowledge(path, topic.content, label)
            saved += 1
    return {"status": "saved", "topics_saved": saved}
```

---

## 9. Atribuição de Docente e Citação

### 9.1 Marcação do Bloco

Quando um bloco é guardado via Portal do Docente (ou via painel admin por um instructor), o label do bloco inclui o nome do docente:

```
# FUC IIA — Avaliação [Docente: João Silva]  [2026-04-26 10:30]
```

### 9.2 Citação pelo LLM

O system prompt inclui a regra de citação:

```
4a. DOCENTE: Quando um bloco de documento tiver o padrão "[Docente: Nome]"
no cabeçalho, cita o docente na resposta:
"De acordo com o/a docente [Nome], ..." ou "Segundo o/a Prof.ª/Prof. [Nome], ...".
Faz isto APENAS quando o bloco identificado for a fonte principal da resposta.
```

**Exemplo de resposta:**
```
Estudante: "Como é a avaliação de IIA?"

Bot: "De acordo com o/a Prof. João Silva, a avaliação de IIA divide-se em:
      - Trabalhos Práticos: 40%
      - Exame Final: 60% (mínimo 8 valores)
      A época normal de exame é em Janeiro de 2026."
```

### 9.3 Benefícios

- **Transparência:** o estudante sabe exactamente quem carregou a informação;
- **Responsabilidade:** o docente tem incentivo para manter os materiais actualizados;
- **Credibilidade:** a informação é atribuível a uma pessoa, não apenas "o sistema".

---

## 10. Modelo de Dados

```
┌──────────────┐         ┌──────────────┐         ┌──────────────────┐
│    users     │         │   semesters  │         │    courses       │
├──────────────┤         ├──────────────┤         ├──────────────────┤
│ id (PK)      │         │ id (PK)      │◀────────│ semester_id (FK) │
│ name         │         │ name         │         │ id (PK)          │
│ email UNIQUE │         │ start_date   │         │ code             │
│ password_hash│         │ end_date     │         │ name             │
│ role (ENUM)  │         │ is_active    │         │ short_name       │
│   student    │         └──────────────┘         └───────┬──────────┘
│   instructor │                                          │
│   admin      │         ┌──────────────┐         ┌───────▼──────────┐
│ is_active    │         │  enrollments │         │    documents     │
└──────┬───────┘         ├──────────────┤         ├──────────────────┤
       ├────────────────▶│ student_id   │         │ course_id (FK)   │
       │                 │ course_id    │◀────────│ id (PK)          │
       │                 └──────────────┘         │ filename         │
       │                                          │ doc_type         │
       │                 ┌──────────────┐         │ indexed (bool)   │
       │                 │  teachings   │         └──────────────────┘
       ├────────────────▶│ instructor_id│
       │                 │ course_id    │
       │                 └──────────────┘
       │
       │                 ┌──────────────────┐     ┌──────────────────┐
       │                 │  chat_sessions   │     │  chat_messages   │
       │                 ├──────────────────┤     ├──────────────────┤
       └────────────────▶│ user_id (FK)     │◀────│ session_id (FK)  │
                         │ course_id (FK)   │     │ role (user/asst) │
                         │ title            │     │ content (TEXT)   │
                         │ updated_at       │     │ sources (JSON)   │
                         └──────────────────┘     │ rating (int)     │
                                                  │ had_results(bool)│
                                                  └──────────────────┘
                         ┌──────────────────┐
                         │  system_settings │
                         ├──────────────────┤
                         │ key (UNIQUE)     │
                         │ value            │
                         └──────────────────┘
```

**Nota:** Os ficheiros de conhecimento (`data/knowledge/*.txt`) não têm metadados na base de dados. São auditáveis e editáveis por qualquer editor de texto. A tabela `teachings` é o mecanismo que liga instrutores a cursos e controla o que cada docente pode ver/editar no Portal do Docente.

---

## 11. Autenticação e Controlo de Acesso

### 11.1 Fluxo de Autenticação JWT

```
   Cliente                              Servidor
      │  POST /api/auth/login              │
      │  {username: email, password}       │
      │───────────────────────────────────▶│
      │                                    │  1. Query User WHERE email=?
      │                                    │  2. bcrypt.verify(pw, hash)
      │                                    │  3. JWT signed HS256, exp=8h
      │  200 OK {access_token, name, role} │
      │◀───────────────────────────────────│
```

### 11.2 Modelo de Papéis (RBAC)

| Papel | Chat (UCs inscritas) | Painel Admin | Portal Docente | Upload/Edição | Gerir Utilizadores |
|---|---|---|---|---|---|
| **student** | Sim | Não | Não | Não | Não |
| **instructor** | Sim (suas UCs) | Não | Sim (suas UCs) | Sim (suas UCs) | Não |
| **admin** | Todas | Sim | Sim | Todas | Sim |

O controlo de acesso é verificado em duas camadas:
1. **Frontend:** redireccionamento com base no `role` guardado no `localStorage`;
2. **Backend:** `require_role()` em todos os endpoints protegidos — um JWT adulterado sem o papel correcto recebe `HTTP 403`.

---

## 12. Stack Tecnológico

### 12.1 Visão Geral

```
┌─────────────────────────────────────────────────────────────────┐
│                      TECNOLOGIAS UTILIZADAS                     │
├─────────────────────┬───────────────────────────────────────────┤
│  CAMADA             │  TECNOLOGIA                               │
├─────────────────────┼───────────────────────────────────────────┤
│  LLM (local)        │  Ollama + qwen2.5:7b / gemma3:12b         │
│  LLM (cloud)        │  Groq, OpenRouter, Gemini (OpenAI API)   │
│  Streaming          │  SSE (Server-Sent Events)                 │
│  PDF Parsing        │  pdfplumber (extração completa)           │
│  Conhecimento       │  Ficheiros .txt estruturados              │
│  Recuperação        │  Keyword scoring TF + sinónimos PT        │
│  Backend API        │  FastAPI 0.115 + Uvicorn                  │
│  ORM                │  SQLAlchemy 2.0                           │
│  Base de Dados      │  SQLite 3 (ficheiro local)                │
│  Auth               │  JWT (python-jose) + bcrypt               │
│  Validação          │  Pydantic v2                              │
│  Frontend           │  HTML5 + CSS3 + JavaScript ES2022         │
│  Markdown           │  marked.js (renderização no chat)         │
│  Runtime            │  Python 3.13                              │
└─────────────────────┴───────────────────────────────────────────┘
```

### 12.2 Justificação das Escolhas Principais

**pdfplumber em vez de PyPDF:** O `layout=True` preserva a estrutura espacial do PDF (colunas, tabelas, indentação), produzindo texto significativamente mais legível que os loaders genéricos.

**Ficheiros .txt em vez de ChromaDB:** A base de dados vetorial introduzia complexidade operacional elevada (versões incompatíveis, corrupção de índices HNSW, necessidade de re-ingestão). Para o volume de dados académicos de uma instituição de ensino, ficheiros de texto com recuperação por palavras-chave são suficientes, mais previsíveis e depuráveis.

**Keyword TF + sinónimos em vez de embeddings:** Para um domínio académico com vocabulário controlado, a correspondência por palavras-chave com expansão semântica por sinónimos é comparável em qualidade à recuperação por cosine similarity, sem necessitar de um modelo de embeddings adicional em RAM.

**Multi-provider LLM:** O sistema suporta Ollama (local, gratuito, privacidade total), Groq (cloud, rápido, contexto 128k), OpenRouter (acesso a centenas de modelos) e Gemini (Google, multimodal). A configuração é feita via variável de ambiente `LLM_PROVIDER`, sem alterar o código.

**SSE Streaming:** As respostas do LLM são transmitidas token a token, eliminando a espera pela resposta completa. Crítico para modelos locais que podem demorar 15–60 segundos por resposta completa.

---

## 13. Interface de Utilizador

### 13.1 Página de Login (`index.html`)

Layout dividido: painel esquerdo com gradiente ISLA (azul escuro), logótipo e funcionalidades; painel direito com formulário de autenticação. Toggle de visibilidade da password.

### 13.2 Interface de Chat (`chat.html`)

```
┌──────────────────────┬──────────────────────────────────────────────────────┐
│      SIDEBAR         │   ☰  ● IIA — Introdução à IA  [IIA ▼] │ [Admin] …  │
│  [ISLA logo]         │   ─────────────────────────────────────────────────── │
│  + Nova Conversa     │                                                       │
│  ─────────────────   │   [Ecrã de boas-vindas com chips de sugestão]        │
│  Hoje                │                                                       │
│  > Quando é o exame? │   ┌──────────────────────────────────────────────┐   │
│  Últimos 7 dias      │   │ Estudante: Quando é o exame de IIA?          │   │
│  > O que é UML?      │   └──────────────────────────────────────────────┘   │
│  ─────────────────   │   ┌──────────────────────────────────────────────┐   │
│  [JS] João · Docente │   │ Bot: De acordo com o Prof. João Silva, o     │   │
│  [logout]            │   │ exame de IIA está marcado para Janeiro 2026  │   │
│                      │   │ [Base de conhecimento da UC]                 │   │
│                      │   └──────────────────────────────────────────────┘   │
│                      │   ┌──────────────────────────────────────────────┐   │
│                      │   │ Faz uma pergunta sobre IIA...             [▶] │   │
│                      │   └──────────────────────────────────────────────┘   │
└──────────────────────┴──────────────────────────────────────────────────────┘
```

**Navbar reestruturada (v2.2):**

```
[☰]  [ISLA logo]  IIA  [IIA ▼]  │  [Admin]  [Docente]  │  [Exportar] [Resumo]  │  [...]
                                 ↑           ↑           ↑           ↑
                           Grupo 1: UC  Grupo 2: Portais  Grupo 3: Acções  Overflow
```

O botão `[...]` abre um dropdown com Exportar e Resumo (acções raramente usadas), mantendo a navbar limpa. A visibilidade de [Admin] e [Docente] é controlada pelo `role` do utilizador.

**Animações (v2.2):**
- Mensagens do utilizador: slide da direita (`msgSlideRight`)
- Mensagens do bot: slide da esquerda (`msgSlideLeft`)
- Avatar pop (`avatarPop`)
- Botão enviar: glow pulse ao focar
- Itens do histórico: staggered fade-in
- Chips de sugestão: bounce sequencial
- Troca de UC: flash no badge (`badgeSwitch`)

### 13.3 Animação de Processamento LLM

Durante o processamento, exibe três partículas morfológicas que percorrem independentemente sequências de formas geométricas (círculo → quadrado rotacionado → elipse → blob orgânico), com temporização diferente para cada partícula, criando animação não repetitiva.

### 13.4 Portal do Docente (`instructor.html`)

Sidebar com apenas "As Minhas UCs". Ao seleccionar uma UC, aparece o painel de upload com:
- Drop zone com visualização do pipeline (PDF → IA → Tópicos → Chatbot)
- Campo de nome do documento
- Após upload: topic tree com cards expansíveis, editáveis e reordenáveis
- Lista de documentos agrupada por nome base com contagem de tópicos

---

## 14. Entrada por Voz e Anexos com OCR

A versão 2.3 transforma o campo de input do chat numa área de entrada multimodal: texto, voz e ficheiros (PDFs e imagens) são aceites de forma transparente.

### 14.1 Entrada por Voz (Web Speech API)

A entrada por voz utiliza a **Web Speech API** nativa do browser — uma API padrão W3C disponível no Chrome, Edge e Safari — sem necessidade de servidor ou dependência externa.

```javascript
const recognition = new webkitSpeechRecognition();
recognition.lang            = 'pt-PT';  // Português de Portugal
recognition.interimResults  = true;     // mostra resultados parciais em tempo real
recognition.maxAlternatives = 1;

recognition.onresult = (event) => {
  const transcript = Array.from(event.results)
    .map(r => r[0].transcript).join('');
  msgInput().value = transcript;  // preenche o campo de texto
  autoResize(msgInput());
};
```

**Fluxo de interação:**

```
Estudante clica [🎤]
      │
      ▼
recognition.start() → browser pede permissão de microfone (primeira vez)
      │
      ▼
Botão fica vermelho + animação de pulso
Placeholder muda para "A ouvir..."
      │
      ▼
Fala do estudante → reconhecimento em tempo real → texto aparece no input
      │
      ▼
Quando para de falar → recognition.onend → botão volta ao normal
Estudante pode editar o texto antes de enviar
      │
      ▼
Clica enviar (ou pressiona Enter)
```

**Língua:** `pt-PT` é passado ao engine de reconhecimento, optimizando para Português de Portugal. O motor de reconhecimento é o do próprio browser (Google, Microsoft, Apple) — não há transcrição do lado do servidor.

**Botão de paragem:** Clicar novamente no microfone durante gravação pára o reconhecimento imediatamente (`recognition.stop()`).

### 14.2 Anexos PDF e Imagem com OCR

O estudante pode carregar um ficheiro clicando no botão de clipe de papel `[📎]`. Tipos aceites: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`.

#### Fluxo de processamento

```
Estudante selecciona ficheiro
      │
      ▼
Preview strip aparece com nome + "A processar..."
      │
      ▼
POST /api/chat/process-attachment (multipart/form-data)
      │
      ├── PDF ──▶ pdfplumber.extract_text()
      │           texto extraído (max 4.000 chars)
      │
      └── Imagem ──▶ Detecta provider:
                     │
                     ├── Gemini/OpenRouter → mensagem multimodal
                     │   {"role": "user", "content": [
                     │     {"type": "image_url", "url": "data:image/png;base64,..."},
                     │     {"type": "text", "text": "Extract all text..."}
                     │   ]}
                     │
                     └── Ollama → /api/generate com "images": [base64]
                                  (requer modelo com suporte a visão, ex: gemma3:12b)
      │
      ▼
texto extraído → mostrado na preview strip ("PDF · 3.2k chars" / "Imagem · texto extraído")
      ▼
Texto pré-preenchido no campo de input (editável)
      ▼
Estudante revê/edita e envia
```

#### Preview strip animada

```
┌──────────────────────────────────────────────────────────────────┐
│ [📄]  relatorio_avaliacao.pdf                             [×]    │
│        PDF · 3.2k chars extraídos                               │
└──────────────────────────────────────────────────────────────────┘
```

A strip aparece com animação de slide-in. O estado pode ser:
- `A processar...` (cinza) — enquanto o backend processa;
- `PDF · X chars extraídos` (verde) — extracção bem-sucedida;
- `OCR requer modelo vision` (vermelho) — Ollama sem modelo multimodal.

#### Backend: endpoint `/api/chat/process-attachment`

```python
@router.post("/process-attachment")
async def process_attachment(file: UploadFile, current: User):
    suffix = Path(file.filename).suffix.lower()
    # Valida tipo
    if suffix not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}:
        raise HTTPException(422, "Tipo não suportado")
    
    if suffix == '.pdf':
        text = extract_text_from_file(tmp_path)  # pdfplumber
        return {"type": "pdf", "text": text[:4000]}
    else:
        b64  = base64.b64encode(image_bytes).decode()
        mime = "image/jpeg"  # ou png/webp
        
        if provider in ('gemini', 'openrouter'):
            # Mensagem multimodal OpenAI-compatible
            resp = _openai_post(url, key, {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text",      "text": "Extract all text..."},
                ]}]
            })
            return {"type": "image", "text": resp.json()["choices"][0]["message"]["content"]}
        
        elif provider == 'ollama':
            # Ollama generate com campo "images"
            resp = requests.post("/api/generate", json={
                "model": model, "prompt": "Extract all text...", "images": [b64]
            })
            return {"type": "image", "text": resp.json()["response"]}
```

### 14.3 Interface Redesenhada do Input (v2.3)

A área de input passou de uma simples linha (textarea + botão enviar) para uma área multimodal estruturada:

```
┌──────────────────────────────────────────────────────────────────┐
│ [preview strip — só visível quando há anexo]                     │
├──────────────────────────────────────────────────────────────────┤
│  [modo]  [texto da pergunta aqui...           ]  [🎤] [📎] [▶]  │
└──────────────────────────────────────────────────────────────────┘
  Suporta voz 🎤, PDF e imagens 📎 · O assistente pode cometer erros...
```

Os botões de acção (`[🎤]` e `[📎]`) são circulares com borda subtil, alinhados ao fundo da textarea (que cresce automaticamente até 160px de altura). Ao clicar `[🎤]`, o botão fica vermelho com animação de pulso. O botão `[📎]` abre o selector de ficheiros nativo do browser.

### 14.4 Casos de Uso Práticos

| Cenário | Entrada | Resultado |
|---|---|---|
| Estudante não quer escrever | Clica 🎤, dita "quando é o exame de IIA" | Texto aparece no input, envia com Enter |
| Estudante tem foto do enunciado | Carrega `.jpg` do enunciado | OCR extrai texto → enviado ao LLM com contexto da UC |
| Estudante tem PDF de exercícios | Carrega `.pdf` | pdfplumber extrai → texto pré-preenchido → pode editar e perguntar "resolve o exercício 3" |
| Estudante num dispositivo móvel | Selecciona microfone no teclado virtual OU usa botão 🎤 | Ditado em PT-PT |

---

## 15. Fluxos de Interação

### 14.1 Fluxo de Resposta a uma Pergunta

```
Estudante        Frontend              Backend           knowledge.py
    │  Pergunta       │                    │                   │
    │────────────────▶│                    │                   │
    │                 │  POST /api/chat/   │                   │
    │                 │  message           │                   │
    │                 │───────────────────▶│                   │
    │                 │ EventStream (SSE)  │  Valida JWT       │
    │                 │                   │  Verifica UC      │
    │                 │                   │──────────────────▶│
    │                 │                   │                   │ Lê course_X.txt
    │                 │                   │                   │ Lê general.txt
    │                 │                   │                   │ Extrai secções
    │                 │                   │                   │ (TF + sinónimos)
    │                 │                   │                   │ Web search?
    │                 │                   │                   │ Constrói mensagens
    │                 │  token token ...  │◀─LLM streaming───│
    │  tokens stream  │◀──────────────────│                   │
    │◀────────────────│                   │                   │
```

### 14.2 Fluxo de Upload de Tópicos (Docente)

```
Docente          instructor.html       /extract-topics       /save-topics
  │  Selecciona PDF │                         │                    │
  │────────────────▶│                         │                    │
  │  Clica Carregar │                         │                    │
  │────────────────▶│  POST /extract-topics   │                    │
  │                 │  (multipart: file,label)│                    │
  │                 │────────────────────────▶│                    │
  │                 │                         │ pdfplumber → text  │
  │                 │                         │ organize_with_     │
  │                 │                         │  topics (LLM)      │
  │                 │                         │ parse_topic_blocks │
  │  Topic cards    │  {topics: [{name,...}]} │                    │
  │◀────────────────│◀────────────────────────│                    │
  │  [Edita se      │                         │                    │
  │   necessário]   │                         │                    │
  │  Clica Guardar  │  POST /save-topics      │                    │
  │────────────────▶│────────────────────────────────────────────▶│
  │                 │                         │                    │ append_to_knowledge()
  │                 │                         │                    │ × N tópicos
  │  "N tópicos     │◀────────────────────────────────────────────│
  │   guardados"    │  {topics_saved: N}      │                    │
```

---

## 15. Evolução Arquitetural — v1 para v2.2

### 16.1 Linha do Tempo

| Versão | Data | Principais Mudanças |
|---|---|---|
| **v1.0** | Set 2025 | ChromaDB + embeddings, `ingest.py` CLI, sem painel web |
| **v2.0** | Nov 2025 | Ficheiros .txt, painel admin web, bulk upload, sem ChromaDB |
| **v2.1** | Jan 2026 | Navbar admin em grupos, grelha de cursos, pesquisa de UCs |
| **v2.2** | Mar 2026 | Portal do Docente, tópicos, web search, animações, SSE streaming |
| **v2.3** | Abr 2026 | Entrada por voz (Web Speech API), anexos PDF/imagem com OCR, input redesenhado |

### 16.2 Problemas da v1 e Soluções

| Problema v1 | Solução v2+ |
|---|---|
| ChromaDB instável (HNSW SIGABRT) | Eliminado — substituído por ficheiros .txt |
| Dois modelos em RAM (qwen + nomic-embed-text) | Apenas qwen2.5:7b necessário |
| Ingestão manual via `ingest.py` | Painel admin web com drag-and-drop |
| Contexto inundado (score=1.0 para todos os chunks) | TF scoring + expansão por sinónimos |
| Docentes sem autonomia | Portal do Docente dedicado |
| Documentos flat — recuperação pouco precisa | Tópicos como blocos independentes |

### 16.3 Tradeoffs da Abordagem Actual

| Aspeto | v1 (ChromaDB + Embeddings) | v2.2 (Ficheiros + Keywords + Tópicos) |
|---|---|---|
| **Qualidade semântica** | Superior (cosine similarity) | Boa para domínio académico controlado |
| **Fiabilidade** | Frágil (índices corrompiam) | Robusta (texto plano) |
| **Depuração** | Difícil (vetores opacos) | Simples (ficheiro .txt editável) |
| **Autonomia docente** | Nenhuma | Total (portal dedicado + tópicos) |
| **Granularidade de recuperação** | Chunks arbitrários | Tópicos semânticos (Avaliação, Conteúdo, etc.) |
| **Dependências** | ChromaDB, LangChain, nomic-embed | Apenas pdfplumber |

---

## 16. Segurança e Privacidade

### 16.1 Princípio da Privacidade Local

Em modo Ollama, toda a computação acontece em infraestrutura local. Nenhum dado — documentos académicos, perguntas dos estudantes, respostas do LLM — sai da rede da instituição.

### 16.2 Isolamento de Dados por UC

**Camada de base de dados:** A query de cursos filtra por `enrollment.student_id = current_user.id`.

**Camada de ficheiros:** O sistema lê exclusivamente o ficheiro `course_{id}_sem_{id}.txt` da UC seleccionada. Não existe acesso cross-UC.

**Camada de teaching:** A query de cursos para instrutores filtra por `Teaching.instructor_id = current.id`. Um instrutor não pode aceder a UCs a que não está atribuído, mesmo que conheça o ID.

### 16.3 Passwords com bcrypt

```python
# rounds=12 → ~300ms por hash (torna brute-force impraticável)
password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
```

### 16.4 JWT com Expiração

Tokens expiram em 8 horas. Não existe refresh token — o utilizador volta a fazer login. Em caso de compromisso de uma conta, o dano é limitado a 8 horas.

---

## 17. Infraestrutura LLM Local — Simulação e Planeamento

Esta secção simula os requisitos de hardware, modelos candidatos e arquitectura de rede necessários para hospedar o ISLA Chatbot com LLM inteiramente local na infraestrutura do ISLA Santarém, sem dependência de serviços cloud.

### 17.1 Contexto do Problema

O modelo actualmente utilizado em produção é o `qwen2.5:7b` via Ollama. A resposta a uma pergunta típica demora 10–45 segundos num computador sem GPU dedicada. Para um ambiente académico com dezenas de estudantes simultâneos, isto cria uma fila de espera inaceitável. O objetivo desta simulação é dimensionar correctamente o hardware para suportar **50–200 utilizadores simultâneos** com tempos de resposta aceitáveis (< 8 segundos para a primeira token).

### 17.2 Comparação de Modelos LLM Open-Source

| Modelo | Tamanho (disco) | VRAM Mín. | Velocidade (tok/s)* | Qualidade PT | Contexto | Licença |
|---|---|---|---|---|---|---|
| Llama 3.2:3b | 2.0 GB | 3 GB | ~80 | Boa | 128k | Llama 3.2 |
| Mistral:7b | 4.1 GB | 5 GB | ~45 | Boa | 32k | Apache 2.0 |
| Qwen2.5:7b *(actual)* | 4.7 GB | 6 GB | ~40 | Boa | 128k | Apache 2.0 |
| **Gemma3:4b** | 2.5 GB | 4 GB | ~70 | Muito boa | 128k | Gemma |
| **Gemma3:12b** | 8.1 GB | 10 GB | ~30 | **Excelente** | 128k | Gemma |
| **Gemma3:27b** | 18 GB | 20 GB | ~15 | **Excelente** | 128k | Gemma |
| Phi-4:14b | 9.1 GB | 11 GB | ~25 | Muito boa | 16k | MIT |
| Qwen2.5:32b | 20 GB | 22 GB | ~12 | Muito boa | 128k | Apache 2.0 |
| DeepSeek-R1:8b | 5.2 GB | 6 GB | ~35 | Boa | 128k | MIT |

*\*Velocidade estimada em RTX 4090 com quantização Q4_K_M*

### 17.3 Recomendação: Gemma3

O modelo recomendado para o ISLA Chatbot em produção local é o **Gemma3:12b**.

**Justificação:**

1. **Qualidade em Português Europeu:** O Gemma3 foi treinado com uma proporção significativa de dados em línguas europeias, incluindo português europeu. Produz respostas mais naturais e gramaticalmente correctas em PT-EU do que modelos focados em inglês.

2. **Contexto de 128k tokens:** O Gemma3 suporta janelas de contexto de 128k tokens, permitindo incluir documentos completos de UC sem truncagem — crítico para FUCs longas e regulamentos extensos.

3. **Instrução-following robusto:** Para um chatbot académico com um system prompt complexo (regras de língua, modo markers, citações de docentes), a capacidade de seguir instruções detalhadas é essencial. O Gemma3 classifica consistentemente acima do Qwen2.5:7b nestas métricas.

4. **Licença permissiva:** A licença Gemma permite uso académico, investigação e deployment interno sem restrições — adequada para uma instituição de ensino.

5. **Multimodalidade (futuro):** O Gemma3 suporta input de imagens. Isto abre a possibilidade futura de processar PDFs digitalizados (baseados em imagem) directamente, sem necessidade de OCR externo.

6. **Eficiência energética:** O Gemma3:12b tem um rácio qualidade/VRAM superior ao Qwen2.5:32b, consumindo menos energia a produzir resultados semelhantes ou melhores.

### 17.4 Tiers de Hardware

#### Tier 1 — Escolar Mínimo
*~10–20 utilizadores simultâneos, tempo de resposta 8–20 segundos*

| Componente | Especificação | Custo Estimado |
|---|---|---|
| CPU | Intel Core i7-13700K ou AMD Ryzen 7 7700X | €350 |
| RAM | 32 GB DDR5-5600 | €120 |
| GPU | NVIDIA RTX 4070 Ti (12 GB VRAM) | €800 |
| Storage | 512 GB NVMe SSD | €80 |
| Motherboard + PSU | ATX, 750W 80+ Gold | €250 |
| **Total estimado** | | **€1.600 – €2.200** |

Modelo recomendado: `gemma3:4b` (cabe inteiramente nos 12 GB VRAM com headroom)

#### Tier 2 — Produção
*~50–100 utilizadores simultâneos (com queue), tempo de resposta < 8 segundos para a primeira token*

| Componente | Especificação | Custo Estimado |
|---|---|---|
| CPU | AMD Ryzen 9 7950X (16 cores) | €650 |
| RAM | 64 GB DDR5-5600 | €250 |
| GPU | NVIDIA RTX 4090 (24 GB VRAM) | €1.900 |
| Storage | 1 TB NVMe SSD (PCIe 4.0) | €150 |
| Motherboard + PSU | ATX, 850W 80+ Platinum | €350 |
| Cooling | 360mm AIO | €120 |
| **Total estimado** | | **€3.500 – €4.500** |

Modelo recomendado: `gemma3:12b` Q4_K_M (8.1 GB → cabe nos 24 GB com boa margem)

#### Tier 3 — Enterprise
*~200+ utilizadores simultâneos, alta disponibilidade, SLA académico*

| Componente | Especificação | Custo Estimado |
|---|---|---|
| 2x Servidores Tier 2 | Para redundância e balanceamento | €9.000 |
| NVIDIA A4000 16GB (alternativa) | Mais eficiente, menos calor | €3.000/unid |
| NAS / RAID | Armazenamento de documentos + backups | €800 |
| Switch 10GbE | Rede interna rápida | €500 |
| Load Balancer | nginx + upstream Ollama | Software (grátis) |
| UPS | Protecção contra falha de energia | €400 |
| **Total estimado** | | **€15.000 – €25.000** |

Modelo recomendado: `gemma3:27b` Q4_K_M em GPU dedicada ou `gemma3:12b` com múltiplas instâncias

### 17.5 Arquitectura de Rede Local

```
Internet
    │
    ▼
[Firewall / Router ISLA]
    │
    ▼
[Switch 10GbE]
    │
    ├──▶ [Servidor Web — nginx]           :80/:443
    │           │
    │           ▼
    │    [FastAPI uvicorn]                :8000
    │           │  HTTP interno
    │           ▼
    ├──▶ [Servidor LLM — Ollama]          :11434
    │       GPU: RTX 4090 + gemma3:12b
    │       RAM: 64 GB
    │
    ├──▶ [Rede WiFi estudantes]
    └──▶ [Rede Docentes]
```

**Separação recomendada:** O servidor Ollama deve estar fisicamente separado do servidor web para isolar os recursos de GPU e evitar que a carga do LLM afecte a responsividade da API.

### 17.6 Migração do Modelo Actual

A migração de `qwen2.5:7b` para `gemma3:12b` é não-destrutiva e reversível:

```bash
# Instalar o novo modelo
ollama pull gemma3:12b

# Actualizar .env
LLM_PROVIDER=ollama
OLLAMA_MODEL=gemma3:12b

# Reiniciar o servidor
uvicorn backend.main:app --reload

# Testar (sem alterar dados)
curl -X POST /api/chat/message ...

# Rollback se necessário
# OLLAMA_MODEL=qwen2.5:7b
```

O formato dos ficheiros de conhecimento e da base de dados SQLite é independente do modelo — não é necessária qualquer migração de dados.

### 17.7 Estimativa de Custos Operacionais

| Cenário | Hardware | Energia (€/ano)* | Manutenção | Total Ano 1 | Total Ano 2+ |
|---|---|---|---|---|---|
| Tier 1 Mínimo | €2.200 | €180 | €200 | €2.580 | €380 |
| Tier 2 Produção | €4.500 | €420 | €400 | €5.320 | €820 |
| Cloud (Groq API) | €0 | €0 | €0 | €600–2.400** | €600–2.400 |

*\*Estimativa baseada em 8h/dia de uso activo, tarifa €0.15/kWh*  
*\*\*Groq: ~$0.05/1M tokens × estimativa de 10–40M tokens/mês para 100 utilizadores activos*

**Conclusão:** Para uma instituição com < 50 utilizadores activos e orçamento limitado, a solução cloud (Groq) é mais económica no curto prazo. Para > 100 utilizadores activos e preocupações de privacidade, o Tier 2 amortiza em 3–4 anos.

---

## 18. Limitações e Trabalho Futuro

### 19.1 Funcionalidades Implementadas (v2.3)

| Funcionalidade | Versão |
|---|---|
| Painel de administração web com drag-and-drop | 2.0 |
| Bulk upload com progresso, extração rápida sem LLM | 2.0 |
| Extração completa PDF (pdfplumber layout=True) | 2.0 |
| Organização flat por LLM com pré-visualização | 2.0 |
| Recuperação por keyword scoring (TF + sinónimos PT) | 2.0 |
| Animação morfológica durante processamento | 2.0 |
| RBAC completo (student / instructor / admin) | 2.0 |
| Streaming SSE (resposta token a token) | 2.0 |
| Suporte multi-provider (Ollama, Groq, OpenRouter, Gemini) | 2.0 |
| Sidebar admin com grupos de navegação | 2.1 |
| Pesquisa de UCs na sidebar | 2.1 |
| Grelha de cards de cursos com filtros | 2.1 |
| Portal do Docente (instructor.html) | 2.2 |
| Organização por tópicos (extract-topics, save-topics) | 2.2 |
| Atribuição de docente nos blocos + citação pelo LLM | 2.2 |
| Web search fallback (site ISLA) | 2.2 |
| Detecção `_wants_isla_search()` | 2.2 |
| Navbar reestruturada com grupos e overflow menu | 2.2 |
| Animações de entrada de mensagens e UI | 2.2 |
| Documentos agrupados por tópico na lista do docente | 2.2 |
| Entrada por voz (Web Speech API, pt-PT) | 2.3 |
| Anexo PDF no chat (pdfplumber, max 4k chars) | 2.3 |
| Anexo imagem no chat com OCR via LLM vision | 2.3 |
| Preview strip animada para anexos (nome, estado, remover) | 2.3 |
| Input redesenhado: textarea + [🎤] + [📎] + send | 2.3 |
| Limpeza de `__pycache__`, `eval_results`, `finetune_dataset.jsonl` | 2.3 |

### 19.2 Limitações Actuais

| Limitação | Impacto | Prioridade |
|---|---|---|
| PDFs digitalizados (imagens) | pdfplumber não extrai texto de PDFs baseados em imagem | Alta |
| Recuperação puramente por keywords | Queries semanticamente distintas mas sinónimas podem não recuperar secções | Média |
| Um servidor Ollama — sem escala horizontal | Com muitos utilizadores simultâneos, a fila de espera cresce | Média |
| Sem notificação quando docente actualiza material | Estudantes não sabem que novos conteúdos foram adicionados | Baixa |
| Sem paginação do histórico | Com muitas sessões a sidebar pode ficar lenta | Baixa |

### 19.3 Melhorias Propostas

**OCR para PDFs digitalizados:** Integrar `ocrmypdf` ou a API Gemini Vision (multimodal) para processar PDFs baseados em imagem antes da extração.

**Recuperação híbrida:** Combinar keyword matching com um modelo de embeddings leve (ex: `all-MiniLM-L6-v2`, 80 MB) para recuperação semântica em queries mais abstractas.

**Migração para Gemma3:12b:** Com base na análise da Secção 17, o modelo Gemma3:12b oferece melhor qualidade para Português Europeu com janela de contexto de 128k tokens.

**Notificações de actualização:** Email ou notificação in-app quando um docente carrega novos materiais para uma UC em que o estudante está inscrito.

**Sincronização com Moodle:** Integrar a API REST do Moodle para sincronização automática de materiais de aula carregados pelos docentes.

---

## 19. Conclusão

O ISLA Chatbot v2.2 demonstra como é possível construir um assistente académico inteligente, fiável e governado, utilizando exclusivamente software *open-source* e potencialmente infraestrutura inteiramente local. As três revisões arquitecturais desde a v1 reflectem um processo iterativo guiado por problemas reais de produção:

- A v2.0 substituiu a fragilidade do ChromaDB por ficheiros de texto simples, eliminando horas de manutenção de índices corrompidos;
- A v2.1 resolveu a escalabilidade da interface administrativa à medida que o número de UCs cresceu;
- A v2.2 democratizou a gestão de conteúdos, transferindo-a dos administradores para os próprios docentes, e melhorou a granularidade da recuperação através de tópicos semânticos.

A análise de infraestrutura da Secção 17 demonstra que um deployment totalmente local com o modelo **Gemma3:12b** é viável para o ISLA Santarém com um investimento de €3.500–4.500 em hardware de Tier 2 — amortizável em 3–4 anos comparado com custos cloud equivalentes — garantindo privacidade total dos dados académicos e independência de fornecedores externos.

Do ponto de vista pedagógico, o projecto integra competências de múltiplas áreas — processamento de linguagem natural, extração de informação, sistemas web, autenticação, segurança e planeamento de infraestrutura — numa aplicação com valor real e imediato para a comunidade académica do ISLA Santarém.

---

## 20. Referências

[1] OpenAI. (2023). *GPT-4 Technical Report*. arXiv:2303.08774.

[2] Lewis, P., Perez, E., Piktus, A., et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. Advances in Neural Information Processing Systems, 33, 9459–9474.

[3] Gao, Y., Xiong, Y., Gao, X., et al. (2023). *Retrieval-Augmented Generation for Large Language Models: A Survey*. arXiv:2312.10997.

[4] Google DeepMind. (2024). *Gemma 3 Technical Report*. Google DeepMind.

[5] Ollama. (2024). *Ollama: Get up and running with large language models locally*. https://ollama.com/

[6] FastAPI. (2024). *FastAPI: Modern, fast web framework for building APIs with Python*. https://fastapi.tiangolo.com/

[7] pdfplumber. (2024). *pdfplumber: Plumb a PDF for detailed information about each text character, rectangle, and line*. https://github.com/jsvine/pdfplumber

[8] Qwen Team. (2024). *Qwen2.5 Technical Report*. Alibaba Group.

[9] SQLAlchemy. (2024). *SQLAlchemy: The Python SQL Toolkit and ORM*. https://www.sqlalchemy.org/

[10] NVIDIA. (2024). *NVIDIA GeForce RTX 4090 Product Brief*. NVIDIA Corporation.

[11] Groq Inc. (2024). *Groq LPU Inference Engine*. https://groq.com/

---

*Documento gerado para o CTeSP em Inteligência Artificial — ISLA Santarém, 2025/26.*  
*v2.2: Portal do Docente · Tópicos · Web Search · Animações · Infraestrutura LLM Local*  
*v2.3: Entrada por Voz · OCR de PDF/Imagem no Chat · Input Multimodal · Limpeza do Projecto*
