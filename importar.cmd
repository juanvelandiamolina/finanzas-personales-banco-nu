@echo off
rem Doble clic: modo guiado (elige el PDF, muestra la vista previa y pregunta antes de escribir).
rem Desde una consola: importar.cmd entrada\CuentaNu_XXXXXX_2026-11.pdf [--escribir]
chcp 65001 >nul
title Importar extracto Nu
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No encuentro el entorno de Python en "%~dp0.venv".
  echo Mira la seccion "Instalacion" del README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m finanzas_nu %*
