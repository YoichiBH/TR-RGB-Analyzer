@echo off
REM ============================================================
REM  TR RGB Analyzer - lanzador para Windows (doble clic)
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] No se ha encontrado Python.
        echo Instale Python 3.10 o superior desde https://www.python.org/downloads/
        echo y marque la casilla "Add python.exe to PATH" durante la instalacion.
        pause
        exit /b 1
    )
    set "PY=py -3"
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Se necesita Python 3.10 o superior.
    %PY% --version
    pause
    exit /b 1
)

%PY% -c "import numpy, PIL, pandas, matplotlib, tkinter" >nul 2>nul
if errorlevel 1 (
    echo Faltan dependencias. Instalando las librerias necesarias, solo la primera vez...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] No se pudieron instalar las dependencias. Revise su conexion a Internet.
        pause
        exit /b 1
    )
)

echo Iniciando TR RGB Analyzer...
%PY% app.py %*
if errorlevel 1 (
    echo.
    echo El programa se ha cerrado con un error. Consulte logs\tr_rgb_analyzer.log
    pause
)
