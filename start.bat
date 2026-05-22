@echo off
setlocal enabledelayedexpansion

echo ==^> A iniciar ISLA Chatbot...

cd /d "%~dp0"

:: Detect active LLM provider
set LLM_PROVIDER_VAL=ollama
if exist backend\.env (
    for /f "tokens=1,2 delims==" %%a in (backend\.env) do (
        if "%%a"=="LLM_PROVIDER" set LLM_PROVIDER_VAL=%%b
    )
)
:: Remove whitespace
set LLM_PROVIDER_VAL=%LLM_PROVIDER_VAL: =%

if "%LLM_PROVIDER_VAL%"=="ollama" (
    :: Check if ollama is installed
    where ollama >nul 2>nul
    if %errorlevel% neq 0 (
        echo ERRO: Ollama nao encontrado. Por favor instala o Ollama em https://ollama.com antes de continuar.
        pause
        exit /b 1
    )

    echo ==^> Ollama encontrado.
    
    :: Check if Ollama is responding
    curl -sf http://localhost:11434/api/tags >nul 2>&1
    if %errorlevel% neq 0 (
        echo ==^> A iniciar servico Ollama...
        start /b "" ollama serve
        timeout /t 4 /nobreak >nul
    )

    set OLLAMA_MODEL_VAL=qwen2.5:3b
    if exist backend\.env (
        for /f "tokens=1,2 delims==" %%a in (backend\.env) do (
            if "%%a"=="OLLAMA_MODEL" set OLLAMA_MODEL_VAL=%%b
        )
    )
    :: Remove whitespace
    set OLLAMA_MODEL_VAL=!OLLAMA_MODEL_VAL: =!
    
    echo ==^> A verificar modelo !OLLAMA_MODEL_VAL!...
    ollama list | findstr /i /c:"!OLLAMA_MODEL_VAL!" >nul
    if %errorlevel% neq 0 (
        echo ==^> A descarregar modelo !OLLAMA_MODEL_VAL! ^(pode demorar alguns minutos^)...
        ollama pull !OLLAMA_MODEL_VAL!
    )
) else (
    echo ==^> Provider: %LLM_PROVIDER_VAL% -- Ollama nao necessario.
)

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
