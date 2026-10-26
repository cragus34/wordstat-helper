"""
Скрипт остановки фонового сервера Wordstat Helper.
"""
import subprocess
import sys


def stop_server():
    try:
        # Находим и закрываем процесс, занимающий 8000 порт
        cmd = 'Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }'
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)
    except Exception as e:
        print("Ошибка остановки:", e)


if __name__ == "__main__":
    stop_server()
