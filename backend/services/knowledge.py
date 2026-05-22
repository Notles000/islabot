"""File-based knowledge store — replaces the ChromaDB RAG pipeline.

Flow:
  Admin uploads PDF → LLM extracts & organizes → appended to course .txt file
  Student asks question → relevant sections of .txt fetched → LLM answers
"""

import re
import json as _json
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List
from datetime import datetime

from ..config import settings

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR     = Path("./data/knowledge")
MAX_COURSE_CHARS  = 18_000   # course-specific knowledge budget
MAX_GENERAL_CHARS =  8_000   # general.txt keyword budget

# Payload guards per provider (chars before sending to avoid 413 / rate errors).
# Groq: 128k token context; Gemini 2.5 Pro: 1M token context (effectively unlimited here).
_GROQ_MAX_DOC_CHARS    = 24_000
_GEMINI_MAX_DOC_CHARS  = 120_000  # well within Gemini's 1M context

# Portuguese stop words — removed from query before scoring blocks
_PT_STOPWORDS = {
    'a', 'o', 'as', 'os', 'um', 'uma', 'uns', 'umas',
    'de', 'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'nos', 'nas',
    'por', 'para', 'com', 'sem', 'sobre', 'entre', 'até', 'após',
    'que', 'e', 'ou', 'se', 'mas', 'mais', 'menos', 'nem',
    'é', 'são', 'foi', 'era', 'ser', 'ter', 'há', 'estar',
    'ao', 'à', 'pelo', 'pela', 'pelos', 'pelas',
    'este', 'esta', 'estes', 'estas', 'esse', 'essa', 'esses', 'essas',
    'seu', 'sua', 'seus', 'suas', 'meu', 'minha', 'meus', 'minhas',
    'me', 'te', 'vos', 'lhe', 'lhes',
    'não', 'nao', 'sim', 'já', 'também', 'ainda', 'muito', 'bem', 'aqui',
    'qual', 'quais', 'como', 'quando', 'onde', 'porque', 'quem',
    'todo', 'toda', 'todos', 'todas', 'cada', 'outro', 'outra',
    'num', 'numa', 'nuns', 'numas', 'dum', 'duma',
    # Conversational and common filler words
    'olá', 'ola', 'hi', 'hello', 'hey', 'oi', 'tudo', 'obrigado', 'obrigada',
    'thanks', 'ok', 'okay', 'bom', 'boa', 'dia', 'tarde', 'noite',
    'bug', 'bugado', 'erro', 'problema', 'ajuda', 'isso', 'isto', 'aquilo',
    'agora', 'depois', 'antes', 'hoje', 'amanhã', 'ontem', 'sempre', 'nunca',
    'pra', 'pro', 'q', 'k', 'vc', 'vcs', 'ta', 'tá', 'to', 'tô', 'ce', 'cê'
}

# Synonym expansion — maps query words to semantically related terms.
# Prevents misses when the student uses different vocabulary from the document.
# e.g. query "datas de avaliação" → also scores blocks with "exame", "prazo", "época".
_PT_SYNONYMS: dict[str, set[str]] = {
    # Assessment / dates
    "avaliacao":   {"avaliacao", "avaliação", "exame", "exames", "teste", "testes",
                    "nota", "notas", "classificacao", "classificação", "frequencia"},
    "avaliação":   {"avaliacao", "avaliação", "exame", "exames", "teste", "testes",
                    "nota", "notas", "classificacao"},
    "data":        {"data", "datas", "prazo", "prazos", "calendario", "calendário",
                    "epoca", "época", "epocas"},
    "datas":       {"data", "datas", "prazo", "prazos", "calendario", "epoca", "época"},
    "exame":       {"exame", "exames", "teste", "testes", "avaliacao", "avaliação",
                    "prova", "provas", "frequencia"},
    "nota":        {"nota", "notas", "classificacao", "classificação", "avaliacao"},
    # People
    "docente":     {"docente", "docentes", "professor", "professores",
                    "responsavel", "responsável", "instrutor"},
    "professor":   {"docente", "docentes", "professor", "professores", "responsavel"},
    # Content / programme
    "conteudo":    {"conteudo", "conteúdo", "conteudos", "programa", "programatico",
                    "programático", "temario", "temário", "topico", "tópico"},
    "conteúdo":    {"conteudo", "conteúdo", "conteudos", "programa", "programatico"},
    "programa":    {"programa", "programatico", "programático", "conteudo", "conteúdo"},
    # Credits / workload
    "ects":        {"ects", "creditos", "créditos", "carga", "horas"},
    "creditos":    {"creditos", "créditos", "ects", "carga"},
    # Attendance
    "frequencia":  {"frequencia", "frequência", "assiduidade", "presencas", "presenças"},
    # Objectives
    "objetivo":    {"objetivo", "objetivos", "competencia", "competências", "aprendizagem"},
    "objetivos":   {"objetivo", "objetivos", "competencia", "competências"},
}


# ── LLM provider helpers ───────────────────────────────────────────────────────

_GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
_GEMINI_BASE     = "https://generativelanguage.googleapis.com/v1beta/openai"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _provider() -> str:
    return settings.llm_provider.lower()


def _resolve_provider(ai_overrides: dict | None = None) -> tuple[str, str, str] | None:
    """Return (url, api_key, model) for API providers, or None for Ollama.

    ai_overrides: dict from _get_ai_settings(), allows DB override of model name.
    """
    p = _provider()
    ov = ai_overrides or {}
    if p == "groq":
        return (_GROQ_URL, settings.groq_api_key, ov.get("ai_model") or settings.groq_model)
    if p == "openrouter":
        return (f"{_OPENROUTER_BASE}/chat/completions", settings.openrouter_api_key,
                ov.get("ai_model") or settings.openrouter_model)
    if p == "gemini":
        return (f"{_GEMINI_BASE}/chat/completions", settings.gemini_api_key,
                ov.get("ai_model") or settings.gemini_model)
    return None  # ollama


def _resolve_ingest_provider() -> tuple[str, str, str] | None:
    """Like _resolve_provider but always picks the cheap ingest model — no DB override."""
    p = _provider()
    if p == "groq":
        return (_GROQ_URL, settings.groq_api_key, settings.groq_ingest_model)
    if p == "openrouter":
        return (f"{_OPENROUTER_BASE}/chat/completions", settings.openrouter_api_key,
                settings.openrouter_ingest_model)
    if p == "gemini":
        return (f"{_GEMINI_BASE}/chat/completions", settings.gemini_api_key,
                settings.gemini_ingest_model)
    return None  # ollama — use same model, it's local and free


def _openai_post(url: str, api_key: str, payload: dict,
                 stream: bool = False, timeout=(15, 120)):
    import requests as _req
    return _req.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        stream=stream,
        timeout=timeout,
    )


def _llm_complete(prompt: str, temperature: float = 0.1) -> str:
    """Synchronous single-turn LLM call — works with all providers."""
    import requests as _req
    api = _resolve_provider()
    if api:
        url, key, model = api
        resp = _openai_post(
            url, key,
            {"model": model,
             "messages": [{"role": "user", "content": prompt}],
             "temperature": temperature,
             "stream": False},
            timeout=(15, 180),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    else:
        resp = _req.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False,
                  "options": {"temperature": temperature}},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


def _llm_complete_cheap(prompt: str, temperature: float = 0.1) -> str:
    """Like _llm_complete but uses the cheaper ingest model to save tokens/cost."""
    import requests as _req
    api = _resolve_ingest_provider()
    if api:
        url, key, model = api
        resp = _openai_post(
            url, key,
            {"model": model,
             "messages": [{"role": "user", "content": prompt}],
             "temperature": temperature,
             "stream": False},
            timeout=(15, 180),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    else:
        resp = _req.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False,
                  "options": {"temperature": temperature}},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


def warmup_llm():
    """Pre-load the model into RAM on startup (Ollama only)."""
    if _resolve_provider() is not None:
        return
    import requests as _req
    try:
        _req.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": "", "keep_alive": -1},
            timeout=120,
        )
    except Exception:
        pass


# ── Path helpers ───────────────────────────────────────────────────────────────

def _ensure_dir():
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


