import json
import asyncio
import threading
from datetime import datetime, timedelta
from typing import List, Optional
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db, SessionLocal
from fastapi.responses import StreamingResponse as _StreamRaw
from ..models import User, Course, Enrollment, ChatSession, ChatMessage, Semester, UserRole, SystemSetting, ChatBookmark, UserPreference
from ..services.knowledge import answer, build_prompt, stream_tokens, _llm_complete, _provider, _detect_language, _clean_response, _ensure_mode_marker


def _auto_title(session_id: int, question: str, answer_text: str):
    """Background: ask LLM for a short session title, update DB.

    Works with both Groq and Ollama via _llm_complete().
    """
    try:
        prompt = (
            "Gera um título muito curto (máx 7 palavras) em Português europeu "
            "para uma conversa académica. Responde APENAS com o título, sem pontuação "
            "final, sem aspas.\n\n"
            f"Pergunta: {question[:300]}\n"
            f"Resposta resumida: {answer_text[:300]}"
        )
        title = _llm_complete(prompt, temperature=0.3).strip().strip('"\'').strip()
        if title:
            db2 = SessionLocal()
            try:
                s = db2.query(ChatSession).filter(ChatSession.id == session_id).first()
                if s:
                    s.title = title[:80]
                    db2.commit()
            finally:
                db2.close()
    except Exception:
        pass


_burst_cache: dict = {}  # user_id → [timestamps]

def _check_rate_limit(db: Session, user: User):
    if user.role != UserRole.student:
        return

    # Burst guard: max 5 requests/minute per user (in-memory)
    now_ts = datetime.utcnow().timestamp()
    window = _burst_cache.setdefault(user.id, [])
    _burst_cache[user.id] = [t for t in window if now_ts - t < 60]
    if len(_burst_cache[user.id]) >= 15:
        raise HTTPException(status_code=429, detail="Demasiadas perguntas em pouco tempo. Aguarda um momento.")
    _burst_cache[user.id].append(now_ts)

    # Hourly DB-based limit (admin-configurable)
    row = db.query(SystemSetting).filter(SystemSetting.key == "rate_limit_per_hour").first()
    limit = int(row.value) if row else 0
    if limit <= 0:
        return
    since = datetime.utcnow() - timedelta(hours=1)
    count = (
        db.query(func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(
            ChatSession.user_id == user.id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= since,
        )
        .scalar() or 0
    )
    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Atingiste o limite de {limit} mensagens por hora. Tenta mais tarde.",
        )

