from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import io
import math

from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

app = Flask(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"

PAGE_W, PAGE_H = A4


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

    return jsonify(
        calculate_values(d)
    )


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
# FORMATTAZIONE
# =========================================================

def euro(value):
    return "€ " + f"{value:,.0f}".replace(",", ".")


def number(value):
    return f"{value:,.0f}".replace(",", ".")


def decimal(value):
    return f"{value:.2f}".replace(".", ",")


# =========================================================
# TESTO CON A CAPO AUTOMATICO
# =========================================================

def wrap_text(text, font="Helvetica", size=8, max_width=50*mm):

    words = str(text).split()

    lines = []
    current = ""

    for word in words:

        test = word if not current else current + " " + word

        if stringWidth(test, font, size) <= max_width:
            current = test
        else:

            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


def draw_wrapped_text(
    c,
    text,
    x,
    y,
    max_width,
    font="Helvetica",
    size=8,
    leading=11,
    color=DARK,
    max_lines=None
):

    lines = wrap_text(
        text,
        font,
        size,
        max_width
    )

    if max_lines:
        lines = lines[:max_lines]

    c.setFillColor(color)
    c.setFont(font, size)

    for i, line in enumerate(lines):

        c.drawString(
            x,
            y - i * leading,
            line
        )

    return len(lines) * leading


# =========================================================
# BOX
# =========================================================

def draw_round_rect(
    c,
    x,
    y,
    w,
    h,
    fill,
    stroke=None,
    radius=7
):

    c.setFillColor(fill)

    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.7)
        stroke_value = 1
    else:
        c.setStrokeColor(fill)
        stroke_value = 0

    c.roundRect(
        x,
        y,
        w,
        h,
        radius,
        fill=1,
        stroke=stroke_value
    )


# =========================================================
# HEADER / FOOTER
# =========================================================

def draw_header(c, title, subtitle=""):

    c.setFillColor(GREEN)

    c.rect(
        0,
        PAGE_H - 22*mm,
        PAGE_W,
        22*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        17
    )

    c.drawString(
        18*mm,
        PAGE_H - 13.5*mm,
        title
    )

    if subtitle:

        c.setFont(
            "Helvetica",
            7.5
        )

        c.drawRightString(
            PAGE_W - 18*mm,
            PAGE_H - 13.5*mm,
            subtitle
        )


def draw_footer(c, page):

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.4)

    c.line(
        15*mm,
        12*mm,
        PAGE_W - 15*mm,
        12*mm
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        6.5
    )

    c.drawString(
        15*mm,
        7*mm,
        "Energia Giusta • Partner ENI Plenitude"
    )

    c.drawRightString(
        PAGE_W - 15*mm,
        7*mm,
        f"Pagina {page}"
    )


def draw_section_title(
    c,
    title,
    subtitle,
    y
):

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        18
    )

    c.drawString(
        18*mm,
        y,
        title
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        8
    )

    draw_wrapped_text(
        c,
        subtitle,
        18*mm,
        y - 8*mm,
        PAGE_W - 36*mm,
        "Helvetica",
        8,
        10,
        GREY
    )


# =========================================================
# ICONA SOLE
# =========================================================

def draw_sun(c, x, y, size=20):

    c.setStrokeColor(YELLOW)
    c.setFillColor(YELLOW)
    c.setLineWidth(1.5)

    c.circle(
        x,
        y,
        size * 0.28,
        fill=1,
        stroke=0
    )

    for i in range(8):

        angle = math.radians(i * 45)

        x1 = x + math.cos(angle) * size * 0.45
        y1 = y + math.sin(angle) * size * 0.45

        x2 = x + math.cos(angle) * size * 0.72
        y2 = y + math.sin(angle) * size * 0.72

        c.line(
            x1,
            y1,
            x2,
            y2
        )