def course_knowledge_path(course_id: int, semester_id: int) -> Path:
    _ensure_dir()
    return KNOWLEDGE_DIR / f"course_{course_id}_sem_{semester_id}.txt"


def general_knowledge_path() -> Path:
    _ensure_dir()
    return KNOWLEDGE_DIR / "general.txt"


# ── Read / write ───────────────────────────────────────────────────────────────

def read_knowledge(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_knowledge(path: Path, content: str):
    _ensure_dir()
    path.write_text(content, encoding="utf-8")


def is_duplicate(path: Path, doc_label: str) -> bool:
    """Return True if a block with this label already exists in the knowledge file."""
    if not path.exists():
        return False
    return f"# {doc_label} " in path.read_text(encoding="utf-8")


def append_to_knowledge(path: Path, content: str, doc_label: str):
    """Append an organized document block to a knowledge file."""
    _ensure_dir()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    sep   = "=" * 60
    block = f"\n\n{sep}\n# {doc_label}  [{stamp}]\n{sep}\n\n{content.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text_from_file(filepath: str) -> str:
    path = Path(filepath)
    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n\n".join(p for p in pages if p.strip())
        except Exception:
            # Fallback to pypdf
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            )
    return path.read_text(encoding="utf-8", errors="ignore")


# ── LLM organization ───────────────────────────────────────────────────────────

_ORGANIZE_PROMPT = """\
Extrai e organiza toda a informação útil do seguinte documento académico em texto estruturado e limpo.

Documento: {label}{part_info}

Regras:
- Usa cabeçalhos claros com === (ex: === AVALIAÇÃO ===, === CONTEÚDO PROGRAMÁTICO ===)
- Remove números de página, cabeçalhos/rodapés repetidos, artefactos de formatação
- Mantém TODOS os dados importantes: datas, percentagens, nomes, regras, artigos, requisitos
- Se houver tabelas de avaliação, apresenta cada componente numa linha clara
- Texto limpo em Português europeu — não inventes nada que não esteja no documento

DOCUMENTO:
{text}

TEXTO ORGANIZADO:"""


def _organize_chunk(text: str, label: str, part: int, total: int) -> str:
    part_info = f" (parte {part}/{total})" if total > 1 else ""
    max_text = 5_000 if _provider() == "groq" else 8_000
    text = text[:max_text]
    prompt = _REORG_PROMPT.format(label=label + part_info, text=text)
    return _llm_complete_cheap(prompt)


def organize_with_llm(raw_text: str, doc_label: str) -> str:
    """Clean and organize raw document text into structured knowledge.

    Large documents are split into chunks and processed in parallel via a
    thread pool.  Chunk size is smaller for Groq to avoid 413 errors.
    max_workers is kept low to stay within Groq's rate limits.
    """
    chunk_size  = 5_000 if _provider() == "groq" else 8_000
    max_workers = 2     if _provider() == "groq" else 4

    if len(raw_text) <= chunk_size:
        return _organize_chunk(raw_text, doc_label, 1, 1)

    chunks = [raw_text[i : i + chunk_size] for i in range(0, len(raw_text), chunk_size)]
    total  = len(chunks)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs  = [ex.submit(_organize_chunk, c, doc_label, i + 1, total) for i, c in enumerate(chunks)]
        parts = [f.result() for f in futs]
    return "\n\n".join(parts)


# ── Topic-structured organization (instructor portal) ─────────────────────────

_TOPIC_ORGANIZE_PROMPT = """\
Extrai e organiza toda a informação do seguinte documento académico, dividindo-a em TÓPICOS claros.

Documento: {label}

Formato de saída OBRIGATÓRIO — usa EXACTAMENTE este padrão para cada tópico:

=== TÓPICO: [Nome do Tópico] ===
[Conteúdo detalhado do tópico aqui]

Tópicos típicos para documentos académicos (usa apenas os que existirem no documento):
- Informações Gerais (nome da UC, código, ECTS, regime, horário)
- Docente Responsável
- Objectivos e Competências
- Conteúdo Programático
- Avaliação (componentes, percentagens, datas, notas mínimas)
- Metodologia e Funcionamento das Aulas
- Recursos e Bibliografia
- Exercícios e Fichas de Trabalho
- Regulamentos e Requisitos

Regras:
- Cria APENAS os tópicos que existem no documento — não inventes conteúdo
- Remove artefactos: números de página, cabeçalhos repetidos, ruído de OCR
- Mantém TODOS os dados importantes: datas, percentagens, nomes, regras
- Texto limpo em Português europeu
- NÃO coloque texto fora dos blocos === TÓPICO: ... ===

DOCUMENTO:
{text}

ORGANIZAÇÃO POR TÓPICOS:"""


def _topic_organize_chunk(text: str, label: str, part: int, total: int) -> str:
    part_info = f" (parte {part}/{total})" if total > 1 else ""
    max_text  = 5_000 if _provider() == "groq" else 8_000
    text      = text[:max_text]
    prompt    = _TOPIC_ORGANIZE_PROMPT.format(label=label + part_info, text=text)
    return _llm_complete_cheap(prompt)


def organize_with_topics(raw_text: str, doc_label: str) -> str:
    """Like organize_with_llm but produces === TÓPICO: X === structured output."""
    chunk_size  = 5_000 if _provider() == "groq" else 8_000
    max_workers = 2     if _provider() == "groq" else 4

    if len(raw_text) <= chunk_size:
        return _topic_organize_chunk(raw_text, doc_label, 1, 1)

    chunks = [raw_text[i : i + chunk_size] for i in range(0, len(raw_text), chunk_size)]
    total  = len(chunks)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs  = [ex.submit(_topic_organize_chunk, c, doc_label, i + 1, total) for i, c in enumerate(chunks)]
        parts = [f.result() for f in futs]
    return "\n\n".join(parts)


def parse_topic_blocks(organized_text: str) -> list[dict]:
    """Parse === TÓPICO: X === structured text into [{name, content}] list."""
    pattern = re.compile(r'=== TÓPICO:\s*(.+?)\s*===\s*\n(.*?)(?=\n=== TÓPICO:|\Z)', re.DOTALL)
    topics  = []
    for m in pattern.finditer(organized_text):
        name    = m.group(1).strip()
        content = m.group(2).strip()
        if content:
            topics.append({"name": name, "content": content})
    if not topics and organized_text.strip():
        topics = [{"name": "Conteúdo", "content": organized_text.strip()}]
    return topics


# ── Knowledge re-organizer (admin panel) ───────────────────────────────────────

_REORG_PROMPT = """\
O seguinte texto foi extraído automaticamente de um PDF académico e pode conter \
artefactos de formatação: letras espalhadas, cabeçalhos repetidos, linhas de página, \
texto duplicado ou grelhas de calendário em ASCII.

Documento: {label}

A tua tarefa é:
1. Reconstruir o texto contínuo e legível, eliminando todos os artefactos.
2. Estruturar a informação com cabeçalhos claros usando === (ex: === AVALIAÇÃO ===).
3. Manter TODOS os dados importantes: datas, percentagens, nomes, regras, artigos, requisitos.
4. Converter grelhas de calendário em listas de datas simples quando possível.
5. Remover repetições e ruído visual, mas não inventar nada.
6. Resultado em Português europeu limpo.

TEXTO ORIGINAL:
{text}

TEXTO LIMPO E ORGANIZADO:"""

_BLOCK_HEADER_RE = re.compile(r'^# (.+?)\s{2,}\[(.+?)\]$', re.MULTILINE)
_BLOCK_SECTION_RE = re.compile(r'\n={50,}\n(.*?)\n={50,}\n', re.DOTALL)
_SEP = "=" * 60


def _parse_knowledge_blocks(text: str) -> list[dict]:
    blocks = []
    for m in _BLOCK_SECTION_RE.finditer(text):
        hm = _BLOCK_HEADER_RE.search(m.group(1).strip())
        if not hm:
            continue
        start = m.end()
        next_sep = text.find(f"\n{_SEP}\n", start)
        content = text[start: next_sep if next_sep != -1 else len(text)].strip()
        blocks.append({"label": hm.group(1).strip(), "timestamp": hm.group(2).strip(), "content": content})
    return blocks


