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
GEOCODING_URL = "https://nominatim.openstreetmap.org/search"

PAGE_W, PAGE_H = A4

# Cache locale per evitare richieste ripetute allo stesso indirizzo
GEOCODE_CACHE = {}

# =========================================================
# IPOTESI PRUDENZIALE
# =========================================================

GRID_PURCHASE_PCT = 0.12


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


def geocode_address(address):

    address = " ".join(
        str(address or "").strip().split()
    )

    if not address:
        raise ValueError(
            "Inserisci un indirizzo completo."
        )

    cache_key = address.lower()

    if cache_key in GEOCODE_CACHE:
        return GEOCODE_CACHE[cache_key]

    params = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "it",
        "addressdetails": 1,
        "accept-language": "it",
    }

    url = (
        GEOCODING_URL
        + "?"
        + urlencode(params)
    )

    req = Request(
        url,
        headers={
            "User-Agent":
                "ConfiguratoreFotovoltaico/1.0 "
                "(Energia Giusta)"
        }
    )

    with urlopen(req, timeout=10) as r:

        data = json.loads(
            r.read().decode("utf-8")
        )

    if not data:

        raise ValueError(
            "Indirizzo non trovato. "
            "Inserisci via, numero civico e comune."
        )

    result = {

        "lat":
            float(data[0]["lat"]),

        "lon":
            float(data[0]["lon"]),

        "display_name":
            data[0].get(
                "display_name",
                address
            )

    }

    GEOCODE_CACHE[
        cache_key
    ] = result

    return result


def resolve_coordinates(d):

    if (
        d.get("lat") is not None
        and d.get("lon") is not None
        and str(d.get("lat")).strip() != ""
        and str(d.get("lon")).strip() != ""
    ):

        return {
            "lat": float(d["lat"]),
            "lon": float(d["lon"])
        }

    return geocode_address(
        d.get("address", "")
    )


# =========================================================
# HOME
# =========================================================

