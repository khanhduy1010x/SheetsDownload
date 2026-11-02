@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Can cai Python 3.11 tro len va bat "Add Python to PATH".
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Can cai FFmpeg va them ffmpeg vao PATH de ghep video chat luong cao nhat.
  pause
  exit /b 1
)
where node >nul 2>nul
if errorlevel 1 echo Khuyen nghi cai Node.js de tai duoc nhieu video YouTube hon.
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe app.py
if errorlevel 1 pause
