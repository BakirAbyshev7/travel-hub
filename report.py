"""PDF-отчёт по заказу: перелёт + проживание + аренда авто. Нужен reportlab (pip install reportlab)."""
import io, os
from datetime import datetime

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
pdfmetrics.registerFont(TTFont("Sans", os.path.join(FONTS, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Bold", os.path.join(FONTS, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans", boldItalic="Sans-Bold")  # для <b> в тексте

BLUE = colors.HexColor("#0c5bd6")
ORANGE = colors.HexColor("#ff6d00")
INK = colors.HexColor("#0b1f35")
MUTED = colors.HexColor("#6b7c93")
LINE = colors.HexColor("#e3e9f1")
SOFT = colors.HexColor("#f4f7fb")

S = {
    "title": ParagraphStyle("t", fontName="Sans-Bold", fontSize=20, leading=24, textColor=INK),
    "h": ParagraphStyle("h", fontName="Sans-Bold", fontSize=12.5, leading=16, textColor=BLUE, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="Sans", fontSize=9.5, leading=13, textColor=INK),
    "m": ParagraphStyle("m", fontName="Sans", fontSize=8.5, leading=12, textColor=MUTED),
    "b": ParagraphStyle("b", fontName="Sans-Bold", fontSize=9.5, leading=13, textColor=INK),
    "r": ParagraphStyle("r", fontName="Sans-Bold", fontSize=9.5, leading=13, textColor=INK, alignment=2),
    "logo": ParagraphStyle("l", fontName="Sans-Bold", fontSize=18, leading=22, textColor=colors.white),
    "wh": ParagraphStyle("w", fontName="Sans", fontSize=9, leading=12, textColor=colors.white, alignment=2),
}
KIND = {"hotel": "Отель", "hostel": "Хостел", "guest_house": "Гостевой дом", "apartment": "Апартаменты"}


def som(n):
    return f"{round(n or 0):,}".replace(",", " ") + " сом"


_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["", "одна", "две"] + _UNITS[3:]  # «тысяча» женского рода: одна тысяча, две тысячи
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_SCALES = [(("", "", ""), False), (("тысяча", "тысячи", "тысяч"), True),
           (("миллион", "миллиона", "миллионов"), False), (("миллиард", "миллиарда", "миллиардов"), False)]


def plural(n, forms):
    """Склонение по числу: (1 сом, 2 сома, 5 сомов)."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def _triad(n, feminine):
    words = [_HUNDREDS[n // 100]]
    t, u = n % 100 // 10, n % 10
    if t == 1:
        words.append(_TEENS[u])
    else:
        words += [_TENS[t], (_UNITS_F if feminine else _UNITS)[u]]
    return [w for w in words if w]


def amount_in_words(amount):
    """18396 -> 'Восемнадцать тысяч триста девяносто шесть сомов 00 тыйынов'."""
    whole = int(round(amount or 0))
    if whole == 0:
        words = ["ноль"]
    else:
        words, n, i = [], whole, 0
        while n:
            n, part = divmod(n, 1000)
            if part:
                forms, fem = _SCALES[i]
                words = _triad(part, fem) + ([plural(part, forms)] if forms[0] else []) + words
            i += 1
    text = " ".join(words)
    return f"{text[0].upper()}{text[1:]} {plural(whole, ('сом', 'сома', 'сомов'))} 00 тыйынов"


def dt(s, with_time=True):
    """'2026-11-05T19:40:00+06:00' -> '05.11.2026 19:40' (местное время, как в ответе API)."""
    if not s:
        return "—"
    try:
        d = datetime.fromisoformat(str(s)[:19])
        return d.strftime("%d.%m.%Y %H:%M" if with_time and len(str(s)) > 10 else "%d.%m.%Y")
    except ValueError:
        return str(s)


def esc(s):
    return str(s if s is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rows_table(rows, width):
    """Таблица «параметр — значение»."""
    data = [[Paragraph(k, S["m"]), Paragraph(v, S["p"])] for k, v in rows]
    t = Table(data, colWidths=[45 * mm, width - 45 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def qr(text, size=28 * mm):
    w = QrCodeWidget(text)
    x1, y1, x2, y2 = w.getBounds()
    d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    d.add(w)
    return d


def build_pdf(order):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm,
                            title=f"Заказ {order['number']}", author="Путник")
    W = doc.width
    tk, h, c, cu = order.get("ticket") or {}, order.get("hotel") or {}, order.get("car") or {}, order.get("customer") or {}
    story = []

    # шапка
    head = Table([[Paragraph("✈ путник", S["logo"]),
                   Paragraph(f"Заказ <b>{esc(order['number'])}</b><br/>от {dt(order['created'])}", S["wh"])]],
                 colWidths=[W * 0.5, W * 0.5])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLUE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story += [head, Spacer(1, 6 * mm)]

    # заголовок + QR с номером заказа
    intro = [Paragraph("Подтверждение заказа", S["title"]), Spacer(1, 3),
             Paragraph(f"{esc(order.get('from'))} → {esc(order.get('to'))}", S["p"]), Spacer(1, 6),
             Paragraph(f"Клиент: <b>{esc(cu.get('name'))}</b><br/>Телефон: {esc(cu.get('phone') or '—')}"
                       f"<br/>Email: {esc(cu.get('email') or '—')}", S["p"])]
    top = Table([[intro, [qr(order["number"]), Paragraph("Номер заказа", S["m"])]]], colWidths=[W - 32 * mm, 32 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(top)

    # 1. перелёт
    story.append(Paragraph("1. Авиаперелёт", S["h"]))
    story.append(rows_table([
        ("Маршрут", f"{esc(tk.get('from_airport'))} → {esc(tk.get('to_airport'))}"),
        ("Авиакомпания / рейс", f"{esc(tk.get('airline'))} {esc(tk.get('flight') or '')}"),
        ("Вылет", dt(tk.get("departure")) + " (местное время)"),
        ("Прилёт", dt(tk.get("arrival")) + " (местное время)"),
        ("Пересадки", "прямой рейс" if not tk.get("transfers") else str(tk.get("transfers"))),
        ("Стоимость", f"<b>{som(tk.get('price'))}</b>"),
        ("Оплата билета", f'на сайте Aviasales: <link href="{esc(tk.get("link"))}" color="#0c73fe">открыть страницу покупки</link>'),
    ], W))

    # 2. проживание
    story.append(Paragraph("2. Проживание", S["h"]))
    story.append(rows_table([
        ("Объект", f"<b>{esc(h.get('name'))}</b> · {KIND.get(h.get('kind'), 'Жильё')}"),
        ("Город / адрес", f"{esc(h.get('city'))}{', ' + esc(h.get('address')) if h.get('address') else ''}"),
        ("Заезд — выезд", f"{dt(h.get('checkin'), False)} — {dt(h.get('checkout'), False)} ({h.get('nights', 1)} ноч.)"),
        ("Гости", esc(h.get("guests") or "—")),
        ("Стоимость", "уточняется и оплачивается в отеле"),
    ], W))

    # 3. авто
    story.append(Paragraph("3. Аренда автомобиля", S["h"]))
    story.append(rows_table([
        ("Автомобиль", f"<b>{esc(c.get('brand'))}</b> · {esc(c.get('class') or '')}"),
        ("Бронь №", esc(c.get("booking_id"))),
        ("Получение — возврат", f"{dt(c.get('date_from'), False)} — {dt(c.get('date_to'), False)} ({c.get('days')} сут.)"),
        ("Тариф", f"{som(c.get('price_day'))} / сутки"),
        ("Стоимость", f"<b>{som(c.get('total'))}</b> · оплата при получении"),
    ], W))

    # итог
    story.append(Spacer(1, 5 * mm))
    total = Table([
        [Paragraph("Авиабилет", S["p"]), Paragraph(som(tk.get("price")), S["r"])],
        [Paragraph("Аренда автомобиля", S["p"]), Paragraph(som(c.get("total")), S["r"])],
        [Paragraph("Проживание", S["p"]), Paragraph("в отеле", S["r"])],
        [Paragraph("<b>ИТОГО К ОПЛАТЕ</b>", S["b"]),
         Paragraph(f'<font size="14" color="#ff6d00">{som(order["total"])}</font>', S["r"])],
        [Paragraph(f'Сумма прописью: <b>{amount_in_words(order["total"])}</b>', S["p"]), ""],
    ], colWidths=[W * 0.6, W * 0.4])
    total.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEABOVE", (0, -2), (-1, -2), 1, ORANGE),
        ("SPAN", (0, -1), (-1, -1)), ("TOPPADDING", (0, -1), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(KeepTogether([total, Spacer(1, 4 * mm), Paragraph(
        "Цены на авиабилеты получены из кэша Aviasales и пересчитаны в сомы по курсу на момент заказа; "
        "итоговую стоимость билета подтверждает страница покупки. Учебный проект «Путник».", S["m"])]))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Sans", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 9 * mm, f"Путник · заказ {order['number']}")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"стр. {_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
