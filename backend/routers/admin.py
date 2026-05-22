from typing import List, Optional
"""Admin panel endpoints — knowledge base management."""

import csv
import io
import os
import uuid
import shutil
import tempfile
import asyncio
from functools import partial
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from datetime import datetime, timedelta
from sqlalchemy import func
from fastapi.responses import StreamingResponse as _StreamingResponse
from ..auth import require_role, hash_password
from ..database import get_db
from ..models import User, Course, Program, Enrollment, Teaching, Semester, UserRole, ChatMessage, ChatSession, SystemSetting, Document
from ..services.knowledge import (
    course_knowledge_path,
    general_knowledge_path,
    secretaria_knowledge_path,
    read_knowledge,
    save_knowledge,
    append_to_knowledge,
    is_duplicate,
    extract_text_from_file,
    organize_with_llm,
    organize_with_topics,
    parse_topic_blocks,
    reorganize_knowledge_files,
    list_documents_in_file,
    delete_document_block,
)

router = APIRouter(prefix="/admin", tags=["admin"])

class ProgramIn(BaseModel):
    name: str

class ProgramOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


# In-memory job store  { job_id: { status, organized?, error?, doc_label, _ts } }
# Entries are evicted after _JOBS_TTL seconds to prevent unbounded memory growth.
import time as _time
_jobs: dict = {}
_JOBS_TTL   = 3_600  # 1 hour


def _cleanup_jobs():
    """Remove finished/errored jobs older than _JOBS_TTL seconds."""
    cutoff = _time.monotonic() - _JOBS_TTL
    stale  = [k for k, v in _jobs.items() if v.get("_ts", 0) < cutoff]
    for k in stale:
        del _jobs[k]


# ── Schemas ────────────────────────────────────────────────────────────────────

class KnowledgeUpdateIn(BaseModel):
    content: str


class SaveIn(BaseModel):
    content:          str
    doc_label:        str
    course_id:        Optional[int] = None
    semester_id:      Optional[int] = None
    teacher_name:     Optional[str] = None  # attributed instructor name


class TopicItem(BaseModel):
    name:    str
    content: str


class SaveTopicsIn(BaseModel):
    doc_label:    str
    topics:       list[TopicItem]
    course_id:    Optional[int] = None
    semester_id:  Optional[int] = None
    teacher_name: Optional[str] = None



# ── Programs (Cursos) ─────────────────────────────────────────────────────────

@router.get("/programs")
def list_programs(
    db: Session = Depends(get_db),
    current: User = Depends(require_role(UserRole.admin)),
):
    """Return all programs (courses/degrees) with their subject count."""
    programs = db.query(Program).all()
    # Build subject counts in a single query
    from sqlalchemy import func as _func
    subject_counts = dict(
        db.query(Course.program_id, _func.count(Course.id))
        .filter(Course.program_id.isnot(None))
        .group_by(Course.program_id)
        .all()
    )
    return [
        {"id": p.id, "name": p.name, "subject_count": subject_counts.get(p.id, 0)}
        for p in programs
    ]

@router.post("/programs", response_model=ProgramOut)
def create_program(body: ProgramIn, db: Session = Depends(get_db), current: User = Depends(require_role(UserRole.admin))):
    prog = Program(name=body.name)
    db.add(prog)
    db.commit()
    db.refresh(prog)
    return prog

@router.patch("/programs/{prog_id}", response_model=ProgramOut)
def update_program(prog_id: int, body: ProgramIn, db: Session = Depends(get_db), current: User = Depends(require_role(UserRole.admin))):
    prog = db.query(Program).filter(Program.id == prog_id).first()
    if not prog: raise HTTPException(404, "Curso não encontrado")
    prog.name = body.name
    db.commit()
    db.refresh(prog)
    return prog

@router.delete("/programs/{prog_id}")
def delete_program(prog_id: int, db: Session = Depends(get_db), current: User = Depends(require_role(UserRole.admin))):
    prog = db.query(Program).filter(Program.id == prog_id).first()
    if not prog: raise HTTPException(404, "Curso não encontrado")
    db.delete(prog)
    db.commit()
    return {"status": "deleted"}