@app.get("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# GEOCODING
# =========================================================

@app.post("/api/geocode")
def api_geocode():

    d = request.get_json(
        force=True
    )

    try:

        result = geocode_address(
            d.get("address", "")
        )

        # IMPORTANTE:
        # success=True serve all'index.html
        return jsonify({

            "success": True,

            "lat":
                result["lat"],

            "lon":
                result["lon"],

            "display_name":
                result["display_name"]

        })

    except Exception as e:

        return jsonify({

            "success": False,

            "error":
                str(e)

        }), 400


# =========================================================
# PVGIS API
# =========================================================

@app.post("/api/pvgis")
def api_pvgis():

    d = request.get_json(
        force=True
    )

    try:

        coords = resolve_coordinates(d)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 400

    try:

        params = {

            "lat":
                coords["lat"],

            "lon":
                coords["lon"],

            "peakpower":
                float(d["kwp"]),

            "loss":
                float(
                    d.get(
                        "loss",
                        14
                    )
                ),

            "angle":
                float(
                    d.get(
                        "angle",
                        30
                    )
                ),

            "aspect":
                float(
                    d.get(
                        "aspect",
                        0
                    )
                ),

            "usehorizon":
                1,

            "outputformat":
                "json"

        }

        data = pvgis(
            params
        )

        fixed = (
            data[
                "outputs"
            ][
                "monthly"
            ][
                "fixed"
            ]
        )

        yearly = (
            data[
                "outputs"
            ][
                "totals"
            ][
                "fixed"
            ]
        )

        return jsonify({

            "annual_kwh":
                yearly["E_y"],

            "monthly_kwh":
                [
                    x["E_m"]
                    for x in fixed
                ],

            "raw":
                data

        })

    except Exception as e:

        return jsonify({
            "error":
                str(e)
        }), 500


# =========================================================
# CALCOLI ECONOMICI
# =========================================================

def calculate_values(d):

    production = float(
        d["production"]
    )

    consumption = float(
        d["consumption"]
    )

    battery = float(
        d.get(
            "battery",
            0
        )
    )

    price = float(
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

    cost = float(
        d["cost"]
    )

    deduction = float(
        d.get(
            "deduction",
            0
        )
    )

    profile = d.get(
        "profile",
        "equilibrato"
    )

    # -----------------------------------------------------
    # MODELLO PRUDENZIALE
    # -----------------------------------------------------

    target_grid = (
        consumption
        * GRID_PURCHASE_PCT
    )

    target_self_used = (
        consumption
        - target_grid
    )

    self_used = min(
        production,
        target_self_used
    )

    grid = max(
        target_grid,
        consumption - self_used
    )

    export = max(
        0,
        production - self_used
    )

    self_consumption_pct = (

        min(
            0.95,
            self_used / production
        )

        if production > 0

        else 0
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

    degradation = 0.005

    energy_price_growth = 0.02

    years = []

    cumulative_benefit = 0

    payback = None

    payback_years = None

    # -----------------------------------------------------
    # PROIEZIONE 25 ANNI
    # -----------------------------------------------------

    for y in range(
        1,
        26
    ):

        production_y = (

            production
            *
            (
                (1 - degradation)
                **
                (y - 1)
            )

        )

        target_self_used_y = (

            consumption
            -
            (
                consumption
                *
                GRID_PURCHASE_PCT
            )

        )

        self_used_y = min(
            production_y,
            target_self_used_y
        )

        grid_y = max(
            consumption
            * GRID_PURCHASE_PCT,

            consumption
            - self_used_y
        )

        export_y = max(
            0,
            production_y
            - self_used_y
        )

        energy_price_y = (

            price
            *
            (
                (1 + energy_price_growth)
                **
                (y - 1)
            )

        )

        benefit = (

            self_used_y
            * energy_price_y

            +

            export_y
            * export_price

        )

        cumulative_benefit += benefit

        # -------------------------------------------------
        # PAYBACK
        # -------------------------------------------------

        if (

            payback is None

            and

            cumulative_benefit
            >= net_cost

        ):

            payback = y

            previous_cumulative = (
                cumulative_benefit
                - benefit
            )

            fraction = (

                (
                    net_cost
                    - previous_cumulative
                )
                /
                benefit

                if benefit > 0

                else 0
            )

            fraction = max(
                0,
                min(
                    1,
                    fraction
                )
            )

            payback_years = (
                y - 1
            ) + fraction

        years.append({

            "year":
                y,

            "benefit":
                benefit,

            "cumulative":
                cumulative_benefit,

            "production":
                production_y,

            "self_used":
                self_used_y,

            "grid_purchase":
                grid_y,

            "export":
                export_y,

            "energy_price":
                energy_price_y

        })

    gross_25 = (
        years[-1]["cumulative"]
    )

    net_25 = max(
        0,
        gross_25 - net_cost
    )

    return {

        "self_used":
            self_used,

        "export":
            export,

        "grid_purchase":
            grid,

        "grid_purchase_pct":
            (
                grid
                /
                consumption
                * 100
                if consumption > 0
                else 0
            ),

        "self_consumption_pct":
            self_consumption_pct
            * 100,

        "annual_saving":
            annual_saving,

        "net_cost":
            net_cost,

        "payback":
            payback,

        "payback_years":
            payback_years,

        "years":
            years,

        "gross_25":
            gross_25,

        "net_25":
            net_25

    }


# =========================================================
# CALCOLO ROI
# =========================================================

@app.post("/api/calculate")
def calculate():

    d = request.get_json(
        force=True
    )

    try:

        result = calculate_values(d)

    except Exception as e:

        return jsonify({
            "error":
                str(e)
        }), 400

    # -----------------------------------------------------
    # DATI MENSILI
    # -----------------------------------------------------

    monthly = d.get(
        "monthly"
    )

    if (
        isinstance(
            monthly,
            list
        )
        and
        len(monthly) == 12
    ):

        result[
            "monthly_kwh"
        ] = monthly

    else:

        try:

            if float(
                d.get(
                    "kwp",
                    0
                )
            ) > 0:

                coords = (
                    resolve_coordinates(d)
                )

                params = {

                    "lat":
                        coords["lat"],

                    "lon":
                        coords["lon"],

                    "peakpower":
                        float(
                            d["kwp"]
                        ),

                    "loss":
                        float(
                            d.get(
                                "loss",
                                14
                            )
                        ),

                    "angle":
                        float(
                            d.get(
                                "angle",
                                30
                            )
                        ),

                    "aspect":
                        float(
                            d.get(
                                "aspect",
                                0
                            )
                        ),

                    "usehorizon":
                        1,

                    "outputformat":
                        "json"

                }

                pv = pvgis(
                    params
                )

                result[
                    "monthly_kwh"
                ] = [

                    float(
                        item["E_m"]
                    )

                    for item
                    in
                    pv[
                        "outputs"
                    ][
                        "monthly"
                    ][
                        "fixed"
                    ]

                ]

            else:

                result[
                    "monthly_kwh"
                ] = []

        except Exception:

            result[
                "monthly_kwh"
            ] = []

    return jsonify(
        result
    )


# =========================================================
# PALETTE PDF
# =========================================================

GREEN = colors.HexColor(
    "#168447"
)

DARK_GREEN = colors.HexColor(
    "#0B5D32"
)

MID_GREEN = colors.HexColor(
    "#39B96B"
)

LIGHT_GREEN = colors.HexColor(
    "#E9F8EF"
)

PALE_GREEN = colors.HexColor(
    "#F5FBF7"
)

YELLOW = colors.HexColor(
    "#F5C518"
)

LIGHT_YELLOW = colors.HexColor(
    "#FFF8D9"
)

DARK = colors.HexColor(
    "#172033"
)

GREY = colors.HexColor(
    "#64748B"
)

LIGHT_GREY = colors.HexColor(
    "#F5F7FA"
)

MID_GREY = colors.HexColor(
    "#D8E0E8"
)

WHITE = colors.white


# =========================================================
# HELPERS
# =========================================================

def euro(value):

    return (
        "€ "
        +
        f"{value:,.0f}".replace(
            ",",
            "."
        )
    )


def number(value):

    return f"{value:,.0f}".replace(
        ",",
        "."
    )


def decimal(value):

    return f"{value:.2f}".replace(
        ".",
        ","
    )


def wrap_text(
    text,
    font="Helvetica",
    size=8,
    max_width=50*mm
):

    words = str(
        text
    ).split()

    lines = []

    current = ""

    for word in words:

        test = (
            word
            if not current
            else
            current
            + " "
            + word
        )

        if stringWidth(
            test,
            font,
            size
        ) <= max_width:

            current = test

        else:

            if current:
                lines.append(
                    current
                )

            current = word

    if current:
        lines.append(
            current
        )

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
    max_lines=None,
    align="left"
):

    lines = wrap_text(
        text,
        font,
        size,
        max_width
    )

    if max_lines:
        lines = lines[
            :max_lines
        ]

    c.setFillColor(
        color
    )

    c.setFont(
        font,
        size
    )

    for i, line in enumerate(
        lines
    ):

        yy = (
            y
            -
            i * leading
        )

        if align == "center":

            c.drawCentredString(
                x,
                yy,
                line
            )

        elif align == "right":

            c.drawRightString(
                x,
                yy,
                line
            )

        else:

            c.drawString(
                x,
                yy,
                line
            )

    return (
        len(lines)
        *
        leading
    )


def round_box(
    c,
    x,
    y,
    w,
    h,
    fill=WHITE,
    stroke=None,
    radius=7,
    lw=0.7
):

    c.setFillColor(
        fill
    )

    c.setStrokeColor(
        stroke
        if stroke
        else fill
    )

    c.setLineWidth(
        lw
    )

    c.roundRect(
        x,
        y,
        w,
        h,
        radius,
        fill=1,
        stroke=1
        if stroke
        else 0
    )


def pill(
    c,
    x,
    y,
    w,
    h,
    text,
    fill=GREEN,
    text_color=WHITE,
    size=7.5
):

    c.setFillColor(
        fill
    )

    c.roundRect(
        x,
        y,
        w,
        h,
        h / 2,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        text_color
    )

    c.setFont(
        "Helvetica-Bold",
        size
    )

    c.drawCentredString(
        x + w / 2,
        y + h / 2 - 2.4,
        text
    )


# =========================================================
# SOLE
# =========================================================

def draw_sun(
    c,
    x,
    y,
    size=24
):

    c.setStrokeColor(
        YELLOW
    )

    c.setFillColor(
        YELLOW
    )

    c.setLineWidth(
        1.5
    )

    c.circle(
        x,
        y,
        size * 0.27,
        fill=1,
        stroke=0
    )

    for i in range(8):

        a = math.radians(
            i * 45
        )

        c.line(

            x
            + math.cos(a)
            * size
            * .42,

            y
            + math.sin(a)
            * size
            * .42,

            x
            + math.cos(a)
            * size
            * .76,

            y
            + math.sin(a)
            * size
            * .76

        )


# =========================================================
# SCENA CASA FOTOVOLTAICA
# =========================================================

def draw_pv_scene(
    c,
    x,
    y,
    w,
    h
):

    round_box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        None,
        9
    )

    c.setFillColor(
        PALE_GREEN
    )

    c.roundRect(
        x + 2*mm,
        y + 2*mm,
        w - 4*mm,
        h - 4*mm,
        6*mm,
        fill=1,
        stroke=0
    )

    # Sole grande

    sx = (
        x
        + w
        - 15*mm
    )

    sy = (
        y
        + h
        - 12*mm
    )

    draw_sun(
        c,
        sx,
        sy,
        24
    )

    # Terreno

    c.setFillColor(
        colors.HexColor(
            "#DCEFE4"
        )
    )

    c.ellipse(
        x + 6*mm,
        y + 5*mm,
        x + w - 6*mm,
        y + 18*mm,
        fill=1,
        stroke=0
    )

    # Casa

    hx = x + 12*mm
    hy = y + 10*mm

    hw = 53*mm
    hh = 27*mm

    c.setFillColor(
        colors.HexColor(
            "#DDE6E2"
        )
    )

    c.roundRect(
        hx + 1.3*mm,
        hy - .9*mm,
        hw,
        hh,
        1.7*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.roundRect(
        hx,
        hy,
        hw,
        hh,
        1.7*mm,
        fill=1,
        stroke=0
    )

    # Tetto

    p = c.beginPath()

    p.moveTo(
        hx - 5*mm,
        hy + hh
    )

    p.lineTo(
        hx + hw / 2,
        hy + hh + 18*mm
    )

    p.lineTo(
        hx + hw + 5*mm,
        hy + hh
    )

    p.close()

    c.setFillColor(
        colors.HexColor(
            "#0D6337"
        )
    )

    c.drawPath(
        p,
        fill=1,
        stroke=0
    )

    p2 = c.beginPath()

    p2.moveTo(
        hx + hw / 2,
        hy + hh + 18*mm
    )

    p2.lineTo(
        hx + hw + 5*mm,
        hy + hh
    )

    p2.lineTo(
        hx + hw / 2,
        hy + hh + 2*mm
    )

    p2.close()

    c.setFillColor(
        colors.HexColor(
            "#0A552F"
        )
    )

    c.drawPath(
        p2,
        fill=1,
        stroke=0
    )

    # Pannelli

    px = hx + 9*mm
    py = hy + hh + 3.5*mm

    pw = 36*mm
    ph = 11.5*mm

    c.saveState()

    c.translate(
        px,
        py
    )

    c.rotate(18)

    c.setFillColor(
        colors.HexColor(
            "#183B5A"
        )
    )

    c.roundRect(
        0,
        0,
        pw,
        ph,
        1.2*mm,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(
        colors.HexColor(
            "#7895A9"
        )
    )

    c.setLineWidth(
        .32
    )

    for xx in [
        pw/4,
        pw/2,
        3*pw/4
    ]:

        c.line(
            xx,
            0,
            xx,
            ph
        )

    for yy in [
        ph/3,
        2*ph/3
    ]:

        c.line(
            0,
            yy,
            pw,
            yy
        )

    c.setStrokeColor(
        colors.HexColor(
            "#C2D1DB"
        )
    )

    c.line(
        0,
        ph - .8*mm,
        pw,
        ph - .8*mm
    )

    c.restoreState()

    # Finestre

    for wx in [
        hx + 6.5*mm,
        hx + 23*mm
    ]:

        c.setFillColor(
            colors.HexColor(
                "#BFE5E6"
            )
        )

        c.roundRect(
            wx,
            hy + 14*mm,
            10.5*mm,
            7.5*mm,
            .7*mm,
            fill=1,
            stroke=0
        )

        c.setStrokeColor(
            WHITE
        )

        c.setLineWidth(
            .55
        )

        c.line(
            wx + 5.25*mm,
            hy + 14*mm,
            wx + 5.25*mm,
            hy + 21.5*mm
        )

        c.line(
            wx,
            hy + 17.75*mm,
            wx + 10.5*mm,
            hy + 17.75*mm
        )

    # Porta

    c.setFillColor(
        DARK_GREEN
    )

    c.roundRect(
        hx + 39*mm,
        hy,
        8.5*mm,
        17*mm,
        1*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        colors.HexColor(
            "#D8B66A"
        )
    )

    c.circle(
        hx + 45.5*mm,
        hy + 8*mm,
        .55*mm,
        fill=1,
        stroke=0
    )

    # Camino

    c.setFillColor(
        colors.HexColor(
            "#D6DAD8"
        )
    )

    c.rect(
        hx + 38*mm,
        hy + hh + 11*mm,
        4.5*mm,
        7*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        colors.HexColor(
            "#B8C0BC"
        )
    )

    c.rect(
        hx + 37.3*mm,
        hy + hh + 18*mm,
        6*mm,
        1.3*mm,
        fill=1,
        stroke=0
    )

    # Batteria

    bx = (
        x
        + w
        - 28*mm
    )

    by = y + 8*mm

    bw = 14*mm
    bh = 25*mm

    c.setFillColor(
        colors.HexColor(
            "#E6ECE9"
        )
    )

    c.roundRect(
        bx + .8*mm,
        by - .5*mm,
        bw,
        bh,
        2*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.roundRect(
        bx,
        by,
        bw,
        bh,
        2*mm,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(
        colors.HexColor(
            "#C4D2CB"
        )
    )

    c.setLineWidth(
        .5
    )

    c.roundRect(
        bx,
        by,
        bw,
        bh,
        2*mm,
        fill=0,
        stroke=1
    )

    c.setFillColor(
        GREEN
    )

    c.roundRect(
        bx + 2.5*mm,
        by + 5*mm,
        bw - 5*mm,
        14*mm,
        1.2*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.setFont(
        "Helvetica-Bold",
        4.3
    )

    c.drawCentredString(
        bx + bw/2,
        by + 10.7*mm,
        "ENERGY"
    )

    # Vegetazione

    c.setFillColor(
        GREEN
    )

    for dx, dy, r in [
        (8, 7, 2.3),
        (13, 6.2, 1.9),
        (18, 7, 2.1)
    ]:

        c.circle(
            x + dx*mm,
            y + dy*mm,
            r*mm,
            fill=1,
            stroke=0
        )

    c.setFillColor(
        DARK_GREEN
    )

    c.rect(
        x + w - 9*mm,
        y + 6*mm,
        1.2*mm,
        7*mm,
        fill=1,
        stroke=0
    )

    c.circle(
        x + w - 8.4*mm,
        y + 14*mm,
        4.3*mm,
        fill=1,
        stroke=0
    )


# =========================================================
# HEADER
# =========================================================

def draw_header(
    c,
    title,
    subtitle=""
):

    c.setFillColor(
        GREEN
    )

    c.rect(
        0,
        PAGE_H - 22*mm,
        PAGE_W,
        22*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.setFont(
        "Helvetica-Bold",
        19.5
    )

    c.drawString(
        18*mm,
        PAGE_H - 13.5*mm,
        title
    )

    if subtitle:

        c.setFont(
            "Helvetica",
            9
        )

        c.drawRightString(
            PAGE_W - 18*mm,
            PAGE_H - 13.5*mm,
            subtitle
        )


# =========================================================
# FOOTER
# =========================================================

def draw_footer(
    c,
    page
):

    c.setStrokeColor(
        MID_GREY
    )

    c.setLineWidth(
        .4
    )

    c.line(
        15*mm,
        11.5*mm,
        PAGE_W - 15*mm,
        11.5*mm
    )

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        7.1
    )

    c.drawString(
        15*mm,
        6.8*mm,
        "ENERGIA GIUSTA  •  Partner ENI Plenitude"
    )

    c.drawRightString(
        PAGE_W - 15*mm,
        6.8*mm,
        f"{page:02d}"
    )


# =========================================================
# TITOLI
# =========================================================

def section_title(
    c,
    title,
    subtitle,
    y
):

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        22
    )

    c.drawString(
        18*mm,
        y,
        title
    )

    if subtitle:

        draw_wrapped_text(
            c,
            subtitle,
            18*mm,
            y - 8*mm,
            PAGE_W - 36*mm,
            "Helvetica",
            9.5,
            11.5,
            GREY,
            2
        )


# =========================================================
# KPI
# =========================================================

def kpi(
    c,
    x,
    y,
    w,
    h,
    label,
    value,
    accent=GREEN,
    icon=None
):

    round_box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        7
    )

    c.setFillColor(
        accent
    )

    c.roundRect(
        x,
        y + h - 10*mm,
        w,
        10*mm,
        7,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.setFont(
        "Helvetica-Bold",
        8
    )

    if icon:

        c.drawString(
            x + 5*mm,
            y + h - 6.6*mm,
            icon
        )

        c.drawString(
            x + 14*mm,
            y + h - 6.6*mm,
            label.upper()
        )

    else:

        c.drawString(
            x + 5*mm,
            y + h - 6.6*mm,
            label.upper()
        )

    fs = 18

    if stringWidth(
        value,
        "Helvetica-Bold",
        fs
    ) > w - 10*mm:

        fs = 15

    if stringWidth(
        value,
        "Helvetica-Bold",
        fs
    ) > w - 10*mm:

        fs = 11.5

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        fs
    )

    c.drawCentredString(
        x + w/2,
        y + 8*mm,
        value
    )


# =========================================================
# STAT LINE
# =========================================================

def stat_line(
    c,
    x,
    y,
    label,
    value,
    color=DARK
):

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        7.5
    )

    c.drawString(
        x,
        y,
        label
    )

    c.setFillColor(
        color
    )

    c.setFont(
        "Helvetica-Bold",
        8.5
    )

    c.drawRightString(
        x + 48*mm,
        y,
        value
    )


# =========================================================
# GRAFICO MENSILE
# =========================================================

def monthly_chart(
    c,
    x,
    y,
    w,
    h,
    monthly
):

    labels = [
        "GEN",
        "FEB",
        "MAR",
        "APR",
        "MAG",
        "GIU",
        "LUG",
        "AGO",
        "SET",
        "OTT",
        "NOV",
        "DIC"
    ]

    values = []

    if isinstance(
        monthly,
        dict
    ):

        for i, lab in enumerate(
            labels,
            1
        ):

            candidates = [

                lab,

                lab.lower(),

                str(i),

                f"{i:02d}",

                [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec"
                ][i - 1]

            ]

            val = None

            for key in candidates:

                if key in monthly:

                    val = monthly[key]

                    break

            try:

                values.append(
                    float(val or 0)
                )

            except (
                TypeError,
                ValueError
            ):

                values.append(
                    0.0
                )

    elif isinstance(
        monthly,
        list
    ):

        for item in monthly[:12]:

            if isinstance(
                item,
                dict
            ):

                val = (
                    item.get("E_m")
                    or
                    item.get("energy")
                    or
                    item.get("value")
                    or
                    0
                )

            else:

                val = item

            try:

                values.append(
                    float(val or 0)
                )

            except (
                TypeError,
                ValueError
            ):

                values.append(
                    0.0
                )

        values += [
            0.0
        ] * (
            12 - len(values)
        )

    else:

        values = [
            0.0
        ] * 12

    # Evita un grafico artificiale se tutti
    # i valori fossero identici

    if (
        len(
            set(
                round(
                    v,
                    6
                )
                for v in values
            )
        ) == 1
        and
        values[0] != 0
    ):

        values = [
            0.0
        ] * 12

    maxv = (
        max(values)
        if values
        else 0
    )

    if maxv <= 0:
        maxv = 1

    c.setFillColor(
        LIGHT_GREY
    )

    c.roundRect(
        x,
        y,
        w,
        h,
        5*mm,
        fill=1,
        stroke=0
    )

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.setFillColor(
        DARK
    )

    c.drawString(
        x + 7*mm,
        y + h - 10*mm,
        "Produzione fotovoltaica mensile"
    )

    chart_x = (
        x + 9*mm
    )

    chart_y = (
        y + 13*mm
    )

    chart_w = (
        w - 18*mm
    )

    chart_h = (
        h - 31*mm
    )

    c.setStrokeColor(
        MID_GREY
    )

    c.setLineWidth(
        .45
    )

    for i in range(5):

        gy = (
            chart_y
            +
            chart_h
            * i
            /
            4
        )

        c.line(
            chart_x,
            gy,
            chart_x + chart_w,
            gy
        )

    slot = (
        chart_w
        /
        12.0
    )

    bar_w = (
        slot
        * .58
    )

    c.setFont(
        "Helvetica",
        6.8
    )

    for i, (
        lab,
        val
    ) in enumerate(
        zip(
            labels,
            values
        )
    ):

        bx = (
            chart_x
            +
            i * slot
            +
            (slot - bar_w) / 2
        )

        bh = (
            chart_h
            * val
            /
            maxv
            if maxv
            else 0
        )

        c.setFillColor(
            GREEN
            if val > 0
            else MID_GREY
        )

        c.roundRect(
            bx,
            chart_y,
            bar_w,
            max(
                bh,
                .8*mm
            ),
            1.2*mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(
            GREY
        )

        c.drawCentredString(
            bx + bar_w/2,
            chart_y - 4.5*mm,
            lab
        )

        if val > 0:

            c.setFillColor(
                DARK
            )

            c.setFont(
                "Helvetica",
                5.9
            )

            c.drawCentredString(
                bx + bar_w/2,
                chart_y + bh + 2*mm,
                f"{val:,.0f}".replace(
                    ",",
                    "."
                )
            )

            c.setFont(
                "Helvetica",
                6.8
            )

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        6.7
    )

    c.drawString(
        chart_x,
        y + 5.5*mm,
        "kWh prodotti"
    )


# =========================================================
# DONUT
# =========================================================

def donut(
    c,
    cx,
    cy,
    r,
    self_used,
    export
):

    total = max(
        self_used + export,
        1
    )

    a = (
        360
        *
        self_used
        /
        total
    )

    c.setFillColor(
        GREEN
    )

    c.wedge(
        cx - r,
        cy - r,
        cx + r,
        cy + r,
        0,
        a,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        YELLOW
    )

    c.wedge(
        cx - r,
        cy - r,
        cx + r,
        cy + r,
        a,
        360 - a,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        WHITE
    )

    c.circle(
        cx,
        cy,
        r * .58,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        15
    )

    c.drawCentredString(
        cx,
        cy + 2,
        f"{self_used / total * 100:.0f}%"
    )

    c.setFont(
        "Helvetica",
        6.8
    )

    c.drawCentredString(
        cx,
        cy - 8,
        "autoconsumo FV"
    )


# =========================================================
# GRAFICO ECONOMICO
# =========================================================

def economic_chart(
    c,
    x,
    y,
    w,
    h,
    years,
    net_cost=0,
    payback_years=None
):

    round_box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        10.5
    )

    c.drawString(
        x + 8*mm,
        y + h - 10*mm,
        "Beneficio cumulato e punto di pareggio"
    )

    pb_label = None

    if (
        payback_years is not None
        and
        net_cost > 0
    ):

        total_months = round(
            payback_years * 12
        )

        pb_year = (
            total_months // 12
        )

        pb_month = (
            total_months % 12
        )

        if (
            pb_year > 0
            and
            pb_month > 0
        ):

            pb_label = (
                f"{pb_year} anni e "
                f"{pb_month} mesi"
            )

        elif pb_year > 0:

            pb_label = (
                f"{pb_year} anni"
            )

        else:

            pb_label = (
                f"{pb_month} mesi"
            )

        c.setFillColor(
            LIGHT_YELLOW
        )

        c.roundRect(
            x + w - 72*mm,
            y + h - 18*mm,
            64*mm,
            8*mm,
            4*mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(
            DARK_GREEN
        )

        c.setFont(
            "Helvetica-Bold",
            7
        )

        c.drawCentredString(
            x + w - 40*mm,
            y + h - 15.1*mm,
            "PAREGGIO: " + pb_label
        )

    vals = [
        z["cumulative"]
        for z in years
    ]

    mx = max(
        max(
            vals or [1]
        ),
        net_cost,
        1
    )

    left = (
        x + 13*mm
    )

    bottom = (
        y + 15*mm
    )

    cw = (
        w - 20*mm
    )

    ch = (
        h - 31*mm
    )

    c.setStrokeColor(
        MID_GREY
    )

    c.setLineWidth(
        .35
    )

    for i in range(4):

        gy = (
            bottom
            +
            ch
            * i
            /
            3
        )

        c.line(
            left,
            gy,
            left + cw,
            gy
        )

    pts = []

    for i, val in enumerate(
        vals
    ):

        px = (
            left
            +
            cw
            * i
            /
            24
        )

        py = (
            bottom
            +
            ch
            * val
            /
            mx
        )

        pts.append(
            (
                px,
                py
            )
        )

    # Curva beneficio

    c.setStrokeColor(
        GREEN
    )

    c.setLineWidth(
        2.2
    )

    for p1, p2 in zip(
        pts,
        pts[1:]
    ):

        c.line(
            p1[0],
            p1[1],
            p2[0],
            p2[1]
        )

    # Soglia investimento

    if net_cost > 0:

        break_y = (
            bottom
            +
            ch
            *
            min(
                net_cost,
                mx
            )
            /
            mx
        )

        c.saveState()

        c.setStrokeColor(
            YELLOW
        )

        c.setLineWidth(
            1.5
        )

        c.setDash(
            5,
            3
        )

        c.line(
            left,
            break_y,
            left + cw,
            break_y
        )

        c.restoreState()

        c.setFillColor(
            YELLOW
        )

        c.roundRect(
            left + 2*mm,
            break_y - 2.5*mm,
            39*mm,
            6*mm,
            3*mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(
            DARK
        )

        c.setFont(
            "Helvetica-Bold",
            6.1
        )

        c.drawCentredString(
            left + 21.5*mm,
            break_y - .2*mm,
            "INVESTIMENTO NETTO  "
            + euro(net_cost)
        )

    # Punto pareggio

    if (
        payback_years is not None
        and
        payback_years <= 25
        and
        net_cost > 0
    ):

        px = (
            left
            +
            cw
            *
            payback_years
            /
            24.0
        )

        py = (
            bottom
            +
            ch
            *
            net_cost
            /
            mx
        )

        c.saveState()

        c.setStrokeColor(
            YELLOW
        )

        c.setLineWidth(
            1
        )

        c.setDash(
            2,
            2
        )

        c.line(
            px,
            bottom,
            px,
            py
        )

        c.restoreState()

        c.setFillColor(
            YELLOW
        )

        c.circle(
            px,
            py,
            3.5,
            fill=1,
            stroke=0
        )

        c.setFillColor(
            DARK_GREEN
        )

        c.circle(
            px,
            py,
            1.7,
            fill=1,
            stroke=0
        )

        label_x = min(
            px + 5*mm,
            left + cw - 39*mm
        )

        label_y = min(
            py + 5.5*mm,
            bottom + ch - 6*mm
        )

        c.setFillColor(
            DARK_GREEN
        )

        c.setFont(
            "Helvetica-Bold",
            6.4
        )

        c.drawString(
            label_x,
            label_y,
            "Rientro: "
            + (
                pb_label
                or ""
            )
        )

    # Scala temporale

    for i in [
        0,
        4,
        9,
        14,
        19,
        24
    ]:

        px, py = pts[i]

        c.setFillColor(
            GREEN
        )

        c.circle(
            px,
            py,
            2.3,
            fill=1,
            stroke=0
        )

        c.setFillColor(
            GREY
        )

        c.setFont(
            "Helvetica",
            5.9
        )

        c.drawCentredString(
            px,
            bottom - 8,
            str(i + 1)
        )

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        5.8
    )

    c.drawCentredString(
        left + cw / 2,
        bottom - 12.5*mm,
        "anni"
    )


# =========================================================
# ICONE CONTATTI
# =========================================================

def draw_contact_icon(
    c,
    cx,
    cy,
    kind
):

    c.saveState()

    c.setStrokeColor(
        DARK_GREEN
    )

    c.setFillColor(
        DARK_GREEN
    )

    c.setLineWidth(
        1.15
    )

    if kind == "phone":

        c.setLineCap(1)

        c.setLineJoin(1)

        c.setLineWidth(
            2
        )

        p = c.beginPath()

        p.moveTo(
            cx - 4.8*mm,
            cy + 3.5*mm
        )

        p.curveTo(
            cx - 2.5*mm,
            cy + 5*mm,
            cx + 2.5*mm,
            cy + 5*mm,
            cx + 4.8*mm,
            cy + 3.5*mm
        )

        p.curveTo(
            cx + 3.6*mm,
            cy - 1*mm,
            cx + 1*mm,
            cy - 3.6*mm,
            cx - 3.5*mm,
            cy - 4.8*mm
        )

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

        c.setLineWidth(
            2.8
        )

        c.line(
            cx - 4.8*mm,
            cy + 3.5*mm,
            cx - 2.8*mm,
            cy + 1.8*mm
        )

        c.line(
            cx + 2.8*mm,
            cy + 1.8*mm,
            cx + 4.8*mm,
            cy + 3.5*mm
        )

        c.setLineCap(0)

    elif kind == "mail":

        c.roundRect(
            cx - 5.5*mm,
            cy - 3.8*mm,
            11*mm,
            7.6*mm,
            1.2*mm,
            fill=0,
            stroke=1
        )

        p = c.beginPath()

        p.moveTo(
            cx - 5*mm,
            cy + 3*mm
        )

        p.lineTo(
            cx,
            cy - .5*mm
        )

        p.lineTo(
            cx + 5*mm,
            cy + 3*mm
        )

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

    elif kind == "web":

        c.circle(
            cx,
            cy,
            5*mm,
            fill=0,
            stroke=1
        )

        c.line(
            cx - 5*mm,
            cy,
            cx + 5*mm,
            cy
        )

        c.arc(
            cx - 2.5*mm,
            cy - 5*mm,
            cx + 2.5*mm,
            cy + 5*mm,
            0,
            360
        )

        c.line(
            cx,
            cy - 5*mm,
            cx,
            cy + 5*mm
        )

    c.restoreState()


# =========================================================
# ICONE SERVIZI
# =========================================================

def draw_service_icon(
    c,
    cx,
    cy,
    kind
):

    c.saveState()

    c.setStrokeColor(
        DARK_GREEN
    )

    c.setFillColor(
        DARK_GREEN
    )

    c.setLineWidth(
        1.25
    )

    if kind == "survey":

        c.circle(
            cx,
            cy + 2.5*mm,
            4*mm,
            fill=0,
            stroke=1
        )

        p = c.beginPath()

        p.moveTo(
            cx - 4*mm,
            cy
        )

        p.lineTo(
            cx,
            cy - 6*mm
        )

        p.lineTo(
            cx + 4*mm,
            cy
        )

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

        c.rect(
            cx - 2.2*mm,
            cy - 1.5*mm,
            4.4*mm,
            3.5*mm,
            fill=0,
            stroke=1
        )

    elif kind == "design":

        c.translate(
            cx,
            cy
        )

        c.rotate(
            45
        )

        c.translate(
            -cx,
            -cy
        )

        c.roundRect(
            cx - 2.5*mm,
            cy - 8*mm,
            5*mm,
            16*mm,
            1.2*mm,
            fill=0,
            stroke=1
        )

        for yy in [
            -5,
            -2,
            1,
            4
        ]:

            c.line(
                cx,
                cy + yy*mm,
                cx + 2*mm,
                cy + yy*mm
            )

    elif kind == "install":

        p = c.beginPath()

        p.moveTo(
            cx - 7*mm,
            cy + 4*mm
        )

        p.lineTo(
            cx + 7*mm,
            cy + 4*mm
        )

        p.lineTo(
            cx + 5*mm,
            cy - 5*mm
        )

        p.lineTo(
            cx - 5*mm,
            cy - 5*mm
        )

        p.close()

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

        c.line(
            cx,
            cy + 4*mm,
            cx,
            cy - 5*mm
        )

        c.line(
            cx - 4*mm,
            cy,
            cx + 6*mm,
            cy
        )

        c.line(
            cx - 3*mm,
            cy - 5*mm,
            cx - 1*mm,
            cy - 9*mm
        )

        c.line(
            cx + 3*mm,
            cy - 5*mm,
            cx + 1*mm,
            cy - 9*mm
        )

    elif kind == "gse":

        c.roundRect(
            cx - 5.5*mm,
            cy - 7*mm,
            11*mm,
            14*mm,
            1.5*mm,
            fill=0,
            stroke=1
        )

        c.line(
            cx - 3*mm,
            cy + 3*mm,
            cx + 3*mm,
            cy + 3*mm
        )

        c.line(
            cx - 3*mm,
            cy,
            cx + 2*mm,
            cy
        )

        c.line(
            cx - 3*mm,
            cy - 3*mm,
            cx - 1*mm,
            cy - 3*mm
        )

        c.line(
            cx,
            cy - 3*mm,
            cx + 3*mm,
            cy - 3*mm
        )

    elif kind == "tax":

        c.circle(
            cx,
            cy,
            5.5*mm,
            fill=0,
            stroke=1
        )

        c.setFont(
            "Helvetica-Bold",
            8
        )

        c.drawCentredString(
            cx,
            cy - 2.7*mm,
            "€"
        )

    elif kind == "monitor":

        c.roundRect(
            cx - 7*mm,
            cy - 5*mm,
            14*mm,
            10*mm,
            1.5*mm,
            fill=0,
            stroke=1
        )

        p = c.beginPath()

        p.moveTo(
            cx - 5*mm,
            cy - 1*mm
        )

        p.lineTo(
            cx - 2*mm,
            cy + 2*mm
        )

        p.lineTo(
            cx,
            cy
        )

        p.lineTo(
            cx + 3*mm,
            cy + 3.5*mm
        )

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

        c.line(
            cx - 3*mm,
            cy - 8*mm,
            cx + 3*mm,
            cy - 8*mm
        )

    elif kind == "warranty":

        p = c.beginPath()

        p.moveTo(
            cx,
            cy + 7*mm
        )

        p.lineTo(
            cx + 6*mm,
            cy + 4*mm
        )

        p.lineTo(
            cx + 5*mm,
            cy - 3*mm
        )

        p.lineTo(
            cx,
            cy - 7*mm
        )

        p.lineTo(
            cx - 5*mm,
            cy - 3*mm
        )

        p.lineTo(
            cx - 6*mm,
            cy + 4*mm
        )

        p.close()

        c.drawPath(
            p,
            fill=0,
            stroke=1
        )

        c.line(
            cx - 2.5*mm,
            cy,
            cx - .5*mm,
            cy - 2*mm
        )

        c.line(
            cx - .5*mm,
            cy - 2*mm,
            cx + 3.5*mm,
            cy + 3*mm
        )

    elif kind == "recycle":

        for a in (
            90,
            210,
            330
        ):

            ang = math.radians(a)

            x1 = (
                cx
                +
                math.cos(ang)
                * 2*mm
            )

            y1 = (
                cy
                +
                math.sin(ang)
                * 2*mm
            )

            x2 = (
                cx
                +
                math.cos(ang)
                * 7*mm
            )

            y2 = (
                cy
                +
                math.sin(ang)
                * 7*mm
            )

            c.line(
                x1,
                y1,
                x2,
                y2
            )

            left = (
                ang
                +
                math.radians(145)
            )

            right = (
                ang
                -
                math.radians(145)
            )

            c.line(
                x2,
                y2,
                x2
                +
                math.cos(left)
                * 2.2*mm,
                y2
                +
                math.sin(left)
                * 2.2*mm
            )

            c.line(
                x2,
                y2,
                x2
                +
                math.cos(right)
                * 2.2*mm,
                y2
                +
                math.sin(right)
                * 2.2*mm
            )

    c.restoreState()


# =========================================================
# SERVICE CARD
# =========================================================

def service_card(
    c,
    x,
    y,
    w,
    h,
    num,
    title,
    text,
    icon_kind
):

    round_box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        6
    )

    ix = x + 6*mm
    iy = y + 5*mm

    iw = 17*mm
    ih = h - 10*mm

    c.setFillColor(
        PALE_GREEN
    )

    c.roundRect(
        ix,
        iy,
        iw,
        ih,
        4*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        LIGHT_GREEN
    )

    c.circle(
        ix + iw/2,
        iy + ih/2,
        6*mm,
        fill=1,
        stroke=0
    )

    draw_service_icon(
        c,
        ix + iw/2,
        iy + ih/2,
        icon_kind
    )

    pill(
        c,
        x + w - 16*mm,
        y + h - 8*mm,
        10*mm,
        4.8*mm,
        num,
        YELLOW,
        DARK,
        5.4
    )

    tx = x + 27*mm

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        9.3
    )

    c.drawString(
        tx,
        y + h - 9*mm,
        title
    )

    draw_wrapped_text(
        c,
        text,
        tx,
        y + h - 17*mm,
        w - 33*mm,
        "Helvetica",
        7.2,
        8.7,
        GREY,
        3
    )


# =========================================================
# PDF
# =========================================================

@app.post("/api/pdf")
def generate_pdf():

    d = request.get_json(
        force=True
    )

    try:

        values = calculate_values(
            d
        )

    except Exception as e:

        return jsonify({
            "error":
                str(e)
        }), 400

    address = str(
        d.get(
            "address",
            "Abitazione"
        )
    ).upper()

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

    aspect = float(
        d.get(
            "aspect",
            0
        )
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

    production = float(
        d.get(
            "production",
            0
        )
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

    monthly = d.get(
        "monthly",
        None
    )

    profile = d.get(
        "profile",
        "equilibrato"
    )

    # -----------------------------------------------------
    # RECUPERO DATI MENSILI
    # -----------------------------------------------------

    if (
        not isinstance(
            monthly,
            list
        )
        or
        len(monthly) != 12
    ):

        monthly = None

        try:

            if kwp > 0:

                coords = (
                    resolve_coordinates(d)
                )

                pv_params = {

                    "lat":
                        coords["lat"],

                    "lon":
                        coords["lon"],

                    "peakpower":
                        kwp,

                    "loss":
                        loss,

                    "angle":
                        angle,

                    "aspect":
                        aspect,

                    "usehorizon":
                        1,

                    "outputformat":
                        "json"

                }

                pv_data = pvgis(
                    pv_params
                )

                monthly = [

                    float(
                        item["E_m"]
                    )

                    for item
                    in
                    pv_data[
                        "outputs"
                    ][
                        "monthly"
                    ][
                        "fixed"
                    ]

                ]

        except Exception:

            monthly = None

    # -----------------------------------------------------
    # FALLBACK STAGIONALE
    # -----------------------------------------------------

    if (
        not isinstance(
            monthly,
            list
        )
        or
        len(monthly) != 12
    ):

        seasonal = [

            0.055,
            0.060,
            0.075,
            0.090,
            0.105,
            0.115,
            0.120,
            0.115,
            0.095,
            0.080,
            0.050,
            0.040

        ]

        total = sum(
            seasonal
        )

        monthly = [

            production
            * v
            / total

            for v
            in seasonal

        ]

    # -----------------------------------------------------
    # PROFILO
    # -----------------------------------------------------

    profile_labels = {

        "giorno":
            "Prevalenza diurna",

        "equilibrato":
            "Equilibrato",

        "sera":
            "Prevalenza serale"

    }

    profile_label = profile_labels.get(
        profile,
        "Equilibrato"
    )

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------

    buf = io.BytesIO()

    c = canvas.Canvas(
        buf,
        pagesize=A4
    )

    c.setTitle(
        "Analisi Fotovoltaica - Energia Giusta"
    )

    # =====================================================
    # PAGINA 1 - COPERTINA
    # =====================================================

    c.setFillColor(
        PALE_GREEN
    )

    c.rect(
        0,
        0,
        PAGE_W,
        PAGE_H,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        GREEN
    )

    c.rect(
        0,
        PAGE_H - 86*mm,
        PAGE_W,
        86*mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(
        DARK_GREEN
    )

    c.circle(
        PAGE_W + 12*mm,
        PAGE_H - 5*mm,
        55*mm,
        fill=1,
        stroke=0
    )

    draw_sun(
        c,
        PAGE_W - 40*mm,
        PAGE_H - 30*mm,
        40
    )

    c.setFillColor(
        WHITE
    )

    c.setFont(
        "Helvetica-Bold",
        25
    )

    c.drawString(
        18*mm,
        PAGE_H - 30*mm,
        "ENERGIA GIUSTA"
    )

    c.setFont(
        "Helvetica",
        8.5
    )

    c.drawString(
        18*mm,
        PAGE_H - 39*mm,
        "Partner ENI Plenitude  •  Soluzioni per l'energia"
    )

    c.setFillColor(
        DARK
    )

    c.setFont(
        "Helvetica-Bold",
        28
    )

    c.drawString(
        18*mm,
        PAGE_H - 108*mm,
        "Analisi Fotovoltaica"
    )

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        10.5
    )

    c.drawString(
        18*mm,
        PAGE_H - 120*mm,
        "Il tuo progetto, spiegato in modo semplice."
    )

    draw_pv_scene(
        c,
        101*mm,
        110*mm,
        92*mm,
        65*mm
    )

    round_box(
        c,
        18*mm,
        66*mm,
        PAGE_W - 36*mm,
        26*mm,
        WHITE,
        MID_GREY,
        8
    )

    pill(
        c,
        26*mm,
        83*mm,
        40*mm,
        6.5*mm,
        "ABITAZIONE",
        GREEN,
        WHITE,
        6.4
    )

    draw_wrapped_text(
        c,
        address,
        26*mm,
        75.5*mm,
        PAGE_W - 52*mm,
        "Helvetica-Bold",
        10,
        11.5,
        DARK,
        2
    )

    kpi(
        c,
        18*mm,
        32*mm,
        53*mm,
        26*mm,
        "Potenza FV",
        f"{decimal(kwp)} kWp",
        GREEN,
        "PV"
    )

    kpi(
        c,
        78*mm,
        32*mm,
        53*mm,
        26*mm,
        "Accumulo",
        f"{decimal(battery)} kWh",
        YELLOW,
        "B"
    )

    kpi(
        c,
        138*mm,
        32*mm,
        53*mm,
        26*mm,
        "Risparmio annuo",
        euro(
            values["annual_saving"]
        ),
        GREEN,
        "€"
    )

    draw_footer(
        c,
        1
    )

    c.showPage()

    # =====================================================
    # PAGINA 2 - IMPIANTO
    # =====================================================

    draw_header(
        c,
        "Il tuo impianto",
        "DATI TECNICI"
    )

    section_title(
        c,
        "Configurazione del sistema",
        "I parametri utilizzati per stimare produzione e prestazioni.",
        PAGE_H - 38*mm
    )

    cards = [

        (
            "Potenza fotovoltaica",
            f"{decimal(kwp)} kWp"
        ),

        (
            "Batteria di accumulo",
            f"{decimal(battery)} kWh"
        ),

        (
            "Consumo annuo",
            f"{number(consumption)} kWh"
        ),

        (
            "Produzione stimata",
            f"{number(production)} kWh"
        ),

        (
            "Inclinazione",
            f"{decimal(angle)}°"
        ),

        (
            "Perdite di sistema",
            f"{decimal(loss)}%"
        )

    ]

    for i, (
        lab,
        val
    ) in enumerate(cards):

        col = i % 3

        row = i // 3

        x = (
            18*mm
            +
            col * 59*mm
        )

        y = (
            PAGE_H
            - 72*mm
            -
            row * 29*mm
        )

        round_box(
            c,
            x,
            y,
            55*mm,
            23*mm,
            WHITE,
            MID_GREY,
            7
        )

        c.setFillColor(
            GREY
        )

        c.setFont(
            "Helvetica",
            7.3
        )

        c.drawString(
            x + 5*mm,
            y + 14*mm,
            lab
        )

        c.setFillColor(
            DARK
        )

        c.setFont(
            "Helvetica-Bold",
            14
        )

        c.drawString(
            x + 5*mm,
            y + 5.3*mm,
            val
        )

    monthly_chart(
        c,
        18*mm,
        53*mm,
        PAGE_W - 36*mm,
        94*mm,
        monthly
    )

    c.setFillColor(
        GREY
    )

    c.setFont(
        "Helvetica",
        7
    )

    c.drawString(
        18*mm,
        48.5*mm,
        "Produzione stimata tramite PVGIS 5.3, strumento ufficiale della Commissione Europea – Joint Research Centre (JRC)."
    )

    c.setFont(
        "Helvetica-Oblique",
        6.7
    )

    c.drawString(
        18*mm,
        43.8*mm,
        "La produzione reale può variare per condizioni meteo, ombreggiamenti e altre condizioni operative."
    )

    c.setFillColor(
        GREEN
    )

    c.setFont(
        "Helvetica-Bold",
        7
    )

    orient = {

        -90: "EST",
        0: "SUD",
        90: "OVEST"

    }.get(
        int(aspect),
        ""
    )

    c.drawCentredString(
        PAGE_W / 2,
        38.8*mm,
        f"ORIENTAMENTO: {orient}  •  INCLINAZIONE: {decimal(angle)}°"
    )

draw_footer(c, 7)        c,
        2
