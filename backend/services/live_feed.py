"""Live feed — scrapes ISLA Santarém website for news, events, and institutional info.

Two modes:
  1. News/events: scrapes the listing pages (always included)
  2. Institutional: maps query keywords to specific pages and scrapes their content,
     so the LLM can answer questions about calendar, fees, scholarships, contacts, etc.
"""

import re
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ISLA_BASE  = "https://www.islasantarem.pt"
NEWS_URL   = f"{ISLA_BASE}/pt/isla-media/noticias"
EVENTS_URL = f"{ISLA_BASE}/pt/isla-media/eventos"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_LISTING_ITEMS = 10
MAX_DETAIL_ITEMS  =  6
MAX_CONTENT_CHARS = 600

# ── Keyword → page mapping ─────────────────────────────────────────────────────
# Each entry: (frozenset of keywords, path, human-readable title)
_PAGE_MAP = [
    (
        frozenset({
            'ferias', 'férias', 'calendario', 'calendário', 'feriado', 'feriados',
            'horario', 'horários', 'horarios', 'aulas', 'semestre', 'trimestre',
            'inicio', 'início', 'fim', 'interrupção', 'interrupcao', 'recesso',
            'natal', 'pascoa', 'páscoa', 'carnaval', 'epocas', 'épocas',
            'academico', 'académico', 'letivo', 'letivas',
        }),
        '/pt/calendario-escolar-e-horarios',
        'Calendário Escolar e Horários',
    ),
    (
        frozenset({
            'propina', 'propinas', 'emolumento', 'emolumentos', 'mensalidade',
            'pagamento', 'pagamentos', 'taxa', 'taxas', 'custo', 'custos',
            'preco', 'preço', 'matricula', 'matrícula', 'inscricao', 'inscrição',
        }),
        '/pt/emolumentos-e-propinas',
        'Emolumentos e Propinas',
    ),
    (
        frozenset({
            'bolsa', 'bolsas', 'apoio', 'social', 'financiamento', 'financiamentos',
            'dges', 'subsidio', 'subsídio', 'credito', 'crédito', 'banco',
        }),
        '/pt/acao-social-bolsas-e-financiamentos',
        'Ação Social, Bolsas e Financiamentos',
    ),
    (
        frozenset({
            'candidatura', 'candidaturas', 'admissao', 'admissão', 'acesso',
            'vagas', 'concurso', 'ingresso', 'entrar', 'inscricao', 'inscrição',
            'candidato', 'candidatos',
        }),
        '/pt/candidaturas',
        'Candidaturas',
    ),
    (
        frozenset({
            'regulamento', 'regulamentos', 'norma', 'normas', 'regras',
            'estatuto', 'estatutos', 'lei', 'despacho', 'creditacao', 'creditação',
        }),
        '/pt/normas-e-regulamentos',
        'Normas e Regulamentos',
    ),
    (
        frozenset({
            'contacto', 'contactos', 'telefone', 'email', 'morada', 'endereco',
            'endereço', 'atendimento', 'secretaria', 'horario', 'horários',
        }),
        '/pt/contactos',
        'Contactos',
    ),
    (
        frozenset({
            'projeto', 'projetos', 'it academy', 'consulting', 'voluntario',
            'voluntário', 'mentoria', 'club', 'clube', 'atividade', 'atividades',
        }),
        '/pt/projetos',
        'Projetos e Atividades',
    ),
    (
        frozenset({
            'erasmus', 'mobilidade', 'internacional', 'estrangeiro', 'intercambio',
            'intercâmbio',
        }),
        '/pt/mobilidade-erasmus',
        'Mobilidade ERASMUS',
    ),
    (
        frozenset({
            'residencia', 'residência', 'alojamento', 'dormitorio', 'dormitório',
        }),
        '/pt/recursos/residencia-de-estudantes',
        'Residência de Estudantes',
    ),
    (
        frozenset({
            'guia', 'acolhimento', 'boas-vindas', 'novo', 'novos', 'caloiro',
            'caloiros', 'primeiro', 'comecar', 'começar',
        }),
        '/pt/guia-de-acolhimento',
        'Guia de Acolhimento',
    ),
    (
        frozenset({
            'professor', 'professores', 'docente', 'docentes', 'corpo',
        }),
        '/pt/corpo-docente',
        'Corpo Docente',
    ),
    (
        frozenset({
            'formulario', 'formulários', 'formularios', 'impresso', 'impressos',
            'documento', 'declaracao', 'declaração', 'certidao', 'certidão',
        }),
        '/pt/recursos/impressos-e-formularios',
        'Impressos e Formulários',
    ),
    (
        frozenset({
            'curso', 'cursos', 'tesp', 'ctesp', 'licenciatura', 'licenciaturas',
            'mestrado', 'mestrados', 'pos-graduacao', 'pós-graduação', 'cet',
            'oferta', 'formativa', 'plano', 'estudos', 'disciplinas', 'disciplina',
            'duracao', 'duração', 'semestres', 'semestre', 'unidades', 'curriculares',
            'ects', 'creditos', 'créditos', 'programa', 'programas',
            'inteligencia', 'inteligência', 'artificial', 'informatica', 'informática',
            'gestao', 'gestão', 'marketing', 'contabilidade', 'direito', 'turismo',
        }),
        '/pt/oferta-formativa',
        'Oferta Formativa — Cursos',
    ),
    (
        frozenset({
            'tesp', 'ctesp', 'tecnico', 'técnico', 'superior', 'profissional',
            'inteligencia', 'inteligência', 'artificial',
        }),
        '/pt/cursos',
        'Cursos ISLA Santarém',
    ),
]


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        logger.warning("live_feed: GET %s failed: %s", url, exc)
        return None


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text or "").strip()


