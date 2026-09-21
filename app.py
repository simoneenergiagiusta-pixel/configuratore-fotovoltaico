from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import io
import math
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart


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
        return json.loads(
            r.read().decode("utf-8")
        )


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

    monthly = [
        x["E_m"]
        for x in fixed
    ]

    return jsonify({
        "annual_kwh": yearly["E_y"],
        "monthly_kwh": monthly,
        "raw": data
    })


# =========================================================
# CALCOLO ECONOMICO
# =========================================================

def calculate_values(d):

    production = float(
        d["production"]
    )

    consumption = float(
        d["consumption"]
    )

    battery = float(
        d.get("battery", 0)
    )

    price = float(
        d.get("energy_price", 0.25)
    )

    export_price = float(
        d.get("export_price", 0.10)
    )

    cost = float(
        d["cost"]
    )

    deduction = float(
        d.get("deduction", 0)
    )

    profile = d.get(
        "profile",
        "equilibrato"
    )

    # -----------------------------------------------------
    # MODELLO DI AUTOCONSUMO
    # -----------------------------------------------------

    direct_pct = {

        "giorno": 0.55,

        "equilibrato": 0.40,

        "sera": 0.28

    }.get(
        profile,
        0.40
    )

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
        +
        export * export_price
    )

    net_cost = max(
        0,
        cost - deduction
    )

    # -----------------------------------------------------
    # PROIEZIONE 25 ANNI
    # -----------------------------------------------------

    degradation = 0.005
    energy_price_growth = 0.02

    years = []
    cumulative_benefit = 0
    payback = None

    for y in range(1, 26):

        production_y = (
            production *
            ((1 - degradation) ** (y - 1))
        )

        self_used_y = min(
            production_y * self_consumption_pct,
            consumption
        )

        export_y = max(
            0,
            production_y - self_used_y
        )

        energy_price_y = (
            price *
            ((1 + energy_price_growth) ** (y - 1))
        )

        benefit = (
            self_used_y * energy_price_y
            +
            export_y * export_price
        )

        cumulative_benefit += benefit

        if (
            payback is None
            and cumulative_benefit >= net_cost
        ):
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

    gross_25 = (
        years[-1]["cumulative"]
        if years
        else 0
    )

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

    d = request.get_json(
        force=True
    )

    return jsonify(
        calculate_values(d)
    )


# =========================================================
# COLORI PDF
# =========================================================

GREEN = colors.HexColor("#15803D")
DARK_GREEN = colors.HexColor("#166534")
LIGHT_GREEN = colors.HexColor("#DCFCE7")

YELLOW = colors.HexColor("#FACC15")
LIGHT_YELLOW = colors.HexColor("#FEF9C3")

DARK = colors.HexColor("#1F2937")
GREY = colors.HexColor("#6B7280")
LIGHT_GREY = colors.HexColor("#F3F4F6")
MID_GREY = colors.HexColor("#D1D5DB")

WHITE = colors.white


# =========================================================
# FUNZIONI FORMATO
# =========================================================

def euro(value):

    return "€ {:,.0f}".format(
        value
    ).replace(",", ".")


def number(value):

    return "{:,.0f}".format(
        value
    ).replace(",", ".")


# =========================================================
# HEADER / FOOTER
# =========================================================

def draw_logo(canvas, doc):

    width, height = A4

    canvas.saveState()

    # Barra superiore
    canvas.setFillColor(GREEN)

    canvas.rect(
        0,
        height - 13 * mm,
        width,
        13 * mm,
        fill=1,
        stroke=0
    )

    # Nome azienda
    canvas.setFillColor(WHITE)

    canvas.setFont(
        "Helvetica-Bold",
        14
    )

    canvas.drawString(
        18 * mm,
        height - 8.7 * mm,
        "ENERGIA GIUSTA"
    )

    canvas.setFont(
        "Helvetica",
        7.5
    )

    canvas.drawRightString(
        width - 18 * mm,
        height - 8.7 * mm,
        "Partner ENI Plenitude"
    )

    # Footer
    canvas.setStrokeColor(MID_GREY)

    canvas.line(
        18 * mm,
        14 * mm,
        width - 18 * mm,
        14 * mm
    )

    canvas.setFillColor(GREY)

    canvas.setFont(
        "Helvetica",
        7
    )

    canvas.drawString(
        18 * mm,
        9 * mm,
        "Analisi fotovoltaica - Stima commerciale"
    )

    canvas.drawRightString(
        width - 18 * mm,
        9 * mm,
        f"Pagina {doc.page}"
    )

    canvas.restoreState()


