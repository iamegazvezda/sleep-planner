"""Запустить настоящий Uvicorn на свободном локальном порту и проверить /health."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    # Порт выбирает ОС. Между закрытием сокета и запуском сервера возможна
    # конкуренция за порт; при такой редкой ошибке скрипт можно повторить.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app_final:app", "--host", "127.0.0.1",
         "--port", str(port), "--no-access-log"],
        cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    failure = None
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise RuntimeError("Uvicorn завершился до проверки /health")
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status != 200 or json.load(response) != {"status": "ok"}:
                        raise RuntimeError("Нарушен контракт /health")
                break
            except URLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Uvicorn не ответил за 10 секунд") from None
                time.sleep(0.05)
    except Exception as error:
        failure = error
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()  # Только собственный дочерний процесс этого скрипта.
            stdout, stderr = process.communicate()
    if failure is not None:
        print(stdout, end="")
        print(stderr, end="", file=sys.stderr)
        raise failure
    print("OK: настоящий Uvicorn вернул /health = {'status': 'ok'} и остановлен")


if __name__ == "__main__":
    main()
