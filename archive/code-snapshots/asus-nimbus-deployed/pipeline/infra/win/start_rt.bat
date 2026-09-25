@echo off
rem Realtime diffusion relay (rt_server.py), started at logon by the "NimbusRT" scheduled task.
cd /d %USERPROFILE%\hackmit
%USERPROFILE%\ComfyUI\venv\Scripts\python.exe rt_server.py >> %USERPROFILE%\hackmit\rt_server.log 2>&1
