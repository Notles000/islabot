from pathlib import Path
from pydantic import validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    database_url:  str = "sqlite:///./data/isla_chatbot.db"
    secret_key:    str  # no default — must be set in .env
    algorithm:     str = "HS256"
    token_expire_minutes: int = 480

    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "qwen3.5:0.8b"

    llm_provider:  str = "ollama"          # "ollama" | "groq" | "gemini" | "openrouter"
    groq_api_key:  str = ""
    groq_model:    str = "llama-3.3-70b-versatile"
    groq_ingest_model: str = "llama-3.1-8b-instant"   # cheap model for PDF extraction

    # Google AI Studio — get key at https://aistudio.google.com/app/apikey
    gemini_api_key:       str = ""
    gemini_model:         str = "gemini-2.5-pro-preview-05-06"
    gemini_ingest_model:  str = "gemini-2.0-flash-lite"  # cheap model for PDF extraction

    # OpenRouter — https://openrouter.ai
    openrouter_api_key:       str = ""
    openrouter_model:         str = "meta-llama/llama-3.3-70b-instruct"
    openrouter_ingest_model:  str = "meta-llama/llama-3.1-8b-instruct"  # cheap model for PDF extraction

    docs_path:     str = "./data/courses"

    @validator("secret_key")
    def _validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    class Config:
        env_file = str(_ENV_FILE)


settings = Settings()