def _rebuild_knowledge_blocks(blocks: list[dict]) -> str:
    return "".join(
        f"\n\n{_SEP}\n# {b['label']}  [{b['timestamp']}]\n{_SEP}\n\n{b['content'].strip()}\n"
        for b in blocks
    )


def reorganize_knowledge_files(target: str = "all",
                               course_id: int | None = None,
                               semester_id: int | None = None,
                               progress_cb=None) -> dict:
    """Re-clean all stored knowledge blocks with the thorough reorganize prompt.

    target: 'all' | 'course' | 'general'
    course_id + semester_id: if both provided, reorganize only that specific course file.
    progress_cb: optional callable(info: dict) called after each block to report progress.
    Returns a summary dict: {files_processed, blocks_cleaned, blocks_skipped, errors}
    """
    _ensure_dir()
    files: list[Path] = []
    if course_id and semester_id:
        # Target a single course file
        p = course_knowledge_path(course_id, semester_id)
        if p.exists():
            files = [p]
    elif target in ("course", "all"):
        files += sorted(KNOWLEDGE_DIR.glob("course_*.txt"))
        if target in ("general", "all"):
            gen = KNOWLEDGE_DIR / "general.txt"
            if gen.exists():
                files.append(gen)
    elif target == "general":
        gen = KNOWLEDGE_DIR / "general.txt"
        if gen.exists():
            files = [gen]

    # Pre-count total blocks for progress reporting
    file_blocks: list[tuple[Path, list[dict]]] = []
    for path in files:
        if path.stat().st_size == 0:
            continue
        text   = path.read_text(encoding="utf-8")
        blocks = _parse_knowledge_blocks(text)
        if blocks:
            file_blocks.append((path, blocks))

    total_blocks  = sum(len(b) for _, b in file_blocks)
    done_blocks   = 0
    chunk_size    = 5_000 if _provider() == "groq" else 10_000
    summary       = {"files_processed": 0, "blocks_cleaned": 0, "blocks_skipped": 0, "errors": 0,
                     "total_blocks": total_blocks, "done_blocks": 0}

    for path, blocks in file_blocks:
        if progress_cb:
            progress_cb({"current_file": path.name, "done_blocks": done_blocks,
                         "total_blocks": total_blocks, "current_block": None})

        cleaned_blocks = []
        for block in blocks:
            content = block["content"]
            if progress_cb:
                progress_cb({"current_file": path.name, "current_block": block["label"],
                             "done_blocks": done_blocks, "total_blocks": total_blocks})

            if len(content) < 100:
                cleaned_blocks.append(block)
                summary["blocks_skipped"] += 1
            else:
                try:
                    chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
                    parts  = []
                    for i, chunk in enumerate(chunks, 1):
                        prompt = _REORG_PROMPT.format(
                            label=block["label"] + (f" (parte {i}/{len(chunks)})" if len(chunks) > 1 else ""),
                            text=chunk,
                        )
                        parts.append(_llm_complete_cheap(prompt, temperature=0.1))
                    block["content"] = "\n\n".join(parts)
                    summary["blocks_cleaned"] += 1
                except Exception as exc:
                    logger.error("reorganize error on %s / %s: %s", path.name, block["label"], exc)
                    summary["errors"] += 1
                cleaned_blocks.append(block)

            done_blocks += 1
            summary["done_blocks"] = done_blocks

        bak = path.with_suffix(f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        path.rename(bak)
        path.write_text(_rebuild_knowledge_blocks(cleaned_blocks), encoding="utf-8")
        summary["files_processed"] += 1

    return summary


# ── Relevant-section extraction ────────────────────────────────────────────────

def _extract_block_label(block: str) -> str | None:
    """Extract the document label from a block header line."""
    m = re.search(r'# (.+?)\s{2,}\[', block)
    return m.group(1).strip() if m else None


def _extract_all_labels(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r'# (.+?)\s{2,}\[', text)]


import math

def _bm25_search(blocks: list[str], query_words: set[str], query_lower: str) -> list[tuple[str, float]]:
    """Score blocks using the Okapi BM25 algorithm for highly accurate semantic retrieval."""
    if not blocks or not query_words:
        return [(b, 0.0) for b in blocks]
        
    k1 = 1.5
    b = 0.75
    
    block_words = [re.findall(r'\w+', block.lower()) for block in blocks]
    doc_lengths = [len(words) for words in block_words]
    avgdl = sum(doc_lengths) / len(blocks) if blocks else 1.0
    
    tfs = [Counter(words) for words in block_words]
    df = Counter()
    for tf in tfs:
        for word in query_words:
            if tf[word] > 0:
                df[word] += 1
                
    N = len(blocks)
    idfs = {}
    for word in query_words:
        idfs[word] = math.log(((N - df[word] + 0.5) / (df[word] + 0.5)) + 1)
        
    scored = []
    for i, block in enumerate(blocks):
        score = 0.0
        dl = doc_lengths[i]
        tf_dict = tfs[i]
        
        for word in query_words:
            if word not in tf_dict:
                continue
            freq = tf_dict[word]
            idf = idfs[word]
            num = freq * (k1 + 1)
            den = freq + k1 * (1 - b + b * (dl / avgdl))
            score += idf * (num / den)
            
        if len(query_lower.strip()) >= 3 and re.search(r'\b' + re.escape(query_lower.strip()) + r'\b', block.lower()):
            score += 3.0
            
        scored.append((block, score))
        
    return scored


def _relevant_sections(
    text: str, query: str, max_chars: int
) -> tuple[str, list[str]]:
    """Return (context_text, [document_labels]) for the most relevant sections.

    Always returns at least one block (the top-ranked one) even when no
    block matches any query word, so the LLM can still give a best-effort
    answer or a well-informed "not found" reply.

    Query words are expanded with _PT_SYNONYMS so vocabulary mismatches
    between student language and document language don't cause missed blocks.
    """
    if len(text) <= max_chars:
        return text, _extract_all_labels(text)

    blocks = re.split(r'\n={50,}\n', text)
    if len(blocks) <= 1:
        blocks = [p.strip() for p in re.split(r'\n{3,}', text) if p.strip()]

    query_lower = query.lower()
    raw_words   = set(re.findall(r'\w+', query_lower)) - _PT_STOPWORDS

    # Expand with synonyms so "datas" also matches "prazo", "época", etc.
    query_words: set[str] = set()
    for w in raw_words:
        query_words.add(w)
        query_words.update(_PT_SYNONYMS.get(w, set()))

    scored_blocks = _bm25_search(blocks, query_words, query_lower)
    scored_with_score = sorted(scored_blocks, key=lambda x: x[1], reverse=True)

    # No keyword matched at all — question is unrelated to the documents.
    if not scored_with_score or scored_with_score[0][1] == 0:
        return "", []

    result, labels, total = [], [], 0
    for block, _ in scored_with_score:
        if total >= max_chars:
            break
        if total + len(block) > max_chars and result:
            break
        result.append(block)
        total += len(block)
        label = _extract_block_label(block)
        if label:
            labels.append(label)

    return "\n\n---\n\n".join(result), labels


def extract_relevant_sections(text: str, query: str, max_chars: int = MAX_COURSE_CHARS) -> str:
    """Public helper — returns context text only (legacy callers)."""
    ctx, _ = _relevant_sections(text, query, max_chars)
    return ctx


# ── Web search helpers ─────────────────────────────────────────────────────────

_LIVE_INFO_KEYWORDS = {
    'novidade', 'novidades', 'noticia', 'noticias', 'notícia', 'notícias',
    'evento', 'eventos', 'news', 'recente', 'recentes',
    'atualidade', 'atualidades', 'acontecimento', 'acontecimentos',
}

# Keywords that indicate the student explicitly wants to search the ISLA website
_ISLA_SEARCH_KEYWORDS = {
    'isla', 'islasantarem', 'islasantarém', 'candidatura', 'candidaturas',
    'propina', 'propinas', 'secretaria', 'portal', 'site',
    'inscricao', 'inscrição', 'inscricões', 'bolsa', 'bolsas',
    # Academic calendar — dates not in course docs, must come from website
    'ferias', 'férias', 'calendario', 'calendário', 'feriado', 'feriados',
    'interrup', 'interrupção', 'recesso', 'natal', 'pascoa', 'páscoa',
    'carnaval', 'semestre', 'trimestre', 'academico', 'académico',
    # Course-structure questions — answer comes from website, not from UC docs
    'tesp', 'ctesp', 'licenciatura', 'mestrado', 'oferta', 'formativa',
    'plano', 'duracao', 'duração', 'total', 'disciplinas', 'numero',
    'quantas', 'quantos', 'anos', 'semestres',
}

