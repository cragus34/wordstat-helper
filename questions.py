"""
Вопросы: маска Вордстата для «вопросительных» ключей и разбор её результата.

Wordstat по маске вида (+как|+почему|...) фраза отдаёт запросы, где рядом
с нашей темой встречается вопросительное слово. Один такой запрос отдаёт
весь список сразу — пагинации у метода нет.
"""
import re

# Порядок важен только для предсказуемости маски, на результат не влияет.
QUESTION_WORDS = [
    "+как", "+почему", "+зачем", "+что", "+когда", "+где", "+сколько",
    "+какой", "+какая", "+какие", "+чем", "+кто", "+куда", "+можно +ли",
]

# Формы, которые реально приходят в выдаче: Вордстат подставляет морфологию,
# поэтому по маске +какой находятся и «какую», и «какое».
_QUESTION_FORMS = {
    "как", "почему", "зачем", "что", "когда", "где", "сколько",
    "какой", "какая", "какие", "какое", "какую", "каких", "каким", "какими",
    "чем", "кто", "куда", "можно",
}

_WORD_RE = re.compile(r"[а-яёa-z]+")


def build_question_mask(phrase: str) -> str:
    """Собирает маску Вордстата: (вопросы) + фраза."""
    return f"({'|'.join(QUESTION_WORDS)}) {phrase.strip()}"


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def detect_question(text: str) -> str | None:
    """
    Возвращает вопросительное слово, из-за которого фраза попала в выдачу,
    или None, если его нет. «можно ли» считается одним вопросом.
    """
    tokens = _tokens(text)
    candidates: list[tuple[int, str]] = []

    for i in range(len(tokens) - 1):
        if tokens[i] == "можно" and tokens[i + 1] == "ли":
            candidates.append((i, "можно ли"))

    for i, token in enumerate(tokens):
        if token in _QUESTION_FORMS:
            candidates.append((i, token))

    if not candidates:
        return None

    # Берём самое раннее вхождение, при равенстве — «можно ли»
    candidates.sort(key=lambda pair: (pair[0], pair[1] != "можно ли"))
    return candidates[0][1]


def extract_questions(phrases: list[dict], min_freq: int = 0) -> list[dict]:
    """
    Превращает выдачу Wordstat в список вопросов:
    {"phrase", "question", "base_freq", "exact_freq"}.

    Фразы без вопросительного слова и с базовой частотностью ниже min_freq
    отбрасываются; дубли убираются. Сортировка — по базовой частотности.
    """
    from filters import dedupe_phrases

    result: list[dict] = []
    for item in dedupe_phrases(phrases):
        value = int(item.get("value") or 0)
        if value < min_freq:
            continue

        text = str(item.get("text", "")).strip()
        question = detect_question(text)
        if not question:
            continue

        result.append({
            "phrase": text,
            "question": question,
            "base_freq": value,
            "exact_freq": None,
        })

    result.sort(key=lambda x: x["base_freq"], reverse=True)
    return result
