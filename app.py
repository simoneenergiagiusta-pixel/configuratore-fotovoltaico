from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import io
import math
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

app = Flask(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"


# =========================================================
# PVGIS
# =========================================================

def pvgis(params):
    url = PVGIS_URL + "?" + urlencode(params)
    req = Request(
        url,
        headers={"User-Agent": "ConfiguratoreFotovoltaico/1.0"}
    )

    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/pvgis")
def api_pvgis():
    d = request.get_json(force=True)

    params = {
        "lat": float(d["lat"]),
        "lon": float(d["lon"]),
        "peakpower": float(d["kwp"]),
        "loss": float(d.get("loss", 14)),
        "angle": float(d.get("angle", 30)),
        "aspect": float(d.get("aspect", 0)),
        "usehorizon": 1,
        "outputformat": "json",
    }

    data = pvgis(params)

    fixed = data["outputs"]["monthly"]["fixed"]
    yearly = data["outputs"]["totals"]["fixed"]

    monthly = [x["E_m"] for x in fixed]

    return jsonify({
        "annual_kwh": yearly["E_y"],
        "monthly_kwh": monthly,
        "raw": data
    })


# =========================================================
# CALCOLI
# =========================================================

def calculate_values(d):

    production = float(d["production"])
    consumption = float(d["consumption"])
    battery = float(d.get("battery", 0))

    price = float(d.get("energy_price", 0.25))
    export_price = float(d.get("export_price", 0.10))

    cost = float(d["cost"])
    deduction = float(d.get("deduction", 0))

    profile = d.get("profile", "equilibrato")

    direct_pct = {
        "giorno": 0.55,
        "equilibrato": 0.40,
        "sera": 0.28
    }.get(profile, 0.40)

    battery_boost = min(0.25, battery / 30.0)

    self_consumption_pct = min(
        0.95,
        direct_pct + battery_boost
    )

    self_used = min(
        production * self_consumption_pct,
        consumption
    )

    export = max(
        0,
        production - self_used
    )

    grid = max(
        0,
        consumption - self_used
    )

    annual_saving = (
        self_used * price
        + export * export_price
    )

    net_cost = max(
        0,
        cost - deduction
    )

    # Proiezione economica
    degradation = 0.005
    energy_price_growth = 0.02

    years = []
    cumulative_benefit = 0
    payback = None

    for y in range(1, 26):

        production_y = production * (
            (1 - degradation) ** (y - 1)
        )

        self_used_y = min(
            production_y * self_consumption_pct,
            consumption
        )

        export_y = max(
            0,
            production_y - self_used_y
        )

        energy_price_y = price * (
            (1 + energy_price_growth) ** (y - 1)
        )

        benefit = (
            self_used_y * energy_price_y
            + export_y * export_price
        )

        cumulative_benefit += benefit

        if payback is None and cumulative_benefit >= net_cost:
            payback = y

        years.append({
            "year": y,
            "benefit": benefit,
            "cumulative": cumulative_benefit,
            "production": production_y,
            "self_used": self_used_y,
            "export": export_y,
            "energy_price": energy_price_y
        })

    gross_25 = years[-1]["cumulative"]

    net_25 = max(
        0,
        gross_25 - net_cost
    )

    return {
        "self_used": self_used,
        "export": export,
        "grid_purchase": grid,
        "annual_saving": annual_saving,
        "net_cost": net_cost,
        "payback": payback,
        "years": years,
        "gross_25": gross_25,
        "net_25": net_25
    }


@app.post("/api/calculate")
def calculate():
    d = request.get_json(force=True)
    return jsonify(calculate_values(d))


# =========================================================
# COLORI
# =========================================================

GREEN = colors.HexColor("#15803D")
DARK_GREEN = colors.HexColor("#14532D")
MID_GREEN = colors.HexColor("#22C55E")
LIGHT_GREEN = colors.HexColor("#DCFCE7")

YELLOW = colors.HexColor("#FACC15")
LIGHT_YELLOW = colors.HexColor("#FEF9C3")

DARK = colors.HexColor("#172033")
GREY = colors.HexColor("#64748B")
LIGHT_GREY = colors.HexColor("#F1F5F9")
MID_GREY = colors.HexColor("#CBD5E1")

WHITE = colors.white


# =========================================================
# FUNZIONI GRAFICHE
# =========================================================

PAGE_W, PAGE_H = A4


def euro(value):
    return "€ " + f"{value:,.0f}".replace(",", ".")


def number(value):
    return f"{value:,.0f}".replace(",", ".")


def decimal(value):
    return f"{value:.2f}".replace(".", ",")


def draw_round_rect(c, x, y, w, h, fill, stroke=None, radius=8):
    c.setFillColor(fill)

    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.7)
    else:
        c.setStrokeColor(fill)

    c.roundRect(
        x,
        y,
        w,
        h,
        radius,
        fill=1,
        stroke=1 if stroke else 0
    )


