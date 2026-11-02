@echo off
setlocal
title ReelForge AI Studio - Local Website
cd /d "%~dp0"

echo.
echo  ==========================================================
echo           REELFORGE AI STUDIO - ON YOUR OWN PC
echo  ==========================================================
echo  Your footage and AI processing stay on this computer.
echo.

if not exist "%~dp0ReelForgeLocal.exe" (
    echo ERROR: ReelForgeLocal.exe is missing.
    echo Extract the FULL downloaded ZIP into a new folder.
    echo.
    pause
    exit /b 1
)

set "OLLAMA=ollama"
where ollama >NUL 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    ) else (
        echo Ollama is not yet installed.
        echo Install the FREE Windows app from https://ollama.com/download/windows
        echo.
        choice /C YN /M "Open the official Ollama download page"
        if not errorlevel 2 start "" "https://ollama.com/download/windows"
        echo.
        echo After installing Ollama, double-click Start-ReelForge.cmd again.
        pause
        exit /b 1
    )
)

echo Checking local AI service...
call "%OLLAMA%" list >NUL 2>&1
if errorlevel 1 (
    echo Starting Ollama in the background...
    start "Ollama AI Server" /MIN "%OLLAMA%" serve
    timeout /t 8 /nobreak >NUL
)

call "%OLLAMA%" list 2>NUL | findstr /i /c:"gemma3:4b" >NUL
if errorlevel 1 (
    echo.
    echo The local vision model has not been downloaded.
    echo This is a ONE-TIME download of about 3.4 GB.
    echo It may take longer to analyze video on a CPU-only computer.
    choice /C YN /M "Download the free gemma3:4b model now"
    if errorlevel 2 (
        echo Model setup cancelled. Nothing uploaded.
        pause
        exit /b 1
    )
    call "%OLLAMA%" pull gemma3:4b
    if errorlevel 1 (
        echo Unable to download the model. Check your internet and Ollama.
        pause
        exit /b 1
    )
)

echo.
echo Opening http://127.0.0.1:18765/
echo Close this window to stop the local web app.
echo.
"%~dp0ReelForgeLocal.exe"

if errorlevel 1 (
    echo.
    echo The website encountered a startup error.
    echo Take a screenshot of the error text and share it for troubleshooting.
    pause
)
endlocal
