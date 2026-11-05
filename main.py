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

from exporter import build_xlsx, build_serp_xlsx, build_questions_xlsx
from filters import dedupe_phrases, filter_phrases
from history import (
    create_query,
    finish_query,
    get_query,
    init_db,
    list_queries,
    update_query_progress,
)
from jsonseo_client import JsonSeoClient
from questions import build_question_mask, extract_questions

# ── Конфиг ──────────────────────────────────────────────────────────────
load_dotenv()
API_KEY = os.getenv("JSONSEO_API_KEY", "")

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
        # ── Шаг 1: сбор фраз ────────────────────────────────────────────
        # У /wordstat нет пагинации: popular и associations приходят целиком
        # за один запрос. Раньше здесь крутился цикл по страницам с параметром
        # page, который метод молча игнорировал, — одна и та же выдача
        # повторялась до 20 раз, раздувая результат одинаковыми строками.
        update_task(status="running", stage="Сбор списка фраз из Wordstat...", progress=10)
        log.info("[%s] Запрос popular-списка для маски «%s»", task_id, seed_phrase)

        try:
            raw_phrases = client.fetch_phrases(seed_phrase)
        except Exception as e:
            log.error("[%s] Ошибка при парсинге Wordstat: %s", task_id, e)
            finish_query(query_id, [], status="failed")
            update_task(status="failed", error=f"Ошибка Wordstat API: {e}")
            return

        # ── Страховка от дублей в ответе API ────────────────────────────
        all_phrases = dedupe_phrases(raw_phrases)
        log.info(
            "[%s] Получено фраз: %d, уникальных: %d",
            task_id, len(raw_phrases), len(all_phrases),
        )

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


def background_questions_worker(task_id: str, phrase: str, min_freq: int, exact_top: int):
    """
    Вкладка «Вопросы»: одна маска + фраза, затем (опционально) точная
    частотность для первых exact_top фраз по базовой частоте.
    """
    def update_task(**kwargs):
        with tasks_lock:
            if task_id in TASKS:
                TASKS[task_id].update(kwargs)

    def is_cancelled() -> bool:
        with tasks_lock:
            return TASKS.get(task_id, {}).get("cancel_requested", False)

    query_id = create_query(phrase, mode="questions")
    mask = build_question_mask(phrase)
    update_task(query_id=query_id, mask=mask)
    log.info("[%s] Вопросы по «%s», маска: %s", task_id, phrase, mask)

    client = JsonSeoClient(API_KEY)
    results: list[dict] = []

    def save(items: list[dict], status: str, stage: str):
        finish_query(query_id, items, status=status)
        update_task(status=status, progress=100, stage=stage, data=items)

    try:
        # ── Шаг 1: ищем вопросы одной маской ────────────────────────────
        update_task(status="running", stage="Ищем вопросы в Wordstat...", progress=15)
        try:
            raw_phrases = client.fetch_phrases(mask)
        except Exception as e:
            log.error("[%s] Ошибка Wordstat по маске вопросов: %s", task_id, e)
            finish_query(query_id, [], status="failed")
            update_task(status="failed", error=f"Ошибка Wordstat API: {e}")
            return

        found = extract_questions(raw_phrases, min_freq)
        log.info("[%s] Wordstat вернул %d фраз, вопросов после фильтра: %d",
                 task_id, len(raw_phrases), len(found))

        if not found:
            save([], "completed", "Готово: вопросов не найдено")
            return

        # Сразу фиксируем в БД — даже если точную частотность не считаем
        update_query_progress(query_id, found, status="running")

        # ── Шаг 2 (опционально): точная частотность top-N ───────────────
        if exact_top > 0:
            top = found[:exact_top]
            for idx, item in enumerate(top, start=1):
                if is_cancelled():
                    log.info("[%s] Остановлено на фразе %d из %d", task_id, idx, len(top))
                    found.sort(key=lambda x: x["base_freq"], reverse=True)
                    save(found, "stopped", f"Остановлено. Сохранено {len(found)} вопросов")
                    return

                pct = 30 + int((idx / len(top)) * 65)
                update_task(
                    stage=f"Точная частотность: {idx} из {len(top)} ({pct}%)",
                    progress=pct,
                )

                try:
                    item["exact_freq"] = client.fetch_exact(item["phrase"])
                except Exception as e:
                    log.warning("[%s] Ошибка частотности для «%s»: %s", task_id, item["phrase"], e)
                    item["exact_freq"] = None

                update_query_progress(query_id, found, status="running")

            found.sort(key=lambda x: (x.get("exact_freq") or 0, x["base_freq"]), reverse=True)

        results = found
        save(results, "completed", f"Готово: {len(results)} вопросов")

    except Exception as e:
        log.exception("[%s] Непредвиденная ошибка в сборе вопросов: %s", task_id, e)
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