def draw_header(c, title, subtitle=None):

    c.setFillColor(GREEN)
    c.rect(
        0,
        PAGE_H - 22 * mm,
        PAGE_W,
        22 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(
        18 * mm,
        PAGE_H - 13 * mm,
        title
    )

    if subtitle:
        c.setFont("Helvetica", 8.5)
        c.drawRightString(
            PAGE_W - 18 * mm,
            PAGE_H - 13 * mm,
            subtitle
        )


def draw_footer(c, page_number):

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.5)

    c.line(
        15 * mm,
        12 * mm,
        PAGE_W - 15 * mm,
        12 * mm
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 7)

    c.drawString(
        15 * mm,
        7 * mm,
        "Energia Giusta • Partner ENI Plenitude"
    )

    c.drawRightString(
        PAGE_W - 15 * mm,
        7 * mm,
        f"Pagina {page_number}"
    )


def draw_sun(c, x, y, size=18):

    c.setStrokeColor(YELLOW)
    c.setFillColor(YELLOW)
    c.setLineWidth(2)

    c.circle(
        x,
        y,
        size * 0.28,
        fill=1,
        stroke=0
    )

    for i in range(8):

        angle = math.radians(i * 45)

        x1 = x + math.cos(angle) * size * 0.48
        y1 = y + math.sin(angle) * size * 0.48

        x2 = x + math.cos(angle) * size * 0.72
        y2 = y + math.sin(angle) * size * 0.72

        c.line(x1, y1, x2, y2)


def draw_battery(c, x, y, w=30, h=48):

    c.setStrokeColor(GREEN)
    c.setLineWidth(2)

    c.roundRect(
        x,
        y,
        w,
        h,
        4,
        fill=0,
        stroke=1
    )

    c.rect(
        x + w * 0.35,
        y + h,
        w * 0.30,
        5,
        fill=0,
        stroke=1
    )

    for i in range(3):

        c.setFillColor(
            MID_GREEN if i < 2 else LIGHT_GREEN
        )

        c.roundRect(
            x + 5,
            y + 7 + i * 12,
            w - 10,
            8,
            2,
            fill=1,
            stroke=0
        )