@router.get("/programs/{prog_id}/subjects")
def list_program_subjects(
    prog_id: int,
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    """Return all subjects (UCs) that belong to a specific program/course."""
    prog = db.query(Program).filter(Program.id == prog_id).first()
    if not prog: raise HTTPException(404, "Curso não encontrado")
    subjects = db.query(Course).filter(Course.program_id == prog_id).all()
    return [
        {
            "id":          s.id,
            "code":        s.code,
            "name":        s.name,
            "short_name":  s.short_name,
            "semester_id": s.semester_id,
            "semester":    s.semester.name if s.semester else "",
        }
        for s in subjects
    ]


# ── Courses list ───────────────────────────────────────────────────────────────

@router.get("/courses")
def list_courses(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    if current.role == UserRole.instructor:
        # Instructors only see courses they teach
        teaching_ids = {t.course_id for t in db.query(Teaching).filter(Teaching.instructor_id == current.id).all()}
        courses = db.query(Course).filter(Course.id.in_(teaching_ids)).all() if teaching_ids else []
    else:
        courses = db.query(Course).all()
    return [
        {
            "id":          c.id,
            "code":        c.code,
            "name":        c.name,
            "short_name":  c.short_name,
            "semester_id": c.semester_id,
            "semester":    c.semester.name if c.semester else "",
        }
        for c in courses
    ]


# ── Extract document ───────────────────────────────────────────────────────────

@router.post("/extract")
async def extract_document(
    doc_label: str        = Form("Documento"),
    file:      UploadFile = File(...),
    current:   User       = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Upload a file, extract + organize with LLM, return the result for admin review."""
    suffix = os.path.splitext(file.filename or "doc.txt")[1] or ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        loop      = asyncio.get_event_loop()
        raw_text  = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
        label     = doc_label or file.filename or "Documento"
        organized = await loop.run_in_executor(
            None, partial(organize_with_llm, raw_text, label)
        )
    finally:
        os.unlink(tmp_path)

    return {
        "organized": organized,
        "filename":  file.filename,
        "doc_label": doc_label,
        "raw_chars": len(raw_text),
    }


@router.post("/extract-raw")
async def extract_raw(
    file:    UploadFile = File(...),
    current: User       = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Stage 1: upload file and extract plain text only — no LLM step."""
    suffix = os.path.splitext(file.filename or "doc.txt")[1] or ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        loop     = asyncio.get_event_loop()
        raw_text = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
    finally:
        os.unlink(tmp_path)

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Não foi possível extrair texto deste ficheiro.")

    return {"raw_text": raw_text, "filename": file.filename, "raw_chars": len(raw_text)}


class OrganizeIn(BaseModel):
    raw_text:  str
    doc_label: str


@router.post("/organize")
async def organize(
    body:    OrganizeIn,
    current: User = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Stage 2 (blocking): run LLM organization on already-extracted raw text."""
    loop      = asyncio.get_event_loop()
    organized = await loop.run_in_executor(
        None, partial(organize_with_llm, body.raw_text, body.doc_label)
    )
    return {"organized": organized}


# ── Async job queue (survives navigation) ─────────────────────────────────────

def _run_organize_job(job_id: str, raw_text: str, doc_label: str):
    try:
        organized = organize_with_llm(raw_text, doc_label)
        _jobs[job_id] = {"status": "done", "organized": organized,
                         "doc_label": doc_label, "_ts": _time.monotonic()}
    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc),
                         "doc_label": doc_label, "_ts": _time.monotonic()}


@router.post("/jobs/organize")
async def start_organize_job(
    body:             OrganizeIn,
    background_tasks: BackgroundTasks,
    current:          User = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Start LLM organization as a background job; returns job_id immediately."""
    _cleanup_jobs()  # evict stale jobs before adding a new one
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "doc_label": body.doc_label, "_ts": _time.monotonic()}
    background_tasks.add_task(_run_organize_job, job_id, body.raw_text, body.doc_label)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id:  str,
    current: User = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Poll the status of an organize job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return {**job, "job_id": job_id}


# ── Reorganize existing knowledge files ───────────────────────────────────────

def _run_reorganize_job(job_id: str, target: str,
                        course_id: Optional[int] = None, semester_id: Optional[int] = None):
    def _progress(info: dict):
        _jobs[job_id] = {**_jobs.get(job_id, {}), "status": "running", **info,
                         "_ts": _time.monotonic()}
    try:
        summary = reorganize_knowledge_files(target, course_id=course_id,
                                             semester_id=semester_id, progress_cb=_progress)
        _jobs[job_id] = {"status": "done", "summary": summary, "_ts": _time.monotonic()}
    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc), "_ts": _time.monotonic()}


class ReorganizeIn(BaseModel):
    target:      str          = "all"   # "all" | "course" | "general"
    course_id:   Optional[int] = None   # if set, reorganize only this course file
    semester_id: Optional[int] = None


@router.post("/jobs/reorganize", status_code=202)
async def start_reorganize_job(
    body:             ReorganizeIn,
    background_tasks: BackgroundTasks,
    current:          User = Depends(require_role(UserRole.admin)),
):
    """Re-clean all knowledge blocks with the thorough reorganize prompt (background job)."""
    _cleanup_jobs()
    job_id = str(uuid.uuid4())
    label  = (f"reorganize:course_{body.course_id}" if body.course_id
              else f"reorganize:{body.target}")
    _jobs[job_id] = {"status": "running", "doc_label": label, "_ts": _time.monotonic()}
    background_tasks.add_task(_run_reorganize_job, job_id, body.target,
                              body.course_id, body.semester_id)
    return {"job_id": job_id}


# ── Bulk ingest (extract raw text, no LLM organize) ───────────────────────────

@router.post("/ingest")
async def ingest_document(
    doc_label:   str        = Form("Documento"),
    course_id:   Optional[int]  = Form(None),
    semester_id: Optional[int]  = Form(None),
    file:        UploadFile = File(...),
    current:     User       = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Extract raw text and save directly — no LLM step, fast for bulk uploads."""
    suffix = os.path.splitext(file.filename or "doc.txt")[1] or ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        loop     = asyncio.get_event_loop()
        raw_text = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
    finally:
        os.unlink(tmp_path)

    if not raw_text.strip():
        return {"status": "skipped", "reason": "no text extracted", "filename": file.filename}

    label = doc_label or file.filename or "Documento"
    path  = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    if is_duplicate(path, label):
        return {"status": "duplicate", "filename": file.filename}

    append_to_knowledge(path, raw_text, label)
    return {"status": "saved", "filename": file.filename, "total_chars": len(read_knowledge(path))}


# ── Save to knowledge file ─────────────────────────────────────────────────────

@router.post("/save")
def save_document(
    body:    SaveIn,
    current: User = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path = (
        course_knowledge_path(body.course_id, body.semester_id)
        if body.course_id and body.semester_id
        else general_knowledge_path()
    )
    # Include teacher name in block label when uploaded by an instructor
    teacher = body.teacher_name or (current.name if current.role == UserRole.instructor else None)
    label = f"{body.doc_label} [Docente: {teacher}]" if teacher else body.doc_label
    append_to_knowledge(path, body.content, label)
    return {"status": "saved", "total_chars": len(read_knowledge(path))}


# ── Save topic blocks (instructor portal) ─────────────────────────────────────

@router.post("/save-topics")
def save_document_topics(
    body:    SaveTopicsIn,
    current: User = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Save each topic as a separate knowledge block — used by instructor portal."""
    path = (
        course_knowledge_path(body.course_id, body.semester_id)
        if body.course_id and body.semester_id
        else general_knowledge_path()
    )
    teacher = body.teacher_name or (current.name if current.role == UserRole.instructor else None)
    saved = 0
    for topic in body.topics:
        base  = f"{body.doc_label} — {topic.name}"
        label = f"{base} [Docente: {teacher}]" if teacher else base
        if not is_duplicate(path, label):
            append_to_knowledge(path, topic.content, label)
            saved += 1
    return {"status": "saved", "topics_saved": saved, "total_chars": len(read_knowledge(path))}


# ── Extract with topic structure (instructor portal) ──────────────────────────

@router.post("/extract-topics")
async def extract_topics(
    doc_label: str        = Form("Documento"),
    file:      UploadFile = File(...),
    current:   User       = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Upload a file → extract text → organise into topics with LLM → return [{name, content}]."""
    suffix = os.path.splitext(file.filename or "doc.txt")[1] or ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        loop      = asyncio.get_event_loop()
        raw_text  = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
        label     = doc_label or file.filename or "Documento"
        organized = await loop.run_in_executor(
            None, partial(organize_with_topics, raw_text, label)
        )
    finally:
        os.unlink(tmp_path)

    topics = parse_topic_blocks(organized)
    return {
        "topics":    topics,
        "organized": organized,
        "filename":  file.filename,
        "doc_label": doc_label,
        "raw_chars": len(raw_text),
    }


# ── Read / update / clear knowledge file ──────────────────────────────────────

@router.get("/knowledge")
def get_knowledge(
    course_id:   Optional[int] = None,
    semester_id: Optional[int] = None,
    offset:      int           = 0,
    limit:       Optional[int] = None,
    current:     User          = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path    = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    content = read_knowledge(path)
    total   = len(content)
    chunk   = content[offset : offset + limit] if limit is not None else content
    return {
        "content":  chunk,
        "chars":    total,
        "offset":   offset,
        "limit":    limit,
        "has_more": limit is not None and (offset + limit) < total,
    }


@router.put("/knowledge")
def update_knowledge(
    body:        KnowledgeUpdateIn,
    course_id:   Optional[int] = None,
    semester_id: Optional[int] = None,
    current:     User          = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    save_knowledge(path, body.content)
    return {"status": "updated", "chars": len(body.content)}


@router.get("/insights")
def get_insights(
    course_id: Optional[int] = None,
    db:        Session        = Depends(get_db),
    current:   User           = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Return analytics from student chat to identify knowledge gaps and quality issues."""
    base = db.query(ChatMessage).join(ChatSession).filter(ChatMessage.role == "assistant")
    if current.role == UserRole.instructor and not course_id:
        # Auto-scope to instructor's own courses
        my_course_ids = [t.course_id for t in db.query(Teaching).filter(Teaching.instructor_id == current.id).all()]
        if my_course_ids:
            base = base.filter(ChatSession.course_id.in_(my_course_ids))
    elif course_id:
        base = base.filter(ChatSession.course_id == course_id)

    all_msgs = base.all()
    total    = len(all_msgs)

    # Failed queries — retrieval found nothing
    failed = [
        {"question": _user_question(db, m), "course_id": m.session.course_id}
        for m in all_msgs if m.had_results is False
    ]

    # Low-rated answers
    low_rated = [
        {
            "question":        _user_question(db, m),
            "answer_excerpt":  m.content[:200],
            "course_id":       m.session.course_id,
            "retrieval_score": m.retrieval_score,
        }
        for m in all_msgs if m.rating == -1
    ]

    # Rating stats
    rated      = [m for m in all_msgs if m.rating is not None]
    thumbs_up  = sum(1 for m in rated if m.rating == 1)
    thumbs_down= sum(1 for m in rated if m.rating == -1)

    return {
        "total_answers":   total,
        "rated":           len(rated),
        "thumbs_up":       thumbs_up,
        "thumbs_down":     thumbs_down,
        "failed_queries":  failed,
        "low_rated":       low_rated,
    }


# ── Settings ───────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    row = db.query(SystemSetting).filter(SystemSetting.key == "rate_limit_per_hour").first()
    return {"rate_limit_per_hour": int(row.value) if row else 0}


class SettingsIn(BaseModel):
    rate_limit_per_hour: int


@router.put("/settings")
def save_settings(
    body:    SettingsIn,
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    if body.rate_limit_per_hour < 0:
        raise HTTPException(status_code=422, detail="Limite não pode ser negativo")
    row = db.query(SystemSetting).filter(SystemSetting.key == "rate_limit_per_hour").first()
    if row:
        row.value = str(body.rate_limit_per_hour)
    else:
        db.add(SystemSetting(key="rate_limit_per_hour", value=str(body.rate_limit_per_hour)))
    db.commit()
    return {"status": "ok", "rate_limit_per_hour": body.rate_limit_per_hour}


# ── Usage stats ────────────────────────────────────────────────────────────────

@router.get("/usage")
def get_usage(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    limit_row = db.query(SystemSetting).filter(SystemSetting.key == "rate_limit_per_hour").first()
    rate_limit = int(limit_row.value) if limit_row else 0

    now     = datetime.utcnow()
    h1_ago  = now - timedelta(hours=1)
    h24_ago = now - timedelta(hours=24)
    d7_ago  = now - timedelta(days=7)

    # --- single batched query for all students + all time windows ---
    # Counts messages grouped by (user_id, window_label) in one DB round-trip.
    from sqlalchemy import case
    window_col = case(
        (ChatMessage.created_at >= h1_ago,  "h1"),
        (ChatMessage.created_at >= h24_ago, "h24"),
        else_="w7",
    )
    rows_q = (
        db.query(
            ChatSession.user_id,
            window_col.label("window"),
            func.count(ChatMessage.id).label("cnt"),
        )
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .join(User, User.id == ChatSession.user_id)
        .filter(
            User.role == UserRole.student,
            ChatMessage.role == "user",
            ChatMessage.created_at >= d7_ago,
        )
        .group_by(ChatSession.user_id, window_col)
        .all()
    )

    # Aggregate per user_id
    from collections import defaultdict
    counts: dict[int, dict] = defaultdict(lambda: {"h1": 0, "h24": 0, "w7": 0})
    for uid, window, cnt in rows_q:
        counts[uid][window] += cnt
        # h1 messages are also part of h24 and w7 — propagate upward
        if window == "h1":
            counts[uid]["h24"] += cnt
            counts[uid]["w7"]  += cnt
        elif window == "h24":
            counts[uid]["w7"]  += cnt

    if not counts:
        return {"users": [], "rate_limit_per_hour": rate_limit}

    # Fetch user details only for active users (one query)
    users = (
        db.query(User)
        .filter(User.role == UserRole.student, User.id.in_(counts.keys()))
        .all()
    )

    rows = []
    for u in users:
        c = counts[u.id]
        rows.append({
            "id":         u.id,
            "name":       u.name,
            "email":      u.email,
            "last_hour":  c["h1"],
            "last_day":   c["h24"],
            "last_week":  c["w7"],
            "over_limit": rate_limit > 0 and c["h1"] >= rate_limit,
        })

    rows.sort(key=lambda r: r["last_day"], reverse=True)
    return {"users": rows, "rate_limit_per_hour": rate_limit}


def _user_question(db, bot_msg: ChatMessage) -> str:
    """Return the user message that immediately preceded this bot message."""
    prev = db.query(ChatMessage).filter(
        ChatMessage.session_id == bot_msg.session_id,
        ChatMessage.id < bot_msg.id,
        ChatMessage.role == "user",
    ).order_by(ChatMessage.id.desc()).first()
    return prev.content if prev else ""


@router.get("/documents")
def list_documents(
    course_id:   Optional[int] = None,
    semester_id: Optional[int] = None,
    current:     User          = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    return {"documents": list_documents_in_file(path)}


@router.delete("/documents/{doc_label:path}")
def delete_document(
    doc_label:   str,
    course_id:   Optional[int] = None,
    semester_id: Optional[int] = None,
    current:     User          = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    found = delete_document_block(path, doc_label)
    if not found:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return {"status": "deleted", "doc_label": doc_label}


@router.delete("/knowledge")
def clear_knowledge(
    course_id:   Optional[int] = None,
    semester_id: Optional[int] = None,
    current:     User          = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    path = (
        course_knowledge_path(course_id, semester_id)
        if course_id and semester_id
        else general_knowledge_path()
    )
    save_knowledge(path, "")
    return {"status": "cleared"}


# ── Dashboard stats ────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    now     = datetime.utcnow()
    today   = now - timedelta(hours=24)
    week    = now - timedelta(days=7)

    total_students    = db.query(func.count(User.id)).filter(User.role == UserRole.student).scalar() or 0
    total_instructors = db.query(func.count(User.id)).filter(User.role == UserRole.instructor).scalar() or 0
    msgs_today        = (db.query(func.count(ChatMessage.id))
                          .filter(ChatMessage.role == "user", ChatMessage.created_at >= today)
                          .scalar() or 0)
    msgs_week         = (db.query(func.count(ChatMessage.id))
                          .filter(ChatMessage.role == "user", ChatMessage.created_at >= week)
                          .scalar() or 0)
    thumbs_up         = db.query(func.count(ChatMessage.id)).filter(ChatMessage.rating == 1).scalar() or 0
    thumbs_down       = db.query(func.count(ChatMessage.id)).filter(ChatMessage.rating == -1).scalar() or 0

    # Top 5 courses by messages this week
    top_courses = (
        db.query(Course.name, Course.short_name, func.count(ChatMessage.id).label("msgs"))
        .join(ChatSession, ChatSession.course_id == Course.id)
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .filter(ChatMessage.role == "user", ChatMessage.created_at >= week)
        .group_by(Course.id)
        .order_by(func.count(ChatMessage.id).desc())
        .limit(5)
        .all()
    )

    # Recent 8 messages
    recent = (
        db.query(ChatMessage, ChatSession, User)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .join(User, ChatSession.user_id == User.id)
        .filter(ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .limit(8)
        .all()
    )

    # Messages per day — last 14 days
    days_14 = now - timedelta(days=14)
    daily_rows = (
        db.query(
            func.date(ChatMessage.created_at).label("day"),
            func.count(ChatMessage.id).label("cnt"),
        )
        .filter(ChatMessage.role == "user", ChatMessage.created_at >= days_14)
        .group_by(func.date(ChatMessage.created_at))
        .order_by(func.date(ChatMessage.created_at))
        .all()
    )
    daily_map = {str(r.day): r.cnt for r in daily_rows}
    daily_labels, daily_counts = [], []
    for i in range(14, -1, -1):
        d = (now - timedelta(days=i)).date()
        daily_labels.append(d.strftime("%d/%m"))
        daily_counts.append(daily_map.get(str(d), 0))

    # Unique active students last 14 days
    active_students = (
        db.query(func.count(func.distinct(ChatSession.user_id)))
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .filter(ChatMessage.role == "user", ChatMessage.created_at >= days_14)
        .scalar() or 0
    )

    return {
        "total_students":    total_students,
        "total_instructors": total_instructors,
        "msgs_today":        msgs_today,
        "msgs_week":         msgs_week,
        "thumbs_up":         thumbs_up,
        "thumbs_down":       thumbs_down,
        "active_students":   active_students,
        "daily_labels":      daily_labels,
        "daily_counts":      daily_counts,
        "top_courses": [
            {"name": c.short_name or c.name, "msgs": c.msgs}
            for c in top_courses
        ],
        "recent_activity": [
            {
                "user":    u.name,
                "content": m.content[:80],
                "time":    m.created_at.isoformat() if m.created_at else "",
            }
            for m, s, u in recent
        ],
    }


# ── User management ────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    users = db.query(User).order_by(User.role, User.name).all()
    
    # Aggregate counts in exactly 2 queries instead of 2N queries
    enroll_counts = dict(db.query(Enrollment.student_id, func.count(Enrollment.id)).group_by(Enrollment.student_id).all())
    
    msg_counts = dict(
        db.query(ChatSession.user_id, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .filter(ChatMessage.role == "user")
        .group_by(ChatSession.user_id)
        .all()
    )

    out = []
    for u in users:
        out.append({
            "id":         u.id,
            "name":       u.name,
            "email":      u.email,
            "role":       u.role,
            "is_active":  u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else "",
            "enrollments": enroll_counts.get(u.id, 0),
            "total_msgs":  msg_counts.get(u.id, 0),
        })
    return out


class UserCreateIn(BaseModel):
    name:     str
    email:    str
    password: str
    role:     str = "student"


class UserUpdateIn(BaseModel):
    name:      Optional[str] = None
    email:     Optional[str] = None
    role:      Optional[str] = None
    is_active: Optional[bool] = None
    password:  Optional[str] = None


@router.post("/users", status_code=201)
def create_user(
    body:    UserCreateIn,
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email já registado")
    try:
        role = UserRole(body.role)
    except ValueError:
        raise HTTPException(status_code=422, detail="Role inválido")
    user = User(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body:    UserUpdateIn,
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    if user.id == current.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Não podes desativar a tua própria conta")
    if body.name      is not None: user.name      = body.name
    if body.email     is not None: user.email     = body.email
    if body.is_active is not None: user.is_active = body.is_active
    if body.password  is not None: user.password_hash = hash_password(body.password)
    if body.role      is not None:
        try:
            user.role = UserRole(body.role)
        except ValueError:
            raise HTTPException(status_code=422, detail="Role inválido")
    db.commit()
    return {"status": "ok"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    if user.id == current.id:
        raise HTTPException(status_code=400, detail="Não podes eliminar a tua própria conta")
    db.query(Enrollment).filter(Enrollment.student_id == user_id).delete()
    db.query(Teaching).filter(Teaching.instructor_id == user_id).delete()
    # sessions + messages cascade via foreign keys or delete manually
    session_ids = [s.id for s in db.query(ChatSession).filter(ChatSession.user_id == user_id).all()]
    if session_ids:
        db.query(ChatMessage).filter(ChatMessage.session_id.in_(session_ids)).delete()
        db.query(ChatSession).filter(ChatSession.user_id == user_id).delete()
    db.delete(user)
    db.commit()
    return {"status": "deleted"}


# ── Semester management ────────────────────────────────────────────────────────

class SemesterIn(BaseModel):
    name:       str
    start_date: str  # ISO date string
    end_date:   str
    is_active:  bool = False


@router.get("/semesters")
def list_semesters(db: Session = Depends(get_db), current: User = Depends(require_role(UserRole.admin))):
    from datetime import date
    rows = db.query(Semester).order_by(Semester.start_date.desc()).all()
    return [{"id": s.id, "name": s.name, "start_date": str(s.start_date),
             "end_date": str(s.end_date), "is_active": s.is_active} for s in rows]


@router.post("/semesters", status_code=201)
def create_semester(body: SemesterIn, db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    from datetime import date
    if body.is_active:
        db.query(Semester).update({"is_active": False})
    s = Semester(name=body.name,
                 start_date=date.fromisoformat(body.start_date),
                 end_date=date.fromisoformat(body.end_date),
                 is_active=body.is_active)
    db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id, "name": s.name}


@router.patch("/semesters/{sem_id}")
def update_semester(sem_id: int, body: SemesterIn, db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    from datetime import date
    s = db.query(Semester).filter(Semester.id == sem_id).first()
    if not s: raise HTTPException(404, "Semestre não encontrado")
    if body.is_active:
        db.query(Semester).filter(Semester.id != sem_id).update({"is_active": False})
    s.name = body.name
    s.start_date = date.fromisoformat(body.start_date)
    s.end_date   = date.fromisoformat(body.end_date)
    s.is_active  = body.is_active
    db.commit()
    return {"status": "ok"}


@router.delete("/semesters/{sem_id}")
def delete_semester(sem_id: int, db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    s = db.query(Semester).filter(Semester.id == sem_id).first()
    if not s: raise HTTPException(404, "Semestre não encontrado")
    if db.query(Course).filter(Course.semester_id == sem_id).first():
        raise HTTPException(400, "Existem UCs neste semestre — remove-as primeiro")
    db.delete(s); db.commit()
    return {"status": "deleted"}


# ── Course management ──────────────────────────────────────────────────────────





class CourseIn(BaseModel):
    code:        str
    name:        str
    short_name:  Optional[str] = None
    program_id: Optional[int] = None
    semester_id: int


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, db: Session = Depends(get_db),
                  current: User = Depends(require_role(UserRole.admin))):
    if not db.query(Semester).filter(Semester.id == body.semester_id).first():
        raise HTTPException(404, "Semestre não encontrado")
    c = Course(code=body.code, name=body.name, short_name=body.short_name,
               program_id=body.program_id, semester_id=body.semester_id)
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "name": c.name}


@router.patch("/courses/{course_id}")
def update_course(course_id: int, body: CourseIn, db: Session = Depends(get_db),
                  current: User = Depends(require_role(UserRole.admin))):
    c = db.query(Course).filter(Course.id == course_id).first()
    if not c: raise HTTPException(404, "UC não encontrada")
    c.code = body.code; c.name = body.name
    c.short_name = body.short_name; c.semester_id = body.semester_id; c.program_id = body.program_id
    db.commit()
    return {"status": "ok"}


@router.delete("/courses/{course_id}")
def delete_course(course_id: int, db: Session = Depends(get_db),
                  current: User = Depends(require_role(UserRole.admin))):
    c = db.query(Course).filter(Course.id == course_id).first()
    if not c: raise HTTPException(404, "UC não encontrada")
    db.query(Enrollment).filter(Enrollment.course_id == course_id).delete()
    db.query(Teaching).filter(Teaching.course_id == course_id).delete()
    session_ids = [s.id for s in db.query(ChatSession).filter(ChatSession.course_id == course_id).all()]
    if session_ids:
        db.query(ChatMessage).filter(ChatMessage.session_id.in_(session_ids)).delete()
        db.query(ChatSession).filter(ChatSession.course_id == course_id).delete()
    db.delete(c); db.commit()
    return {"status": "deleted"}


@router.get("/courses/{course_id}/enrollments")
def get_enrollments(course_id: int, db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin, UserRole.instructor))):
    rows = db.query(Enrollment).filter(Enrollment.course_id == course_id).all()
    return [{"id": e.id, "student_id": e.student_id,
             "name": e.student.name, "email": e.student.email} for e in rows]


class EnrollIn(BaseModel):
    student_id: int


@router.post("/courses/{course_id}/enrollments", status_code=201)
def add_enrollment(course_id: int, body: EnrollIn, db: Session = Depends(get_db),
                   current: User = Depends(require_role(UserRole.admin))):
    if db.query(Enrollment).filter(Enrollment.course_id == course_id,
                                    Enrollment.student_id == body.student_id).first():
        raise HTTPException(409, "Aluno já inscrito")
    db.add(Enrollment(course_id=course_id, student_id=body.student_id))
    db.commit()
    return {"status": "enrolled"}


@router.delete("/courses/{course_id}/enrollments/{student_id}")
def remove_enrollment(course_id: int, student_id: int, db: Session = Depends(get_db),
                      current: User = Depends(require_role(UserRole.admin))):
    e = db.query(Enrollment).filter(Enrollment.course_id == course_id,
                                     Enrollment.student_id == student_id).first()
    if not e: raise HTTPException(404, "Inscrição não encontrada")
    db.delete(e); db.commit()
    return {"status": "removed"}


class BulkEnrollIn(BaseModel):
    emails: str  # newline or comma separated


@router.post("/courses/{course_id}/bulk-enroll")
def bulk_enroll(course_id: int, body: BulkEnrollIn, db: Session = Depends(get_db),
                current: User = Depends(require_role(UserRole.admin))):
    import re
    emails = [e.strip().lower() for e in re.split(r'[\n,;]+', body.emails) if e.strip()]
    added = skipped = not_found = 0
    for email in emails:
        user = db.query(User).filter(func.lower(User.email) == email).first()
        if not user: not_found += 1; continue
        if db.query(Enrollment).filter(Enrollment.course_id == course_id,
                                        Enrollment.student_id == user.id).first():
            skipped += 1; continue
        db.add(Enrollment(course_id=course_id, student_id=user.id))
        added += 1
    db.commit()
    return {"added": added, "skipped": skipped, "not_found": not_found}


@router.get("/courses/{course_id}/instructors")
def get_instructors(course_id: int, db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    rows = db.query(Teaching).filter(Teaching.course_id == course_id).all()
    return [{"id": t.id, "instructor_id": t.instructor_id,
             "name": t.instructor.name, "email": t.instructor.email} for t in rows]


class TeachIn(BaseModel):
    instructor_id: int


@router.post("/courses/{course_id}/instructors", status_code=201)
def add_instructor(course_id: int, body: TeachIn, db: Session = Depends(get_db),
                   current: User = Depends(require_role(UserRole.admin))):
    if db.query(Teaching).filter(Teaching.course_id == course_id,
                                  Teaching.instructor_id == body.instructor_id).first():
        raise HTTPException(409, "Docente já atribuído")
    db.add(Teaching(course_id=course_id, instructor_id=body.instructor_id))
    db.commit()
    return {"status": "assigned"}


@router.delete("/courses/{course_id}/instructors/{instructor_id}")
def remove_instructor(course_id: int, instructor_id: int, db: Session = Depends(get_db),
                      current: User = Depends(require_role(UserRole.admin))):
    t = db.query(Teaching).filter(Teaching.course_id == course_id,
                                   Teaching.instructor_id == instructor_id).first()
    if not t: raise HTTPException(404, "Atribuição não encontrada")
    db.delete(t); db.commit()
    return {"status": "removed"}


# ── AI Settings ────────────────────────────────────────────────────────────────

_AI_KEYS = {"ai_model", "ai_system_prompt", "ai_temperature"}


@router.get("/ai-settings")
def get_ai_settings(db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    from ..config import settings as cfg
    rows = {r.key: r.value for r in db.query(SystemSetting).filter(SystemSetting.key.in_(_AI_KEYS)).all()}
    return {
        "model":         rows.get("ai_model",         cfg.ollama_model),
        "system_prompt": rows.get("ai_system_prompt", ""),
        "temperature":   float(rows.get("ai_temperature", "1.0")),
    }


class AISettingsIn(BaseModel):
    model:         str
    system_prompt: str
    temperature:   float


@router.put("/ai-settings")
def save_ai_settings(body: AISettingsIn, db: Session = Depends(get_db),
                     current: User = Depends(require_role(UserRole.admin))):
    if not (0.0 <= body.temperature <= 2.0):
        raise HTTPException(422, "Temperature deve ser entre 0 e 2")
    for key, val in [("ai_model", body.model),
                     ("ai_system_prompt", body.system_prompt),
                     ("ai_temperature", str(body.temperature))]:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row: row.value = val
        else:   db.add(SystemSetting(key=key, value=val))
    db.commit()
    return {"status": "ok"}


# ── Chat history (admin view) ──────────────────────────────────────────────────

@router.get("/history/sessions")
def admin_list_sessions(
    course_id: Optional[int] = None,
    user_id:   Optional[int] = None,
    limit:     int = 50,
    db:        Session = Depends(get_db),
    current:   User    = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    q = db.query(ChatSession)
    if course_id: q = q.filter(ChatSession.course_id == course_id)
    if user_id:   q = q.filter(ChatSession.user_id == user_id)
    sessions = q.order_by(ChatSession.updated_at.desc()).limit(limit).all()
    return [
        {
            "id":         s.id,
            "title":      s.title,
            "user_name":  s.user.name,
            "user_email": s.user.email,
            "course":     s.course.short_name or s.course.code,
            "updated_at": s.updated_at.isoformat(),
            "msg_count":  len([m for m in s.messages if m.role == "user"]),
        }
        for s in sessions
    ]


@router.get("/history/sessions/{session_id}")
def admin_get_session(session_id: int, db: Session = Depends(get_db),
                      current: User = Depends(require_role(UserRole.admin, UserRole.instructor))):
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s: raise HTTPException(404, "Sessão não encontrada")
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
    import json as _json
    return {
        "id": s.id, "title": s.title,
        "user": {"name": s.user.name, "email": s.user.email},
        "course": s.course.name,
        "messages": [
            {"role": m.role, "content": m.content,
             "rating": m.rating, "created_at": m.created_at.isoformat() if m.created_at else "",
             "sources": _json.loads(m.sources) if m.sources else []}
            for m in msgs
        ],
    }


# ── Insights ───────────────────────────────────────────────────────────────────
# (existing /admin/insights endpoint — no change needed, already implemented)


# ── Export CSV ─────────────────────────────────────────────────────────────────

@router.get("/export/usage")
def export_usage(db: Session = Depends(get_db),
                 current: User = Depends(require_role(UserRole.admin))):
    from datetime import timedelta
    now = datetime.utcnow()
    users = db.query(User).filter(User.role == UserRole.student).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Nome", "Email", "Última hora", "Últimas 24h", "7 dias", "Total"])
    for u in users:
        def cnt(since):
            return (db.query(func.count(ChatMessage.id))
                      .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                      .filter(ChatSession.user_id == u.id, ChatMessage.role == "user",
                              ChatMessage.created_at >= since).scalar() or 0)
        w.writerow([u.name, u.email, cnt(now - timedelta(hours=1)),
                    cnt(now - timedelta(hours=24)), cnt(now - timedelta(days=7)),
                    cnt(datetime(2000, 1, 1))])
    buf.seek(0)
    return _StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                               headers={"Content-Disposition": "attachment; filename=utilizacao.csv"})


@router.get("/export/sessions")
def export_sessions(db: Session = Depends(get_db),
                    current: User = Depends(require_role(UserRole.admin))):
    import json as _json
    sessions = db.query(ChatSession).order_by(ChatSession.updated_at.desc()).limit(1000).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Sessão ID", "Utilizador", "Email", "UC", "Data", "Pergunta", "Resposta", "Avaliação"])
    for s in sessions:
        msgs = s.messages
        for i, m in enumerate(msgs):
            if m.role != "user": continue
            bot = msgs[i+1] if i+1 < len(msgs) and msgs[i+1].role == "assistant" else None
            w.writerow([s.id, s.user.name, s.user.email,
                        s.course.short_name or s.course.code,
                        m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
                        m.content, bot.content if bot else "", bot.rating if bot else ""])
    buf.seek(0)
    return _StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                               headers={"Content-Disposition": "attachment; filename=conversas.csv"})


# ── Live feed ──────────────────────────────────────────────────────────────────

_LIVE_FEED_LABEL = "ISLA Web — Notícias & Eventos"
_FEED_LAST_KEY   = "live_feed_last_fetch"
_FEED_COUNT_KEY  = "live_feed_items_count"
_FEED_ERROR_KEY  = "live_feed_last_error"


@router.get("/live-feed/status")
def live_feed_status(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    rows = {r.key: r.value for r in db.query(SystemSetting).filter(
        SystemSetting.key.in_({_FEED_LAST_KEY, _FEED_COUNT_KEY, _FEED_ERROR_KEY})
    ).all()}
    return {
        "last_fetch":  rows.get(_FEED_LAST_KEY),
        "items_count": int(rows.get(_FEED_COUNT_KEY, 0) or 0),
        "last_error":  rows.get(_FEED_ERROR_KEY),
    }


@router.post("/live-feed/fetch")
async def fetch_live_feed(
    db:      Session = Depends(get_db),
    current: User    = Depends(require_role(UserRole.admin, UserRole.instructor)),
):
    """Scrape ISLA Santarém website and update the general knowledge base."""
    from ..services.live_feed import fetch_isla_feed, format_feed_as_knowledge
    from ..services.knowledge import (
        general_knowledge_path, read_knowledge, save_knowledge, append_to_knowledge,
    )

    loop = asyncio.get_event_loop()
    feed = await loop.run_in_executor(None, fetch_isla_feed)

    if feed.get("error") and not feed["news"] and not feed["events"]:
        raise HTTPException(status_code=503, detail=feed["error"])

    # Remove previous live-feed block so stale entries don't accumulate
    path     = general_knowledge_path()
    existing = read_knowledge(path)
    if existing and _LIVE_FEED_LABEL in existing:
        cleaned = re.sub(
            r'\n\n={60}\n# ' + re.escape(_LIVE_FEED_LABEL) + r'\s+\[.*?\]\n={60}\n\n.*?(?=\n\n={60}\n#|\Z)',
            '',
            existing,
            flags=re.DOTALL,
        )
        save_knowledge(path, cleaned)

    content = format_feed_as_knowledge(feed)
    append_to_knowledge(path, content, _LIVE_FEED_LABEL)

    total = len(feed["news"]) + len(feed["events"])

    def _upsert(key: str, val: str):
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            row.value = val
        else:
            db.add(SystemSetting(key=key, value=val))

    _upsert(_FEED_LAST_KEY,  feed["fetched_at"])
    _upsert(_FEED_COUNT_KEY, str(total))
    _upsert(_FEED_ERROR_KEY, feed.get("error") or "")
    db.commit()

    return {
        "success":     True,
        "fetched_at":  feed["fetched_at"],
        "news_count":  len(feed["news"]),
        "event_count": len(feed["events"]),
        "items":       feed["news"] + feed["events"],
        "error":       feed.get("error"),
    }


# ── Secretaria document management ────────────────────────────────────────────

@router.get("/secretaria/documents")
def list_secretaria_documents(
    current: User = Depends(require_role(UserRole.admin, UserRole.secretaria)),
):
    """List all documents loaded into the secretaria knowledge base."""
    path = secretaria_knowledge_path()
    docs = list_documents_in_file(path)
    return {"documents": docs, "total": len(docs)}


@router.post("/secretaria/upload")
async def upload_secretaria_document(
    doc_label: str        = Form(...),
    file:      UploadFile = File(...),
    current:   User       = Depends(require_role(UserRole.admin, UserRole.secretaria)),
):
    """Upload a PDF/TXT to the secretaria knowledge base (processes with LLM)."""
    suffix = os.path.splitext(file.filename or "doc.txt")[1] or ".txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        path = secretaria_knowledge_path()
        if is_duplicate(path, doc_label):
            raise HTTPException(409, f"Documento '{doc_label}' já existe na Secretaria.")

        loop      = asyncio.get_event_loop()
        raw_text  = await loop.run_in_executor(None, extract_text_from_file, tmp_path)
        organized = await loop.run_in_executor(None, partial(organize_with_llm, raw_text, doc_label))
        append_to_knowledge(path, organized, doc_label)
    finally:
        os.unlink(tmp_path)

    return {"status": "ok", "doc_label": doc_label, "chars": len(organized)}


@router.delete("/secretaria/documents/{doc_label:path}")
def delete_secretaria_document(
    doc_label: str,
    current:   User = Depends(require_role(UserRole.admin, UserRole.secretaria)),
):
    """Remove a document block from the secretaria knowledge base."""
    path    = secretaria_knowledge_path()
    deleted = delete_document_block(path, doc_label)
    if not deleted:
        raise HTTPException(404, f"Documento '{doc_label}' não encontrado.")
    return {"status": "deleted", "doc_label": doc_label}
