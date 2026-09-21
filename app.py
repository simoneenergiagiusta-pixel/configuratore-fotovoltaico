from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether
)
from reportlab.graphics.shapes import Drawing, String, Rect, Line
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend


app = Flask(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"


# =========================================================
# PVGIS
# =========================================================

def pvgis(params):
    url = PVGIS_URL + "?" + urlencode(params)

    req = Request(
        url,
        headers={
            "User-Agent": "ConfiguratoreFotovoltaico/1.0"
        }
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
# CALCOLO ECONOMICO
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

    battery_boost = min(
        0.25,
        battery / 30.0
    )

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

    payback = None

    degradation = 0.005
    energy_price_growth = 0.02

    years = []

    cumulative_benefit = 0

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

    return {
        "self_used": self_used,
        "export": export,
        "grid_purchase": grid,
        "annual_saving": annual_saving,
        "net_cost": net_cost,
        "payback": payback,
        "years": years
    }


@app.post("/api/calculate")
def calculate():

    d = request.get_json(force=True)

    result = calculate_values(d)

    return jsonify(result)


# =========================================================
# PDF - STILI
# =========================================================

GREEN = colors.HexColor("#138A4B")
DARK_GREEN = colors.HexColor("#075C32")
YELLOW = colors.HexColor("#F5C400")
LIGHT_GREEN = colors.HexColor("#EAF6EF")
LIGHT_YELLOW = colors.HexColor("#FFF8D9")
LIGHT_GREY = colors.HexColor("#F4F5F5")
GREY = colors.HexColor("#666666")
DARK = colors.HexColor("#222222")


def euro(value):

    try:
        value = float(value)
    except:
        value = 0

    return "€ {:,.0f}".format(value).replace(",", ".")


def number(value):

    try:
        value = float(value)
    except:
        value = 0

    return "{:,.0f}".format(value).replace(",", ".")


def profile_text(profile):

    return {
        "giorno": "Prevalentemente giorno",
        "equilibrato": "Equilibrato",
        "sera": "Prevalentemente sera/notte"
    }.get(profile, profile)


def aspect_text(aspect):

    try:
        aspect = float(aspect)
    except:
        return str(aspect)

    if aspect == -90:
        return "Est (-90°)"

    if aspect == 90:
        return "Ovest (90°)"

    return "Sud (0°)"


# =========================================================
# HEADER / FOOTER PDF
# =========================================================

def draw_page(canvas, doc):

    canvas.saveState()

    width, height = A4

    # Barra superiore
    canvas.setFillColor(DARK_GREEN)
    canvas.rect(
        0,
        height - 9 * mm,
        width,
        9 * mm,
        fill=1,
        stroke=0
    )

    # Nome brand
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(
        18 * mm,
        height - 6 * mm,
        "ENERGIAGIUSTA"
    )

    # Footer
    canvas.setStrokeColor(colors.HexColor("#DDDDDD"))
    canvas.line(
        18 * mm,
        14 * mm,
        width - 18 * mm,
        14 * mm
    )

    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7)

    canvas.drawString(
        18 * mm,
        9 * mm,
        "Configuratore Fotovoltaico - Stima commerciale"
    )

    canvas.drawRightString(
        width - 18 * mm,
        9 * mm,
        "Pagina {}".format(doc.page)
    )

    canvas.restoreState()


# =========================================================
# TITOLI
# =========================================================

def title_style():

    return ParagraphStyle(
        "MainTitle",
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=DARK_GREEN,
        alignment=TA_LEFT,
        spaceAfter=8 * mm
    )


def subtitle_style():

    return ParagraphStyle(
        "Subtitle",
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        textColor=GREY,
        spaceAfter=8 * mm
    )


def section_style():

    return ParagraphStyle(
        "Section",
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=DARK_GREEN,
        spaceBefore=4 * mm,
        spaceAfter=4 * mm
    )


def normal_style():

    return ParagraphStyle(
        "Normal",
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=DARK
    )


def small_style():

    return ParagraphStyle(
        "Small",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=GREY
    )


# =========================================================
# RIQUADRO KPI
# =========================================================

def kpi_box(label, value):

    data = [
        [
            Paragraph(
                label,
                ParagraphStyle(
                    "KpiLabel",
                    fontName="Helvetica",
                    fontSize=8,
                    textColor=GREY,
                    alignment=TA_CENTER
                )
            )
        ],
        [
            Paragraph(
                value,
                ParagraphStyle(
                    "KpiValue",
                    fontName="Helvetica-Bold",
                    fontSize=15,
                    textColor=DARK_GREEN,
                    alignment=TA_CENTER
                )
            )
        ]
    ]

    table = Table(
        data,
        colWidths=[48 * mm],
        rowHeights=[9 * mm, 13 * mm]
    )

    table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREEN
            ),
            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.7,
                colors.HexColor("#B9DCC8")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                4
            )
        ])
    )

    return table