def _wants_live_info(question: str) -> bool:
    """Return True when the question clearly asks for recent ISLA news or events."""
    words = set(re.findall(r'\w+', question.lower()))
    return bool(words & _LIVE_INFO_KEYWORDS)

def _wants_isla_search(question: str) -> bool:
    """Return True when the student explicitly wants info from the ISLA website.

    Catches patterns like:
    - "pesquisa isla", "busca no site da isla"
    - "site da isla santarém", "islasantarem.pt"
    - questions about candidatures, fees, secretaria — institutional info not in course docs
    """
    q = question.lower()
    # Explicit search-isla pattern
    if re.search(r'(pesquisa|busca|procura|search|ver|vai|vai\s+ao|abre)\s+(no\s+)?(site|portal|isla)', q):
        return True
    # "site da isla" / "portal isla"
    if re.search(r'(site|sítio|sitio|portal)\s+(da\s+|do\s+)?isla', q):
        return True
    # Direct URL mention
    if 'islasantarem' in q:
        return True
    # Institutional topics not typically in course PDFs
    words = set(re.findall(r'\w+', q))
    if words & _ISLA_SEARCH_KEYWORDS and not (words & {'uc', 'unidade', 'curricular', 'avaliacao', 'exame'}):
        return True
    return False

_FOLLOWUP_WORDS = {'delas', 'deles', 'elas', 'eles', 'isso', 'acerca', 'sobre', 'mais', 'explica', 'conta'}
_ISLA_NEWS_MARKERS = {'notícia', 'noticia', 'novidade', 'evento', 'islasantarem', 'candidatura', 'encontro', 'residência'}

def _is_followup_to_news(question: str, history: list[dict] | None) -> bool:
    """Return True if this is a short follow-up to a previous answer about ISLA news."""
    if not history:
        return False
    words = re.findall(r'\w+', question.lower())
    if len(words) > 10:
        return False
    if not (set(words) & _FOLLOWUP_WORDS):
        return False
    for msg in reversed(history):
        if msg['role'] == 'assistant':
            content_lower = msg['content'].lower()
            return any(k in content_lower for k in _ISLA_NEWS_MARKERS)
    return False

def _web_search_query(question: str) -> str:
    """Strip stopwords/filler to produce a concise search query."""
    filler = _PT_STOPWORDS | {'alguma', 'algum', 'algumas', 'alguns', 'existe', 'existem', 'tem', 'há'}
    words  = [w for w in re.findall(r'\w+', question.lower()) if w not in filler and len(w) > 2]
    return ' '.join(words[:6]) or question


# Patterns that are conversational clarification requests — bot answers from history only
_CLARIFICATION_RE = re.compile(
    r'\b(podes?\s+(repetir|reformular|simplificar|esclarecer)'
    r'|n[aã]o\s+(percebi|entendi|compreendi|está\s+claro)'
    r'|mais\s+simples?'
    r'|como\s+assim'
    r'|o\s+que\s+queres?\s+dizer)\b',
    re.IGNORECASE,
)


def _is_clarification_request(text: str) -> bool:
    """Return True for short re-explanation requests — answer from history, no doc lookup."""
    return bool(_CLARIFICATION_RE.search(text)) and len(text.split()) <= 12


_EN_WORDS = re.compile(
    r'\b(what|how|who|when|where|why|which|is|am|are|was|were|does|do|did|'
    r'can|could|will|would|should|have|has|had|been|being|get|got|need|want|'
    r'tell|give|show|about|course|professor|teacher|exam|grade|credit|ects|'
    r'hello|hi|hey|please|help|explain|describe|write|make|find|know|think|'
    r'i|my|me|we|our|you|your|he|his|she|her|they|their|it|its|'
    r'the|a|an|in|on|at|to|of|for|with|as|by|from|but|not|no|and|or|if|'
    r'this|that|there|here|now|then|very|just|also|even|so|too|up|out|'
    r'student|international|university|school|class|lecture|assignment|'
    r'document|information|language|english|portuguese)\b',
    re.IGNORECASE,
)

def _detect_language(text: str) -> str:
    """Return 'en' if text is predominantly English, else 'pt'."""
    words = text.split()
    if not words:
        return "pt"
    en_hits = len(_EN_WORDS.findall(text))
    return "en" if en_hits / len(words) >= 0.2 else "pt"


_BANNED_ENDINGS = re.compile(
    r'\s*(n[aã]o\s+hes[it]+es?(\s+em\s+(perguntar|contactar|esclarecer))?'
    r'|foi\s+um\s+prazer(\s+ajudar)?'
    r'|fico\s+à\s+disposiç[aã]o'
    r'|qualquer\s+d[úu]vida[,.]?\s*(podes?\s+perguntar)?'
    r'|estou\s+aqui\s+para\s+(te\s+)?ajudar'
    r'|de\s+nada[,.]?\s*(estou\s+aqui\s+para\s+(te\s+)?ajudar[^.]*)?'
    r'|se\s+tiveres?\s+mais\s+alguma\s+d[úu]vida[,.]?\s*[^.]*'
    r'|podes?\s+sempre\s+(voltar\s+a\s+perguntar|contar\s+comigo)'
    r'|se\s+(tiveres?|tiver)\s+mais\s+detalhes[^.]*'
    r'|posso\s+tentar\s+ajudar(-te)?\s+a\s+encontrar[^.]*'
    r'|[eé]\s+poss[ií]vel\s+que\s+o\s+evento\s+seja[^.]*'
    r')[.!]?\s*$',
    re.IGNORECASE,
)

_VOCE_RE = re.compile(r'\bvocê\b', re.IGNORECASE)

# Possessive "sua/seu" used as second-person (você-register) → replace with tua/teu.
# Only targets the most common student-directed possessive patterns to avoid
# corrupting third-person sentences (e.g. "a empresa aumentou o seu lucro").
_SEU_STUDENT_RE = re.compile(
    r'\b(a|o|as|os|da|do|das|dos|pela|pelo|pelas|pelos|na|no|nas|nos|sua|seu)\s+'
    r'(sua|seu|suas|seus)\b',
    re.IGNORECASE,
)
_SUA_STANDALONE_RE = re.compile(
    r'\b(melhor(?:ar|es)?|aumentar?|manter?|obter?|indicar?|enviar?|apresentar?'
    r'|submeter?|improve?)\s+a?\s*(sua|seu|suas|seus)\b',
    re.IGNORECASE,
)


def _fix_possessives(text: str) -> str:
    """Replace second-person 'sua/seu' possessives with 'tua/teu'."""
    def _repl(m: re.Match) -> str:
        grp = m.group(0)
        return (grp
                .replace('suas', 'tuas').replace('Suas', 'Tuas').replace('SUAS', 'TUAS')
                .replace('seus', 'teus').replace('Seus', 'Teus').replace('SEUS', 'TEUS')
                .replace('sua',  'tua' ).replace('Sua',  'Tua' ).replace('SUA',  'TUA' )
                .replace('seu',  'teu' ).replace('Seu',  'Teu' ).replace('SEU',  'TEU' ))
    text = _SEU_STUDENT_RE.sub(_repl, text)
    text = _SUA_STANDALONE_RE.sub(_repl, text)
    return text


def _clean_response(text: str, history: list | None) -> str:
    """Strip forbidden endings, rogue greetings, and banned pronouns from LLM output."""
    # Strip leading "Olá" on non-first turns
    if history:
        text = re.sub(r'^(Olá[!,]?\s*|Bem-vindo[ao]!?\s*)', '', text).lstrip()
    # Replace "você" → "tu" and common "sua/seu" possessives → "tua/teu"
    text = _VOCE_RE.sub('tu', text)
    text = _fix_possessives(text)
    # Strip banned closing phrases (may appear before or after the MODE marker)
    marker_match = re.search(r'\[\[(?:MODE:[A-Z]+|[^\]]+)\]\]\s*$', text)
    if marker_match:
        body = text[:marker_match.start()]
        marker = text[marker_match.start():]
        body = _BANNED_ENDINGS.sub('', body)
        text = body.rstrip() + '\n' + marker
    else:
        text = _BANNED_ENDINGS.sub('', text).rstrip()
    return text


