@echo off
rem Installs numpy + Pillow for the texture tool (run once).
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m pip install -r "%~dp0requirements.txt"
) else (
    python -m pip install -r "%~dp0requirements.txt"
)
pause
