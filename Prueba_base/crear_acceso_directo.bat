@echo off
cd /d "%~dp0"

echo ================================================
echo   Creando acceso directo en el Escritorio...
echo ================================================
echo.

:: ── Generar icono ────────────────────────────────────────────────────────────
if not exist "icon.ico" (
    echo Generando icono...
    python _crear_icono.py 2>nul || python3 _crear_icono.py
    if not exist "icon.ico" (
        echo [AVISO] No se pudo generar el icono. El acceso directo usara icono generico.
    )
)

:: ── Crear acceso directo via PowerShell ──────────────────────────────────────
set "PROJ=%~dp0"
set "LINK=%USERPROFILE%\Desktop\Conciliacion IVA.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$wsh = New-Object -ComObject WScript.Shell;" ^
    "$s = $wsh.CreateShortcut('%LINK%');" ^
    "$s.TargetPath = '%PROJ%inicio.bat';" ^
    "$s.WorkingDirectory = '%PROJ%';" ^
    "$s.IconLocation = '%PROJ%icon.ico';" ^
    "$s.Description = 'Conciliacion IVA - Colppy vs ARCA';" ^
    "$s.Save()"

if exist "%LINK%" (
    echo [OK] Acceso directo creado en el Escritorio.
    echo      Doble clic en "Conciliacion IVA" para abrir la app.
) else (
    echo [ERROR] No se pudo crear el acceso directo.
)

echo.
pause
