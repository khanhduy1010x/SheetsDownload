@echo off
setlocal
cd /d "%~dp0"

echo === Build Windows EXE ===
where py.exe >nul 2>nul
if errorlevel 1 (
  echo Can cai Python 3.11 tro len tren MAY DONG GOI.
  pause
  exit /b 1
)

set "FFMPEG_EXE="
set "FFPROBE_EXE="
set "NODE_EXE="
for /f "delims=" %%I in ('where ffmpeg.exe 2^>nul') do if not defined FFMPEG_EXE set "FFMPEG_EXE=%%I"
for /f "delims=" %%I in ('where ffprobe.exe 2^>nul') do if not defined FFPROBE_EXE set "FFPROBE_EXE=%%I"
for /f "delims=" %%I in ('where node.exe 2^>nul') do if not defined NODE_EXE set "NODE_EXE=%%I"
if not defined FFMPEG_EXE (
  echo Can cai FFmpeg va them vao PATH tren MAY DONG GOI.
  pause
  exit /b 1
)
if not defined FFPROBE_EXE (
  echo Can cai FFprobe kem FFmpeg tren MAY DONG GOI.
  pause
  exit /b 1
)
if not defined NODE_EXE (
  echo Can cai Node.js va them vao PATH tren MAY DONG GOI.
  pause
  exit /b 1
)

if not exist ".build-venv\Scripts\python.exe" (
  py -3 -m venv .build-venv
  if errorlevel 1 goto build_failed
)
".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt "pyinstaller>=6.21,<7"
if errorlevel 1 goto build_failed

".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name VideoChannelDownloader ^
  --icon "assets\icon.ico" ^
  --add-data "assets\icon.png;assets" ^
  --add-data "assets\icon.ico;assets" ^
  --add-binary "%FFMPEG_EXE%;." ^
  --add-binary "%FFPROBE_EXE%;." ^
  --add-binary "%NODE_EXE%;." ^
  --collect-all yt_dlp_ejs ^
  app.py
if errorlevel 1 goto build_failed

if not exist "dist\VideoChannelDownloader.exe" goto build_failed
echo.
echo EXE san sang: dist\VideoChannelDownloader.exe

set "ISCC_EXE="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC_EXE (
  "%ISCC_EXE%" windows_installer.iss
  if errorlevel 1 goto build_failed
  echo Bo cai san sang: dist\VideoChannelDownloader-Setup.exe
) else (
  echo Khong tim thay Inno Setup 6. EXE van dung doc lap duoc.
)
echo Setup.exe da kem extension Chrome. Neu giao EXE rieng, giao them thu muc chrome_extension.
pause
exit /b 0

:build_failed
echo Dong goi that bai. Xem loi o tren.
pause
exit /b 1