# =========================================================
# CASA
# =========================================================

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

    path = c.beginPath()

    path.moveTo(
        x - 5,
        y + h
    )

    path.lineTo(
        x + w/2,
        y + h + 28
    )

    path.lineTo(
        x + w + 5,
        y + h
    )

    path.close()

    c.setFillColor(GREEN)

    c.drawPath(
        path,
        fill=1,
        stroke=0
    )

    # porta
    c.setFillColor(WHITE)

    c.rect(
        x + w*0.42,
        y,
        w*0.18,
        h*0.55,
        fill=1,
        stroke=0
    )

    # pannello
    c.setFillColor(DARK)

    c.rect(
        x + 8,
        y + h + 4,
        w*0.68,
        13,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(WHITE)
    c.setLineWidth(0.4)

    for i in range(1, 4):

        px = x + 8 + i*(w*0.68/4)

        c.line(
            px,
            y + h + 4,
            px,
            y + h + 17
        )


# =========================================================
# BATTERIA
# =========================================================

def draw_battery(c, x, y, w=27, h=45):

    c.setStrokeColor(GREEN)
    c.setLineWidth(1.5)

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
        x + w*0.35,
        y + h,
        w*0.30,
        4,
        fill=0,
        stroke=1
    )

    for i in range(3):

        c.setFillColor(
            MID_GREEN if i < 2 else LIGHT_GREEN
        )

        c.roundRect(
            x + 5,
            y + 7 + i*11,
            w - 10,
            7,
            2,
            fill=1,
            stroke=0
        )


# =========================================================
# KPI
# =========================================================

def draw_kpi(
    c,
    x,
    y,
    w,
    h,
    icon,
    label,
    value,
    accent=GREEN
):

    draw_round_rect(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        7
    )

    c.setFillColor(accent)

    c.circle(
        x + 14*mm,
        y + h - 12*mm,
        8*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawCentredString(
        x + 14*mm,
        y + h - 15*mm,
        icon
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7
    )

    c.drawString(
        x + 26*mm,
        y + h - 10*mm,
        label
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        15
    )

    # valore leggermente più piccolo se molto lungo
    font_size = 15

    if stringWidth(
        value,
        "Helvetica-Bold",
        font_size
    ) > w - 31*mm:

        font_size = 11

    c.setFont(
        "Helvetica-Bold",
        font_size
    )

    c.drawString(
        x + 26*mm,
        y + 6*mm,
        value
    )


# =========================================================
# GRAFICO PRODUZIONE MENSILE
# =========================================================

def draw_chart_monthly(
    c,
    x,
    y,
    w,
    h,
    monthly
):

    if not monthly:
        return

    draw_round_rect(
        c,
        x,
        y,
        w,
        h,
        LIGHT_GREY,
        None,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        x + 10*mm,
        y + h - 11*mm,
        "Produzione fotovoltaica mensile"
    )

    left = x + 15*mm
    bottom = y + 13*mm

    chart_w = w - 23*mm
    chart_h = h - 30*mm

    maximum = max(
        max(monthly),
        1
    )

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.4)

    for i in range(5):

        gy = bottom + chart_h*i/4

        c.line(
            left,
            gy,
            left + chart_w,
            gy
        )

    months = [
        "Gen", "Feb", "Mar", "Apr",
        "Mag", "Giu", "Lug", "Ago",
        "Set", "Ott", "Nov", "Dic"
    ]

    slot = chart_w / 12
    bar_w = slot * 0.55

    for i, value in enumerate(monthly[:12]):

        bx = (
            left
            + i*slot
            + (slot-bar_w)/2
        )

        bh = (
            chart_h
            * value
            / maximum
        )

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

        c.setFont(
            "Helvetica",
            6
        )

        c.drawCentredString(
            bx + bar_w/2,
            bottom - 9,
            months[i]
        )


# =========================================================
# DONUT
# =========================================================

