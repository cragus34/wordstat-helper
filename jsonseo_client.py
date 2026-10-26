"""
Клиент json seo API.
Методы:
  - fetch_phrases(mask, page) → список фраз с базовой частотностью
  - fetch_exact(phrase)       → точная частотность ("!слово !слово", kind="exact")
"""
import httpx
from typing import Optional


class JsonSeoClient:
    BASE_URL = "https://jsonseo.ru/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.Client(timeout=20)

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

    def close(self):
        self._client.close()
