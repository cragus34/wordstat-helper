"""
Фильтрация списка фраз по порогу частотности и стоп-словам.
"""


def filter_phrases(
    phrases: list[dict],
    min_freq: int,
    stop_words: list[str],
) -> list[dict]:
    """
    phrases  — список {"text": "...", "value": int}
    min_freq — минимальная базовая частотность (включительно)
    stop_words — слова-исключения (без учёта регистра)

    Возвращает отфильтрованный список.
    """
    stop_lower = [w.strip().lower() for w in stop_words if w.strip()]

    result = []
    for p in phrases:
        if p["value"] < min_freq:
            continue
        text_lower = p["text"].lower()
        if any(sw in text_lower for sw in stop_lower):
            continue
        result.append(p)
    return result


def dedupe_phrases(phrases: list[dict]) -> list[dict]:
    """
    Убирает повторы фраз, сохраняя порядок и первое вхождение.

    Сравнение идёт по нормализованному тексту: без учёта регистра,
    лишних пробелов по краям и двойных пробелов внутри. Нужна защита
    от дублей в ответе API и от повторных запусков по одной маске.
    """
    seen: set[str] = set()
    result: list[dict] = []

    for p in phrases:
        text = str(p.get("text", ""))
        normalized = " ".join(text.split()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(p)

    return result
