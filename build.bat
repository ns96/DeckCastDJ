@echo off
title Rebuilding DeckCastDJ Release

REM Parse command-line arguments
set CLEAN_BUILD=0
set REFRESH_CACHE=0
set NO_PAUSE=0

for %%A in (%*) do (
    if /I "%%A"=="-clean" set CLEAN_BUILD=1
    if /I "%%A"=="--clean" set CLEAN_BUILD=1
    if /I "%%A"=="clean" set CLEAN_BUILD=1
    if /I "%%A"=="-refresh" set REFRESH_CACHE=1
    if /I "%%A"=="--refresh" set REFRESH_CACHE=1
    if /I "%%A"=="refresh" set REFRESH_CACHE=1
    if /I "%%A"=="nopause" set NO_PAUSE=1
)

echo ========================================================
if "%CLEAN_BUILD%"=="1" (
    echo Rebuilding DeckCastDJ Release [CLEAN DE-PERSONALIZED MODE]
) else (
    echo Rebuilding DeckCastDJ Release [DEVELOPER MODE]
)
echo Python Environment: C:\ProgramData\anaconda3\python.exe
echo ========================================================
echo.

REM Ensure running instance is closed before rebuilding
taskkill /F /IM DeckCastDJ.exe >nul 2>&1

REM 1. Run PyInstaller using your Conda Python 3.11 environment (staging in TEMP to avoid Google Drive file locks)
set STAGING_DIST=%TEMP%\deckcast_dist
set STAGING_WORK=%TEMP%\deckcast_work

"C:\ProgramData\anaconda3\python.exe" -m PyInstaller DeckCastDJ.spec --distpath "%STAGING_DIST%" --workpath "%STAGING_WORK%" --noconfirm
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] PyInstaller build failed!
    if "%NO_PAUSE%"=="0" pause
    exit /b %ERRORLEVEL%
)

echo.
echo ========================================================
echo Syncing compiled binaries and assets to dist\DeckCastDJ...
echo ========================================================

REM 2. Copy compiled binary & _internal from staging
if not exist "dist\DeckCastDJ" mkdir "dist\DeckCastDJ"
copy /Y "%STAGING_DIST%\DeckCastDJ\DeckCastDJ.exe" "dist\DeckCastDJ\DeckCastDJ.exe" >nul
xcopy "%STAGING_DIST%\DeckCastDJ\_internal" "dist\DeckCastDJ\_internal\" /E /I /Y >nul

REM 3. Copy/Update root assets into dist\DeckCastDJ\
if "%CLEAN_BUILD%"=="1" (
    echo.
    echo --------------------------------------------------------
    echo [CLEAN MODE] Preparing de-personalized data ^& config...
    echo --------------------------------------------------------
    if exist "dist\DeckCastDJ\data" rmdir /S /Q "dist\DeckCastDJ\data"
    
    set REFRESH_ARG=
    if "%REFRESH_CACHE%"=="1" set REFRESH_ARG=--refresh
    
    "C:\ProgramData\anaconda3\python.exe" prepare_clean_distro.py --dest "dist\DeckCastDJ\data" --config-dest "dist\DeckCastDJ\config.py" %REFRESH_ARG%
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Clean distribution data generation failed!
        if "%NO_PAUSE%"=="0" pause
        exit /b %ERRORLEVEL%
    )
) else (
    if not exist "dist\DeckCastDJ\config.py" copy "config.py" "dist\DeckCastDJ\config.py" >nul
    if not exist "dist\DeckCastDJ\data" xcopy "data" "dist\DeckCastDJ\data\" /E /I /Y >nul
)

xcopy "templates" "dist\DeckCastDJ\templates\" /E /I /Y >nul
xcopy "static" "dist\DeckCastDJ\static\" /E /I /Y >nul

REM 4. Ensure ffmpeg and run.bat launcher are present
if exist "C:\Windows\System32\ffmpeg.exe" (
    if not exist "dist\DeckCastDJ\ffmpeg.exe" copy "C:\Windows\System32\ffmpeg.exe" "dist\DeckCastDJ\ffmpeg.exe" >nul
)

if not exist "dist\DeckCastDJ\run.bat" (
    (
        echo @echo off
        echo title DeckCastDJ
        echo echo Starting DeckCastDJ...
        echo start "" "DeckCastDJ.exe"
        echo timeout /t 2 ^>nul
        echo start http://localhost:5054
    ) > "dist\DeckCastDJ\run.bat"
)

echo.
echo ========================================================
echo [SUCCESS] DeckCastDJ Release built successfully!
echo Output folder: dist\DeckCastDJ\
echo ========================================================
echo.
if "%NO_PAUSE%"=="0" pause