# ── Institutional page scraper ─────────────────────────────────────────────────

def _scrape_page(path: str, title: str) -> str:
    """Scrape a single ISLA institutional page and return structured text."""
    soup = _get(ISLA_BASE + path)
    if not soup:
        return ""

    main = soup.find("main") or soup

    # Remove navigation, header, footer noise
    for tag in main.find_all(["nav", "header", "footer", "script", "style"]):
        tag.decompose()
    for tag in main.find_all(True, class_=lambda c: c and any(
        k in " ".join(c) for k in ["navbar", "uk-navbar", "breadcrumb", "uk-offcanvas"]
    )):
        tag.decompose()

    lines = [f"=== {title} ===", f"Fonte: {ISLA_BASE + path}", ""]
    seen_texts: set[str] = set()
    seen_links: set[str] = set()

    _NAV_ITEMS = {"início", "inicio", "instituto", "ensino", "candidaturas",
                  "internacional", "recursos", "contactos", "notícias", "eventos",
                  "nos média", "informações académicas", "informacoes academicas"}

    for tag in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "a", "td"]):
        text = _clean(tag.get_text())
        href = tag.get("href", "")

        if len(text) < 8:
            continue

        if href and tag.name == "a":
            # Normalise to absolute URL; keep mailto: as-is
            if href.startswith("mailto:"):
                full_href = href
            elif href.startswith("http"):
                full_href = href
            else:
                full_href = ISLA_BASE + href
            if full_href in seen_links:
                continue
            seen_links.add(full_href)
            if text.lower() not in _NAV_ITEMS and len(text) > 5:
                lines.append(f"• {text}: {full_href}")
        elif tag.name != "a":
            if text in seen_texts or text.lower() in _NAV_ITEMS:
                continue
            seen_texts.add(text)
            if len(text) > 15:
                lines.append(text)

        if len(lines) > 50:
            break

    return "\n".join(lines) if len(lines) > 3 else ""


# ── News/events scrapers (unchanged logic) ─────────────────────────────────────

def _scrape_listing(listing_url: str, path_keyword: str) -> list[dict]:
    soup = _get(listing_url)
    if not soup:
        return []

    items: list[dict] = []
    seen:  set[str]   = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if path_keyword not in href:
            continue
        full = href if href.startswith("http") else ISLA_BASE + href
        if full in seen or full.rstrip("/") == listing_url.rstrip("/"):
            continue
        seen.add(full)

        title = ""
        el = a.find(["h2", "h3", "h4"])
        if not el:
            for parent in list(a.parents)[:4]:
                el = parent.find(["h2", "h3", "h4"])
                if el:
                    break
        title = _clean(el.get_text()) if el else _clean(a.get_text())

        if title and 5 <= len(title) <= 220:
            items.append({"title": title, "url": full, "date": "", "content": ""})

        if len(items) >= MAX_LISTING_ITEMS:
            break

    return items


def _scrape_article(url: str) -> dict:
    soup = _get(url)
    if not soup:
        return {"date": "", "content": ""}

    date = ""
    time_el = soup.find("time")
    if time_el:
        date = time_el.get("datetime") or _clean(time_el.get_text())
    if not date:
        for el in soup.find_all(["span", "div", "p"]):
            cls = " ".join(el.get("class", []))
            if re.search(r'date|data|publicad|created', cls, re.I):
                date = _clean(el.get_text())
                if date:
                    break

    content = ""
    for selector in ["article .uk-article-body", "article", ".item-page",
                     ".article-body", "main .uk-container", "main"]:
        container = soup.select_one(selector)
        if not container:
            continue
        for tag in container.find_all(["p", "li", "h2", "h3"]):
            t = _clean(tag.get_text())
            if len(t) > 25:
                content += t + " "
            if len(content) > MAX_CONTENT_CHARS:
                break
        if content:
            break

    return {"date": date[:60], "content": content.strip()[:MAX_CONTENT_CHARS]}


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_isla_feed() -> dict:
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    error: Optional[str] = None

    news   = _scrape_listing(NEWS_URL,   "/noticias/")
    events = _scrape_listing(EVENTS_URL, "/eventos/")

    if not news and not events:
        error = "Não foi possível obter conteúdo do site da ISLA Santarém."

    for item in (news + events)[:MAX_DETAIL_ITEMS]:
        detail = _scrape_article(item["url"])
        item.update(detail)

    return {
        "news":       news,
        "events":     events,
        "fetched_at": fetched_at,
        "source":     ISLA_BASE,
        "error":      error,
    }


