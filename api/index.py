import os
import shutil

# If running on Vercel, Vercel filesystem is read-only.
# We must copy the data folder to /tmp/data to make it writable for SQLite and knowledge base.
if os.environ.get("VERCEL"):
    if not os.path.exists("/tmp/data"):
        if os.path.exists("data"):
            shutil.copytree("data", "/tmp/data")
        else:
            os.makedirs("/tmp/data")
    
    # Override database and docs paths to use the writable /tmp directory
    os.environ["DATABASE_URL"] = "sqlite:////tmp/data/isla_chatbot.db"
    os.environ["DOCS_PATH"] = "/tmp/data/courses"
    os.environ["LLM_PROVIDER"] = "openrouter" # Override to OpenRouter as requested

from backend.main import app
