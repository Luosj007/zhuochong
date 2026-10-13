@echo off
rem 双击启动桌宠小狗
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw desktop_pet.py
) else (
    start "" /min python desktop_pet.py
)