@app.post("/api/run-questions")
def run_questions(
    background_tasks: BackgroundTasks,
    phrase: str = Form(...),
    min_freq: int = Form(0),
    exact_top: int = Form(0),
):
    """Вкладка «Вопросы»: запуск поиска вопросительных ключей по фразе."""
    if not API_KEY:
        raise HTTPException(500, "JSONSEO_API_KEY не задан в .env")

    clean_phrase = phrase.strip()
    if not clean_phrase:
        raise HTTPException(400, "Фраза не может быть пустой")

    # Потолок на точную частотность: 0.01 ₽ за фразу
    exact_top = max(0, min(int(exact_top), 500))

    task_id = str(uuid.uuid4())
    mask = build_question_mask(clean_phrase)

    with tasks_lock:
        TASKS[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "stage": "Инициализация задачи...",
            "cancel_requested": False,
            "query_id": None,
            "data": None,
            "error": None,
            "mode": "questions",
            "mask": mask,
        }

    background_tasks.add_task(background_questions_worker, task_id, clean_phrase, min_freq, exact_top)
    return {"status": "accepted", "task_id": task_id, "mask": mask}


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


@app.get("/api/export-questions/{query_id}")
def api_export_questions(query_id: int):
    import urllib.parse
    import re

    item = get_query(query_id)
    if not item:
        raise HTTPException(404, "Запрос не найден")
    if item.get("mode") != "questions":
        raise HTTPException(400, "Это не сбор вопросов")

    seed_phrase = item["seed_phrase"]
    xlsx_bytes = build_questions_xlsx(
        seed_phrase,
        item["results"],
        created_at=item.get("created_at", ""),
        mask=build_question_mask(seed_phrase),
    )

    clean_phrase = re.sub(r'[\\/*?:"<>|]', '', seed_phrase).strip().replace(" ", "_")
    clean_phrase = clean_phrase[:40] if clean_phrase else "questions"

    filename_utf8 = f"questions_{clean_phrase}_{query_id}.xlsx"
    encoded_filename = urllib.parse.quote(filename_utf8)
    ascii_fallback = f"questions_{query_id}.xlsx"

    content_disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition},
    )


# ── API: Поисковая выдача Яндекса (SERP) ─────────────────────────────────

@app.post("/api/serp")
def api_serp(
    query: str = Form(...),
    region: int = Form(213),
    device: str = Form("desktop"),
    limit: int = Form(10),
):
    if not API_KEY:
        raise HTTPException(500, "JSONSEO_API_KEY не задан в .env")

    clean_query = query.strip()
    if not clean_query:
        raise HTTPException(400, "Поисковый запрос не может быть пустым")

    # Рассчитываем количество страниц (1 страница = до 10-15 результатов)
    pages = 1
    if limit > 10:
        pages = min(2, (limit + 9) // 10)

    client = JsonSeoClient(API_KEY)
    try:
        data = client.fetch_yandex_serp(
            query=clean_query,
            region=region,
            pages=pages,
            device=device,
            limit=limit,
        )
        return {"status": "success", "data": data}
    except Exception as e:
        log.error("Ошибка при получении SERP Яндекса: %s", e)
        raise HTTPException(500, f"Ошибка при запросе к Яндексу: {e}")
    finally:
        client.close()


@app.post("/api/export-serp")
async def api_export_serp(request: Request):
    import urllib.parse
    import re

    body = await request.json()
    query = body.get("query", "serp")
    results = body.get("results", [])
    region = body.get("region", 213)
    device = body.get("device", "desktop")

    if not results:
        raise HTTPException(400, "Нет результатов для экспорта")

    xlsx_bytes = build_serp_xlsx(query, results, region=region, device=device)

    clean_query = re.sub(r'[\\/*?:"<>|]', '', query).strip().replace(" ", "_")
    clean_query = clean_query[:40] if clean_query else "serp"

    filename_utf8 = f"yandex_top_{clean_query}.xlsx"
    encoded_filename = urllib.parse.quote(filename_utf8)
    ascii_fallback = "yandex_serp.xlsx"

    content_disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition},
    )

