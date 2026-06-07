@echo off
setlocal enabledelayedexpansion

echo ==^> A iniciar ISLA Chatbot...

cd /d "%~dp0"


:: Python venv
if not exist "venv" (
    echo ==^> A criar ambiente virtual Python...
    python -m venv venv
)

echo ==^> A activar venv e instalar dependencias...
call venv\Scripts\activate.bat
pip install -q -r backend\requirements.txt

:: Data folders
if not exist "data\courses" mkdir "data\courses"
if not exist "data\chroma" mkdir "data\chroma"

:: Seed DB
if not exist "data\isla_chatbot.db" (
    echo ==^> A criar base de dados e dados iniciais...
    python seed.py
)

:: Backend
echo.
echo ==^> Tudo pronto! A iniciar servidor...
echo ==^> Abre o browser em: http://localhost:8080
echo ==^> Admin: admin@islasantarem.pt / admin1234
echo.

uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
pause