# =========================================================
# GRAFICO PRODUZIONE MENSILE
# =========================================================

def monthly_chart(monthly):

    drawing = Drawing(
        170 * mm,
        72 * mm
    )

    chart = VerticalBarChart()

    chart.x = 25
    chart.y = 20
    chart.height = 45 * mm
    chart.width = 145 * mm

    values = [
        float(x)
        for x in monthly[:12]
    ]

    while len(values) < 12:
        values.append(0)

    chart.data = [values]

    chart.categoryAxis.categoryNames = [
        "Gen",
        "Feb",
        "Mar",
        "Apr",
        "Mag",
        "Giu",
        "Lug",
        "Ago",
        "Set",
        "Ott",
        "Nov",
        "Dic"
    ]

    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 6

    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 6

    chart.valueAxis.valueMin = 0

    maximum = max(values) if values else 1000

    chart.valueAxis.valueMax = maximum * 1.15

    chart.valueAxis.valueStep = max(
        100,
        round((maximum / 5) / 100) * 100
    )

    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = GREEN

    drawing.add(chart)

    return drawing


# =========================================================
# GRAFICO PROIEZIONE ECONOMICA
# =========================================================

def economic_chart(years):

    drawing = Drawing(
        170 * mm,
        72 * mm
    )

    if not years:
        return drawing

    points = []

    selected = [
        y for y in years
        if y["year"] in [1, 5, 10, 15, 20, 25]
    ]

    for item in selected:

        points.append(
            (
                float(item["year"]),
                float(item["cumulative"])
            )
        )

    if not points:
        return drawing

    plot = LinePlot()

    plot.x = 25
    plot.y = 20
    plot.width = 145 * mm
    plot.height = 45 * mm

    plot.data = [points]

    plot.xValueAxis.valueMin = 0
    plot.xValueAxis.valueMax = 25
    plot.xValueAxis.valueStep = 5

    max_value = max(
        p[1] for p in points
    )

    plot.yValueAxis.valueMin = 0
    plot.yValueAxis.valueMax = max_value * 1.15

    plot.xValueAxis.labels.fontName = "Helvetica"
    plot.xValueAxis.labels.fontSize = 7

    plot.yValueAxis.labels.fontName = "Helvetica"
    plot.yValueAxis.labels.fontSize = 7

    plot.lines[0].strokeColor = GREEN
    plot.lines[0].strokeWidth = 2

    plot.lines[0].symbol = None

    drawing.add(plot)

    return drawing


# =========================================================
# PDF
# =========================================================