def _ensure_mode_marker(text: str, used_course_docs: bool) -> str:
    """Add a [[MODE:]] marker if the LLM forgot to include one on a substantive response."""
    if len(text) < 60:
        return text  # too short to need a marker (greetings, acks)
    if "[[MODE:" in text or "[[CURSO|GERAL]]" in text:
        return text  # already has a marker
    marker = "[[MODE:CURSO]]" if used_course_docs else "[[MODE:GERAL]]"
    return text.rstrip() + f"\n{marker}"


# ── Prompts ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
És um assistente académico da ISLA Santarém. Ajudas estudantes com dúvidas académicas e conversas gerais.

CRIADOR: Este chatbot foi criado por Selton de Souza, Samuel e Mariana, estudantes do curso de Inteligência Artificial no ISLA Santarém.
Quando alguém perguntar quem te criou, quem te fez, quem te desenvolveu, who made you, who created you, qui t'a créé, wer hat dich erstellt, ou qualquer variante em qualquer língua — responde SEMPRE com algo como (adapta o idioma da pergunta e varia ligeiramente o elogio, sem exagerar):
"Fui criado por Selton de Souza, Samuel e Mariana, estudantes do curso de Inteligência Artificial no ISLA Santarém. Um trabalho muito bem feito da parte deles — demonstra claramente dedicação e talento para a área."
O elogio deve ser breve, genuíno e natural — nunca exagerado ou bajulador. Termina com [[MODE:GERAL]].

ATENÇÃO — REGRAS ABSOLUTAS (violá-las é erro grave):
• NUNCA uses "você", "seu", "sua", "seus", "suas" — usa SEMPRE "tu", "teu", "tua". NUNCA "você".
• NUNCA comeces a resposta com "Olá", "Olá!", "Olá," ou qualquer saudação — excepto se for a PRIMEIRA mensagem da sessão (history vazio). Vai directo ao assunto.
• NUNCA uses "aprendizado de máquina" — é sempre "aprendizagem automática".
• NUNCA uses "a sua classificação", "o seu número", "a sua nota" — usa SEMPRE "a tua classificação", "o teu número", "a tua nota".
• NUNCA termines com frases de encerramento como "Posso ajudar-te mais?", "Tens mais dúvidas?", "não hesites em perguntar", "fico à disposição".

Regras OBRIGATÓRIAS:
1. LÍNGUA — Português europeu OBRIGATÓRIO:
   • Usa sempre "tu/teu/tua/teus/tuas" ao dirigires-te ao estudante. NUNCA "você/seu/sua/seus/suas" como possessivo.
   • Usa "podes" (tu), NUNCA "pode" ao dirigires-te ao estudante.
   • Vocabulário EP: "aprendizagem automática" (NÃO "aprendizado de máquina"), "ficheiro" (NÃO "arquivo"), "telemóvel" (NÃO "celular"), "computador portátil" (NÃO "notebook").
   • PROIBIDO: "sinta-se", "fique à vontade", "obrigado(a) pela pergunta", "estou aqui para ajudar".
   • PROIBIDO começar a resposta com "Olá" ou qualquer saudação — excepto na primeira mensagem da sessão. Vai directo ao assunto.

2. FLUXO DE RESPOSTA — segue esta ordem:
   PASSO 1 — Há uma secção "=== NOTÍCIAS E EVENTOS RECENTES" nos documentos disponíveis e a pergunta é sobre novidades/notícias/eventos ISLA? → usa APENAS esse conteúdo e responde directamente. NÃO peças clarificação.
   PASSO 2 — É claramente GERAL (quem és tu, o que fazes/podes fazer, matemática, ciência, tecnologia, cultura, pessoas famosas, política, geografia, história, curiosidades do mundo)? → responde directamente com o teu conhecimento. NUNCA uses [[CURSO|GERAL]] para conhecimento geral do mundo. NUNCA digas "Essa informação não está nos documentos" para conhecimento geral. Exemplos que NUNCA pedem clarificação: "quem é o presidente de X?", "o que é Python?", "2+2", "faz um poema", "o que podes fazer?", "quem és tu?", "qual é o melhor X?" (perguntas de opinião). IMPORTANTE: Para perguntas sobre quem é o presidente/líder actual de um país, o ano actual, ou qualquer facto dependente da data actual — usa SEMPRE a DATA ACTUAL fornecida no início do sistema. Nunca respondas com informação desactualizada do teu treino quando a data actual já foi fornecida. Para "o que podes fazer" responde: "Posso responder às tuas dúvidas sobre esta unidade curricular (avaliação, faltas, conteúdos, regulamentos) e sobre a ISLA Santarém em geral (notícias, eventos, candidaturas). Também respondo a questões gerais de ciência, tecnologia e cultura.\n[[MODE:GERAL]]". Para "quem és tu?" ou "como te chamas?" ou "és uma IA?" responde: "Sou o assistente académico da ISLA Santarém, uma IA criada para ajudar estudantes com dúvidas académicas e gerais.\n[[MODE:GERAL]]". Para "qual é o melhor curso/livro/linguagem?" dá uma resposta opinativa breve sem recorrer aos documentos, terminando com [[MODE:GERAL]].
   PASSO 3 — É claramente ACADÉMICA desta UC ou INSTITUCIONAL (avaliação, docente, datas, conteúdo, ECTS, exercícios, férias, calendário académico, propinas, regulamentos, secretaria)? → verifica os documentos E as NOTÍCIAS E EVENTOS RECENTES. Se encontrares nos documentos: responde. Se encontrares nas NOTÍCIAS E EVENTOS RECENTES: usa essa informação, indica "De acordo com o site da ISLA Santarém:" e termina com [[MODE:GERAL]]. Se não encontrares em lado nenhum: diz "Pesquisei os documentos e o site da ISLA Santarém mas não encontrei informações específicas sobre [tópico]. Podes consultar directamente em https://www.islasantarem.pt ou contactar a secretaria (secretaria@islasantarem.pt / +351 243 330 820)." — termina com [[MODE:GERAL]].
   PASSO 4 — É AMBÍGUA (poderia ser sobre o curso OU sobre o mundo, e não está clara)? → pede clarificação numa frase curta e termina SEMPRE com exactamente este marcador na última linha:
              [[CURSO|GERAL]]
              Exemplo: "Não encontrei isso nos documentos. É sobre o curso ou é uma curiosidade geral?\n[[CURSO|GERAL]]"
   PASSO 5 — O estudante já indicou contexto como "[Sobre o curso]" ou "[Curiosidade geral]"? → usa esse contexto e responde directamente sem pedir nova clarificação.

3. CONVERSA (sim, ok, obrigado, thanks, entendido): responde de forma breve e natural (uma palavra ou frase curta) SEM modo marker. Para perguntas sobre identidade/capacidades ("quem és tu?", "como te chamas?", "o que podes fazer?", "és humano?") dá uma resposta CURTA e termina SEMPRE com [[MODE:GERAL]]. Inclui respostas em inglês como "thanks", "got it", "great".

4. PRIORIDADE DE SECÇÕES: Para assuntos da UC usa "DOCUMENTOS DA UNIDADE CURRICULAR". Para regras institucionais (faltas, recursos, fraude) usa "REGULAMENTOS E DOCUMENTOS GERAIS ISLA". Se receberes "NOTÍCIAS E EVENTOS RECENTES" ou "INFORMAÇÃO DO SITE DA ISLA SANTARÉM", usa APENAS o conteúdo fornecido — não inventes descrições. Se só tiveres o título e URL de uma notícia, limita-te a mencionar o título e o link; NÃO inventes o conteúdo.
   REGRA CRÍTICA — quando recebes contexto do site ISLA (NOTÍCIAS, EVENTOS ou INFORMAÇÃO DO SITE) mas a resposta específica não está nesse contexto: NUNCA digas apenas "não está nos documentos" — diz SEMPRE "Pesquisei o site da ISLA Santarém mas não encontrei informação específica sobre [tópico]. Podes consultar directamente em https://www.islasantarem.pt ou contactar a secretaria (secretaria@islasantarem.pt / +351 243 330 820)." — termina com [[MODE:GERAL]].

