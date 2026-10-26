"""
Wordstat Helper — бэкенд FastAPI с поддержкой фоновых задач, прерывания (Cancel)
и немедленного сохранения данных в SQLite по ходу парсинга.
"""
import logging
import os
import uuid
import threading
from contextlib import asynccontextmanager
from typing import Dict, Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from exporter import build_xlsx
from filters import filter_phrases, should_stop_pagination
from history import (
    create_query,
    finish_query,
    get_query,
    init_db,
    list_queries,
    update_query_progress,
)
from jsonseo_client import JsonSeoClient

# ── Конфиг ──────────────────────────────────────────────────────────────
load_dotenv()
API_KEY = os.getenv("JSONSEO_API_KEY", "")
MAX_PAGES = 20  # потолок пагинации Вордстата

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Хранилище фоновых задач в памяти
# task_id -> { status, progress, stage, query_id, data, error, cancel_requested }
TASKS: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()

# ── Приложение ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("БД инициализирована")
    yield


app = FastAPI(title="Wordstat Helper", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ── Фронтенд ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── Фоновая логика сбора ──────────────────────────────────────────────────

def background_parse_worker(task_id: str, seed_phrase: str, min_freq: int, stop_words: list[str]):
    def update_task(**kwargs):
        with tasks_lock:
            if task_id in TASKS:
                TASKS[task_id].update(kwargs)

    def is_cancelled() -> bool:
        with tasks_lock:
            return TASKS.get(task_id, {}).get("cancel_requested", False)

    # 1. Создаём запись в БД сразу на старте
    query_id = create_query(seed_phrase)
    update_task(query_id=query_id)
    log.info("[%s] Создана запись в БД #%d для «%s»", task_id, query_id, seed_phrase)

    client = JsonSeoClient(API_KEY)
    results = []

    try:
        update_task(status="running", stage="Сбор списка фраз из Wordstat...", progress=5)

        # ── Шаг 1: парсинг фраз ─────────────────────────────────────────
        all_phrases: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            if is_cancelled():
                log.info("[%s] Прервано пользователем во время сбора страниц", task_id)
                finish_query(query_id, [], status="stopped")
                update_task(status="stopped", stage="Остановлено пользователем", progress=100, data=[])
                return

            update_task(
                stage=f"Сбор фраз из Wordstat (страница {page})... Собрано: {len(all_phrases)}",
                progress=min(25, 5 + page * 2)
            )
            log.info("[%s] Парсинг страницы %d для маски «%s»", task_id, page, seed_phrase)
            
            try:
                page_phrases = client.fetch_phrases(seed_phrase, page)
            except Exception as e:
                log.error("[%s] Ошибка при парсинге Wordstat: %s", task_id, e)
                finish_query(query_id, [], status="failed")
                update_task(status="failed", error=f"Ошибка Wordstat API: {e}")
                return

            if not page_phrases:
                break

            all_phrases.extend(page_phrases)

            if should_stop_pagination(page_phrases, min_freq):
                log.info("[%s] Стоп на странице %d: последняя фраза ниже порога", task_id, page)
                break

        log.info("[%s] Собрано фраз до фильтрации: %d", task_id, len(all_phrases))

        # ── Шаг 2: фильтрация ────────────────────────────────────────────
        if is_cancelled():
            finish_query(query_id, [], status="stopped")
            update_task(status="stopped", stage="Остановлено пользователем", progress=100, data=[])
            return

        update_task(stage="Фильтрация стоп-слов и базовой частотности...", progress=28)
        filtered = filter_phrases(all_phrases, min_freq, stop_words)
        total_filtered = len(filtered)
        log.info("[%s] После фильтрации: %d фраз", task_id, total_filtered)

        if total_filtered == 0:
            finish_query(query_id, [], status="completed")
            update_task(
                status="completed",
                progress=100,
                stage="Готово (подходящих фраз не найдено)",
                data=[],
            )
            return

        # ── Шаг 3: точная частотность с непрерывным сохранением в БД ──────
        for idx, item in enumerate(filtered, start=1):
            if is_cancelled():
                log.info("[%s] Пользователь остановил сбор на фразе %d из %d", task_id, idx, total_filtered)
                results.sort(key=lambda x: x.get("exact_freq") or 0, reverse=True)
                finish_query(query_id, results, status="stopped")
                update_task(
                    status="stopped",
                    progress=100,
                    stage=f"Остановлено пользователем. Сохранено {len(results)} фраз.",
                    data=results
                )
                return

            phrase = item["text"]
            base_freq = item["value"]
            
            pct = 30 + int((idx / total_filtered) * 65)
            update_task(
                stage=f"Сбор точной частотности: {idx} из {total_filtered} фраз ({pct}%)",
                progress=pct
            )

            try:
                exact_freq = client.fetch_exact(phrase)
            except Exception as e:
                log.warning("[%s] Ошибка частотности для «%s»: %s", task_id, phrase, e)
                exact_freq = None

            results.append({
                "phrase": phrase,
                "base_freq": base_freq,
                "exact_freq": exact_freq,
            })

            # Сохраняем прогресс в БД на каждой итерации
            update_query_progress(query_id, results, status="running")

        # ── Шаг 4: сортировка по точной частотности (убывание) ──────────
        update_task(stage="Финализация и сортировка...", progress=98)
        results.sort(key=lambda x: x.get("exact_freq") or 0, reverse=True)

        # ── Шаг 5: завершение в БД ───────────────────────────────────────
        finish_query(query_id, results, status="completed")
        log.info("[%s] Запрос #%d успешно завершён и сохранён", task_id, query_id)

        update_task(
            status="completed",
            progress=100,
            stage="Завершено успешно!",
            data=results
        )

    except Exception as e:
        log.exception("[%s] Непредвиденная ошибка в задаче: %s", task_id, e)
        finish_query(query_id, results, status="failed")
        update_task(status="failed", error=str(e), data=results)
    finally:
        client.close()


# ── API: запуск, статус и отмена ─────────────────────────────────────────

@app.post("/api/run-parsing")
def run_parsing(
    background_tasks: BackgroundTasks,
    seed_phrase: str = Form(...),
    min_freq: int = Form(100),
    custom_stop_words: str = Form(""),
):
    if not API_KEY:
        raise HTTPException(500, "JSONSEO_API_KEY не задан в .env")

    stop_words = [w.strip() for w in custom_stop_words.split(",") if w.strip()]
    task_id = str(uuid.uuid4())

    with tasks_lock:
        TASKS[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "stage": "Инициализация задачи...",
            "cancel_requested": False,
            "query_id": None,
            "data": None,
            "error": None
        }

    background_tasks.add_task(background_parse_worker, task_id, seed_phrase, min_freq, stop_words)
    return {"status": "accepted", "task_id": task_id}


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    with tasks_lock:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    with tasks_lock:
        task = TASKS.get(task_id)
        if not task:
            raise HTTPException(404, "Задача не найдена")
        if task["status"] in ("completed", "failed", "stopped"):
            return {"status": "already_finished"}
        
        task["cancel_requested"] = True
        task["stage"] = "Остановка процесса..."
    
    log.info("Запрошена остановка задачи %s", task_id)
    return {"status": "cancelling"}


# ── API: история ─────────────────────────────────────────────────────────

@app.get("/api/history")
def api_history():
    return {"status": "success", "data": list_queries()}


@app.get("/api/history/{query_id}")
def api_history_item(query_id: int):
    item = get_query(query_id)
    if not item:
        raise HTTPException(404, "Запрос не найден")
    return {"status": "success", **item}


# ── API: экспорт Excel ───────────────────────────────────────────────────

@app.get("/api/export/{query_id}")
def api_export(query_id: int):
    import urllib.parse
    import re

    item = get_query(query_id)
    if not item:
        raise HTTPException(404, "Запрос не найден")

    seed_phrase = item["seed_phrase"]
    created_at = item.get("created_at", "")
    xlsx_bytes = build_xlsx(seed_phrase, item["results"], created_at=created_at)

    # Очищаем фразу от недопустимых в именах файлов символов
    clean_phrase = re.sub(r'[\\/*?:"<>|]', '', seed_phrase).strip().replace(" ", "_")
    clean_phrase = clean_phrase[:40] if clean_phrase else "keywords"

    # Формируем имя файла
    filename_utf8 = f"wordstat_{clean_phrase}_{query_id}.xlsx"
    encoded_filename = urllib.parse.quote(filename_utf8)
    ascii_fallback = f"wordstat_{query_id}.xlsx"

    # Заголовок по стандарту RFC 5987 для поддержки кириллицы во всех браузерах
    content_disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition},
    )
