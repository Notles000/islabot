#!/usr/bin/env python3
"""
eval.py — Interactive Q&A test harness for the ISLA chatbot.

Usage:
    python eval.py                      # interactive course picker
    python eval.py --course IIA         # test a specific UC
    python eval.py --course IIA -n 8    # generate 8 questions
    python eval.py --course IIA --file  # load custom questions from a file

Workflow:
    1. Loads the knowledge base for the chosen UC
    2. Generates realistic student questions with the LLM
    3. Runs each question through the REAL answer() pipeline
    4. You rate each answer: [G]ood / [B]ad / [S]kip / [Q]uit
    5. Saves all results to eval_results/<date>_<course>.json
    6. Prints a summary with pass rate
"""

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# ── Bootstrap path so we can import the backend ───────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database import SessionLocal
from backend.models import Course
from backend.services.knowledge import (
    _llm_complete,
    answer,
    course_knowledge_path,
    general_knowledge_path,
    read_knowledge,
)

# ── ANSI colours (degrade gracefully if terminal doesn't support them) ─────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
GREY   = "\033[90m"

EVAL_DIR = Path("eval_results")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hr(char="─", width=70, colour=GREY):
    print(f"{colour}{char * width}{RESET}")


def _wrap(text: str, width=70, indent="  ") -> str:
    lines = text.splitlines()
    wrapped = []
    for line in lines:
        if line.strip():
            wrapped.extend(textwrap.wrap(line, width=width - len(indent),
                                         subsequent_indent=indent))
        else:
            wrapped.append("")
    return "\n".join(f"{indent}{l}" for l in wrapped)


def _pick_course() -> Course:
    db = SessionLocal()
    try:
        courses = db.query(Course).order_by(Course.code).all()
        if not courses:
            print(f"{RED}Nenhuma UC encontrada na base de dados.{RESET}")
            sys.exit(1)

        print(f"\n{BOLD}Escolhe uma UC para avaliar:{RESET}\n")
        for i, c in enumerate(courses, 1):
            has_kb = course_knowledge_path(c.id, c.semester_id).exists()
            kb_tag = f"{GREEN}✓ tem docs{RESET}" if has_kb else f"{YELLOW}⚠ sem docs{RESET}"
            print(f"  {CYAN}{i:2}.{RESET} [{c.code}] {c.name}  {kb_tag}")

        print()
        while True:
            raw = input(f"{BOLD}Número ou código: {RESET}").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(courses):
                return courses[int(raw) - 1]
            match = [c for c in courses if c.code.upper() == raw.upper()]
            if match:
                return match[0]
            print(f"{RED}  Opção inválida. Tenta de novo.{RESET}")
    finally:
        db.close()


def _get_course_by_code(code: str) -> Course:
    db = SessionLocal()
    try:
        c = db.query(Course).filter(Course.code == code.upper()).first()
        if not c:
            print(f"{RED}UC '{code}' não encontrada.{RESET}")
            sys.exit(1)
        return c
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Question generation
# ─────────────────────────────────────────────────────────────────────────────

_GEN_PROMPT = """\
És um gerador de perguntas de teste para um chatbot académico.

Baseando-te no seguinte excerto de documentos da unidade curricular "{course_name}",
gera exactamente {n} perguntas realistas que um estudante português faria ao chatbot.

Regras:
- As perguntas devem ser variadas: avaliação, datas, conteúdos, docentes, requisitos
- Escreve em Português europeu informal (como um estudante fala)
- Uma pergunta por linha, sem numeração, sem bullet points, sem explicações
- Inclui pelo menos uma pergunta de meta (ex: "que UC é esta?")
- Inclui pelo menos uma pergunta difícil onde a resposta pode não estar nos docs

DOCUMENTOS (excerto):
{context}

PERGUNTAS:"""