4a. DOCENTE: Quando um bloco de documento tiver o padrão "[Docente: Nome]" no cabeçalho, cita o docente na resposta: "De acordo com o/a docente [Nome], ..." ou "Segundo o/a Prof.ª/Prof. [Nome], ...". Faz isto APENAS quando o bloco identificado for a fonte principal da resposta.

5. Cita valores directamente quando encontrares campos estruturados (Docente:, ECTS:, Avaliação:, Data:). NUNCA inventes percentagens, datas ou notas específicas que não estejam nos documentos. Se não tiveres os valores exactos, diz que não estão nos documentos. Quando deres um exemplo de avaliação, OBRIGATÓRIO começar com: "Exemplo ilustrativo (os valores reais são definidos pelo docente):" — nunca omitas este aviso.

6. Sê conciso. Usa listas para múltiplos itens.

7. PROIBIDO terminar com "Posso ajudar-te mais?", "Tens mais dúvidas?", "Espero ter ajudado", "estou aqui para ajudar", "não hesites em perguntar", "foi um prazer ajudar", "foi um prazer", "fico à disposição", "qualquer dúvida", "de nada, estou aqui para ajudar". Termina com a última informação relevante.

8. MARCADOR DE MODO — OBRIGATÓRIO em TODAS as respostas substanciais (exceto saudações simples, confirmações como "ok"/"sim", e pedidos de clarificação com [[CURSO|GERAL]]):
   Adiciona numa linha isolada no FINAL da resposta um destes marcadores:
   [[MODE:CURSO]] — APENAS se usaste documentos locais da UC (fichas de UC, regulamentos internos carregados pelo admin)
   [[MODE:GERAL]] — se respondeste com conhecimento geral, notícias do site ISLA Santarém, ou quando não há documentos da UC relevantes
   Exemplos:
   - Resposta sobre avaliação da UC (dos docs): "...os exames são em Junho.\n[[MODE:CURSO]]"
   - Resposta sobre Napoleon: "...nasceu em 1769.\n[[MODE:GERAL]]"
   - Resposta sobre notícias ISLA do site: "...há um Encontro Científico...\n[[MODE:GERAL]]" """


# TTL cache for AI settings — avoids a new DB round-trip on every LLM call.
_ai_settings_cache: dict  = {}
_ai_settings_ts:   float  = 0.0
_AI_SETTINGS_TTL           = 30  # seconds


def _get_ai_settings() -> dict:
    """Load model/temperature/system_prompt overrides from DB (cached for 30 s)."""
    global _ai_settings_cache, _ai_settings_ts
    if time.monotonic() - _ai_settings_ts < _AI_SETTINGS_TTL:
        return _ai_settings_cache
    try:
        from ..database import SessionLocal
        from ..models import SystemSetting
        db = SessionLocal()
        try:
            rows = {r.key: r.value for r in db.query(SystemSetting).filter(
                SystemSetting.key.in_({"ai_model", "ai_system_prompt", "ai_temperature"})
            ).all()}
        finally:
            db.close()
        _ai_settings_cache = rows
        _ai_settings_ts    = time.monotonic()
        return rows
    except Exception:
        return _ai_settings_cache  # return stale cache rather than empty dict on error


def _build_messages(
    question: str,
    parts: list[str],
    history: list[dict] | None,
    language: str = "pt",
    course_name: str = "",
    user_name: str = "",
    user_role: str = "",
) -> list[dict]:
    """Build the LLM messages list (works with Groq and Ollama)."""
    # How many recent messages to include. 2 exchanges = 4 messages (user+assistant).
    MAX_HISTORY_MSGS = 6

    ai = _get_ai_settings()
    system = ai.get("ai_system_prompt") or _SYSTEM_PROMPT
    current_date = datetime.now().strftime("%A, %d de %B de %Y")
    system = f"DATA ACTUAL: {current_date}. Usa esta data como referência para todas as perguntas sobre o ano, mês, dia ou eventos actuais. NUNCA uses dados desactualizados do teu treino para determinar a data actual.\n\n" + system
    if language == "en":
        system = f"CURRENT DATE: {datetime.now().strftime('%A, %B %d, %Y')}. Use this as the reference date for any questions about the current year, month, day, or current events. NEVER rely on outdated training data to determine the current date.\n\n" + system
    messages: list[dict] = [{"role": "system", "content": system}]

    if history:
        # Include last MAX_HISTORY_MSGS turns but cap each message at 700 chars
        # so old long answers don't eat into the document budget.
        for msg in history[-MAX_HISTORY_MSGS:]:
            content = msg["content"]
            # Strip UI markers stored in DB — they are not meaningful to the LLM
            content = re.sub(r'\[\[MODE:[A-Z]+\]\]', '', content)
            content = re.sub(r'\[\[[^\]]+\]\]', '', content)
            content = content.strip()
            if len(content) > 700:
                content = content[:700] + " [...]"
            messages.append({"role": msg["role"], "content": content})

    doc_block = "\n\n".join(parts)

    # Hard payload guard — truncate only when needed for the active provider
    _max_doc = _GROQ_MAX_DOC_CHARS if _provider() == "groq" else _GEMINI_MAX_DOC_CHARS
    if len(doc_block) > _max_doc:
        doc_block = doc_block[:_max_doc] + "\n\n[... conteúdo truncado para caber nos limites da API ...]"

    # Inject course context so the LLM can answer meta questions (e.g. "que curso estou inscrito?")
    context_header_parts = []
    if user_name and user_role:
        r_str = "aluno" if user_role == "student" else "docente" if user_role == "instructor" else "administrador"
        context_header_parts.append(f"O utilizador atual chama-se {user_name} e o seu papel é {r_str}.")
    if course_name:
        context_header_parts.append(f"A conversa decorre no chat da Unidade Curricular '{course_name}'. Nunca partilhes dados de outras UCs ou de outros utilizadores.")
    
    context_header = "CONTEXTO OBRIGATÓRIO: " + " ".join(context_header_parts) + "\n\n" if context_header_parts else ""
    lang_note = "RESPOND ENTIRELY IN ENGLISH — the student wrote in English.\n\n" if language == "en" else ""
    if doc_block:
        user_content = f"{lang_note}{context_header}DOCUMENTOS DISPONÍVEIS:\n{doc_block}\n\nPergunta: {question}"
    else:
        user_content = f"{lang_note}{context_header}Pergunta: {question}"
    messages.append({"role": "user", "content": user_content})

    return messages


# ── Answer (non-streaming) ─────────────────────────────────────────────────────

def answer(
    question:    str,
    course_id:   int,
    semester_id: int,
    history:     List[dict] | None = None,
    course_name: str = "",
    language:    str = "",
    user_name:   str = "",
    user_role:   str = "",
) -> dict:
    """Answer a student question using knowledge files (non-streaming)."""
    language = language or _detect_language(question)

    import requests as _req

    course_text  = read_knowledge(course_knowledge_path(course_id, semester_id))
    general_text = read_knowledge(general_knowledge_path())

    if not course_text and not general_text:
        return {
            "answer": "Ainda não há informação disponível para esta unidade curricular. O administrador ainda não carregou documentos.",
            "sources": [],
        }

    # Clarification requests ("podes repetir?", "não percebi") → answer from history only
    if _is_clarification_request(question):
        messages = _build_messages(question, [], history, language, course_name=course_name, user_name=user_name, user_role=user_role)
        ai = _get_ai_settings()
        _temp = float(ai.get("ai_temperature", 0.3))
        api = _resolve_provider(ai)
        try:
            if api:
                url, key, model = api
                resp = _openai_post(url, key, {"model": model, "messages": messages,
                                               "temperature": _temp, "stream": False}, timeout=(15, 120))
                resp.raise_for_status()
                ans = _clean_response(resp.json()["choices"][0]["message"]["content"].strip(), history)
                return {"answer": ans, "sources": []}
        except Exception as exc:
            logger.error("LLM clarification call failed: %s", exc)
            return {"answer": "Ocorreu um erro ao processar a tua pergunta. Tenta novamente.", "sources": []}

    followup_news = _is_followup_to_news(question, history)
    course_ctx,  course_labels  = _relevant_sections(course_text,  question, MAX_COURSE_CHARS)  if course_text and not followup_news  else ("", [])
    general_ctx, general_labels = _relevant_sections(general_text, question, MAX_GENERAL_CHARS) if general_text else ("", [])

    parts = []
    if course_ctx:
        parts.append(f"=== DOCUMENTOS DA UNIDADE CURRICULAR ===\n{course_ctx}")
    if general_ctx:
        parts.append(f"=== REGULAMENTOS E DOCUMENTOS GERAIS ISLA ===\n{general_ctx}")

    # Fetch live ISLA news when explicitly requested, or fall back to the website
    # whenever course docs have no relevant answer for the question.
    if _wants_live_info(question) or _wants_isla_search(question) or followup_news or not course_ctx:
        try:
            from .live_feed import search_isla_website
            web_ctx = search_isla_website(query=question)
            if web_ctx:
                parts.append(web_ctx)
                logger.info("Web search used for: %s", question[:80])
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)

    messages = _build_messages(question, parts, history, language, course_name=course_name, user_name=user_name, user_role=user_role)
    ai = _get_ai_settings()
    _temp = float(ai.get("ai_temperature", 0.3))
    api = _resolve_provider(ai)

    try:
        if api:
            url, key, model = api
            resp = _openai_post(
                url, key,
                {"model": model, "messages": messages,
                 "temperature": _temp, "stream": False},
                timeout=(15, 120),
            )
            resp.raise_for_status()
            answer_text = resp.json()["choices"][0]["message"]["content"].strip()
        else:
            import requests as _req
            _model = ai.get("ai_model") or settings.ollama_model
            resp = _req.post(
                f"{settings.ollama_base_url}/api/chat",
                json={"model": _model, "messages": messages, "stream": False},
                timeout=300,
            )
            resp.raise_for_status()
            answer_text = resp.json().get("message", {}).get("content", "").strip()
    except Exception as exc:
        logger.error("LLM call failed in answer(): %s", exc)
        return {"answer": "Ocorreu um erro ao processar a tua pergunta. Tenta novamente.", "sources": []}

    answer_text = _clean_response(answer_text, history)
    answer_text = _ensure_mode_marker(answer_text, used_course_docs=bool(course_ctx))

    sources = [{"label": lbl, "page": ""} for lbl in (course_labels + general_labels)]
    return {"answer": answer_text, "sources": sources}


# ── Streaming support ──────────────────────────────────────────────────────────

def build_prompt(
    question:    str,
    course_id:   int,
    semester_id: int,
    history:     List[dict] | None = None,
    language:    str = "pt",
    course_name: str = "",
    user_name:   str = "",
    user_role:   str = "",
) -> tuple[bool, list[dict] | str, list]:
    """Build the LLM messages list without calling the LLM.

    Returns (has_context, messages_or_fallback, sources).
    When has_context is False, the second element is the ready-made fallback string.
    """
    if _is_clarification_request(question):
        messages = _build_messages(question, [], history, language, course_name=course_name, user_name=user_name, user_role=user_role)
        return True, messages, []

    course_text  = read_knowledge(course_knowledge_path(course_id, semester_id))
    general_text = read_knowledge(general_knowledge_path())

    if not course_text and not general_text:
        return False, "Ainda não há informação disponível para esta unidade curricular. O administrador ainda não carregou documentos.", []

    followup_news = _is_followup_to_news(question, history)
    course_ctx,  course_labels  = _relevant_sections(course_text,  question, MAX_COURSE_CHARS)  if course_text and not followup_news  else ("", [])
    general_ctx, general_labels = _relevant_sections(general_text, question, MAX_GENERAL_CHARS) if general_text else ("", [])

    parts = []
    if course_ctx:
        parts.append(f"=== DOCUMENTOS DA UNIDADE CURRICULAR ===\n{course_ctx}")
    if general_ctx:
        parts.append(f"=== REGULAMENTOS E DOCUMENTOS GERAIS ISLA ===\n{general_ctx}")

    # Fetch live ISLA news when explicitly requested, or fall back to the website
    # whenever course docs have no relevant answer for the question.
    if _wants_live_info(question) or _wants_isla_search(question) or followup_news or not course_ctx:
        try:
            from .live_feed import search_isla_website
            web_ctx = search_isla_website(query=question)
            if web_ctx:
                parts.append(web_ctx)
                logger.info("Web search used for: %s", question[:80])
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)

    messages = _build_messages(question, parts, history, language, course_name=course_name, user_name=user_name, user_role=user_role)

    sources = [{"label": lbl, "page": ""} for lbl in (course_labels + general_labels)]
    return True, messages, sources


def stream_tokens(messages: list[dict]):
    """Yield raw text tokens from the LLM streaming API."""
    import requests as _req
    ai = _get_ai_settings()
    _temp = float(ai.get("ai_temperature", 0.3))
    api   = _resolve_provider(ai)

    if api:
        url, key, model = api
        with _openai_post(
            url, key,
            {"model": model, "messages": messages,
             "temperature": _temp, "stream": True},
            stream=True,
            timeout=(15, 180),
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if not text.startswith("data:"):
                    continue
                payload = text[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    data    = _json.loads(payload)
                    content = data["choices"][0]["delta"].get("content", "")
                    if content:
                        yield content
                except Exception:
                    pass
    else:
        _model = ai.get("ai_model") or settings.ollama_model
        with _req.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": _model,
                "messages": messages,
                "stream": True,
                "think": True,
                "options": {"num_ctx": 8192, "num_predict": -1, "temperature": _temp},
            },
            stream=True,
            timeout=(30, 600),
        ) as resp:
            resp.raise_for_status()
            thinking_open = False
            for line in resp.iter_lines():
                if line:
                    try:
                        data = _json.loads(line)
                        msg  = data.get("message", {})

                        thinking = msg.get("thinking", "")
                        if thinking:
                            if not thinking_open:
                                yield "<think>"
                                thinking_open = True
                            yield thinking

                        content = msg.get("content", "")
                        if content:
                            if thinking_open:
                                yield "</think>"
                                thinking_open = False
                            yield content

                        if data.get("done"):
                            if thinking_open:
                                yield "</think>"
                            return
                    except Exception:
                        pass


# ── Document management ────────────────────────────────────────────────────────

def list_documents_in_file(path: Path) -> list:
    """Return [{label, added}] for each document block in a knowledge file."""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    pattern = r'# (.+?)\s{2,}\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]'
    return [
        {"label": m.group(1), "added": m.group(2)}
        for m in re.finditer(pattern, content)
    ]


def delete_document_block(path: Path, doc_label: str) -> bool:
    """Remove the block for doc_label from the knowledge file. Returns True if found."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    sep = "=" * 60
    pattern = (
        r'\n\n' + re.escape(sep) + r'\n'
        r'# ' + re.escape(doc_label) + r'\s+\[.*?\]\n'
        + re.escape(sep) + r'\n\n'
        r'.*?(?=\n\n' + re.escape(sep) + r'\n#|\Z)'
    )
    new_content, count = re.subn(pattern, '', content, flags=re.DOTALL)
    if count == 0:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