def draw_donut(
    c,
    cx,
    cy,
    radius,
    self_used,
    export
):

    total = max(
        self_used + export,
        1
    )

    angle = (
        360
        * self_used
        / total
    )

    c.setFillColor(GREEN)

    c.wedge(
        cx-radius,
        cy-radius,
        cx+radius,
        cy+radius,
        0,
        angle,
        fill=1,
        stroke=0
    )

    c.setFillColor(YELLOW)

    c.wedge(
        cx-radius,
        cy-radius,
        cx+radius,
        cy+radius,
        angle,
        360-angle,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.circle(
        cx,
        cy,
        radius*0.57,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        13
    )

    c.drawCentredString(
        cx,
        cy + 2,
        f"{self_used/total*100:.0f}%"
    )

    c.setFont(
        "Helvetica",
        7
    )

    c.drawCentredString(
        cx,
        cy - 8,
        "autoconsumo"
    )


# =========================================================
# GRAFICO ECONOMICO
# =========================================================

def draw_economic_chart(
    c,
    x,
    y,
    w,
    h,
    years
):

    if not years:
        return

    draw_round_rect(
        c,
        x,
        y,
        w,
        h,
        LIGHT_GREY,
        None,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        x + 10*mm,
        y + h - 11*mm,
        "Crescita del beneficio cumulato"
    )

    values = [
        item["cumulative"]
        for item in years
    ]

    maximum = max(
        max(values),
        1
    )

    left = x + 17*mm
    bottom = y + 15*mm

    chart_w = w - 25*mm
    chart_h = h - 32*mm

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(0.4)

    for i in range(5):

        gy = bottom + chart_h*i/4

        c.line(
            left,
            gy,
            left + chart_w,
            gy
        )

    c.setStrokeColor(GREEN)
    c.setLineWidth(2)

    previous = None

    for i, value in enumerate(values):

        px = (
            left
            + chart_w*i/24
        )

        py = (
            bottom
            + chart_h*value/maximum
        )

        if previous:

            c.line(
                previous[0],
                previous[1],
                px,
                py
            )

        previous = (
            px,
            py
        )

        if i in [0, 4, 9, 14, 19, 24]:

            c.setFillColor(GREEN)

            c.circle(
                px,
                py,
                2.5,
                fill=1,
                stroke=0
            )

            c.setFillColor(GREY)

            c.setFont(
                "Helvetica",
                6
            )

            c.drawCentredString(
                px,
                bottom - 9,
                str(i+1)
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

    c.setFillColor(GREEN)

    c.rect(
        0,
        PAGE_H - 80*mm,
        PAGE_W,
        80*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        24
    )

    c.drawString(
        20*mm,
        PAGE_H - 28*mm,
        "ENERGIA GIUSTA"
    )

    c.setFont(
        "Helvetica",
        9
    )

    c.drawString(
        20*mm,
        PAGE_H - 37*mm,
        "Partner ENI Plenitude"
    )

    draw_sun(
        c,
        PAGE_W - 45*mm,
        PAGE_H - 30*mm,
        25
    )

    # titolo
    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        27
    )

    c.drawString(
        20*mm,
        PAGE_H - 111*mm,
        "Analisi Fotovoltaica"
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        10
    )

    c.drawString(
        20*mm,
        PAGE_H - 122*mm,
        "Analisi energetica ed economica del tuo impianto"
    )

    # illustrazione
    draw_house(
        c,
        PAGE_W - 92*mm,
        PAGE_H - 173*mm,
        58,
        42
    )

    draw_battery(
        c,
        PAGE_W - 38*mm,
        PAGE_H - 168*mm,
        25,
        44
    )

    # indirizzo
    draw_round_rect(
        c,
        20*mm,
        66*mm,
        PAGE_W - 40*mm,
        28*mm,
        WHITE,
        MID_GREY,
        7
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7
    )

    c.drawString(
        28*mm,
        83*mm,
        "ABITAZIONE ANALIZZATA"
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        11
    )

    draw_wrapped_text(
        c,
        address,
        28*mm,
        74*mm,
        PAGE_W - 56*mm,
        "Helvetica-Bold",
        11,
        13,
        DARK,
        2
    )

    # KPI
    draw_kpi(
        c,
        20*mm,
        32*mm,
        50*mm,
        24*mm,
        "S",
        "Potenza FV",
        f"{decimal(kwp)} kWp",
        GREEN
    )

    draw_kpi(
        c,
        77*mm,
        32*mm,
        50*mm,
        24*mm,
        "B",
        "Accumulo",
        f"{decimal(battery)} kWh",
        YELLOW
    )

    draw_kpi(
        c,
        134*mm,
        32*mm,
        50*mm,
        24*mm,
        "€",
        "Risparmio annuo",
        euro(values["annual_saving"]),
        GREEN
    )

    draw_footer(
        c,
        1
    )

    c.showPage()

    # =====================================================
    # PAGINA 2
    # =====================================================

    draw_header(
        c,
        "Il tuo impianto",
        "Dati tecnici e produzione"
    )

    draw_section_title(
        c,
        "Configurazione del sistema",
        "I principali parametri utilizzati nella simulazione.",
        PAGE_H - 38*mm
    )

    cards = [
        ("Potenza fotovoltaica", f"{decimal(kwp)} kWp"),
        ("Batteria di accumulo", f"{decimal(battery)} kWh"),
        ("Consumo annuo", f"{number(consumption)} kWh"),
        ("Produzione stimata", f"{number(production)} kWh"),
        ("Inclinazione", f"{decimal(angle)}°"),
        ("Perdite sistema", f"{decimal(loss)}%")
    ]

    card_w = 55*mm
    card_h = 23*mm

    for i, (label, value) in enumerate(cards):

        col = i % 3
        row = i // 3

        x = 18*mm + col*59*mm
        y = PAGE_H - 72*mm - row*29*mm

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
        c.setFont(
            "Helvetica",
            6.8
        )

        c.drawString(
            x + 5*mm,
            y + 14*mm,
            label
        )

        c.setFillColor(DARK)
        c.setFont(
            "Helvetica-Bold",
            12.5
        )

        c.drawString(
            x + 5*mm,
            y + 5*mm,
            value
        )

    draw_chart_monthly(
        c,
        18*mm,
        57*mm,
        PAGE_W - 36*mm,
        78*mm,
        monthly
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7
    )

    c.drawString(
        18*mm,
        48*mm,
        "Produzione stimata tramite PVGIS sulla base della localizzazione e dei parametri inseriti."
    )

    draw_footer(
        c,
        2
    )

    c.showPage()

    # =====================================================
    # PAGINA 3
    # =====================================================

    draw_header(
        c,
        "Come utilizzi la tua energia",
        "Autoconsumo e immissione"
    )

    draw_section_title(
        c,
        "Dove finisce l'energia prodotta?",
        "La simulazione distingue l'energia utilizzata direttamente dall'abitazione da quella immessa in rete.",
        PAGE_H - 38*mm
    )

    draw_donut(
        c,
        68*mm,
        PAGE_H - 100*mm,
        33*mm,
        values["self_used"],
        values["export"]
    )

    # legenda
    c.setFillColor(GREEN)

    c.rect(
        120*mm,
        PAGE_H - 88*mm,
        7*mm,
        7*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawString(
        132*mm,
        PAGE_H - 86*mm,
        "Autoconsumo"
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        8
    )

    c.drawString(
        132*mm,
        PAGE_H - 95*mm,
        f"{number(values['self_used'])} kWh/anno"
    )

    c.setFillColor(YELLOW)

    c.rect(
        120*mm,
        PAGE_H - 108*mm,
        7*mm,
        7*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawString(
        132*mm,
        PAGE_H - 106*mm,
        "Energia immessa"
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        8
    )

    c.drawString(
        132*mm,
        PAGE_H - 115*mm,
        f"{number(values['export'])} kWh/anno"
    )

    # KPI
    draw_kpi(
        c,
        18*mm,
        91*mm,
        53*mm,
        25*mm,
        "A",
        "Autoconsumo",
        f"{values['self_used']/max(production,1)*100:.0f}%",
        GREEN
    )

    draw_kpi(
        c,
        78*mm,
        91*mm,
        53*mm,
        25*mm,
        "I",
        "Energia immessa",
        f"{values['export']/max(production,1)*100:.0f}%",
        YELLOW
    )

    draw_kpi(
        c,
        138*mm,
        91*mm,
        53*mm,
        25*mm,
        "€",
        "Risparmio annuo",
        euro(values["annual_saving"]),
        GREEN
    )

    # box
    draw_round_rect(
        c,
        18*mm,
        45*mm,
        PAGE_W - 36*mm,
        35*mm,
        LIGHT_GREEN,
        None,
        7
    )

    c.setFillColor(DARK_GREEN)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        27*mm,
        70*mm,
        "Cosa significa?"
    )

    draw_wrapped_text(
        c,
        "Una parte dell'energia prodotta viene utilizzata direttamente "
        "dall'abitazione. La parte non utilizzata viene immessa in rete "
        "e valorizzata secondo il valore inserito nella simulazione.",
        27*mm,
        62*mm,
        PAGE_W - 54*mm,
        "Helvetica",
        8,
        10,
        DARK
    )

    draw_footer(
        c,
        3
    )

    c.showPage()

    # =====================================================
    # PAGINA 4
    # =====================================================

    draw_header(
        c,
        "Il ritorno dell'investimento",
        "Analisi economica"
    )

    draw_section_title(
        c,
        "I numeri principali",
        "Una sintesi immediata del risultato economico della simulazione.",
        PAGE_H - 38*mm
    )

    payback = (
        f"{values['payback']:.1f} anni"
        if values["payback"] is not None
        else "Oltre 25 anni"
    )

    draw_kpi(
        c,
        18*mm,
        PAGE_H - 85*mm,
        55*mm,
        29*mm,
        "€",
        "Investimento netto",
        euro(values["net_cost"]),
        GREEN
    )

    draw_kpi(
        c,
        77*mm,
        PAGE_H - 85*mm,
        55*mm,
        29*mm,
        "T",
        "Rientro stimato",
        payback,
        YELLOW
    )

    draw_kpi(
        c,
        136*mm,
        PAGE_H - 85*mm,
        55*mm,
        29*mm,
        "€",
        "Beneficio 25 anni",
        euro(values["gross_25"]),
        GREEN
    )

    # investimento
    draw_round_rect(
        c,
        18*mm,
        97*mm,
        PAGE_W - 36*mm,
        34*mm,
        WHITE,
        MID_GREY,
        7
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        27*mm,
        120*mm,
        "Composizione dell'investimento"
    )

    c.setFont(
        "Helvetica",
        8
    )

    c.setFillColor(GREY)

    c.drawString(
        27*mm,
        109*mm,
        "Costo complessivo"
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawRightString(
        102*mm,
        109*mm,
        euro(cost)
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        8
    )

    c.drawString(
        115*mm,
        109*mm,
        "Detrazione totale"
    )

    c.setFillColor(GREEN)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawRightString(
        185*mm,
        109*mm,
        euro(deduction)
    )

    # barra detrazione
    c.setFillColor(LIGHT_GREEN)

    c.roundRect(
        27*mm,
        101*mm,
        158*mm,
        4*mm,
        2,
        fill=1,
        stroke=0
    )

    if cost > 0:

        deduction_width = min(
            158*mm,
            158*mm*deduction/cost
        )

        c.setFillColor(GREEN)

        c.roundRect(
            27*mm,
            101*mm,
            deduction_width,
            4*mm,
            2,
            fill=1,
            stroke=0
        )

    draw_economic_chart(
        c,
        18*mm,
        25*mm,
        PAGE_W - 36*mm,
        63*mm,
        values["years"]
    )

    draw_footer(
        c,
        4
    )

    c.showPage()

    # =====================================================
    # PAGINA 5
    # =====================================================

    draw_header(
        c,
        "Proiezione economica",
        "Orizzonte 25 anni"
    )

    draw_section_title(
        c,
        "Come evolve il beneficio nel tempo",
        "La simulazione considera un degrado della produzione dello 0,5% annuo e una crescita del prezzo dell'energia del 2% annuo.",
        PAGE_H - 38*mm
    )

    # tabella
    x = 18*mm
    y = PAGE_H - 78*mm
    table_w = PAGE_W - 36*mm
    row_h = 10*mm

    col1 = 35*mm
    col2 = 58*mm
    col3 = table_w - col1 - col2

    c.setFillColor(GREEN)

    c.roundRect(
        x,
        y,
        table_w,
        row_h,
        3,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        8
    )

    c.drawString(
        x + 5*mm,
        y + 3.5*mm,
        "Periodo"
    )

    c.drawString(
        x + col1 + 5*mm,
        y + 3.5*mm,
        "Beneficio annuo"
    )

    c.drawString(
        x + col1 + col2 + 5*mm,
        y + 3.5*mm,
        "Beneficio cumulato"
    )

    for i, year_number in enumerate(
        [1, 5, 10, 15, 20, 25]
    ):

        item = values["years"][year_number-1]

        row_y = y - (i+1)*row_h

        c.setFillColor(
            LIGHT_GREY
            if i % 2 == 0
            else WHITE
        )

        c.rect(
            x,
            row_y,
            table_w,
            row_h,
            fill=1,
            stroke=0
        )

        c.setFillColor(DARK)

        c.setFont(
            "Helvetica-Bold",
            8
        )

        c.drawString(
            x + 5*mm,
            row_y + 3.5*mm,
            f"{year_number}° anno"
        )

        c.drawString(
            x + col1 + 5*mm,
            row_y + 3.5*mm,
            euro(item["benefit"])
        )

        c.setFillColor(GREEN)

        c.drawString(
            x + col1 + col2 + 5*mm,
            row_y + 3.5*mm,
            euro(item["cumulative"])
        )

    # box 25 anni
    draw_round_rect(
        c,
        18*mm,
        70*mm,
        PAGE_W - 36*mm,
        34*mm,
        LIGHT_GREEN,
        None,
        7
    )

    c.setFillColor(DARK_GREEN)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        27*mm,
        91*mm,
        "Beneficio cumulato stimato a 25 anni"
    )

    c.setFillColor(GREEN)

    c.setFont(
        "Helvetica-Bold",
        20
    )

    c.drawString(
        27*mm,
        78*mm,
        euro(values["gross_25"])
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica",
        8
    )

    c.drawRightString(
        PAGE_W - 27*mm,
        81*mm,
        "Beneficio netto: " + euro(values["net_25"])
    )

    # assunzioni
    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawString(
        18*mm,
        59*mm,
        "Assunzioni della simulazione"
    )

    assumptions = [
        "Degrado produzione: 0,5% annuo",
        "Crescita prezzo energia acquistata: 2% annuo",
        f"Prezzo energia acquistata: {decimal(energy_price)} €/kWh",
        f"Valore energia immessa: {decimal(export_price)} €/kWh",
        f"Profilo consumi: {profile_label}"
    ]

    yy = 52*mm

    for text in assumptions:

        c.setFillColor(GREY)

        c.setFont(
            "Helvetica",
            7
        )

        c.drawString(
            18*mm,
            yy,
            "• " + text
        )

        yy -= 5*mm

    draw_footer(
        c,
        5
    )

    c.showPage()

    # =====================================================
    # PAGINA 6
    # =====================================================

    draw_header(
        c,
        "Cosa comprende la soluzione",
        "Servizi e garanzie"
    )

    draw_section_title(
        c,
        "Un progetto completo",
        "Dalla progettazione al monitoraggio dell'impianto.",
        PAGE_H - 38*mm
    )

    services = [
        (
            "FV",
            "Impianto fotovoltaico",
            "Pannelli e componentistica dimensionati sul progetto."
        ),
        (
            "BA",
            "Accumulo energetico",
            "Batterie per aumentare l'utilizzo dell'energia prodotta."
        ),
        (
            "PR",
            "Progettazione",
            "Analisi tecnica e dimensionamento dell'impianto."
        ),
        (
            "IN",
            "Installazione",
            "Installazione e messa in servizio dell'impianto."
        ),
        (
            "GS",
            "Pratiche GSE",
            "Gestione delle pratiche necessarie."
        ),
        (
            "AP",
            "Monitoraggio",
            "Controllo di produzione e consumi."
        )
    ]

    card_w = 82*mm
    card_h = 34*mm

    for i, (icon, title, text) in enumerate(services):

        col = i % 2
        row = i // 2

        x = 18*mm + col*88*mm
        y = PAGE_H - 72*mm - row*40*mm

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

        c.setFillColor(GREEN)

        c.circle(
            x + 12*mm,
            y + 24*mm,
            7*mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(WHITE)

        c.setFont(
            "Helvetica-Bold",
            7
        )

        c.drawCentredString(
            x + 12*mm,
            y + 22*mm,
            icon
        )

        c.setFillColor(DARK)

        c.setFont(
            "Helvetica-Bold",
            8.5
        )

        c.drawString(
            x + 23*mm,
            y + 26*mm,
            title
        )

        draw_wrapped_text(
            c,
            text,
            x + 23*mm,
            y + 16*mm,
            card_w - 29*mm,
            "Helvetica",
            6.8,
            9,
            GREY,
            3
        )

    # garanzie
    draw_round_rect(
        c,
        18*mm,
        35*mm,
        PAGE_W - 36*mm,
        30*mm,
        LIGHT_YELLOW,
        None,
        7
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawString(
        27*mm,
        56*mm,
        "Garanzie indicative della soluzione"
    )

    guarantees = [
        "Pannelli: prodotto fino a 25 anni",
        "Pannelli: prestazione fino a 30 anni",
        "Inverter: garanzia fino a 12 anni",
        "Batterie: garanzia fino a 11 anni"
    ]

    for i, text in enumerate(guarantees):

        col = i % 2
        row = i // 2

        c.setFillColor(DARK)

        c.setFont(
            "Helvetica",
            7
        )

        c.drawString(
            27*mm + col*78*mm,
            47*mm - row*9*mm,
            "✓ " + text
        )

    draw_footer(
        c,
        6
    )

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
        PAGE_W + 15*mm,
        PAGE_H - 15*mm,
        65*mm,
        fill=1,
        stroke=0
    )

    draw_sun(
        c,
        PAGE_W - 45*mm,
        PAGE_H - 40*mm,
        22
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        25
    )

    c.drawString(
        20*mm,
        PAGE_H - 48*mm,
        "La tua energia."
    )

    c.drawString(
        20*mm,
        PAGE_H - 62*mm,
        "Il tuo risparmio."
    )

    c.setFont(
        "Helvetica",
        9
    )

    c.drawString(
        20*mm,
        PAGE_H - 77*mm,
        "Per informazioni e un'analisi personalizzata."
    )

    # scheda contatto
    draw_round_rect(
        c,
        20*mm,
        72*mm,
        PAGE_W - 40*mm,
        64*mm,
        WHITE,
        None,
        9
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        17
    )

    c.drawString(
        30*mm,
        119*mm,
        "Simone Alfarano"
    )

    c.setFillColor(GREEN)

    c.setFont(
        "Helvetica-Bold",
        8.5
    )

    c.drawString(
        30*mm,
        109*mm,
        "RESPONSABILE COMMERCIALE"
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        8
    )

    lines = [
        "Energia Giusta",
        "Partner ENI Plenitude",
        "Tel. / WhatsApp: 351.7478652",
        "Email: simone.alfarano@energiagiusta.it",
        "Sito: www.energiagiusta.it"
    ]

    yy = 98*mm

    for line in lines:

        c.drawString(
            30*mm,
            yy,
            line
        )

        yy -= 7*mm

    # CTA
    c.setFillColor(YELLOW)

    c.roundRect(
        30*mm,
        48*mm,
        65*mm,
        13*mm,
        6,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9
    )

    c.drawCentredString(
        62.5*mm,
        52.5*mm,
        "CONTATTAMI PER INFO"
    )

    # disclaimer
    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica",
        6.5
    )

    draw_wrapped_text(
        c,
        "Stima commerciale. Il risultato dipende da tariffe, profilo reale "
        "dei consumi, condizioni di scambio/ritiro, ombreggiamento e altri fattori.",
        20*mm,
        20*mm,
        115*mm,
        "Helvetica",
        6.5,
        8,
        WHITE
    )

    c.drawRightString(
        PAGE_W - 20*mm,
        13*mm,
        "Non è un preventivo finanziario."
    )

    c.showPage()

    c.save()

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="Analisi_Fotovoltaica_Energia_Giusta.pdf"
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
