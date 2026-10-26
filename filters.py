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


def should_stop_pagination(page_phrases: list[dict], min_freq: int) -> bool:
    """
    Вариант B: останавливаем пагинацию, если минимальная частотность
    на текущей странице уже ниже порога (список отсортирован по убыванию).
    """
    if not page_phrases:
        return True
    return page_phrases[-1]["value"] < min_freq