@app.post("/api/pdf")
def create_pdf():

    d = request.get_json(force=True)

    production = float(
        d.get("production", 0)
    )

    monthly = d.get(
        "monthly_production",
        []
    )

    calculation = calculate_values({
        "production": production,
        "consumption": d.get("consumption", 0),
        "battery": d.get("battery", 0),
        "energy_price": d.get("energy_price", 0.25),
        "export_price": d.get("export_price", 0.10),
        "cost": d.get("cost", 0),
        "deduction": d.get("deduction", 0),
        "profile": d.get("profile", "equilibrato")
    })

    self_used = calculation["self_used"]
    export = calculation["export"]
    grid = calculation["grid_purchase"]
    annual_saving = calculation["annual_saving"]
    net_cost = calculation["net_cost"]
    payback = calculation["payback"]
    years = calculation["years"]

    gross_25 = (
        years[-1]["cumulative"]
        if years
        else 0
    )

    net_25 = max(
        0,
        gross_25 - net_cost
    )

    address = d.get(
        "address",
        ""
    )

    lat = d.get(
        "lat",
        ""
    )

    lon = d.get(
        "lon",
        ""
    )

    kwp = d.get(
        "kwp",
        ""
    )

    battery = d.get(
        "battery",
        ""
    )

    angle = d.get(
        "angle",
        ""
    )

    aspect = d.get(
        "aspect",
        0
    )

    loss = d.get(
        "loss",
        ""
    )

    consumption = d.get(
        "consumption",
        ""
    )

    profile = d.get(
        "profile",
        "equilibrato"
    )

    cost = d.get(
        "cost",
        0
    )

    deduction = d.get(
        "deduction",
        0
    )

    energy_price = d.get(
        "energy_price",
        0.25
    )

    export_price = d.get(
        "export_price",
        0.10
    )

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="Analisi Fotovoltaica",
        author="Energiagiusta - Simone Alfarano"
    )

    styles = getSampleStyleSheet()

    story = []

    # =====================================================
    # PAGINA 1
    # =====================================================

    story.append(
        Spacer(1, 12 * mm)
    )

    story.append(
        Paragraph(
            "Analisi Fotovoltaica",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Analisi personalizzata dell'impianto e stima del ritorno dell'investimento",
            subtitle_style()
        )
    )

    story.append(
        Table(
            [
                [
                    Paragraph(
                        "<b>ENERGIAGIUSTA</b>",
                        ParagraphStyle(
                            "Brand",
                            fontName="Helvetica-Bold",
                            fontSize=20,
                            textColor=GREEN
                        )
                    )
                ],
                [
                    Paragraph(
                        "Partner Eni Plenitude",
                        normal_style()
                    )
                ]
            ],
            colWidths=[170 * mm],
            style=[
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    LIGHT_GREEN
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.8,
                    colors.HexColor("#B9DCC8")
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8 * mm
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    5 * mm
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    5 * mm
                )
            ]
        )
    )

    story.append(
        Spacer(1, 8 * mm)
    )

    story.append(
        Paragraph(
            "DATI DEL CLIENTE E DELL'IMPIANTO",
            section_style()
        )
    )

    data = [
        ["Indirizzo", str(address)],
        ["Latitudine", str(lat)],
        ["Longitudine", str(lon)],
        ["Potenza fotovoltaica", "{} kWp".format(kwp)],
        ["Batteria di accumulo", "{} kWh".format(battery)],
        ["Inclinazione", "{}°".format(angle)],
        ["Orientamento", aspect_text(aspect)],
        ["Perdite di sistema", "{} %".format(loss)],
        ["Consumo annuo", "{} kWh".format(number(consumption))],
        ["Profilo consumi", profile_text(profile)],
    ]

    table = Table(
        data,
        colWidths=[
            62 * mm,
            108 * mm
        ]
    )

    table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                LIGHT_GREY
            ),
            (
                "TEXTCOLOR",
                (0, 0),
                (0, -1),
                DARK_GREEN
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "FONTNAME",
                (1, 0),
                (1, -1),
                "Helvetica"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                colors.HexColor("#DDDDDD")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                5
            )
        ])
    )

    story.append(table)

    story.append(
        Spacer(1, 10 * mm)
    )

    story.append(
        Paragraph(
            "Report generato il {}".format(
                datetime.now().strftime("%d/%m/%Y")
            ),
            small_style()
        )
    )

    # =====================================================
    # PAGINA 2
    # =====================================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "Produzione e Risparmio",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Stima della produzione fotovoltaica e dei benefici energetici annuali.",
            subtitle_style()
        )
    )

    story.append(
        monthly_chart(monthly)
    )

    story.append(
        Spacer(1, 5 * mm)
    )

    kpis = Table(
        [
            [
                kpi_box(
                    "Produzione annua",
                    "{} kWh".format(
                        number(production)
                    )
                ),
                kpi_box(
                    "Autoconsumo stimato",
                    "{} kWh".format(
                        number(self_used)
                    )
                ),
                kpi_box(
                    "Energia immessa",
                    "{} kWh".format(
                        number(export)
                    )
                )
            ]
        ],
        colWidths=[
            54 * mm,
            54 * mm,
            54 * mm
        ]
    )

    kpis.setStyle(
        TableStyle([
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            )
        ])
    )

    story.append(kpis)

    story.append(
        Spacer(1, 7 * mm)
    )

    story.append(
        Table(
            [
                [
                    "Energia acquistata dalla rete",
                    "{} kWh".format(number(grid))
                ],
                [
                    "Risparmio economico annuo",
                    euro(annual_saving)
                ],
                [
                    "Prezzo energia acquistata",
                    "€ {:.2f}/kWh".format(
                        float(energy_price)
                    )
                ],
                [
                    "Valore energia immessa",
                    "€ {:.2f}/kWh".format(
                        float(export_price)
                    )
                ]
            ],
            colWidths=[
                110 * mm,
                60 * mm
            ],
            style=[
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    LIGHT_GREY
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (0, -1),
                    "Helvetica-Bold"
                ),
                (
                    "ALIGN",
                    (1, 0),
                    (1, -1),
                    "RIGHT"
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor("#DDDDDD")
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                )
            ]
        )
    )

    # =====================================================
    # PAGINA 3
    # =====================================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "Analisi Economica",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Stima economica basata sui dati inseriti e sulla simulazione effettuata.",
            subtitle_style()
        )
    )

    economic_kpis = Table(
        [
            [
                kpi_box(
                    "Costo impianto",
                    euro(cost)
                ),
                kpi_box(
                    "Detrazione totale",
                    euro(deduction)
                ),
                kpi_box(
                    "Investimento netto",
                    euro(net_cost)
                )
            ],
            [
                kpi_box(
                    "Risparmio primo anno",
                    euro(annual_saving)
                ),
                kpi_box(
                    "Rientro stimato",
                    "{} anni".format(
                        payback
                        if payback is not None
                        else "n.d."
                    )
                ),
                kpi_box(
                    "Beneficio netto a 25 anni",
                    euro(net_25)
                )
            ]
        ],
        colWidths=[
            54 * mm,
            54 * mm,
            54 * mm
        ]
    )

    economic_kpis.setStyle(
        TableStyle([
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                5 * mm
            )
        ])
    )

    story.append(economic_kpis)

    story.append(
        Spacer(1, 7 * mm)
    )

    story.append(
        Paragraph(
            "Il beneficio netto a 25 anni è calcolato sottraendo l'investimento netto al beneficio economico cumulato della simulazione.",
            normal_style()
        )
    )

    story.append(
        Spacer(1, 5 * mm)
    )

    story.append(
        Table(
            [
                [
                    "Beneficio cumulato lordo a 25 anni",
                    euro(gross_25)
                ],
                [
                    "Investimento netto",
                    euro(net_cost)
                ],
                [
                    "Beneficio netto stimato a 25 anni",
                    euro(net_25)
                ]
            ],
            colWidths=[
                110 * mm,
                60 * mm
            ],
            style=[
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    LIGHT_GREEN
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (0, -1),
                    "Helvetica-Bold"
                ),
                (
                    "FONTNAME",
                    (1, 0),
                    (1, -1),
                    "Helvetica-Bold"
                ),
                (
                    "ALIGN",
                    (1, 0),
                    (1, -1),
                    "RIGHT"
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor("#C8DDD0")
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                )
            ]
        )
    )

    # =====================================================
    # PAGINA 4
    # =====================================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "Proiezione Economica a 25 Anni",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Simulazione del beneficio economico nel tempo.",
            subtitle_style()
        )
    )

    story.append(
        economic_chart(years)
    )

    story.append(
        Spacer(1, 5 * mm)
    )

    rows = [
        [
            "Periodo",
            "Beneficio dell'anno",
            "Beneficio cumulato"
        ]
    ]

    for year_number in [
        1,
        5,
        10,
        15,
        20,
        25
    ]:

        item = next(
            (
                x for x in years
                if x["year"] == year_number
            ),
            None
        )

        if item:

            rows.append([
                "{}° anno".format(year_number),
                euro(item["benefit"]),
                euro(item["cumulative"])
            ])

    projection_table = Table(
        rows,
        colWidths=[
            50 * mm,
            60 * mm,
            60 * mm
        ]
    )

    projection_table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                DARK_GREEN
            ),
            (
                "TEXTCOLOR",
                (0, 0),
                (-1, 0),
                colors.white
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold"
            ),
            (
                "FONTNAME",
                (0, 1),
                (-1, -1),
                "Helvetica"
            ),
            (
                "ALIGN",
                (1, 1),
                (-1, -1),
                "RIGHT"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                colors.HexColor("#CCCCCC")
            ),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [
                    colors.white,
                    LIGHT_GREY
                ]
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            )
        ])
    )

    story.append(projection_table)

    story.append(
        Spacer(1, 7 * mm)
    )

    story.append(
        Paragraph(
            "<b>Assunzioni della simulazione</b>",
            section_style()
        )
    )

    story.append(
        Paragraph(
            "La simulazione considera un degrado della produzione dello 0,5% annuo e una crescita ipotizzata del prezzo dell'energia acquistata del 2% annuo. Il valore dell'energia immessa è mantenuto separato.",
            normal_style()
        )
    )

    story.append(
        Spacer(1, 5 * mm)
    )

    story.append(
        Paragraph(
            "<b>Nota importante</b><br/>"
            "Stima commerciale: il risultato dipende da tariffe, profilo reale dei consumi, condizioni di scambio/ritiro, ombreggiamento e altri fattori. Non è un preventivo finanziario.",
            small_style()
        )
    )

    # =====================================================
    # PAGINA 5
    # =====================================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "Cosa Include l'Offerta",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Servizi, assistenza e garanzie previste per la soluzione fotovoltaica.",
            subtitle_style()
        )
    )

    story.append(
        Paragraph(
            "PRIMA DELL'INSTALLAZIONE",
            section_style()
        )
    )

    pre_install = [
        [
            "Sopralluogo tecnico",
            "Verifica preliminare delle caratteristiche dell'immobile e dell'installazione."
        ],
        [
            "Progettazione",
            "Progettazione della soluzione fotovoltaica in base alle caratteristiche dell'impianto."
        ],
        [
            "Pratiche GSE",
            "Gestione delle pratiche necessarie per l'impianto."
        ],
        [
            "Pratiche detrazione fiscale",
            "Gestione della documentazione prevista per la detrazione fiscale."
        ]
    ]

    table = Table(
        pre_install,
        colWidths=[
            55 * mm,
            115 * mm
        ]
    )

    table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                LIGHT_GREEN
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8.5
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                colors.HexColor("#D4E5DA")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            )
        ])
    )

    story.append(table)

    story.append(
        Paragraph(
            "INSTALLAZIONE E SERVIZI",
            section_style()
        )
    )

    services = [
        [
            "Installazione",
            "Installazione effettuata da installatori specializzati."
        ],
        [
            "Monitoraggio tramite App",
            "Possibilità di monitorare il funzionamento dell'impianto tramite App."
        ],
        [
            "Smaltimento a fine vita",
            "Smaltimento dei moduli fotovoltaici e dell'inverter a fine vita."
        ]
    ]

    table = Table(
        services,
        colWidths=[
            55 * mm,
            115 * mm
        ]
    )

    table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                LIGHT_YELLOW
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8.5
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                colors.HexColor("#E8DCA5")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            )
        ])
    )

    story.append(table)

    story.append(
        Paragraph(
            "GARANZIE",
            section_style()
        )
    )

    warranties = [
        [
            "Pannelli - garanzia di fabbricazione",
            "25 anni"
        ],
        [
            "Pannelli - garanzia sulla potenza",
            "30 anni"
        ],
        [
            "Inverter",
            "12 anni"
        ],
        [
            "Batterie",
            "11 anni"
        ]
    ]

    table = Table(
        warranties,
        colWidths=[
            120 * mm,
            50 * mm
        ]
    )

    table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREEN
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "FONTNAME",
                (1, 0),
                (1, -1),
                "Helvetica-Bold"
            ),
            (
                "TEXTCOLOR",
                (1, 0),
                (1, -1),
                DARK_GREEN
            ),
            (
                "ALIGN",
                (1, 0),
                (1, -1),
                "CENTER"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                10
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#B9DCC8")
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                7
            )
        ])
    )

    story.append(table)

    # =====================================================
    # PAGINA 6
    # =====================================================

    story.append(PageBreak())

    story.append(
        Spacer(1, 25 * mm)
    )

    story.append(
        Paragraph(
            "Contatti",
            ParagraphStyle(
                "ContactTitle",
                fontName="Helvetica-Bold",
                fontSize=28,
                leading=32,
                textColor=DARK_GREEN,
                alignment=TA_CENTER,
                spaceAfter=8 * mm
            )
        )
    )

    story.append(
        Paragraph(
            "Per informazioni, approfondimenti o per trovare insieme la soluzione fotovoltaica più adatta alle tue esigenze.",
            ParagraphStyle(
                "ContactIntro",
                fontName="Helvetica",
                fontSize=11,
                leading=16,
                textColor=GREY,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        Spacer(1, 15 * mm)
    )

    contact_data = [
        [
            Paragraph(
                "Simone Alfarano",
                ParagraphStyle(
                    "ContactName",
                    fontName="Helvetica-Bold",
                    fontSize=18,
                    textColor=DARK_GREEN,
                    alignment=TA_CENTER
                )
            )
        ],
        [
            Paragraph(
                "Responsabile Commerciale",
                ParagraphStyle(
                    "ContactRole",
                    fontName="Helvetica-Bold",
                    fontSize=11,
                    textColor=DARK,
                    alignment=TA_CENTER
                )
            )
        ],
        [
            Paragraph(
                "Energiagiusta - Partner Eni Plenitude",
                ParagraphStyle(
                    "ContactCompany",
                    fontName="Helvetica",
                    fontSize=10,
                    textColor=GREY,
                    alignment=TA_CENTER
                )
            )
        ],
        [Spacer(1, 5 * mm)],
        [
            Paragraph(
                "<b>Telefono / WhatsApp</b><br/>351.7478652",
                ParagraphStyle(
                    "ContactItem",
                    fontName="Helvetica",
                    fontSize=11,
                    leading=16,
                    textColor=DARK,
                    alignment=TA_CENTER
                )
            )
        ],
        [
            Paragraph(
                "<b>Email</b><br/>simone.alfarano@energiagiusta.it",
                ParagraphStyle(
                    "ContactItem2",
                    fontName="Helvetica",
                    fontSize=11,
                    leading=16,
                    textColor=DARK,
                    alignment=TA_CENTER
                )
            )
        ],
        [
            Paragraph(
                "<b>Sito internet</b><br/>www.energiagiusta.it",
                ParagraphStyle(
                    "ContactItem3",
                    fontName="Helvetica",
                    fontSize=11,
                    leading=16,
                    textColor=DARK,
                    alignment=TA_CENTER
                )
            )
        ]
    ]

    contact_table = Table(
        contact_data,
        colWidths=[150 * mm]
    )

    contact_table.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREEN
            ),
            (
                "BOX",
                (0, 0),
                (-1, -1),
                1,
                colors.HexColor("#B9DCC8")
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                10 * mm
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                10 * mm
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                7
            )
        ])
    )

    story.append(contact_table)

    story.append(
        Spacer(1, 15 * mm)
    )

    story.append(
        Paragraph(
            "Grazie per aver scelto di valutare una soluzione energetica più efficiente.",
            ParagraphStyle(
                "ContactEnd",
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=16,
                textColor=DARK_GREEN,
                alignment=TA_CENTER
            )
        )
    )

    # =====================================================
    # GENERAZIONE
    # =====================================================

    doc.build(
        story,
        onFirstPage=draw_page,
        onLaterPages=draw_page
    )

    buffer.seek(0)

    filename = "Analisi_Fotovoltaica.pdf"

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
