"""
Тест подключения к json seo API.
Проверяем:
  1. /wordstat — парсинг фраз по маске (первая страница)
  2. /wordstat/frequency?kind=superexact — суперточная частотность одной фразы
"""
import urllib.request
import urllib.parse
import json

API_KEY = "zw027uMWtfbV48xhnYXS"
BASE_URL = "https://jsonseo.ru/api"

def get(path: str, params: dict) -> dict:
    params["key"] = API_KEY
    url = f"{BASE_URL}{path}?" + urllib.parse.urlencode(params)
    print(f"\n>>> GET {url}\n")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read().decode("utf-8"))


# --- 1. Парсинг фраз по маске ---
print("=" * 60)
print("TEST 1: /wordstat — парсинг фраз по маске «каркасная баня»")
print("=" * 60)

data = get("/wordstat", {"text": "каркасная баня", "page": 1})
print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])


# --- 2. Суперточная частотность ---
print("\n" + "=" * 60)
print("TEST 2: /wordstat/frequency — суперточная для «каркасная баня»")
print("=" * 60)

data2 = get("/wordstat/frequency", {"text": "каркасная баня", "kind": "superexact"})
print(json.dumps(data2, ensure_ascii=False, indent=2))
