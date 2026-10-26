"""
Клиент json seo API.
Методы:
  - fetch_phrases(mask, page) → список фраз с базовой частотностью
  - fetch_exact(phrase)       → точная частотность ("!слово !слово", kind="exact")
  - fetch_yandex_serp(query)  → органическая поисковая выдача Яндекса (ТОП сайтов)
"""
import httpx
from typing import Optional, List, Dict, Any


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
        Возвращает словарь с метаданными и списком сайтов:
        [
          {
            "position": 1,
            "url": "https://...",
            "domain": "example.com",
            "title": "...",
            "passage": "...",
            "breadcrumbs": "..."
          },
          ...
        ]
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

