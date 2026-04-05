@echo off
REM Start ARIA Python Service
set PATH=%LOCALAPPDATA%\Python\pythoncore-3.14-64;%LOCALAPPDATA%\Python\pythoncore-3.14-64\Scripts;%PATH%
echo Starting ARIA Service on port 8000...
python -m aria_service.main
