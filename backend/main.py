import logging
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import auth, chat, courses
from .routers.admin import router as admin_router
from .routers.secretaria import router as secretaria_router
from .services.knowledge import warmup_llm

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ISLA Santarém — Assistente Académico",
    version="2.0.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# No-cache for HTML files so the browser always fetches fresh JS/CSS
@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.endswith(".html") or request.url.path in ("/", "/admin", "/chat"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(auth.router,         prefix="/api")
app.include_router(chat.router,         prefix="/api")
app.include_router(courses.router,      prefix="/api")
app.include_router(admin_router,        prefix="/api")
app.include_router(secretaria_router,   prefix="/api")

# Serve frontend
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


def _scheduled_feed_fetch():
    """Fetch ISLA news/events and update the general knowledge base."""
    try:
        from .services.live_feed import fetch_isla_feed, format_feed_as_knowledge
        from .services.knowledge import (
            general_knowledge_path, read_knowledge, save_knowledge, append_to_knowledge,
        )
        from .database import SessionLocal
        from .models import SystemSetting

        _LABEL = "ISLA Web — Notícias & Eventos"

        feed = fetch_isla_feed()
        if feed.get("error") and not feed["news"] and not feed["events"]:
            logger.warning("Live feed scheduled fetch failed: %s", feed["error"])
            return

        path     = general_knowledge_path()
        existing = read_knowledge(path)
        if existing and _LABEL in existing:
            cleaned = re.sub(
                r'\n\n={60}\n# ' + re.escape(_LABEL) + r'\s+\[.*?\]\n={60}\n\n.*?(?=\n\n={60}\n#|\Z)',
                '', existing, flags=re.DOTALL,
            )
            save_knowledge(path, cleaned)

        append_to_knowledge(path, format_feed_as_knowledge(feed), _LABEL)

        total = len(feed["news"]) + len(feed["events"])
        db = SessionLocal()
        try:
            for key, val in [
                ("live_feed_last_fetch",   feed["fetched_at"]),
                ("live_feed_items_count",  str(total)),
                ("live_feed_last_error",   feed.get("error") or ""),
            ]:
                row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
                if row: row.value = val
                else:   db.add(SystemSetting(key=key, value=val))
            db.commit()
        finally:
            db.close()

        logger.info("Live feed auto-fetch: %d items saved.", total)
    except Exception as exc:
        logger.error("Live feed scheduled fetch error: %s", exc)


@app.on_event("startup")
def startup():
    init_db()

    import threading
    threading.Thread(target=warmup_llm, daemon=True).start()

    # Schedule daily news fetch at 08:00 using a background thread
    import threading, time
    from datetime import datetime, timedelta

    def _feed_scheduler():
        while True:
            now  = datetime.now()
            next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            time.sleep((next_run - datetime.now()).total_seconds())
            _scheduled_feed_fetch()

    threading.Thread(target=_feed_scheduler, daemon=True, name="feed-scheduler").start()
    logger.info("Live feed scheduler started — runs daily at 08:00.")
