"""Secretaria Virtual — chat endpoints for institutional/administrative queries."""

import json
import asyncio
import threading
from datetime import datetime
from typing import List, Optional
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db, SessionLocal
from ..models import User, SecretariaSession, SecretariaMessage
from ..services.knowledge import (
    build_prompt_secretaria,
    answer_secretaria,
    stream_tokens,
    _llm_complete,
    _detect_language,
    _clean_secretaria_response,
)

router = APIRouter(prefix="/secretaria", tags=["secretaria"])


# ── Auto-title ─────────────────────────────────────────────────────────────────

def _auto_title_sec(session_id: int, question: str, answer_text: str):
    try:
        prompt = (
            "Gera um título muito curto (máx 7 palavras) em Português europeu "
            "para uma conversa com a secretaria académica. Responde APENAS com o título, "
            "sem pontuação final, sem aspas.\n\n"
            f"Pergunta: {question[:300]}\n"
            f"Resposta resumida: {answer_text[:300]}"
        )
        title = _llm_complete(prompt, temperature=0.3).strip().strip('"\'').strip()
        if title:
            db2 = SessionLocal()
            try:
                s = db2.query(SecretariaSession).filter(SecretariaSession.id == session_id).first()
                if s:
                    s.title = title[:80]
                    db2.commit()
            finally:
                db2.close()
    except Exception:
        pass


# ── Schemas ────────────────────────────────────────────────────────────────────

class SecMsgIn(BaseModel):
    session_id: Optional[int] = None
    question:   str
    language:   Optional[str] = "pt"


class SourceOut(BaseModel):
    label:    str
    page:     str
    from_web: bool = False


class SecMsgOut(BaseModel):
    session_id: int
    message_id: int
    answer:     str
    sources:    List[SourceOut]
    used_web:   bool


class SessionOut(BaseModel):
    id:         int
    title:      Optional[str]
    updated_at: str

    class Config:
        from_attributes = True


class HistoryMsg(BaseModel):
    role:    str
    content: str
    sources: Optional[List[SourceOut]] = None


class RenameIn(BaseModel):
    title: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/message", response_model=SecMsgOut)
async def send_message(
    body: SecMsgIn,
    db:   Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    if body.session_id:
        session = db.query(SecretariaSession).filter(
            SecretariaSession.id == body.session_id,
            SecretariaSession.user_id == current.id,
        ).first()
        if not session:
            raise HTTPException(404, "Sessão não encontrada")
    else:
        session = SecretariaSession(user_id=current.id, title=body.question[:80])
        db.add(session)
        db.commit()
        db.refresh(session)

    past    = db.query(SecretariaMessage).filter(SecretariaMessage.session_id == session.id).order_by(SecretariaMessage.id).all()
    history = [{"role": m.role, "content": m.content} for m in past]
    lang    = body.language or _detect_language(body.question)

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        partial(answer_secretaria, question=body.question, history=history,
                language=lang, user_name=current.name, user_role=current.role.value),
    )

    import re as _re
    stored_answer = _re.sub(r'\[\[FONTE:[A-Z]+\]\]', '', result["answer"]).strip()

    user_msg = SecretariaMessage(session_id=session.id, role="user", content=body.question)
    db.add(user_msg)
    bot_msg = SecretariaMessage(
        session_id=session.id, role="assistant", content=stored_answer,
        sources=json.dumps(result["sources"]),
    )
    db.add(bot_msg)
    db.commit()
    db.refresh(bot_msg)

    return SecMsgOut(
        session_id=session.id,
        message_id=bot_msg.id,
        answer=result["answer"],
        sources=[SourceOut(**s) for s in result["sources"]],
        used_web=bool(result.get("used_web")),
    )


