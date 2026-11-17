@echo off
title Wordstat Helper
cd /d "C:\Py\wordstat-helper"

echo ===================================================
echo   Wordstat Helper запущен!
echo   Адрес: http://127.0.0.1:8000
echo   Чтобы выключить сервер - просто закройте это окно.
echo ===================================================

start http://127.0.0.1:8000
python -m uvicorn main:app --host 127.0.0.1 --port 8000