router = APIRouter(prefix="/chat", tags=["chat"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class MessageIn(BaseModel):
    session_id: Optional[int] = None
    course_id:  int
    question:   str
    language:   Optional[str] = "pt"


class SourceOut(BaseModel):
    label: str
    page:  str


class MessageOut(BaseModel):
    session_id:  int
    message_id:  int
    answer:      str
    sources:     List[SourceOut]
    had_results: bool


class RatingIn(BaseModel):
    rating: int  # 1 or -1


class RenameIn(BaseModel):
    title: str


class SessionOut(BaseModel):
    id:        int
    course_id: int
    title:     Optional[str]
    updated_at: str

    class Config:
        from_attributes = True


class HistoryMsg(BaseModel):
    role:    str
    content: str
    sources: Optional[List[SourceOut]] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/message", response_model=MessageOut)
async def send_message(body: MessageIn, db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    _check_rate_limit(db, current)
    # Verify student is enrolled
    enrolled = db.query(Enrollment).filter(
        Enrollment.student_id == current.id,
        Enrollment.course_id  == body.course_id,
    ).first()
    if not enrolled and current.role == UserRole.student:
        raise HTTPException(status_code=403, detail="Não estás inscrito nesta UC")

    # Get active semester for this course
    course = db.query(Course).filter(Course.id == body.course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="UC não encontrada")

    # Get or create session
    if body.session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id      == body.session_id,
            ChatSession.user_id == current.id,
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
    else:
        title   = body.question[:80]
        session = ChatSession(user_id=current.id, course_id=body.course_id, title=title)
        db.add(session)
        db.commit()
        db.refresh(session)

    # Load history for context
    past = db.query(ChatMessage).filter(
        ChatMessage.session_id == session.id
    ).order_by(ChatMessage.id).all()
    history = [{"role": m.role, "content": m.content} for m in past]

    # Run RAG in a thread so it doesn't block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        partial(answer, question=body.question, course_id=course.id,
                semester_id=course.semester_id, history=history,
                course_name=course.name,
                language=body.language or _detect_language(body.question)),
    )

    # Persist messages
    import re as _re
    raw_answer    = result["answer"]
    stored_answer = _re.sub(r'\[\[MODE:[A-Z]+\]\]', '', raw_answer).strip()
    stored_answer = _re.sub(r'\[\[[^\]]+\]\]', '', stored_answer).strip()

    user_msg = ChatMessage(session_id=session.id, role="user", content=body.question)
    db.add(user_msg)

    bot_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=stored_answer,
        sources=json.dumps(result["sources"]),
        had_results=result.get("had_results"),
        retrieval_score=result.get("retrieval_score"),
    )
    db.add(bot_msg)
    db.commit()
    db.refresh(bot_msg)

    return MessageOut(
        session_id=session.id,
        message_id=bot_msg.id,
        answer=raw_answer,  # frontend JS strips [[MODE:...]] client-side
        sources=[SourceOut(**s) for s in result["sources"]],
        had_results=bool(result.get("had_results")),
    )


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    sessions = db.query(ChatSession).filter(
        ChatSession.user_id == current.id
    ).order_by(ChatSession.updated_at.desc()).all()

    return [
        SessionOut(
            id=s.id,
            course_id=s.course_id,
            title=s.title,
            updated_at=s.updated_at.isoformat(),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}/messages", response_model=List[HistoryMsg])
def get_messages(session_id: int, db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    session = db.query(ChatSession).filter(
        ChatSession.id      == session_id,
        ChatSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    msgs = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.id).all()

    out = []
    for m in msgs:
        sources = json.loads(m.sources) if m.sources else []
        out.append(HistoryMsg(role=m.role, content=m.content, sources=sources))
    return out


@router.patch("/messages/{message_id}/rating")
def rate_message(
    message_id: int,
    body: RatingIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    if body.rating not in (1, -1):
        raise HTTPException(status_code=422, detail="Rating deve ser 1 ou -1")

    msg = db.query(ChatMessage).join(ChatSession).filter(
        ChatMessage.id == message_id,
        ChatMessage.role == "assistant",
        ChatSession.user_id == current.id,
    ).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada")

    msg.rating = body.rating
    db.commit()
    return {"status": "ok", "rating": body.rating}


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"status": "deleted"}


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: int,
    body: RenameIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    session.title = body.title[:80]
    db.commit()
    return {"status": "ok", "title": session.title}


@router.post("/message/stream")
async def stream_message(
    body: MessageIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    _check_rate_limit(db, current)
    enrolled = db.query(Enrollment).filter(
        Enrollment.student_id == current.id,
        Enrollment.course_id  == body.course_id,
    ).first()
    if not enrolled and current.role == UserRole.student:
        raise HTTPException(status_code=403, detail="Não estás inscrito nesta UC")

    course = db.query(Course).filter(Course.id == body.course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="UC não encontrada")

    if body.session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id      == body.session_id,
            ChatSession.user_id == current.id,
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
    else:
        session = ChatSession(user_id=current.id, course_id=body.course_id, title=body.question[:80])
        db.add(session)
        db.commit()
        db.refresh(session)

    past    = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.id).all()
    history = [{"role": m.role, "content": m.content} for m in past]

    loop = asyncio.get_running_loop()
    has_ctx, messages, sources = await loop.run_in_executor(
        None,
        partial(build_prompt, body.question, course.id, course.semester_id,
                history, body.language or _detect_language(body.question), course.name,
                current.name, current.role.value),
    )

    session_id   = session.id
    question     = body.question
    is_new_session = not past  # True when this is the very first message

    async def event_generator():
        yield f"data: {json.dumps({'type': 'meta', 'session_id': session_id, 'sources': sources, 'had_results': has_ctx})}\n\n"

        if not has_ctx:
            fallback = messages  # fallback is a plain string when has_ctx=False
            user_msg = ChatMessage(session_id=session_id, role="user", content=question)
            db.add(user_msg)
            bot_msg  = ChatMessage(session_id=session_id, role="assistant", content=fallback,
                                   sources=json.dumps([]), had_results=False)
            db.add(bot_msg)
            db.commit()
            db.refresh(bot_msg)
            yield f"data: {json.dumps({'type': 'token', 'text': fallback})}\n\n"
            yield f"data: {json.dumps({'type': 'end', 'message_id': bot_msg.id})}\n\n"
            if is_new_session:
                threading.Thread(
                    target=_auto_title, args=(session_id, question, fallback), daemon=True
                ).start()
            return

        token_queue: asyncio.Queue = asyncio.Queue()

        def produce():
            try:
                for tok in stream_tokens(messages):
                    loop.call_soon_threadsafe(token_queue.put_nowait, tok)
            except Exception as exc:
                loop.call_soon_threadsafe(token_queue.put_nowait, f"\n[Erro: {exc}]")
            finally:
                loop.call_soon_threadsafe(token_queue.put_nowait, None)

        threading.Thread(target=produce, daemon=True).start()

        answer_parts: list[str] = []
        buf      = ""
        in_think = False
        OPEN     = "<think>"
        CLOSE    = "</think>"

        try:
            while True:
                tok = await token_queue.get()
                if tok is None:
                    break
                buf += tok

                while buf:
                    if not in_think:
                        idx = buf.find(OPEN)
                        if idx == -1:
                            safe = buf[: max(0, len(buf) - len(OPEN) + 1)]
                            if safe:
                                answer_parts.append(safe)
                                yield f"data: {json.dumps({'type': 'token', 'text': safe})}\n\n"
                                buf = buf[len(safe):]
                            break
                        else:
                            before = buf[:idx]
                            if before:
                                answer_parts.append(before)
                                yield f"data: {json.dumps({'type': 'token', 'text': before})}\n\n"
                            buf      = buf[idx + len(OPEN):]
                            in_think = True
                            yield f"data: {json.dumps({'type': 'thinking_start'})}\n\n"
                    else:
                        idx = buf.find(CLOSE)
                        if idx == -1:
                            safe = buf[: max(0, len(buf) - len(CLOSE) + 1)]
                            if safe:
                                yield f"data: {json.dumps({'type': 'thinking_token', 'text': safe})}\n\n"
                                buf = buf[len(safe):]
                            break
                        else:
                            chunk = buf[:idx]
                            if chunk:
                                yield f"data: {json.dumps({'type': 'thinking_token', 'text': chunk})}\n\n"
                            buf      = buf[idx + len(CLOSE):]
                            in_think = False
                            yield f"data: {json.dumps({'type': 'thinking_end'})}\n\n"

            if buf.strip():
                answer_parts.append(buf)
                yield f"data: {json.dumps({'type': 'token', 'text': buf})}\n\n"

            full_text = "".join(answer_parts).strip()
            full_text = _clean_response(full_text, history)
            # Determine if course docs were in context (sources were set by build_prompt)
            used_course_docs = any(s.get("label") for s in sources)
            full_text = _ensure_mode_marker(full_text, used_course_docs=used_course_docs)
            # Strip UI-only markers before persisting — they don't belong in stored history
            import re as _re
            stored_text = _re.sub(r'\[\[MODE:[A-Z]+\]\]', '', full_text).strip()
            stored_text = _re.sub(r'\[\[[^\]]+\]\]', '', stored_text).strip()
            user_msg  = ChatMessage(session_id=session_id, role="user", content=question)
            db.add(user_msg)
            bot_msg   = ChatMessage(session_id=session_id, role="assistant", content=stored_text,
                                    sources=json.dumps(sources), had_results=True)
            db.add(bot_msg)
            db.commit()
            db.refresh(bot_msg)
            yield f"data: {json.dumps({'type': 'end', 'message_id': bot_msg.id})}\n\n"

            # Fire LLM title generation in background for new sessions
            if is_new_session and full_text:
                threading.Thread(
                    target=_auto_title, args=(session_id, question, full_text), daemon=True
                ).start()

        except Exception:
            full_text = "".join(answer_parts).strip()
            if full_text:
                try:
                    user_msg = ChatMessage(session_id=session_id, role="user", content=question)
                    db.add(user_msg)
                    bot_msg  = ChatMessage(session_id=session_id, role="assistant", content=full_text,
                                           sources=json.dumps(sources), had_results=True)
                    db.add(bot_msg)
                    db.commit()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── Bookmarks ──────────────────────────────────────────────────────────────────

class BookmarkIn(BaseModel):
    message_id: int
    note: Optional[str] = None


@router.post("/bookmarks", status_code=201)
def add_bookmark(body: BookmarkIn, db: Session = Depends(get_db),
                 current: User = Depends(get_current_user)):
    msg = db.query(ChatMessage).join(ChatSession).filter(
        ChatMessage.id == body.message_id,
        ChatMessage.role == "assistant",
        ChatSession.user_id == current.id,
    ).first()
    if not msg: raise HTTPException(404, "Mensagem não encontrada")
    if db.query(ChatBookmark).filter(ChatBookmark.user_id == current.id,
                                      ChatBookmark.message_id == body.message_id).first():
        raise HTTPException(409, "Já guardado")
    bm = ChatBookmark(user_id=current.id, message_id=body.message_id, note=body.note)
    db.add(bm); db.commit(); db.refresh(bm)
    return {"id": bm.id}


@router.get("/bookmarks")
def list_bookmarks(db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    bms = db.query(ChatBookmark).filter(ChatBookmark.user_id == current.id)\
            .order_by(ChatBookmark.created_at.desc()).all()
    out = []
    for bm in bms:
        m = bm.message
        s = db.query(ChatSession).filter(ChatSession.id == m.session_id).first()
        out.append({
            "id": bm.id, "note": bm.note,
            "message_id": bm.message_id,
            "content": m.content[:300],
            "session_id": m.session_id,
            "session_title": s.title if s else "",
            "course_id": s.course_id if s else None,
            "created_at": bm.created_at.isoformat() if bm.created_at else "",
        })
    return out


@router.delete("/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: int, db: Session = Depends(get_db),
                    current: User = Depends(get_current_user)):
    bm = db.query(ChatBookmark).filter(ChatBookmark.id == bookmark_id,
                                        ChatBookmark.user_id == current.id).first()
    if not bm: raise HTTPException(404, "Marcador não encontrado")
    db.delete(bm); db.commit()
    return {"status": "deleted"}


# ── Search ─────────────────────────────────────────────────────────────────────

@router.get("/search")
def search_messages(q: str, db: Session = Depends(get_db),
                    current: User = Depends(get_current_user)):
    if len(q) < 2: return []
    msgs = (db.query(ChatMessage).join(ChatSession)
              .filter(ChatSession.user_id == current.id,
                      ChatMessage.role == "user",
                      ChatMessage.content.ilike(f"%{q}%"))
              .order_by(ChatMessage.id.desc()).limit(20).all())
    return [{"session_id": m.session_id, "message_id": m.id,
             "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else ""}
            for m in msgs]


# ── Session export ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/export")
def export_session(session_id: int, db: Session = Depends(get_db),
                   current: User = Depends(get_current_user)):
    s = db.query(ChatSession).filter(ChatSession.id == session_id,
                                      ChatSession.user_id == current.id).first()
    if not s: raise HTTPException(404, "Sessão não encontrada")
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
             .order_by(ChatMessage.id).all()
    lines = [f"# {s.title or 'Conversa'}", f"UC: {s.course.name}", ""]
    for m in msgs:
        prefix = "Eu" if m.role == "user" else "ISLA Bot"
        lines.append(f"**{prefix}:** {m.content}")
        lines.append("")
    content = "\n".join(lines)
    return _StreamRaw(iter([content.encode()]), media_type="text/plain",
                      headers={"Content-Disposition": f"attachment; filename=conversa-{session_id}.txt"})


# ── Session summary ────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/summary")
async def summarise_session(session_id: int, db: Session = Depends(get_db),
                             current: User = Depends(get_current_user)):
    s = db.query(ChatSession).filter(ChatSession.id == session_id,
                                      ChatSession.user_id == current.id).first()
    if not s: raise HTTPException(404, "Sessão não encontrada")
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
             .order_by(ChatMessage.id).all()
    if not msgs: raise HTTPException(400, "Sessão vazia")
    history = [{"role": m.role, "content": m.content} for m in msgs]
    from ..services.knowledge import _llm_complete
    prompt = (
        "Resume a seguinte conversa académica em 5 pontos-chave em Português europeu, "
        "em formato de lista com bullet points (•). Sê conciso.\n\n"
        + "\n".join(
            f"{'Estudante' if m['role'] == 'user' else 'Assistente'}: {m['content'][:300]}"
            for m in history
        )
    )
    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(None, partial(_llm_complete, prompt, 0.3))
    return {"summary": summary}


# ── User preferences ───────────────────────────────────────────────────────────

@router.get("/preferences")
def get_preferences(db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    p = db.query(UserPreference).filter(UserPreference.user_id == current.id).first()
    if not p:
        return {"theme": "light", "font_size": "medium", "language": "pt"}
    return {"theme": p.theme, "font_size": p.font_size, "language": p.language}


class PrefsIn(BaseModel):
    theme:     Optional[str] = None
    font_size: Optional[str] = None
    language:  Optional[str] = None


@router.put("/preferences")
def save_preferences(body: PrefsIn, db: Session = Depends(get_db),
                     current: User = Depends(get_current_user)):
    p = db.query(UserPreference).filter(UserPreference.user_id == current.id).first()
    if not p:
        p = UserPreference(user_id=current.id, theme="light", font_size="medium", language="pt")
        db.add(p)
    if body.theme     in ("light", "dark"):     p.theme     = body.theme
    if body.font_size in ("small","medium","large"): p.font_size = body.font_size
    if body.language  in ("pt", "en"):          p.language  = body.language
    db.commit()
    return {"status": "ok"}


# ── Attachment OCR (voice/image/pdf in chat input) ─────────────────────────────

import os as _os
import shutil as _shutil
import tempfile as _tempfile
import base64 as _base64
from fastapi import UploadFile as _UploadFile, File as _File
from pathlib import Path as _Path

_ATTACHMENT_ALLOWED = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}
_MIME_MAP = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
             '.png': 'image/png',  '.webp': 'image/webp'}


@router.post("/process-attachment")
async def process_attachment(
    file:    _UploadFile = _File(...),
    db:      Session     = Depends(get_db),
    current: User        = Depends(get_current_user),
):
    """Extract text from a PDF or OCR an image for use in the chat input."""
    from ..services.knowledge import (
        extract_text_from_file, _provider, _resolve_provider, _openai_post,
    )
    from ..config import settings as _cfg

    suffix = _Path(file.filename or "f").suffix.lower()
    if suffix not in _ATTACHMENT_ALLOWED:
        raise HTTPException(422, "Tipo de ficheiro não suportado. Usa PDF, PNG, JPG ou WebP.")

    with _tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        _shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        # ── PDF: use pdfplumber ──────────────────────────────────────────────
        if suffix == '.pdf':
            loop     = asyncio.get_event_loop()
            raw_text = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
            return {
                "type":     "pdf",
                "text":     raw_text[:4000].strip(),
                "filename": file.filename,
                "chars":    len(raw_text),
            }

        # ── Image: multimodal LLM vision ─────────────────────────────────────
        with open(tmp_path, 'rb') as fh:
            b64 = _base64.b64encode(fh.read()).decode()
        mime    = _MIME_MAP.get(suffix, 'image/jpeg')
        ocr_q   = ("Extract all text visible in this image, preserving structure and layout. "
                   "Return only the extracted text. If no text is found, describe the image briefly.")
        provider = _provider()
        api      = _resolve_provider()

        # OpenAI-compatible providers (Gemini, OpenRouter) — send image_url message
        if api and provider in ('gemini', 'openrouter'):
            url, key, model = api
            payload = {
                "model":    model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": ocr_q},
                ]}],
                "stream": False,
            }
            import asyncio as _aio
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: _openai_post(url, key, payload, timeout=(15, 60))
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return {"type": "image", "text": text, "filename": file.filename}

        # Ollama with vision model
        if provider == 'ollama':
            import requests as _req
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _req.post(
                    f"{_cfg.ollama_base_url}/api/generate",
                    json={"model": _cfg.ollama_model, "prompt": ocr_q,
                          "images": [b64], "stream": False},
                    timeout=90,
                ),
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "").strip()
                return {"type": "image", "text": text, "filename": file.filename}

        # No vision support detected
        return {
            "type":     "image",
            "text":     "",
            "filename": file.filename,
            "warning":  "OCR de imagens requer um modelo com suporte a visão (Gemini, OpenRouter com modelo multimodal, ou Ollama com gemma3:12b).",
        }

    finally:
        _os.unlink(tmp_path)