@router.post("/message/stream")
async def stream_message(
    body: SecMsgIn,
    db:   Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    if body.session_id:
        session = db.query(SecretariaSession).filter(
            SecretariaSession.id == body.session_id,
            SecretariaSession.user_id == current.id,
        ).first()
        if not session:
            raise HTTPException(404, "Sessão não encontrada")
    else:
        session = SecretariaSession(user_id=current.id, title=body.question[:80])
        db.add(session)
        db.commit()
        db.refresh(session)

    past    = db.query(SecretariaMessage).filter(SecretariaMessage.session_id == session.id).order_by(SecretariaMessage.id).all()
    history = [{"role": m.role, "content": m.content} for m in past]
    lang    = body.language or _detect_language(body.question)
    is_new  = not past

    loop = asyncio.get_running_loop()
    has_ctx, messages, sources, used_web = await loop.run_in_executor(
        None,
        partial(build_prompt_secretaria, body.question, history, lang, current.name, current.role.value),
    )

    session_id = session.id
    question   = body.question

    async def event_generator():
        yield f"data: {json.dumps({'type': 'meta', 'session_id': session_id, 'sources': sources, 'used_web': used_web})}\n\n"

        if not has_ctx:
            fallback = messages
            user_msg = SecretariaMessage(session_id=session_id, role="user", content=question)
            db.add(user_msg)
            bot_msg  = SecretariaMessage(session_id=session_id, role="assistant", content=fallback,
                                         sources=json.dumps([]))
            db.add(bot_msg)
            db.commit()
            db.refresh(bot_msg)
            yield f"data: {json.dumps({'type': 'token', 'text': fallback})}\n\n"
            yield f"data: {json.dumps({'type': 'end', 'message_id': bot_msg.id})}\n\n"
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
            full_text = _clean_secretaria_response(full_text, history)

            # Ensure source marker
            if len(full_text) >= 60 and "[[FONTE:" not in full_text:
                if used_web:
                    full_text = full_text.rstrip() + "\n[[FONTE:WEB]]"
                elif sources:
                    full_text = full_text.rstrip() + "\n[[FONTE:DOCS]]"
                else:
                    full_text = full_text.rstrip() + "\n[[FONTE:GERAL]]"

            import re as _re
            stored_text = _re.sub(r'\[\[FONTE:[A-Z]+\]\]', '', full_text).strip()

            user_msg = SecretariaMessage(session_id=session_id, role="user", content=question)
            db.add(user_msg)
            bot_msg  = SecretariaMessage(session_id=session_id, role="assistant", content=stored_text,
                                          sources=json.dumps(sources))
            db.add(bot_msg)
            db.commit()
            db.refresh(bot_msg)
            yield f"data: {json.dumps({'type': 'end', 'message_id': bot_msg.id})}\n\n"

            if is_new and full_text:
                threading.Thread(
                    target=_auto_title_sec, args=(session_id, question, full_text), daemon=True
                ).start()

        except Exception:
            full_text = "".join(answer_parts).strip()
            if full_text:
                try:
                    user_msg = SecretariaMessage(session_id=session_id, role="user", content=question)
                    db.add(user_msg)
                    bot_msg  = SecretariaMessage(session_id=session_id, role="assistant", content=full_text,
                                                  sources=json.dumps(sources))
                    db.add(bot_msg)
                    db.commit()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    sessions = db.query(SecretariaSession).filter(
        SecretariaSession.user_id == current.id
    ).order_by(SecretariaSession.updated_at.desc()).all()
    return [SessionOut(id=s.id, title=s.title, updated_at=s.updated_at.isoformat()) for s in sessions]


@router.get("/sessions/{session_id}/messages", response_model=List[HistoryMsg])
def get_messages(session_id: int, db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    session = db.query(SecretariaSession).filter(
        SecretariaSession.id == session_id,
        SecretariaSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(404, "Sessão não encontrada")
    msgs = db.query(SecretariaMessage).filter(SecretariaMessage.session_id == session_id).order_by(SecretariaMessage.id).all()
    return [HistoryMsg(role=m.role, content=m.content,
                       sources=json.loads(m.sources) if m.sources else []) for m in msgs]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    session = db.query(SecretariaSession).filter(
        SecretariaSession.id == session_id,
        SecretariaSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(404, "Sessão não encontrada")
    db.query(SecretariaMessage).filter(SecretariaMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"status": "deleted"}


@router.patch("/sessions/{session_id}")
def rename_session(session_id: int, body: RenameIn, db: Session = Depends(get_db),
                   current: User = Depends(get_current_user)):
    session = db.query(SecretariaSession).filter(
        SecretariaSession.id == session_id,
        SecretariaSession.user_id == current.id,
    ).first()
    if not session:
        raise HTTPException(404, "Sessão não encontrada")
    session.title = body.title[:80]
    db.commit()
    return {"status": "ok", "title": session.title}
