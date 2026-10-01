@echo off
setlocal EnableExtensions
title Supervision Reseau - Switch Monitor Pro

REM --- FIX : on se place TOUJOURS dans le dossier du script (double-clic, raccourci, UNC) ---
cd /d "%~dp0" 2>nul
if errorlevel 1 (
  REM Cas d'un chemin reseau UNC : on mappe temporairement un lecteur
  pushd "%~dp0" || goto :no_dir
)

set "PYEXE="

REM --- 1) pythonw.exe : lance l'interface SANS fenetre noire ---
for %%C in (pythonw.exe) do (
  if not defined PYEXE if exist "%%~$PATH:C" set "PYEXE=%%~$PATH:C"
)

REM --- 2) py.exe (Python Launcher officiel) ---
if not defined PYEXE (
  where pyw.exe >nul 2>&1 && set "PYEXE=pyw.exe"
)
if not defined PYEXE (
  where py.exe >nul 2>&1 && set "PYEXE=py.exe"
)

REM --- 3) python.exe classique ---
if not defined PYEXE (
  where python.exe >nul 2>&1 && set "PYEXE=python.exe"
)

if not defined PYEXE goto :no_python

echo Lancement de la supervision reseau avec : %PYEXE%
start "" "%PYEXE%" "%~dp0network_monitor.py"
exit /b 0

:no_python
echo.
echo ============================================================
echo  [ERREUR] Python n'a pas ete trouve sur ce poste.
echo ============================================================
echo.
echo  1. Telechargez Python 3 : https://www.python.org/downloads/
echo  2. A l'installation, COCHEZ la case "Add python.exe to PATH".
echo  3. Relancez ce fichier lancer.bat.
echo.
echo  (Tkinter est inclus par defaut dans l'installateur officiel.)
echo.
pause
exit /b 1

:no_dir
echo [ERREUR] Impossible d'acceder au dossier du programme.
pause
exit /b 1