def draw_house(c, x, y, w=55, h=40):

    c.setFillColor(LIGHT_GREEN)
    c.setStrokeColor(GREEN)
    c.setLineWidth(1.5)

    c.rect(
        x,
        y,
        w,
        h,
        fill=1,
        stroke=1
    )

    roof = [
        x - 5,
        y + h,
        x + w / 2,
        y + h + 30,
        x + w + 5,
        y + h
    ]

    path = c.beginPath()
    path.moveTo(roof[0], roof[1])
    path.lineTo(roof[2], roof[3])
    path.lineTo(roof[4], roof[5])
    path.close()

    c.setFillColor(GREEN)
    c.drawPath(path, fill=1, stroke=0)

    c.setFillColor(WHITE)
    c.rect(
        x + w * 0.42,
        y,
        w * 0.18,
        h * 0.55,
        fill=1,
        stroke=0
    )

    # pannelli
    c.setFillColor(DARK)
    c.rect(
        x + 8,
        y + h + 4,
        w * 0.68,
        13,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(WHITE)
    c.setLineWidth(0.5)

    for i in range(1, 4):
        px = x + 8 + i * (w * 0.68 / 4)
        c.line(
            px,
            y + h + 4,
            px,
            y + h + 17
        )


def draw_lightning(c, x, y, size=28):

    path = c.beginPath()

    path.moveTo(
        x + size * 0.20,
        y + size
    )

    path.lineTo(
        x + size * 0.72,
        y + size
    )

    path.lineTo(
        x + size * 0.45,
        y + size * 0.55
    )

    path.lineTo(
        x + size * 0.85,
        y + size * 0.55
    )

    path.lineTo(
        x + size * 0.15,
        y
    )

    path.lineTo(
        x + size * 0.38,
        y + size * 0.45
    )

    path.lineTo(
        x,
        y + size * 0.45
    )

    path.close()

    c.setFillColor(YELLOW)
    c.drawPath(path, fill=1, stroke=0)


def draw_euro(c, x, y, size=24):

    c.setFillColor(GREEN)
    c.circle(
        x,
        y,
        size / 2,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", size * 0.75)

    c.drawCentredString(
        x,
        y - size * 0.25,
        "€"
    )


def draw_chart_monthly(c, x, y, w, h, monthly):

    if not monthly:
        return

    max_value = max(monthly)
    max_value = max(max_value, 1)

    left = x + 14
    bottom = y + 22
    chart_w = w - 25
    chart_h = h - 35

    # area
    c.setFillColor(LIGHT_GREY)
    c.roundRect(
        x,
        y,
        w,
        h,
        8,
        fill=1,
        stroke=0
    )

    # titolo
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(
        x + 12,
        y + h - 17,
        "Produzione fotovoltaica mensile"
    )

    # griglia
    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.4)

    for i in range(5):

        gy = bottom + chart_h * i / 4

        c.line(
            left,
            gy,
            left + chart_w,
            gy
        )

    bar_w = chart_w / 12 * 0.62

    months = [
        "Gen", "Feb", "Mar", "Apr",
        "Mag", "Giu", "Lug", "Ago",
        "Set", "Ott", "Nov", "Dic"
    ]

    for i, value in enumerate(monthly):

        bx = left + i * chart_w / 12 + (
            chart_w / 12 - bar_w
        ) / 2

        bh = chart_h * value / max_value

        c.setFillColor(GREEN)

        c.roundRect(
            bx,
            bottom,
            bar_w,
            bh,
            2,
            fill=1,
            stroke=0
        )

        c.setFillColor(GREY)
        c.setFont("Helvetica", 6.5)

        c.drawCentredString(
            bx + bar_w / 2,
            bottom - 10,
            months[i]
        )


def draw_donut(c, cx, cy, radius, self_used, export):

    total = max(
        self_used + export,
        1
    )

    self_angle = 360 * self_used / total

    c.setFillColor(GREEN)

    c.wedge(
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
        0,
        self_angle,
        fill=1,
        stroke=0
    )

    c.setFillColor(YELLOW)

    c.wedge(
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
        self_angle,
        360 - self_angle,
        fill=1,
        stroke=0
    )

    # centro bianco
    c.setFillColor(WHITE)

    c.circle(
        cx,
        cy,
        radius * 0.55,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 12)

    c.drawCentredString(
        cx,
        cy + 2,
        f"{self_used / total * 100:.0f}%"
    )

    c.setFont("Helvetica", 7)

    c.drawCentredString(
        cx,
        cy - 8,
        "autoconsumo"
    )


def draw_economic_chart(c, x, y, w, h, years):

    if not years:
        return

    values = [
        item["cumulative"]
        for item in years
    ]

    max_value = max(values)
    max_value = max(max_value, 1)

    left = x + 30
    bottom = y + 25
    chart_w = w - 42
    chart_h = h - 42

    c.setFillColor(LIGHT_GREY)

    c.roundRect(
        x,
        y,
        w,
        h,
        8,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10)

    c.drawString(
        x + 12,
        y + h - 17,
        "Crescita del beneficio cumulato"
    )

    # griglia
    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.4)

    for i in range(5):

        gy = bottom + chart_h * i / 4

        c.line(
            left,
            gy,
            left + chart_w,
            gy
        )

    # linea
    c.setStrokeColor(GREEN)
    c.setLineWidth(2.2)

    previous = None

    for i, value in enumerate(values):

        px = left + chart_w * i / 24
        py = bottom + chart_h * value / max_value

        if previous:
            c.line(
                previous[0],
                previous[1],
                px,
                py
            )

        previous = (px, py)

        if i in [0, 4, 9, 14, 19, 24]:

            c.setFillColor(GREEN)

            c.circle(
                px,
                py,
                3,
                fill=1,
                stroke=0
            )

            c.setFillColor(GREY)
            c.setFont("Helvetica", 6.5)

            c.drawCentredString(
                px,
                bottom - 11,
                str(i + 1)
            )

    # asse
    c.setFillColor(GREY)
    c.setFont("Helvetica", 6)

    c.drawString(
        left,
        bottom - 21,
        "Anno"
    )

    c.drawRightString(
        left + chart_w,
        bottom - 21,
        "25"
    )


