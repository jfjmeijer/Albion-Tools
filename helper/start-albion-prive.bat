@echo off
title Albion Tools - prive data
cd /d "%~dp0"

rem 1. Hulpprogramma starten (ontvangt de data op je eigen pc)
start "Albion Tools hulpprogramma" "%~dp0albion-helper.exe"

rem 2. Albion Data Client starten die ALLEEN naar je eigen pc stuurt
set "CLIENT=%ProgramFiles%\Albion Data Client\albiondata-client.exe"
if not exist "%CLIENT%" (
  echo.
  echo De Albion Data Client is niet gevonden op:
  echo   %CLIENT%
  echo Installeer hem via https://www.albion-online-data.com/client
  echo of pas in dit bestand de regel "set CLIENT=..." aan naar de juiste plek.
  echo.
  pause
  exit /b 1
)
start "" "%CLIENT%" -i http://127.0.0.1:47800 -minimize

echo.
echo Gestart. Laat het venster "Albion Tools hulpprogramma" open terwijl je speelt.
echo Zet op de site bij "Eigen data (prive)" het vinkje aan.
echo.
timeout /t 8 >nul