def generate_questions(course: Course, n: int) -> list[str]:
    print(f"\n{DIM}A gerar {n} perguntas com o LLM...{RESET}", end="", flush=True)

    kb_path = course_knowledge_path(course.id, course.semester_id)
    context = read_knowledge(kb_path)
    if not context:
        context = read_knowledge(general_knowledge_path())
    if not context:
        print(f"\n{YELLOW}⚠  Sem documentos carregados para esta UC.{RESET}")
        context = f"UC: {course.name} ({course.code})"

    # Send only a preview to avoid 413
    context_preview = context[:6_000]

    prompt = _GEN_PROMPT.format(
        course_name=course.name,
        n=n,
        context=context_preview,
    )
    try:
        raw = _llm_complete(prompt, temperature=0.8)
    except Exception as exc:
        print(f"\n{RED}Erro ao gerar perguntas: {exc}{RESET}")
        sys.exit(1)

    questions = [
        line.strip().lstrip("-•*0123456789.) ")
        for line in raw.splitlines()
        if line.strip() and len(line.strip()) > 10
    ][:n]

    print(f"\r{GREEN}✓ {len(questions)} perguntas geradas.{RESET}          ")
    return questions


def load_questions_from_input() -> list[str]:
    """Let the user type custom questions, one per line. Empty line = done."""
    print(f"\n{BOLD}Escreve as tuas perguntas (linha vazia para terminar):{RESET}\n")
    questions = []
    while True:
        try:
            line = input(f"  {CYAN}>{RESET} ").strip()
        except EOFError:
            break
        if not line:
            break
        questions.append(line)
    return questions


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation loop
# ─────────────────────────────────────────────────────────────────────────────

def run_eval(course: Course, questions: list[str]) -> list[dict]:
    results = []
    total = len(questions)

    print()
    _hr("═")
    print(f"  {BOLD}Avaliação: {course.code} — {course.name}{RESET}")
    print(f"  {DIM}{total} perguntas  |  G=Boa  B=Má  S=Skip  Q=Sair{RESET}")
    _hr("═")

    for i, question in enumerate(questions, 1):
        print(f"\n{BOLD}{BLUE}[{i}/{total}]{RESET} {BOLD}{question}{RESET}")
        print(f"{DIM}A obter resposta...{RESET}", end="\r", flush=True)

        try:
            result = answer(
                question=question,
                course_id=course.id,
                semester_id=course.semester_id,
                course_name=course.name,
            )
            ans = result.get("answer", "")
            sources = result.get("sources", [])
        except Exception as exc:
            ans = f"[ERRO: {exc}]"
            sources = []

        print(" " * 30, end="\r")  # clear "A obter resposta..."

        # Print the answer
        _hr()
        print(f"{_wrap(ans, width=72)}")
        if sources:
            src_labels = ", ".join(s.get("label", "") for s in sources if s.get("label"))
            print(f"\n  {GREY}Fontes: {src_labels}{RESET}")
        _hr()

        # Rating prompt
        while True:
            raw = input(
                f"  Avaliação  "
                f"{GREEN}[G]{RESET}boa  "
                f"{RED}[B]{RESET}má  "
                f"{YELLOW}[S]{RESET}kip  "
                f"{DIM}[Q]{RESET}uit  > "
            ).strip().lower()

            if raw in ("g", "b", "s", "q"):
                break
            print(f"  {RED}Opção inválida.{RESET} Usa G, B, S ou Q.")

        note = ""
        if raw == "b":
            note = input(f"  {DIM}Nota opcional (Enter para ignorar): {RESET}").strip()
        if raw == "q":
            print(f"\n{YELLOW}Saindo...{RESET}")
            results.append({
                "question": question,
                "answer": ans,
                "sources": [s.get("label", "") for s in sources],
                "rating": "quit",
                "note": note,
            })
            break

        rating_label = {"g": "good", "b": "bad", "s": "skip"}.get(raw, raw)
        colour = GREEN if raw == "g" else RED if raw == "b" else GREY
        print(f"  {colour}→ {rating_label.upper()}{RESET}" + (f"  {DIM}{note}{RESET}" if note else ""))

        results.append({
            "question": question,
            "answer": ans,
            "sources": [s.get("label", "") for s in sources],
            "rating": rating_label,
            "note": note,
        })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Summary + save
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: list[dict], course: Course):
    rated   = [r for r in results if r["rating"] not in ("skip", "quit")]
    good    = [r for r in rated if r["rating"] == "good"]
    bad     = [r for r in rated if r["rating"] == "bad"]
    skipped = [r for r in results if r["rating"] == "skip"]

    pct = round(len(good) / len(rated) * 100) if rated else 0
    colour = GREEN if pct >= 70 else YELLOW if pct >= 40 else RED

    print()
    _hr("═")
    print(f"  {BOLD}Resumo — {course.code}{RESET}")
    _hr()
    print(f"  {GREEN}Boas:    {len(good):3}{RESET}")
    print(f"  {RED}Más:     {len(bad):3}{RESET}")
    print(f"  {GREY}Skips:   {len(skipped):3}{RESET}")
    print(f"  {BOLD}Taxa de acerto: {colour}{pct}%{RESET}")
    _hr()

    if bad:
        print(f"\n  {RED}{BOLD}Respostas Más:{RESET}")
        for r in bad:
            print(f"\n  {BOLD}Q:{RESET} {r['question']}")
            print(f"  {DIM}{_wrap(r['answer'][:300], width=68)}{RESET}")
            if r["note"]:
                print(f"  {YELLOW}Nota: {r['note']}{RESET}")
    _hr("═")