# ── Secretaria knowledge ───────────────────────────────────────────────────────

def secretaria_knowledge_path() -> Path:
    _ensure_dir()
    return KNOWLEDGE_DIR / "secretaria.txt"


_SECRETARIA_SYSTEM_PROMPT = """\
És a Secretaria Virtual do ISLA Santarém. Ajudas estudantes, candidatos e funcionários com \
questões administrativas e institucionais.

CRIADOR: Este chatbot foi criado por Selton de Souza, Samuel e Mariana, estudantes do curso de \
Inteligência Artificial no ISLA Santarém.

ATENÇÃO — REGRAS ABSOLUTAS:
• NUNCA uses "você", "seu", "sua" — usa SEMPRE "tu", "teu", "tua".
• NUNCA uses "aprendizado de máquina" — é sempre "aprendizagem automática".
• NUNCA termines com frases de encerramento como "não hesites em perguntar", "fico à disposição".
• NUNCA comeces com "Olá" excepto na primeira mensagem da sessão.

Áreas de competência:
- Matrículas e inscrições
- Propinas e pagamentos
- Bolsas de estudo e apoios sociais
- Calendário académico e prazos
- Certidões, declarações e documentos oficiais
- Equivalências e transferências
- Regulamentos académicos e estatutos
- Cursos e candidaturas
- Contactos e localização da secretaria

Regras de resposta:
1. Língua: Português europeu. Trata o utilizador por "tu". Nunca "você".
2. Se encontrares a informação nos documentos disponíveis: responde directamente com os detalhes.
3. Se não encontrares nos documentos mas tiveres resultados do site ISLA: usa esses resultados e \
   indica "De acordo com o site da ISLA Santarém:".
4. Se não tiveres informação nem nos documentos nem no site: diz claramente que não tens essa \
   informação e sugere contactar a secretaria directamente (secretaria@islasantarem.pt ou \
   +351 243 330 820).
5. Para questões sobre matrículas, propinas ou prazos: sê específico e cita os valores/datas \
   exactos dos documentos. Nunca inventes valores.
6. Sê conciso e directo. Usa listas para múltiplos itens.
7. Marca de fonte OBRIGATÓRIA no final de respostas substanciais:
   [[FONTE:DOCS]] — quando usaste documentos locais carregados
   [[FONTE:WEB]]  — quando usaste informação do site da ISLA Santarém
   [[FONTE:GERAL]] — quando respondeste com conhecimento geral sem fonte específica"""


