"""
Скрипт бесшумного запуска Wordstat Helper.
Проверяет доступность сервера, при необходимости запускает uvicorn через pythonw (без окон)
и открывает вкладку в браузере.
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
PORT = 8000


def is_server_running(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    if not is_server_running():
        # Ищем pythonw.exe для скрытого запуска без мигания консоли
        python_exe = sys.executable
        pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw_exe):
            pythonw_exe = python_exe

        creation_flags = 0
        if sys.platform == "win32":
            # 0x08000000 = CREATE_NO_WINDOW, 0x00000008 = DETACHED_PROCESS
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            [
                pythonw_exe,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=str(PROJECT_DIR),
            creationflags=creation_flags,
            close_fds=True,
        )

        # Ждем секунду готовности сокета
        for _ in range(20):
            time.sleep(0.2)
            if is_server_running():
                break

    # Открываем браузер
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
