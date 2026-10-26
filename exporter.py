"""
Генерация красивого Excel-файла из результатов запроса.
Включает заголовок с исходной фразой (маской) и метаданными.
"""
import io
import re
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def sanitize_sheet_title(title: str) -> str:
    """Очищает строку для использования в названии листа Excel (макс 31 символ, без :\\/?*[])."""
    clean = re.sub(r'[:\\/?*\[\]]', '', title).strip()
    return clean[:30] if clean else "Семантика"


def build_xlsx(seed_phrase: str, results: list[dict], created_at: str = "") -> bytes:
    """
    Принимает seed_phrase и список {"phrase", "base_freq", "exact_freq"}.
    Возвращает байты .xlsx файла со стильным оформлением.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = sanitize_sheet_title(seed_phrase)
    ws.views.sheetView[0].showGridLines = True

    # Стили
    title_font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    meta_font = Font(name="Calibri", size=10, italic=True, color="6B7280")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2563EB")
    
    thin_border_side = Side(style="thin", color="E5E7EB")
    border_data = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    
    # ── Блок 1: Заголовок отчёта ──────────────────────────────────────────
    ws.cell(row=1, column=1, value=f"Маска запроса: «{seed_phrase}»").font = title_font
    
    date_str = created_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.cell(
        row=2, column=1, 
        value=f"Дата сбора: {date_str}  |  Всего фраз: {len(results)}  |  Сортировка: по точной частотности [!фраза]"
    ).font = meta_font

    # ── Блок 2: Заголовки таблицы ─────────────────────────────────────────
    table_headers = ["Ключевая фраза", "Базовая частотность", "Точная частотность [!фраза]"]
    start_row = 4

    for col_idx, header_text in enumerate(table_headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx, value=header_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[start_row].height = 26

    # ── Блок 3: Строки данных ─────────────────────────────────────────────
    data_font = Font(name="Calibri", size=11)
    zebra_fill = PatternFill("solid", fgColor="F8FAFC")

    for i, item in enumerate(results):
        r_idx = start_row + 1 + i
        exact_value = item.get("exact_freq")
        if exact_value is None:
            exact_value = item.get("super_exact_freq")

        c1 = ws.cell(row=r_idx, column=1, value=item["phrase"])
        c2 = ws.cell(row=r_idx, column=2, value=item["base_freq"])
        c3 = ws.cell(row=r_idx, column=3, value=exact_value)

        c1.font = data_font
        c2.font = data_font
        c3.font = Font(name="Calibri", size=11, bold=True, color="1D4ED8")

        c1.alignment = Alignment(horizontal="left", vertical="center")
        c2.alignment = Alignment(horizontal="right", vertical="center")
        c3.alignment = Alignment(horizontal="right", vertical="center")

        # Чередование строк
        if i % 2 == 1:
            c1.fill = zebra_fill
            c2.fill = zebra_fill
            c3.fill = zebra_fill

        c1.border = border_data
        c2.border = border_data
        c3.border = border_data

    # Ширина колонок
    max_phrase_len = max([len(item["phrase"]) for item in results], default=25)
    ws.column_dimensions["A"].width = max(max_phrase_len + 4, 35)
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 30

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_serp_xlsx(query: str, results: list[dict], region: int = 213, device: str = "desktop") -> bytes:
    """
    Генерирует Excel-отчёт для результатов органической выдачи Яндекса.
    Колонки: Позиция, Домен, Заголовок (Title), Описание (Passage), URL.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = sanitize_sheet_title(f"Выдача - {query}")
    ws.views.sheetView[0].showGridLines = True

    # Стили
    title_font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    meta_font = Font(name="Calibri", size=10, italic=True, color="6B7280")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2563EB")
    
    thin_border_side = Side(style="thin", color="E5E7EB")
    border_data = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    
    # ── Блок 1: Заголовок отчёта ──────────────────────────────────────────
    ws.cell(row=1, column=1, value=f"Поисковая выдача Яндекс: «{query}»").font = title_font
    
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.cell(
        row=2, column=1, 
        value=f"Дата: {date_str}  |  Регион: {region}  |  Устройство: {device}  |  Сайтов в ТОП: {len(results)}  |  Только органика (без рекламы)"
    ).font = meta_font

    # ── Блок 2: Заголовки таблицы ─────────────────────────────────────────
    table_headers = ["№", "Домен", "Заголовок страницы (Title)", "Описание (Сниппет)", "URL страницы"]
    start_row = 4

    for col_idx, header_text in enumerate(table_headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx, value=header_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[start_row].height = 26

    # ── Блок 3: Данные ────────────────────────────────────────────────────
    data_font = Font(name="Calibri", size=11)
    url_font = Font(name="Calibri", size=10, color="2563EB", underline="single")
    zebra_fill = PatternFill("solid", fgColor="F8FAFC")

    for i, item in enumerate(results):
        r_idx = start_row + 1 + i
        pos = item.get("position", i + 1)
        domain = item.get("domain", "")
        title = item.get("title", "")
        passage = item.get("passage", "")
        url = item.get("url", "")

        c1 = ws.cell(row=r_idx, column=1, value=pos)
        c2 = ws.cell(row=r_idx, column=2, value=domain)
        c3 = ws.cell(row=r_idx, column=3, value=title)
        c4 = ws.cell(row=r_idx, column=4, value=passage)
        c5 = ws.cell(row=r_idx, column=5, value=url)

        c1.font = Font(name="Calibri", size=11, bold=True, color="1E3A8A")
        c2.font = Font(name="Calibri", size=11, bold=True)
        c3.font = data_font
        c4.font = data_font
        c5.font = url_font
        
        # Делаем URL кликабельным в Excel
        if url.startswith("http"):
            c5.hyperlink = url

        c1.alignment = Alignment(horizontal="center", vertical="top")
        c2.alignment = Alignment(horizontal="left", vertical="top")
        c3.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        c4.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        c5.alignment = Alignment(horizontal="left", vertical="top")

        # Чередование строк
        if i % 2 == 1:
            for cell in (c1, c2, c3, c4, c5):
                cell.fill = zebra_fill

        for cell in (c1, c2, c3, c4, c5):
            cell.border = border_data

    # Ширина колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 45
    ws.column_dimensions["D"].width = 55
    ws.column_dimensions["E"].width = 40

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

