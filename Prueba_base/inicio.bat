@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ================================================
echo   Conciliacion IVA - Iniciando...
echo ================================================
echo.

:: ── Verificar Python ─────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado.
    echo.
    echo Instala Python 3.11+ desde https://www.python.org/downloads/
    echo Durante la instalacion, marca "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% encontrado.

:: ── Crear entorno virtual si no existe ───────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo [Setup] Creando entorno virtual ^(solo la primera vez^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo [OK] Entorno virtual creado.
)

:: ── Activar entorno virtual ───────────────────────────────────────────────────
call .venv\Scripts\activate.bat

:: ── Instalar dependencias si faltan ─────────────────────────────────────────
python -c "import streamlit, pandas, openpyxl, xlrd, xlsxwriter, python_calamine" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [Setup] Instalando dependencias ^(puede tardar unos minutos^)...
    pip install --upgrade pip -q
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Fallo la instalacion de dependencias.
        echo Revisa tu conexion a Internet e intenta nuevamente.
        pause
        exit /b 1
    )
    echo [OK] Dependencias instaladas.
)

:: ── Verificar LibreOffice ────────────────────────────────────────────────────
set LO_OK=0
if exist "C:\Program Files\LibreOffice\program\soffice.exe"     set LO_OK=1
if exist "C:\Program Files (x86)\LibreOffice\program\soffice.exe" set LO_OK=1
where soffice >nul 2>&1 && set LO_OK=1

if "!LO_OK!"=="0" (
    echo [INFO] LibreOffice no detectado ^(opcional^).
) else (
    echo [OK] LibreOffice encontrado.
)

:: ── Cerrar instancia anterior en el puerto 8502 ──────────────────────────────
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8502 " ^| findstr "LISTENING"') do (
    echo Cerrando proceso anterior ^(PID %%a^)...
    taskkill /F /PID %%a >nul 2>&1
)

:: ── Abrir navegador con delay ────────────────────────────────────────────────
start "" cmd /c "timeout /t 4 /nobreak >nul && start http://localhost:8502"

:: ── Lanzar la app ────────────────────────────────────────────────────────────
echo.
echo ================================================
echo   Abriendo en http://localhost:8502
echo   Presiona Ctrl+C para cerrar la app.
echo ================================================
echo.

streamlit run app.py --server.fileWatcherType none

echo.
echo La app se cerro.
pause