# =========================================================
# STILI
# =========================================================

def title_style():

    return ParagraphStyle(
        "TitleCustom",
        fontName="Helvetica-Bold",
        fontSize=25,
        leading=29,
        textColor=DARK,
        spaceAfter=8 * mm
    )


def subtitle_style():

    return ParagraphStyle(
        "SubtitleCustom",
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        textColor=GREY,
        spaceAfter=8 * mm
    )


def section_style():

    return ParagraphStyle(
        "SectionCustom",
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=DARK_GREEN,
        spaceBefore=2 * mm,
        spaceAfter=6 * mm
    )


def normal_style():

    return ParagraphStyle(
        "NormalCustom",
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=DARK
    )


def small_style():

    return ParagraphStyle(
        "SmallCustom",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=GREY
    )


def centered_style():

    return ParagraphStyle(
        "CenteredCustom",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        alignment=TA_CENTER,
        textColor=DARK
    )


# =========================================================
# KPI
# =========================================================

def kpi_box(label, value):

    data = [

        [
            Paragraph(
                label,
                ParagraphStyle(
                    "KPILabel",
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
                    "KPIValue",
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
        colWidths=[43 * mm],
        rowHeights=[10 * mm, 15 * mm]
    )

    table.setStyle(
        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                colors.white
            ),

            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.8,
                MID_GREY
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
                4
            ),

            (
                "BOTTOMPADDING",
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

def make_monthly_chart(monthly):

    drawing = Drawing(
        175 * mm,
        82 * mm
    )

    chart = VerticalBarChart()

    chart.x = 10 * mm
    chart.y = 13 * mm

    chart.height = 55 * mm
    chart.width = 150 * mm

    chart.data = [
        monthly
    ]

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

    chart.valueAxis.valueMin = 0

    maximum = max(
        monthly
    ) if monthly else 1000

    chart.valueAxis.valueMax = (
        math.ceil(
            maximum / 500
        ) * 500
    )

    chart.valueAxis.valueStep = (
        chart.valueAxis.valueMax / 5
    )

    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = GREEN

    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7

    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7

    chart.categoryAxis.strokeColor = MID_GREY
    chart.valueAxis.strokeColor = MID_GREY

    drawing.add(chart)

    return drawing


# =========================================================
# GRAFICO ECONOMICO
# =========================================================

def make_economic_chart(years):

    drawing = Drawing(
        175 * mm,
        80 * mm
    )

    chart = VerticalBarChart()

    chart.x = 10 * mm
    chart.y = 13 * mm

    chart.height = 52 * mm
    chart.width = 150 * mm

    chart.data = [[
        item["cumulative"]
        for item in years
    ]]

    chart.categoryAxis.categoryNames = [
        str(item["year"])
        for item in years
    ]

    maximum = max(
        item["cumulative"]
        for item in years
    ) if years else 1000

    chart.valueAxis.valueMin = 0

    chart.valueAxis.valueMax = (
        math.ceil(maximum / 5000) * 5000
        if maximum > 0
        else 5000
    )

    chart.valueAxis.valueStep = (
        chart.valueAxis.valueMax / 5
    )

    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = GREEN

    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 6

    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7

    chart.categoryAxis.strokeColor = MID_GREY
    chart.valueAxis.strokeColor = MID_GREY

    drawing.add(chart)

    return drawing


# =========================================================
# PDF
# =========================================================

@app.post("/api/pdf")
def generate_pdf():

    d = request.get_json(
        force=True
    )

    result = calculate_values(d)

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

    kwp = float(
        d.get(
            "kwp",
            0
        )
    )

    battery = float(
        d.get(
            "battery",
            0
        )
    )

    angle = float(
        d.get(
            "angle",
            30
        )
    )

    aspect = d.get(
        "aspect",
        "0"
    )

    loss = float(
        d.get(
            "loss",
            14
        )
    )

    consumption = float(
        d.get(
            "consumption",
            0
        )
    )

    profile = d.get(
        "profile",
        "equilibrato"
    )

    cost = float(
        d.get(
            "cost",
            0
        )
    )

    deduction = float(
        d.get(
            "deduction",
            0
        )
    )

    energy_price = float(
        d.get(
            "energy_price",
            0.25
        )
    )

    export_price = float(
        d.get(
            "export_price",
            0.10
        )
    )

    production = float(
        d.get(
            "production",
            0
        )
    )

    monthly = d.get(
        "monthly_production",
        []
    )

    if not monthly:
        monthly = [0] * 12

    profile_labels = {

        "giorno":
            "Prevalentemente giorno",

        "equilibrato":
            "Equilibrato",

        "sera":
            "Prevalentemente sera/notte"

    }

    profile_label = profile_labels.get(
        profile,
        "Equilibrato"
    )

    aspect_labels = {

        "-90":
            "Est",

        "0":
            "Sud",

        "90":
            "Ovest"

    }

    aspect_label = aspect_labels.get(
        str(aspect),
        "Personalizzato"
    )

    today = datetime.now().strftime(
        "%d/%m/%Y"
    )

    gross_25 = result[
        "gross_25"
    ]

    net_25 = result[
        "net_25"
    ]

    payback = result[
        "payback"
    ]

    payback_text = (
        f"{payback},0 anni"
        if payback
        else "Non raggiunto"
    )

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(

        buffer,

        pagesize=A4,

        rightMargin=18 * mm,

        leftMargin=18 * mm,

        topMargin=23 * mm,

        bottomMargin=20 * mm,

        title="Analisi Fotovoltaica",

        author="Energia Giusta"

    )

    story = []


    # =====================================================
    # PAGINA 1 - COPERTINA
    # =====================================================

    story.append(
        Spacer(
            1,
            18 * mm
        )
    )

    story.append(
        Paragraph(
            "ANALISI<br/>FOTOVOLTAICA",
            title_style()
        )
    )

    story.append(
        Paragraph(
            "Analisi energetica ed economica dell'impianto",
            subtitle_style()
        )
    )

    cover_data = [

        [
            Paragraph(
                "<b>CLIENTE / INDIRIZZO</b>",
                small_style()
            ),

            Paragraph(
                "<b>DATA ANALISI</b>",
                small_style()
            )
        ],

        [

            Paragraph(
                address
                if address
                else "Indirizzo non specificato",

                ParagraphStyle(
                    "CoverAddress",
                    fontName="Helvetica-Bold",
                    fontSize=13,
                    leading=17,
                    textColor=DARK
                )
            ),

            Paragraph(
                today,

                ParagraphStyle(
                    "CoverDate",
                    fontName="Helvetica-Bold",
                    fontSize=13,
                    textColor=DARK
                )
            )

        ]

    ]

    cover_table = Table(

        cover_data,

        colWidths=[
            125 * mm,
            35 * mm
        ]

    )

    cover_table.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREY
            ),

            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.8,
                MID_GREY
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
                6 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                6 * mm
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

        ])

    )

    story.append(
        cover_table
    )

    story.append(
        Spacer(
            1,
            18 * mm
        )
    )

    intro_box = Table(

        [[

            Paragraph(
                "<b>OBIETTIVO DELL'ANALISI</b><br/><br/>"
                "Questa analisi stima la produzione energetica "
                "dell'impianto fotovoltaico e il relativo beneficio "
                "economico sulla base dei dati inseriti e della "
                "simulazione PVGIS.",
                normal_style()
            )

        ]],

        colWidths=[
            160 * mm
        ]

    )

    intro_box.setStyle(

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
                GREEN
            ),

            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            ),

            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
            ),

            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
            )

        ])

    )

    story.append(
        intro_box
    )

    story.append(
        Spacer(
            1,
            35 * mm
        )
    )

    story.append(
        Paragraph(
            "Energia Giusta",

            ParagraphStyle(
                "BrandBig",
                fontName="Helvetica-Bold",
                fontSize=18,
                textColor=GREEN,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        Paragraph(
            "Consulenza energetica e soluzioni per l'efficienza",

            ParagraphStyle(
                "BrandSub",
                fontName="Helvetica",
                fontSize=9,
                textColor=GREY,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        PageBreak()
    )


    # =====================================================
    # PAGINA 2 - PRODUZIONE E RISPARMIO
    # =====================================================

    story.append(
        Paragraph(
            "Produzione e Risparmio",
            section_style()
        )
    )

    story.append(
        Paragraph(
            "La produzione è stimata tramite PVGIS 5.3 "
            "sulla base della posizione geografica e dei "
            "parametri tecnici dell'impianto.",
            normal_style()
        )
    )

    story.append(
        Spacer(
            1,
            5 * mm
        )
    )

    kpi_table = Table(

        [[

            kpi_box(
                "PRODUZIONE ANNUA",
                number(production) + " kWh"
            ),

            kpi_box(
                "AUTOCONSUMO",
                number(result["self_used"]) + " kWh"
            ),

            kpi_box(
                "ENERGIA IMMESSA",
                number(result["export"]) + " kWh"
            ),

            kpi_box(
                "RISPARMIO ANNUO",
                euro(result["annual_saving"])
            )

        ]],

        colWidths=[
            43 * mm,
            43 * mm,
            43 * mm,
            43 * mm
        ]

    )

    kpi_table.setStyle(

        TableStyle([

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
                1.5 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                1.5 * mm
            )

        ])

    )

    story.append(
        kpi_table
    )

    story.append(
        Spacer(
            1,
            8 * mm
        )
    )

    story.append(
        Paragraph(
            "Produzione energetica stimata",

            ParagraphStyle(
                "ChartTitle",
                fontName="Helvetica-Bold",
                fontSize=12,
                textColor=DARK
            )
        )
    )

    story.append(
        Spacer(
            1,
            3 * mm
        )
    )

    story.append(
        make_monthly_chart(
            monthly
        )
    )

    story.append(
        Spacer(
            1,
            5 * mm
        )
    )

    technical_data = [

        [
            "Potenza impianto",
            f"{kwp:.2f} kWp",

            "Batteria",
            f"{battery:.1f} kWh"
        ],

        [
            "Inclinazione",
            f"{angle:.0f}°",

            "Orientamento",
            aspect_label
        ],

        [
            "Perdite sistema",
            f"{loss:.1f}%",

            "Consumo annuo",
            f"{number(consumption)} kWh"
        ]

    ]

    technical_table = Table(

        technical_data,

        colWidths=[
            38 * mm,
            42 * mm,
            38 * mm,
            42 * mm
        ]

    )

    technical_table.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREY
            ),

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
            ),

            (
                "FONTNAME",
                (0, 0),
                (-1, -1),
                "Helvetica"
            ),

            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold"
            ),

            (
                "FONTNAME",
                (2, 0),
                (2, -1),
                "Helvetica-Bold"
            ),

            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8
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

    story.append(
        technical_table
    )

    story.append(
        PageBreak()
    )


    # =====================================================
    # PAGINA 3 - ANALISI ECONOMICA
    # =====================================================

    story.append(
        Paragraph(
            "Analisi Economica",
            section_style()
        )
    )

    story.append(
        Paragraph(
            "Sintesi economica dell'investimento sulla base "
            "dei parametri inseriti nella simulazione.",
            normal_style()
        )
    )

    story.append(
        Spacer(
            1,
            8 * mm
        )
    )

    economic_kpis = Table(

        [[

            kpi_box(
                "INVESTIMENTO",
                euro(cost)
            ),

            kpi_box(
                "DETRAZIONE",
                euro(deduction)
            ),

            kpi_box(
                "INVESTIMENTO NETTO",
                euro(result["net_cost"])
            )

        ]],

        colWidths=[
            53 * mm,
            53 * mm,
            53 * mm
        ]

    )

    economic_kpis.setStyle(

        TableStyle([

            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                2 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                2 * mm
            )

        ])

    )

    story.append(
        economic_kpis
    )

    story.append(
        Spacer(
            1,
            10 * mm
        )
    )

    payback_box = Table(

        [[

            Paragraph(
                "TEMPO DI RIENTRO STIMATO",

                ParagraphStyle(
                    "PaybackLabel",
                    fontName="Helvetica-Bold",
                    fontSize=9,
                    textColor=DARK_GREEN,
                    alignment=TA_CENTER
                )
            )

        ], [

            Paragraph(
                payback_text,

                ParagraphStyle(
                    "PaybackValue",
                    fontName="Helvetica-Bold",
                    fontSize=27,
                    textColor=DARK,
                    alignment=TA_CENTER
                )
            )

        ]],

        colWidths=[
            160 * mm
        ]

    )

    payback_box.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_YELLOW
            ),

            (
                "BOX",
                (0, 0),
                (-1, -1),
                1,
                YELLOW
            ),

            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6 * mm
            ),

            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6 * mm
            )

        ])

    )

    story.append(
        payback_box
    )

    story.append(
        Spacer(
            1,
            10 * mm
        )
    )

    long_term = Table(

        [[

            Paragraph(
                "BENEFICIO CUMULATO LORDO A 25 ANNI",
                centered_style()
            ),

            Paragraph(
                "BENEFICIO NETTO A 25 ANNI",
                centered_style()
            )

        ], [

            Paragraph(
                euro(gross_25),

                ParagraphStyle(
                    "LongValue1",
                    fontName="Helvetica-Bold",
                    fontSize=18,
                    textColor=DARK_GREEN,
                    alignment=TA_CENTER
                )
            ),

            Paragraph(
                euro(net_25),

                ParagraphStyle(
                    "LongValue2",
                    fontName="Helvetica-Bold",
                    fontSize=18,
                    textColor=DARK_GREEN,
                    alignment=TA_CENTER
                )
            )

        ]],

        colWidths=[
            80 * mm,
            80 * mm
        ]

    )

    long_term.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREEN
            ),

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.white
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

        ])

    )

    story.append(
        long_term
    )

    story.append(
        Spacer(
            1,
            12 * mm
        )
    )

    explanation = Table(

        [[

            Paragraph(
                "<b>Come leggere il risultato</b><br/><br/>"
                "L'investimento netto considera il costo dell'impianto "
                "al netto della detrazione indicata. Il beneficio "
                "economico considera l'energia autoconsumata e il "
                "valore attribuito all'energia immessa.",
                normal_style()
            )

        ]],

        colWidths=[
            160 * mm
        ]

    )

    explanation.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREY
            ),

            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
            ),

            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
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

        ])

    )

    story.append(
        explanation
    )

    story.append(
        PageBreak()
    )


    # =====================================================
    # PAGINA 4 - PROIEZIONE 25 ANNI
    # =====================================================

    story.append(
        Paragraph(
            "Proiezione Economica a 25 Anni",
            section_style()
        )
    )

    story.append(
        Paragraph(
            "Stima dell'evoluzione del beneficio economico "
            "nel tempo, considerando un degrado della produzione "
            "dello 0,5% annuo e una crescita del prezzo dell'energia "
            "acquistata del 2% annuo.",
            normal_style()
        )
    )

    story.append(
        Spacer(
            1,
            5 * mm
        )
    )

    story.append(
        make_economic_chart(
            result["years"]
        )
    )

    story.append(
        Spacer(
            1,
            3 * mm
        )
    )

    milestones = [
        1,
        5,
        10,
        15,
        20,
        25
    ]

    projection_rows = [

        [
            "Periodo",
            "Beneficio annuo",
            "Beneficio cumulato"
        ]

    ]

    for year in milestones:

        item = next(

            (
                x
                for x in result["years"]
                if x["year"] == year
            ),

            None

        )

        if item:

            projection_rows.append([

                f"{year}° anno",

                euro(
                    item["benefit"]
                ),

                euro(
                    item["cumulative"]
                )

            ])

    projection_table = Table(

        projection_rows,

        colWidths=[
            42 * mm,
            55 * mm,
            63 * mm
        ]

    )

    projection_table.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                GREEN
            ),

            (
                "TEXTCOLOR",
                (0, 0),
                (-1, 0),
                WHITE
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
                (0, -1),
                "Helvetica-Bold"
            ),

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
            ),

            (
                "BACKGROUND",
                (0, 1),
                (-1, -1),
                colors.white
            ),

            (
                "ALIGN",
                (1, 1),
                (-1, -1),
                "RIGHT"
            ),

            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8.5
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

    story.append(
        projection_table
    )

    story.append(
        Spacer(
            1,
            7 * mm
        )
    )

    assumptions = Table(

        [[

            Paragraph(
                "<b>PARAMETRI DELLA SIMULAZIONE</b><br/><br/>"
                "Degrado produzione: 0,5% annuo<br/>"
                "Crescita prezzo energia acquistata: 2% annuo<br/>"
                f"Prezzo energia acquistata: {energy_price:.2f} €/kWh<br/>"
                f"Valore energia immessa: {export_price:.2f} €/kWh<br/>"
                f"Profilo consumi: {profile_label}",
                normal_style()
            )

        ]],

        colWidths=[
            160 * mm
        ]

    )

    assumptions.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_GREY
            ),

            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
            ),

            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7 * mm
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

        ])

    )

    story.append(
        assumptions
    )

    story.append(
        Spacer(
            1,
            7 * mm
        )
    )

    story.append(
        Paragraph(
            "Nota: la proiezione rappresenta una simulazione "
            "commerciale e non costituisce una garanzia dei "
            "risultati economici futuri.",
            small_style()
        )
    )

    story.append(
        PageBreak()
    )


    # =====================================================
    # PAGINA 5 - COSA INCLUDE L'OFFERTA
    # =====================================================

    story.append(
        Paragraph(
            "Cosa Include l'Offerta",
            section_style()
        )
    )

    story.append(
        Paragraph(
            "Un servizio completo dalla progettazione alla gestione "
            "dell'impianto nel tempo.",
            normal_style()
        )
    )

    story.append(
        Spacer(
            1,
            8 * mm
        )
    )

    services = [

        [
            "SOPRALLUOGO",
            "Analisi preliminare dell'immobile e degli spazi disponibili."
        ],

        [
            "PROGETTAZIONE",
            "Dimensionamento dell'impianto in funzione delle esigenze energetiche."
        ],

        [
            "INSTALLAZIONE",
            "Installazione eseguita da personale specializzato."
        ],

        [
            "PRATICHE GSE",
            "Gestione delle pratiche necessarie per l'impianto."
        ],

        [
            "DETRAZIONE FISCALE",
            "Gestione della documentazione relativa alla detrazione indicata."
        ],

        [
            "MONITORAGGIO APP",
            "Controllo della produzione e delle prestazioni dell'impianto tramite App."
        ],

        [
            "SMALTIMENTO",
            "Gestione dello smaltimento di moduli fotovoltaici e inverter a fine vita."
        ]

    ]

    service_data = []

    for title, text in services:

        service_data.append([

            Paragraph(
                title,

                ParagraphStyle(
                    "ServiceTitle",
                    fontName="Helvetica-Bold",
                    fontSize=8.5,
                    textColor=DARK_GREEN
                )
            ),

            Paragraph(
                text,
                normal_style()
            )

        ])

    service_table = Table(

        service_data,

        colWidths=[
            43 * mm,
            117 * mm
        ]

    )

    service_table.setStyle(

        TableStyle([

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
            ),

            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                LIGHT_GREEN
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
                5 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5 * mm
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

        ])

    )

    story.append(
        service_table
    )

    story.append(
        Spacer(
            1,
            10 * mm
        )
    )

    story.append(
        Paragraph(
            "Garanzie",

            ParagraphStyle(
                "GuaranteeTitle",
                fontName="Helvetica-Bold",
                fontSize=14,
                textColor=DARK_GREEN,
                spaceAfter=5 * mm
            )
        )
    )

    guarantees = [

        [
            "Pannelli - fabbricazione",
            "25 anni"
        ],

        [
            "Pannelli - garanzia di potenza",
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

    guarantee_table = Table(

        guarantees,

        colWidths=[
            115 * mm,
            45 * mm
        ]

    )

    guarantee_table.setStyle(

        TableStyle([

            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                LIGHT_YELLOW
            ),

            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                MID_GREY
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
                "CENTER"
            ),

            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9
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

    story.append(
        guarantee_table
    )

    story.append(
        PageBreak()
    )


    # =====================================================
    # PAGINA 6 - CONTATTI
    # =====================================================

    story.append(
        Spacer(
            1,
            12 * mm
        )
    )

    story.append(
        Paragraph(
            "Grazie per la fiducia",

            ParagraphStyle(
                "Thanks",
                fontName="Helvetica-Bold",
                fontSize=25,
                leading=30,
                textColor=DARK_GREEN,
                alignment=TA_CENTER,
                spaceAfter=5 * mm
            )
        )
    )

    story.append(
        Paragraph(
            "Per qualsiasi approfondimento o per definire "
            "la configurazione dell'impianto, resto a disposizione.",

            ParagraphStyle(
                "ThanksSub",
                fontName="Helvetica",
                fontSize=11,
                leading=16,
                textColor=GREY,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        Spacer(
            1,
            18 * mm
        )
    )

    contact_table = Table(

        [[

            Paragraph(
                "<b>Simone Alfarano</b><br/><br/>"
                "Responsabile Commerciale<br/>"
                "Energia Giusta<br/>"
                "Partner ENI Plenitude",

                ParagraphStyle(
                    "ContactName",
                    fontName="Helvetica",
                    fontSize=11,
                    leading=16,
                    textColor=DARK
                )
            ),

            Paragraph(
                "<b>CONTATTI</b><br/><br/>"
                "Tel. / WhatsApp: 351.7478652<br/>"
                "WhatsApp disponibile<br/><br/>"
                "Email: simone.alfarano@energiagiusta.it<br/><br/>"
                "Sito: www.energiagiusta.it",

                ParagraphStyle(
                    "ContactInfo",
                    fontName="Helvetica",
                    fontSize=9.5,
                    leading=14,
                    textColor=DARK
                )
            )

        ]],

        colWidths=[
            80 * mm,
            80 * mm
        ]

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
                GREEN
            ),

            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),

            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            ),

            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            ),

            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                8 * mm
            )

        ])

    )

    story.append(
        contact_table
    )

    story.append(
        Spacer(
            1,
            25 * mm
        )
    )

    story.append(
        Paragraph(
            "ENERGIA GIUSTA",

            ParagraphStyle(
                "FinalBrand",
                fontName="Helvetica-Bold",
                fontSize=22,
                textColor=GREEN,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        Spacer(
            1,
            4 * mm
        )
    )

    story.append(
        Paragraph(
            "Felicità sostenibile. Semplicemente quella Giusta.",

            ParagraphStyle(
                "FinalClaim",
                fontName="Helvetica",
                fontSize=10,
                textColor=GREY,
                alignment=TA_CENTER
            )
        )
    )

    story.append(
        Spacer(
            1,
            15 * mm
        )
    )

    story.append(
        Paragraph(
            "Stima commerciale: il risultato dipende da tariffe, "
            "profilo reale dei consumi, condizioni di scambio/ritiro, "
            "ombreggiamento e altri fattori.<br/><br/>"
            "<b>Il presente documento non costituisce un preventivo finanziario.</b>",

            ParagraphStyle(
                "DisclaimerFinal",
                fontName="Helvetica",
                fontSize=7.5,
                leading=11,
                textColor=GREY,
                alignment=TA_CENTER
            )
        )
    )


    # =====================================================
    # COSTRUZIONE PDF
    # =====================================================

    doc.build(
        story,
        onFirstPage=draw_logo,
        onLaterPages=draw_logo
    )

    buffer.seek(0)

    return send_file(

        buffer,

        mimetype="application/pdf",

        as_attachment=True,

        download_name="Analisi_Fotovoltaica.pdf"

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