def search_isla_website(query: str = "", max_news: int = 5, max_events: int = 3,
                         detail_items: int = 3) -> str:
    """Scrape the ISLA Santarém website and return context text for the LLM.

    Always includes recent news/events. When the query matches institutional
    keywords (calendar, fees, scholarships, contacts, projects, etc.) it also
    scrapes the relevant institutional pages so the LLM can give real answers
    instead of redirecting the user.
    """
    # Determine which institutional pages are relevant for this query
    query_words = set(re.findall(r'\w+', query.lower()))
    relevant_pages: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for keywords, path, title in _PAGE_MAP:
        if query_words & keywords and path not in seen_paths:
            relevant_pages.append((path, title))
            seen_paths.add(path)

    # When no specific page matches, pick a sensible fallback based on query type
    if not relevant_pages and query:
        _academic_words = {
            'curso', 'cursos', 'tesp', 'licenciatura', 'mestrado', 'plano', 'disciplina',
            'disciplinas', 'duracao', 'duração', 'semestre', 'semestres', 'ects',
            'creditos', 'créditos', 'unidade', 'unidades', 'programa',
        }
        if query_words & _academic_words:
            relevant_pages = [
                ('/pt/oferta-formativa', 'Oferta Formativa — Cursos'),
                ('/pt/cursos', 'Cursos ISLA Santarém'),
            ]
        else:
            relevant_pages = [
                ('/pt/calendario-escolar-e-horarios', 'Calendário Escolar e Horários'),
                ('/pt/contactos', 'Contactos'),
            ]

    parts: list[str] = []

    # 1. Institutional pages
    for path, title in relevant_pages:
        content = _scrape_page(path, title)
        if content:
            parts.append(content)

    # 2. News + events
    news   = _scrape_listing(NEWS_URL,   "/noticias/")[:max_news]
    events = _scrape_listing(EVENTS_URL, "/eventos/")[:max_events]

    for item in news[:detail_items]:
        detail = _scrape_article(item["url"])
        item.update(detail)

    news_lines = ["=== NOTÍCIAS E EVENTOS RECENTES — ISLA SANTARÉM ===", ""]
    if news:
        news_lines.append("NOTÍCIAS RECENTES:")
        for item in news:
            news_lines.append(f"• {item['title']}")
            if item.get("date"):
                news_lines.append(f"  Data: {item['date']}")
            if item.get("content"):
                news_lines.append(f"  {item['content'][:400]}")
            news_lines.append(f"  Mais informação: {item['url']}")
        news_lines.append("")
    if events:
        news_lines.append("EVENTOS RECENTES:")
        for item in events:
            news_lines.append(f"• {item['title']}")
            if item.get("content"):
                news_lines.append(f"  {item['content'][:200]}")
            news_lines.append(f"  Mais informação: {item['url']}")

    if news or events:
        parts.append("\n".join(news_lines))

    if not parts:
        return ""

    return "\n\n".join(parts)


def format_feed_as_knowledge(feed: dict) -> str:
    lines = [
        f"Fonte: {feed['source']}",
        f"Última actualização automática: {feed['fetched_at']}",
        "",
    ]

    news = feed.get("news", [])
    if news:
        lines.append("=== NOTÍCIAS RECENTES DA ISLA SANTARÉM ===")
        lines.append("")
        for item in news:
            lines.append(f"• {item['title']}")
            if item.get("date"):
                lines.append(f"  Data: {item['date']}")
            if item.get("content"):
                lines.append(f"  {item['content']}")
            lines.append(f"  Mais informação: {item['url']}")
            lines.append("")

    events = feed.get("events", [])
    if events:
        lines.append("=== EVENTOS E ACTIVIDADES ===")
        lines.append("")
        for item in events:
            lines.append(f"• {item['title']}")
            if item.get("date"):
                lines.append(f"  Data: {item['date']}")
            if item.get("content"):
                lines.append(f"  {item['content']}")
            lines.append(f"  Mais informação: {item['url']}")
            lines.append("")

    return "\n".join(lines)