def save_results(results: list[dict], course: Course) -> Path:
    EVAL_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = EVAL_DIR / f"{stamp}_{course.code}.json"
    payload = {
        "course": course.code,
        "course_name": course.name,
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": {
            "total": len(results),
            "good":  sum(1 for r in results if r["rating"] == "good"),
            "bad":   sum(1 for r in results if r["rating"] == "bad"),
            "skip":  sum(1 for r in results if r["rating"] == "skip"),
        },
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interactive eval harness for the ISLA chatbot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python eval.py                   # interactive picker
              python eval.py --course IIA      # test IIA with 5 auto questions
              python eval.py --course EST -n 10
              python eval.py --course IIA --manual   # type your own questions
        """),
    )
    parser.add_argument("--course", "-c", help="UC code (e.g. IIA, EST)")
    parser.add_argument("--n", "-n", type=int, default=5,
                        help="Number of questions to generate (default: 5)")
    parser.add_argument("--manual", "-m", action="store_true",
                        help="Type your own questions instead of auto-generating")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════╗")
    print(f"║   ISLA Chatbot — Eval Harness 🔬    ║")
    print(f"╚══════════════════════════════════════╝{RESET}")

    # Pick course
    course = _get_course_by_code(args.course) if args.course else _pick_course()
    print(f"\n  UC seleccionada: {BOLD}{course.code} — {course.name}{RESET}")

    # Get questions
    if args.manual:
        questions = load_questions_from_input()
    else:
        questions = generate_questions(course, args.n)

    if not questions:
        print(f"{RED}Nenhuma pergunta disponível. Saindo.{RESET}")
        sys.exit(0)

    # Preview questions
    print(f"\n{BOLD}Perguntas a testar:{RESET}\n")
    for i, q in enumerate(questions, 1):
        print(f"  {DIM}{i:2}.{RESET} {q}")

    input(f"\n{DIM}  Prima Enter para começar...{RESET}")

    # Run evaluation
    results = run_eval(course, questions)

    # Summary + save
    print_summary(results, course)
    out = save_results(results, course)
    print(f"\n  {GREEN}✓ Resultados guardados em: {BOLD}{out}{RESET}\n")


if __name__ == "__main__":
    main()