def draw_kpi(c, x, y, w, h, icon, label, value, accent):

    draw_round_rect(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(accent)
    c.circle(
        x + 18,
        y + h - 20,
        11,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10)

    c.drawCentredString(
        x + 18,
        y + h - 23,
        icon
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 7.5)

    c.drawString(
        x + 35,
        y + h - 17,
        label
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 17)

    c.drawString(
        x + 35,
        y + 14,
        value
    )


def draw_section_title(c, x, y, title, subtitle=None):

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 17)

    c.drawString(
        x,
        y,
        title
    )

    if subtitle:

        c.setFillColor(GREY)
        c.setFont("Helvetica", 8.5)

        c.drawString(
            x,
            y - 14,
            subtitle
        )


def draw_bullet(c, x, y, title, text):

    c.setFillColor(GREEN)

    c.circle(
        x,
        y + 3,
        4,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

    c.setFont("Helvetica-Bold", 9)

    c.drawString(
        x + 12,
        y,
        title
    )

    c.setFillColor(GREY)

    c.setFont("Helvetica", 7.5)

    c.drawString(
        x + 12,
        y - 11,
        text
    )


# =========================================================
# PDF
# =========================================================

@app.post("/api/pdf")
def generate_pdf():

    d = request.get_json(force=True)

    values = calculate_values(d)

    address = d.get(
        "address",
        "Abitazione"
    )

    kwp = float(d.get("kwp", 0))
    battery = float(d.get("battery", 0))
    angle = float(d.get("angle", 30))
    aspect = float(d.get("aspect", 0))
    loss = float(d.get("loss", 14))

    consumption = float(
        d.get("consumption", 0)
    )

    production = float(
        d.get("production", 0)
    )

    cost = float(
        d.get("cost", 0)
    )

    deduction = float(
        d.get("deduction", 0)
    )

    energy_price = float(
        d.get("energy_price", 0.25)
    )

    export_price = float(
        d.get("export_price", 0.10)
    )

    monthly = d.get(
        "monthly",
        []
    )

    if not monthly:
        monthly = [
            production / 12
            for _ in range(12)
        ]

    profile = d.get(
        "profile",
        "equilibrato"
    )

    profile_labels = {
        "giorno": "Prevalenza diurna",
        "equilibrato": "Equilibrato",
        "sera": "Prevalenza serale"
    }

    profile_label = profile_labels.get(
        profile,
        "Equilibrato"
    )

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    c.setTitle(
        "Analisi Fotovoltaica - Energia Giusta"
    )

    # =====================================================
    # PAGINA 1 - COPERTINA
    # =====================================================

    c.setFillColor(LIGHT_GREY)
    c.rect(
        0,
        0,
        PAGE_W,
        PAGE_H,
        fill=1,
        stroke=0
    )

    # fascia verde
    c.setFillColor(GREEN)
    c.rect(
        0,
        PAGE_H - 85 * mm,
        PAGE_W,
        85 * mm,
        fill=1,
        stroke=0
    )

    # marchio testuale
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 25)

    c.drawString(
        20 * mm,
        PAGE_H - 30 * mm,
        "ENERGIA GIUSTA"
    )

    c.setFont("Helvetica", 9)

    c.drawString(
        20 * mm,
        PAGE_H - 39 * mm,
        "Partner ENI Plenitude"
    )

    # titolo
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 28)

    c.drawString(
        20 * mm,
        PAGE_H - 115 * mm,
        "Analisi Fotovoltaica"
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 11)

    c.drawString(
        20 * mm,
        PAGE_H - 126 * mm,
        "Analisi energetica ed economica del tuo impianto"
    )

    # illustrazione
    draw_sun(
        c,
        PAGE_W - 48 * mm,
        PAGE_H - 104 * mm,
        25
    )

    draw_house(
        c,
        PAGE_W - 105 * mm,
        PAGE_H - 177 * mm,
        62,
        43
    )

    draw_battery(
        c,
        PAGE_W - 38 * mm,
        PAGE_H - 170 * mm,
        25,
        45
    )

    # indirizzo
    draw_round_rect(
        c,
        20 * mm,
        66 * mm,
        PAGE_W - 40 * mm,
        27 * mm,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)

    c.drawString(
        28 * mm,
        82 * mm,
        "ABITAZIONE ANALIZZATA"
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 12)

    c.drawString(
        28 * mm,
        72 * mm,
        address
    )

    # tre indicatori
    box_y = 32 * mm
    box_w = 50 * mm

    draw_kpi(
        c,
        20 * mm,
        box_y,
        box_w,
        23 * mm,
        "S",
        "Potenza FV",
        f"{decimal(kwp)} kWp",
        GREEN
    )

    draw_kpi(
        c,
        77 * mm,
        box_y,
        box_w,
        23 * mm,
        "B",
        "Accumulo",
        f"{decimal(battery)} kWh",
        YELLOW
    )

    draw_kpi(
        c,
        134 * mm,
        box_y,
        box_w,
        23 * mm,
        "€",
        "Risparmio annuo",
        euro(values["annual_saving"]),
        GREEN
    )

    draw_footer(c, 1)

    c.showPage()

    # =====================================================
    # PAGINA 2 - IL TUO IMPIANTO
    # =====================================================

    draw_header(
        c,
        "Il tuo impianto",
        "Dati tecnici e produzione"
    )

    draw_section_title(
        c,
        18 * mm,
        PAGE_H - 38 * mm,
        "Configurazione del sistema",
        "I principali parametri utilizzati per la simulazione"
    )

    # schede tecniche
    cards = [
        ("Potenza fotovoltaica", f"{decimal(kwp)} kWp"),
        ("Batteria di accumulo", f"{decimal(battery)} kWh"),
        ("Consumo annuo", f"{number(consumption)} kWh"),
        ("Produzione stimata", f"{number(production)} kWh"),
        ("Inclinazione", f"{decimal(angle)}°"),
        ("Perdite sistema", f"{decimal(loss)}%"),
    ]

    start_x = 18 * mm
    start_y = PAGE_H - 70 * mm

    card_w = 55 * mm
    card_h = 23 * mm
    gap_x = 5 * mm
    gap_y = 5 * mm

    for i, (label, value) in enumerate(cards):

        col = i % 3
        row = i // 3

        x = start_x + col * (card_w + gap_x)
        y = start_y - row * (card_h + gap_y)

        draw_round_rect(
            c,
            x,
            y,
            card_w,
            card_h,
            WHITE,
            MID_GREY,
            7
        )

        c.setFillColor(GREY)
        c.setFont("Helvetica", 7)

        c.drawString(
            x + 6 * mm,
            y + 14 * mm,
            label
        )

        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 13)

        c.drawString(
            x + 6 * mm,
            y + 5 * mm,
            value
        )

    # grafico
    draw_chart_monthly(
        c,
        18 * mm,
        63 * mm,
        PAGE_W - 36 * mm,
        82 * mm,
        monthly
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 7.5)

    c.drawString(
        18 * mm,
        53 * mm,
        "Produzione annuale stimata da PVGIS sulla base dei dati di localizzazione e configurazione inseriti."
    )

    draw_footer(c, 2)

    c.showPage()

    # =====================================================
    # PAGINA 3 - AUTOCONSUMO
    # =====================================================

    draw_header(
        c,
        "Come utilizzi la tua energia",
        "Autoconsumo e immissione"
    )

    draw_section_title(
        c,
        18 * mm,
        PAGE_H - 38 * mm,
        "Dove finisce l'energia prodotta?",
        "La simulazione distingue l'energia utilizzata direttamente da quella immessa in rete."
    )

    # donut
    draw_donut(
        c,
        72 * mm,
        PAGE_H - 100 * mm,
        35 * mm,
        values["self_used"],
        values["export"]
    )

    # legenda
    c.setFillColor(GREEN)
    c.rect(
        125 * mm,
        PAGE_H - 87 * mm,
        7 * mm,
        7 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 9)

    c.drawString(
        136 * mm,
        PAGE_H - 85 * mm,
        "Autoconsumo"
    )

    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)

    c.drawString(
        136 * mm,
        PAGE_H - 94 * mm,
        f"{number(values['self_used'])} kWh/anno"
    )

    c.setFillColor(YELLOW)
    c.rect(
        125 * mm,
        PAGE_H - 108 * mm,
        7 * mm,
        7 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 9)

    c.drawString(
        136 * mm,
        PAGE_H - 106 * mm,
        "Energia immessa"
    )

    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)

    c.drawString(
        136 * mm,
        PAGE_H - 115 * mm,
        f"{number(values['export'])} kWh/anno"
    )

    # KPI
    kpi_y = 91 * mm

    draw_kpi(
        c,
        18 * mm,
        kpi_y,
        53 * mm,
        25 * mm,
        "A",
        "Autoconsumo",
        f"{values['self_used'] / max(production, 1) * 100:.0f}%",
        GREEN
    )

    draw_kpi(
        c,
        78 * mm,
        kpi_y,
        53 * mm,
        25 * mm,
        "I",
        "Energia immessa",
        f"{values['export'] / max(production, 1) * 100:.0f}%",
        YELLOW
    )

    draw_kpi(
        c,
        138 * mm,
        kpi_y,
        53 * mm,
        25 * mm,
        "R",
        "Risparmio annuo",
        euro(values["annual_saving"]),
        GREEN
    )

    # box spiegazione
    draw_round_rect(
        c,
        18 * mm,
        45 * mm,
        PAGE_W - 36 * mm,
        35 * mm,
        LIGHT_GREEN,
        None,
        8
    )

    c.setFillColor(DARK_GREEN)
    c.setFont("Helvetica-Bold", 10)

    c.drawString(
        27 * mm,
        70 * mm,
        "Cosa significa?"
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica", 8)

    text_lines = [
        "Una parte dell'energia prodotta viene utilizzata direttamente dall'abitazione.",
        "La parte non utilizzata viene immessa in rete e valorizzata secondo il valore",
        "inserito nella simulazione."
    ]

    for i, line in enumerate(text_lines):

        c.drawString(
            27 * mm,
            61 * mm - i * 9,
            line
        )

    draw_footer(c, 3)

    c.showPage()

    # =====================================================
    # PAGINA 4 - RITORNO INVESTIMENTO
    # =====================================================

    draw_header(
        c,
        "Il ritorno dell'investimento",
        "Analisi economica"
    )

    draw_section_title(
        c,
        18 * mm,
        PAGE_H - 38 * mm,
        "I numeri principali",
        "Una sintesi immediata del risultato economico della simulazione."
    )

    payback_text = (
        f"{values['payback']:.1f} anni"
        if values["payback"] is not None
        else "Oltre 25 anni"
    )

    draw_kpi(
        c,
        18 * mm,
        PAGE_H - 85 * mm,
        55 * mm,
        30 * mm,
        "€",
        "Investimento netto",
        euro(values["net_cost"]),
        GREEN
    )

    draw_kpi(
        c,
        77 * mm,
        PAGE_H - 85 * mm,
        55 * mm,
        30 * mm,
        "T",
        "Rientro stimato",
        payback_text,
        YELLOW
    )

    draw_kpi(
        c,
        136 * mm,
        PAGE_H - 85 * mm,
        55 * mm,
        30 * mm,
        "€",
        "Beneficio 25 anni",
        euro(values["gross_25"]),
        GREEN
    )

    # confronto investimento / detrazione
    draw_round_rect(
        c,
        18 * mm,
        96 * mm,
        PAGE_W - 36 * mm,
        35 * mm,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10)

    c.drawString(
        27 * mm,
        120 * mm,
        "Composizione dell'investimento"
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)

    c.drawString(
        27 * mm,
        109 * mm,
        "Costo complessivo"
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 9)

    c.drawRightString(
        105 * mm,
        109 * mm,
        euro(cost)
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)

    c.drawString(
        115 * mm,
        109 * mm,
        "Detrazione"
    )

    c.setFillColor(GREEN)
    c.setFont("Helvetica-Bold", 9)

    c.drawRightString(
        185 * mm,
        109 * mm,
        euro(deduction)
    )

    # beneficio netto
    c.setFillColor(LIGHT_GREEN)

    c.roundRect(
        27 * mm,
        100 * mm,
        158 * mm,
        5 * mm,
        2,
        fill=1,
        stroke=0
    )

    # barra
    if cost > 0:

        deduction_width = min(
            158,
            158 * deduction / cost
        )

        c.setFillColor(GREEN)

        c.roundRect(
            27 * mm,
            100 * mm,
            deduction_width,
            5 * mm,
            2,
            fill=1,
            stroke=0
        )

    # grafico economico
    draw_economic_chart(
        c,
        18 * mm,
        25 * mm,
        PAGE_W - 36 * mm,
        63 * mm,
        values["years"]
    )

    draw_footer(c, 4)

    c.showPage()

    # =====================================================
    # PAGINA 5 - PROIEZIONE 25 ANNI
    # =====================================================

    draw_header(
        c,
        "Proiezione economica",
        "Orizzonte 25 anni"
    )

    draw_section_title(
        c,
        18 * mm,
        PAGE_H - 38 * mm,
        "Come evolve il beneficio nel tempo",
        "La simulazione considera un degrado della produzione dello 0,5% annuo e una crescita del prezzo dell'energia del 2% annuo."
    )

    # tabella
    table_x = 18 * mm
    table_y = PAGE_H - 80 * mm

    col_widths = [
        25 * mm,
        45 * mm,
        55 * mm
    ]

    headers = [
        "Periodo",
        "Beneficio annuo",
        "Beneficio cumulato"
    ]

    c.setFillColor(GREEN)
    c.roundRect(
        table_x,
        table_y,
        sum(col_widths),
        10 * mm,
        3,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8)

    xx = table_x

    for i, header in enumerate(headers):

        c.drawString(
            xx + 4 * mm,
            table_y + 3.5 * mm,
            header
        )

        xx += col_widths[i]

    selected_years = [1, 5, 10, 15, 20, 25]

    row_y = table_y - 9 * mm

    for index, year_number in enumerate(selected_years):

        item = values["years"][year_number - 1]

        if index % 2 == 0:
            c.setFillColor(LIGHT_GREY)
        else:
            c.setFillColor(WHITE)

        c.rect(
            table_x,
            row_y,
            sum(col_widths),
            9 * mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 8)

        c.drawString(
            table_x + 4 * mm,
            row_y + 3 * mm,
            f"{year_number}° anno"
        )

        c.drawString(
            table_x + col_widths[0] + 4 * mm,
            row_y + 3 * mm,
            euro(item["benefit"])
        )

        c.setFillColor(GREEN)
        c.drawString(
            table_x + col_widths[0] + col_widths[1] + 4 * mm,
            row_y + 3 * mm,
            euro(item["cumulative"])
        )

        row_y -= 9 * mm

    # box finale
    draw_round_rect(
        c,
        18 * mm,
        72 * mm,
        PAGE_W - 36 * mm,
        32 * mm,
        LIGHT_GREEN,
        None,
        8
    )

    c.setFillColor(DARK_GREEN)
    c.setFont("Helvetica-Bold", 11)

    c.drawString(
        27 * mm,
        91 * mm,
        "Beneficio cumulato stimato a 25 anni"
    )

    c.setFillColor(GREEN)
    c.setFont("Helvetica-Bold", 21)

    c.drawString(
        27 * mm,
        79 * mm,
        euro(values["gross_25"])
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica", 8)

    c.drawRightString(
        PAGE_W - 27 * mm,
        82 * mm,
        f"Beneficio netto dopo l'investimento: {euro(values['net_25'])}"
    )

    # assunzioni
    c.setFillColor(GREY)
    c.setFont("Helvetica", 7)

    assumptions = [
        "Degrado produzione: 0,5% annuo",
        "Crescita prezzo energia acquistata: 2% annuo",
        f"Prezzo energia acquistata: {decimal(energy_price)} €/kWh",
        f"Valore energia immessa: {decimal(export_price)} €/kWh",
        f"Profilo consumi: {profile_label}"
    ]

    yy = 57 * mm

    for line in assumptions:

        c.drawString(
            18 * mm,
            yy,
            "• " + line
        )

        yy -= 5 * mm

    draw_footer(c, 5)

    c.showPage()

    # =====================================================
    # PAGINA 6 - COSA COMPRENDE
    # =====================================================

    draw_header(
        c,
        "Cosa comprende la soluzione",
        "Servizi, assistenza e garanzie"
    )

    draw_section_title(
        c,
        18 * mm,
        PAGE_H - 38 * mm,
        "Un progetto completo, dalla progettazione al monitoraggio",
        "L'obiettivo è accompagnarti in tutte le fasi della realizzazione."
    )

    services = [
        ("FV", "Impianto fotovoltaico", "Pannelli e componentistica dimensionati sul progetto."),
        ("BA", "Accumulo energetico", "Sistema di batterie per aumentare l'utilizzo dell'energia prodotta."),
        ("PR", "Progettazione", "Analisi tecnica e dimensionamento dell'impianto."),
        ("IN", "Installazione", "Installazione e messa in servizio dell'impianto."),
        ("GS", "Pratiche GSE", "Gestione delle pratiche necessarie."),
        ("AP", "Monitoraggio App", "Controllo della produzione e dei consumi."),
    ]

    start_y = PAGE_H - 72 * mm

    for i, (icon, title, text) in enumerate(services):

        col = i % 2
        row = i // 2

        x = 18 * mm + col * 88 * mm
        y = start_y - row * 42 * mm

        draw_round_rect(
            c,
            x,
            y,
            82 * mm,
            34 * mm,
            WHITE,
            MID_GREY,
            8
        )

        c.setFillColor(GREEN)
        c.circle(
            x + 13 * mm,
            y + 23 * mm,
            8 * mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 7)

        c.drawCentredString(
            x + 13 * mm,
            y + 21 * mm,
            icon
        )

        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 9)

        c.drawString(
            x + 25 * mm,
            y + 24 * mm,
            title
        )

        c.setFillColor(GREY)
        c.setFont("Helvetica", 7)

        # testo su due righe
        words = text.split()
        line1 = ""
        line2 = ""

        for word in words:

            if stringWidth(
                line1 + " " + word,
                "Helvetica",
                7
            ) < 48 * mm:
                line1 += (" " if line1 else "") + word
            else:
                line2 += (" " if line2 else "") + word

        c.drawString(
            x + 25 * mm,
            y + 14 * mm,
            line1
        )

        c.drawString(
            x + 25 * mm,
            y + 7 * mm,
            line2
        )

    # garanzie
    draw_round_rect(
        c,
        18 * mm,
        37 * mm,
        PAGE_W - 36 * mm,
        30 * mm,
        LIGHT_YELLOW,
        None,
        8
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10)

    c.drawString(
        27 * mm,
        57 * mm,
        "Garanzie indicative della soluzione"
    )

    guarantees = [
        "Pannelli: garanzia prodotto fino a 25 anni",
        "Potenza pannelli: garanzia prestazionale fino a 30 anni",
        "Inverter: garanzia fino a 12 anni",
        "Batterie: garanzia fino a 11 anni"
    ]

    for i, item in enumerate(guarantees):

        c.setFillColor(DARK)
        c.setFont("Helvetica", 7.5)

        c.drawString(
            27 * mm + (i % 2) * 80 * mm,
            47 * mm - (i // 2) * 9 * mm,
            "✓ " + item
        )

    draw_footer(c, 6)

    c.showPage()

    # =====================================================
    # PAGINA 7 - CONTATTI
    # =====================================================

    c.setFillColor(GREEN)
    c.rect(
        0,
        0,
        PAGE_W,
        PAGE_H,
        fill=1,
        stroke=0
    )

    # decorazione
    c.setFillColor(DARK_GREEN)
    c.circle(
        PAGE_W + 15 * mm,
        PAGE_H - 20 * mm,
        65 * mm,
        fill=1,
        stroke=0
    )

    draw_sun(
        c,
        PAGE_W - 45 * mm,
        PAGE_H - 42 * mm,
        22
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 25)

    c.drawString(
        20 * mm,
        PAGE_H - 50 * mm,
        "La tua energia."
    )

    c.drawString(
        20 * mm,
        PAGE_H - 63 * mm,
        "Il tuo risparmio."
    )

    c.setFont("Helvetica", 10)

    c.drawString(
        20 * mm,
        PAGE_H - 79 * mm,
        "Per informazioni, approfondimenti e un'analisi personalizzata"
    )

    # contatto
    draw_round_rect(
        c,
        20 * mm,
        72 * mm,
        PAGE_W - 40 * mm,
        65 * mm,
        WHITE,
        None,
        10
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 17)

    c.drawString(
        30 * mm,
        121 * mm,
        "Simone Alfarano"
    )

    c.setFillColor(GREEN)
    c.setFont("Helvetica-Bold", 9)

    c.drawString(
        30 * mm,
        111 * mm,
        "RESPONSABILE COMMERCIALE"
    )

    c.setFillColor(GREY)
    c.setFont("Helvetica", 8.5)

    contact_lines = [
        "Energia Giusta",
        "Partner ENI Plenitude",
        "",
        "Tel. / WhatsApp: 351.7478652",
        "Email: simone.alfarano@energiagiusta.it",
        "Sito: www.energiagiusta.it"
    ]

    yy = 99 * mm

    for line in contact_lines:

        c.drawString(
            30 * mm,
            yy,
            line
        )

        yy -= 7 * mm

    # call to action
    c.setFillColor(YELLOW)
    c.roundRect(
        30 * mm,
        48 * mm,
        65 * mm,
        13 * mm,
        6,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 9)

    c.drawCentredString(
        62.5 * mm,
        52.5 * mm,
        "CONTATTAMI PER INFO"
    )

    c.setFillColor(WHITE)
    c.setFont("Helvetica", 7)

    c.drawString(
        20 * mm,
        18 * mm,
        "Stima commerciale. Il risultato dipende da tariffe, profilo reale dei consumi,"
    )

    c.drawString(
        20 * mm,
        13 * mm,
        "condizioni di scambio/ritiro, ombreggiamento e altri fattori."
    )

    c.drawRightString(
        PAGE_W - 20 * mm,
        13 * mm,
        "Non è un preventivo finanziario."
    )

    c.showPage()

    c.save()

    buffer.seek(0)

    filename = (
        "Analisi_Fotovoltaica_Energia_Giusta.pdf"
    )

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )


# =========================================================
# AVVIO
# =========================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
