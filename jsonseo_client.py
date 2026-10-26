"""
Клиент json seo API.
Методы:
  - fetch_phrases(mask, page) → список фраз с базовой частотностью
  - fetch_exact(phrase)       → точная частотность ("!слово !слово", kind="exact")
  - fetch_yandex_serp(query)  → органическая поисковая выдача Яндекса (ТОП сайтов) с полными Title
"""
import html
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

import httpx


def _fetch_single_page_title(url: str, timeout: float = 3.0) -> Optional[str]:
    """Быстро вытягивает реальный полный тег <title> со страницы сайта конкурента."""
    if not url or not url.startswith("http"):
        return None
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
                if m:
                    raw_title = html.unescape(m.group(1)).strip()
                    clean_title = re.sub(r"\s+", " ", raw_title)
                    if len(clean_title) >= 3:
                        return clean_title
    except Exception:
        pass
    return None


def enrich_real_titles(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Параллельно обогащает результаты выдачи настоящими полными тегами <title> страниц."""
    if not results:
        return results

    def _worker(item: Dict[str, Any]):
        url = item.get("url")
        if url:
            real_title = _fetch_single_page_title(url)
            if real_title:
                item["title"] = real_title

    with ThreadPoolExecutor(max_workers=min(len(results), 10)) as executor:
        list(executor.map(_worker, results))

    return results


class JsonSeoClient:
    BASE_URL = "https://jsonseo.ru/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        # Без глобального заголовка: wordstat использует key в params,
        # SERP-запросы добавляют Authorization: Bearer отдельно
        self._client = httpx.Client(timeout=25)

    def _params(self, extra: dict) -> dict:
        return {"key": self.api_key, **extra}

    def fetch_phrases(self, mask: str, page: int = 1) -> list[dict]:
        """
        GET /wordstat — возвращает список фраз по маске.
        Каждый элемент: {"text": "...", "value": 1234}
        """
        resp = self._client.get(
            f"{self.BASE_URL}/wordstat",
            params=self._params({"text": mask, "page": page}),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", {}).get("popular", [])

    def fetch_exact(self, phrase: str) -> Optional[int]:
        """
        GET /wordstat/frequency?kind=exact
        Возвращает точную частотность ("!слово !слово") одним числом, или None при ошибке.
        """
        resp = self._client.get(
            f"{self.BASE_URL}/wordstat/frequency",
            params=self._params({"text": phrase, "kind": "exact"}),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", {}).get("totalValue")

    def fetch_yandex_serp(
        self,
        query: str,
        region: int = 213,
        pages: int = 1,
        device: str = "desktop",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        GET /yandex — получение органической поисковой выдачи Яндекса без рекламы.
        Автоматически обогащает результаты полными реальными Title со страниц сайтов.
        """
        params = {
            "text": query,
            "region": region,
            "pages": pages,
            "device": device,
            "ads": 0,    # 0 = только органическая выдача, без рекламы
            "all": 0,    # 0 = только органические результаты
        }

        resp = self._client.get(
            f"{self.BASE_URL}/yandex",
            params=self._params(params),
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        raw_results = data.get("results", [])
        clean_results: List[Dict[str, Any]] = []

        for idx, item in enumerate(raw_results, start=1):
            if limit and idx > limit:
                break
            clean_results.append({
                "position": idx,
                "url": item.get("url", ""),
                "domain": item.get("domain", ""),
                "title": item.get("title", ""),
                "passage": item.get("passage", ""),
                "breadcrumbs": item.get("breadcrumbs", ""),
            })

        # Обогащаем полными настоящими Title со страниц сайтов (убираем троеточия Яндекса)
        clean_results = enrich_real_titles(clean_results)

        return {
            "query": data.get("query", query),
            "rawQuery": data.get("rawQuery", query),
            "found": data.get("found"),
            "found_human": data.get("found_human", ""),
            "region": data.get("lr", region),
            "device": device,
            "total_items": len(clean_results),
            "results": clean_results,
        }

    def close(self):
        self._client.close()