def _build_secretaria_messages(
    question: str,
    context_parts: list[str],
    history: list[dict] | None,
    language: str = "pt",
    user_name: str = "",
) -> list[dict]:
    """Build the LLM messages list for the secretaria assistant."""
    MAX_HISTORY_MSGS = 6
    ai = _get_ai_settings()
    system = _SECRETARIA_SYSTEM_PROMPT
    current_date = datetime.now().strftime("%A, %d de %B de %Y")
    system = f"DATA ACTUAL: {current_date}. Usa esta data como referência para todas as perguntas sobre o ano, mês, dia ou eventos actuais.\n\n" + system
    if language == "en":
        system = f"CURRENT DATE: {datetime.now().strftime('%A, %B %d, %Y')}. Use this as the reference date.\n\n" + system

    messages: list[dict] = [{"role": "system", "content": system}]

    if history:
        for msg in history[-MAX_HISTORY_MSGS:]:
            content = re.sub(r'\[\[FONTE:[A-Z]+\]\]', '', msg["content"]).strip()
            if len(content) > 700:
                content = content[:700] + " [...]"
            messages.append({"role": msg["role"], "content": content})

    doc_block = "\n\n".join(context_parts)
    _max_doc = _GROQ_MAX_DOC_CHARS if _provider() == "groq" else _GEMINI_MAX_DOC_CHARS
    if len(doc_block) > _max_doc:
        doc_block = doc_block[:_max_doc] + "\n\n[... conteúdo truncado ...]"

    header = f"CONTEXTO: O utilizador chama-se {user_name}.\n\n" if user_name else ""
    lang_note = "RESPOND ENTIRELY IN ENGLISH.\n\n" if language == "en" else ""

    if doc_block:
        user_content = f"{lang_note}{header}INFORMAÇÃO DISPONÍVEL:\n{doc_block}\n\nPergunta: {question}"
    else:
        user_content = f"{lang_note}{header}Pergunta: {question}"

    messages.append({"role": "user", "content": user_content})
    return messages


def _clean_secretaria_response(text: str, history: list | None) -> str:
    """Strip banned endings and greetings from secretaria LLM output."""
    if history:
        text = re.sub(r'^(Olá[!,]?\s*|Bem-vindo[ao]!?\s*)', '', text).lstrip()
    text = _VOCE_RE.sub('tu', text)
    text = _fix_possessives(text)
    text = _BANNED_ENDINGS.sub('', text).rstrip()
    return text


def build_prompt_secretaria(
    question: str,
    history: list[dict] | None = None,
    language: str = "pt",
    user_name: str = "",
    user_role: str = "",
) -> tuple[bool, list[dict] | str, list, bool]:
    """Build LLM messages for secretaria — always falls back to web if docs are empty.

    Returns (has_context, messages_or_fallback, sources, used_web).
    """
    if _is_clarification_request(question):
        messages = _build_secretaria_messages(question, [], history, language, user_name)
        return True, messages, [], False

    sec_text     = read_knowledge(secretaria_knowledge_path())
    general_text = read_knowledge(general_knowledge_path())

    sec_ctx,     sec_labels     = _relevant_sections(sec_text,     question, MAX_COURSE_CHARS)  if sec_text     else ("", [])
    general_ctx, general_labels = _relevant_sections(general_text, question, MAX_GENERAL_CHARS) if general_text else ("", [])

    parts: list[str] = []
    if sec_ctx:
        parts.append(f"=== DOCUMENTOS DA SECRETARIA ===\n{sec_ctx}")
    if general_ctx:
        parts.append(f"=== REGULAMENTOS E DOCUMENTOS GERAIS ISLA ===\n{general_ctx}")

    # Always web-search when local docs have no relevant answer
    used_web = False
    if not sec_ctx and not general_ctx:
        try:
            from .live_feed import search_isla_website
            web_ctx = search_isla_website(query=question)
            if web_ctx:
                parts.append(web_ctx)
                used_web = True
                logger.info("Secretaria: web fallback for: %s", question[:80])
        except Exception as exc:
            logger.warning("Secretaria web search failed: %s", exc)
    # Also web-search if question explicitly requests website/live info
    elif _wants_live_info(question) or _wants_isla_search(question):
        try:
            from .live_feed import search_isla_website
            web_ctx = search_isla_website(query=question)
            if web_ctx:
                parts.append(web_ctx)
                used_web = True
        except Exception as exc:
            logger.warning("Secretaria web search failed: %s", exc)

    messages = _build_secretaria_messages(question, parts, history, language, user_name)
    sources = [{"label": lbl, "page": "", "from_web": False} for lbl in (sec_labels + general_labels)]
    if used_web:
        sources.append({"label": "ISLA Santarém — site oficial", "page": "", "from_web": True})

    return True, messages, sources, used_web


def answer_secretaria(
    question: str,
    history: list[dict] | None = None,
    language: str = "",
    user_name: str = "",
    user_role: str = "",
) -> dict:
    """Answer a question through the secretaria assistant (non-streaming)."""
    language = language or _detect_language(question)
    has_ctx, messages_or_fb, sources, used_web = build_prompt_secretaria(
        question, history, language, user_name, user_role
    )

    if not has_ctx:
        return {"answer": messages_or_fb, "sources": [], "used_web": False}

    ai   = _get_ai_settings()
    temp = float(ai.get("ai_temperature", 0.3))
    api  = _resolve_provider(ai)

    try:
        if api:
            url, key, model = api
            resp = _openai_post(url, key, {"model": model, "messages": messages_or_fb,
                                           "temperature": temp, "stream": False}, timeout=(15, 120))
            resp.raise_for_status()
            answer_text = resp.json()["choices"][0]["message"]["content"].strip()
        else:
            import requests as _req
            _model = ai.get("ai_model") or settings.ollama_model
            resp = _req.post(f"{settings.ollama_base_url}/api/chat",
                             json={"model": _model, "messages": messages_or_fb, "stream": False},
                             timeout=300)
            resp.raise_for_status()
            answer_text = resp.json().get("message", {}).get("content", "").strip()
    except Exception as exc:
        logger.error("Secretaria LLM call failed: %s", exc)
        return {"answer": "Ocorreu um erro ao processar a tua pergunta. Tenta novamente.", "sources": [], "used_web": False}

    answer_text = _clean_secretaria_response(answer_text, history)
    # Ensure source marker
    if len(answer_text) >= 60 and "[[FONTE:" not in answer_text:
        if used_web:
            answer_text = answer_text.rstrip() + "\n[[FONTE:WEB]]"
        elif sources:
            answer_text = answer_text.rstrip() + "\n[[FONTE:DOCS]]"
        else:
            answer_text = answer_text.rstrip() + "\n[[FONTE:GERAL]]"

    return {"answer": answer_text, "sources": sources, "used_web": used_web}
