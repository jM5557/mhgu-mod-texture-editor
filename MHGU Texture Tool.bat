@echo off
rem Double-click to open the editor, or drop a mod .zip or folder onto this file.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0mhgu_tex_tool.py" %*
) else (
    python "%~dp0mhgu_tex_tool.py" %*
)
if errorlevel 1 pause
