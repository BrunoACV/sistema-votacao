@echo off
REM ---------------------------------------------------------------------------
REM  Inicia o Sistema de Votação do Concurso de Halloween INTS (Windows).
REM ---------------------------------------------------------------------------
setlocal
title Sistema de Votacao de Halloween INTS
cd /d "%~dp0"

echo ==================================================
echo   Sistema de Votacao de Halloween - INTS
echo ==================================================
echo.

REM Verifica se o Docker esta disponivel e rodando
where docker >nul 2>&1
if %ERRORLEVEL% equ 0 (
  echo [1/2] Verificando Docker...
  docker info >nul 2>&1
  if %ERRORLEVEL% equ 0 (
    echo [INFO] Docker detectado e ativo! Subindo via Docker Compose...
    docker compose up -d --build
    echo.
    echo [SUCESSO] Sistema rodando no Docker na porta 9090!
    echo Acesse: http://localhost:9090
    echo Para ver os logs: docker compose logs -f votacao
    goto :FIM
  )
)

REM Fallback: Execucao local via Python
echo [1/2] Verificando Python local...
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%p in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%p"

if not defined PYTHON_EXE (
  echo [ERRO] Python ou ambiente .venv nao encontrado!
  echo Instale o Python 3.11+ ou o Docker Desktop para executar.
  pause
  exit /b 1
)

echo [2/2] Iniciando servidor Flask nativo...
echo.
"%PYTHON_EXE%" run.py

:FIM
endlocal
