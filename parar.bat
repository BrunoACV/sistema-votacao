@echo off
REM ---------------------------------------------------------------------------
REM  Para o Sistema de Votação do Concurso de Halloween INTS (Windows).
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo Encerrando Sistema de Votacao de Halloween...

REM Tenta parar via Docker Compose se instalado
where docker >nul 2>&1
if %ERRORLEVEL% equ 0 (
  docker compose down >nul 2>&1
)

REM Encerra qualquer processo python rodando run.py
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :9090 ^| findstr LISTENING 2^>nul') do (
  echo Finalizando processo na porta 9090 (PID %%a)...
  taskkill /F /PID %%a >nul 2>&1
)

echo [OK] Sistema finalizado com sucesso.
endlocal
