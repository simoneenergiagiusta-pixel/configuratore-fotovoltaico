from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json, io, math

from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

app = Flask(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
GEOCODING_URL = "https://nominatim.openstreetmap.org/search"
PAGE_W, PAGE_H = A4
GEOCODE_CACHE = {}
GRID_PURCHASE_PCT = 0.12


# ============================================================
# ORIENTAMENTO
# ============================================================

def normalize_aspect(value):
    """
    Normalizza l'orientamento secondo la convenzione PVGIS:

    0°      = Sud
    -45°    = Sud-Est
    -90°    = Est
    -135°   = Nord-Est
    ±180°   = Nord
    +135°   = Nord-Ovest
    +90°    = Ovest
    +45°    = Sud-Ovest
    """

    try:
        aspect = float(value)
    except (TypeError, ValueError):
        aspect = 0.0

    aspect = max(-180.0, min(180.0, aspect))

    # Evita valori tipo -0.00001
    if abs(aspect) < 0.000001:
        aspect = 0.0

    return aspect


def aspect_label(value):
    """
    Restituisce una descrizione leggibile dell'orientamento.
    """

    aspect = normalize_aspect(value)

    directions = {
        -180.0: "Nord",
        -135.0: "Nord-Est",
        -90.0: "Est",
        -45.0: "Sud-Est",
        0.0: "Sud",
        45.0: "Sud-Ovest",
        90.0: "Ovest",
        135.0: "Nord-Ovest",
        180.0: "Nord"
    }

    # Controllo valori standard
    for degrees, label in directions.items():
        if abs(aspect - degrees) < 0.000001:
            return f"{label} ({int(degrees)}°)"

    # Valore personalizzato
    if aspect > 0:
        return f"Personalizzato (+{aspect:g}°)"

    return f"Personalizzato ({aspect:g}°)"


# ============================================================
# WEB / PVGIS
# ============================================================

def pvgis(params):
    req = Request(
        PVGIS_URL + "?" + urlencode(params),
        headers={
            "User-Agent": "ConfiguratoreFotovoltaico/1.0"
        }
    )

    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode_address(address):
    address = " ".join(str(address or "").strip().split())

    if not address:
        raise ValueError("Inserisci un indirizzo completo.")

    key = address.lower()

    if key in GEOCODE_CACHE:
        return GEOCODE_CACHE[key]

    params = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "it",
        "addressdetails": 1,
        "accept-language": "it"
    }

    req = Request(
        GEOCODING_URL + "?" + urlencode(params),
        headers={
            "User-Agent": "ConfiguratoreFotovoltaico/1.0 (Energia Giusta)"
        }
    )

    with urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))

    if not data:
        raise ValueError(
            "Indirizzo non trovato. Inserisci via, numero civico e comune."
        )

    out = {
        "lat": float(data[0]["lat"]),
        "lon": float(data[0]["lon"]),
        "display_name": data[0].get("display_name", address)
    }

    GEOCODE_CACHE[key] = out

    return out


def resolve_coordinates(d):
    if str(d.get("lat", "")).strip() and str(d.get("lon", "")).strip():
        return {
            "lat": float(d["lat"]),
            "lon": float(d["lon"])
        }

    return geocode_address(d.get("address", ""))


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/geocode")
def api_geocode():
    try:
        r = geocode_address(
            (request.get_json(force=True) or {}).get("address", "")
        )

        return jsonify({
            "success": True,
            **r
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


@app.post("/api/pvgis")
def api_pvgis():
    d = request.get_json(force=True) or {}

    try:
        co = resolve_coordinates(d)

        aspect = normalize_aspect(d.get("aspect", 0))

        data = pvgis({
            "lat": co["lat"],
            "lon": co["lon"],
            "peakpower": float(d["kwp"]),
            "loss": float(d.get("loss", 14)),
            "angle": float(d.get("angle", 30)),
            "aspect": aspect,
            "usehorizon": 1,
            "outputformat": "json"
        })

        fixed = data["outputs"]["monthly"]["fixed"]

        return jsonify({
            "annual_kwh": data["outputs"]["totals"]["fixed"]["E_y"],
            "monthly_kwh": [
                x["E_m"] for x in fixed
            ],
            "aspect": aspect,
            "aspect_label": aspect_label(aspect),
            "raw": data
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


# ============================================================
# CALCOLO
# ============================================================

def calculate_values(d):
    production = float(d["production"])
    consumption = float(d["consumption"])

    price = float(
        d.get("energy_price", .25)
    )

    export_price = float(
        d.get("export_price", .10)
    )

    cost = float(d["cost"])

    deduction = float(
        d.get("deduction", 0)
    )

    target_grid = consumption * GRID_PURCHASE_PCT

    self_used = min(
        production,
        consumption - target_grid
    )

    grid = max(
        target_grid,
        consumption - self_used
    )

    export = max(
        0,
        production - self_used
    )

    self_pct = (
        min(.95, self_used / production)
        if production > 0
        else 0
    )

    annual_saving = (
        self_used * price
        + export * export_price
    )

    net_cost = max(
        0,
        cost - deduction
    )

    years = []
    cumulative = 0

    payback = None
    payback_years = None

    for y in range(1, 26):

        prod_y = production * (
            (1 - .005) ** (y - 1)
        )

        self_y = min(
            prod_y,
            consumption * (1 - GRID_PURCHASE_PCT)
        )

        grid_y = max(
            consumption * GRID_PURCHASE_PCT,
            consumption - self_y
        )

        export_y = max(
            0,
            prod_y - self_y
        )

        price_y = price * (
            (1 + .02) ** (y - 1)
        )

        benefit = (
            self_y * price_y
            + export_y * export_price
        )

        prev = cumulative

        cumulative += benefit

        if payback is None and cumulative >= net_cost:

            payback = y

            payback_years = (
                (y - 1)
                + max(
                    0,
                    min(
                        1,
                        (
                            (net_cost - prev) / benefit
                            if benefit
                            else 0
                        )
                    )
                )
            )

        years.append({
            "year": y,
            "benefit": benefit,
            "cumulative": cumulative,
            "production": prod_y,
            "self_used": self_y,
            "grid_purchase": grid_y,
            "export": export_y,
            "energy_price": price_y
        })

    return {
        "self_used": self_used,
        "export": export,
        "grid_purchase": grid,
        "grid_purchase_pct": (
            grid / consumption * 100
            if consumption
            else 0
        ),
        "self_consumption_pct": self_pct * 100,
        "annual_saving": annual_saving,
        "net_cost": net_cost,
        "payback": payback,
        "payback_years": payback_years,
        "years": years,
        "gross_25": years[-1]["cumulative"],
        "net_25": max(
            0,
            years[-1]["cumulative"] - net_cost
        )
    }


@app.post("/api/calculate")
def calculate():
    d = request.get_json(force=True) or {}

    try:
        result = calculate_values(d)

        monthly = d.get("monthly")

        if isinstance(monthly, list) and len(monthly) == 12:
            result["monthly_kwh"] = monthly
        else:
            result["monthly_kwh"] = []

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 400


# ============================================================
# PDF GRAFICA
# ============================================================

GREEN = colors.HexColor("#168447")
DARK_GREEN = colors.HexColor("#0B5D32")
MID_GREEN = colors.HexColor("#39B96B")
LIGHT_GREEN = colors.HexColor("#E9F8EF")
PALE_GREEN = colors.HexColor("#F5FBF7")
YELLOW = colors.HexColor("#F5C518")
LIGHT_YELLOW = colors.HexColor("#FFF8D9")
DARK = colors.HexColor("#172033")
GREY = colors.HexColor("#64748B")
LIGHT_GREY = colors.HexColor("#F5F7FA")
MID_GREY = colors.HexColor("#D8E0E8")
WHITE = colors.white


def euro(v):
    return "€ " + f"{v:,.0f}".replace(",", ".")


def number(v):
    return f"{v:,.0f}".replace(",", ".")


def decimal(v):
    return f"{v:.2f}".replace(".", ",")


def wrap_text(
    text,
    font="Helvetica",
    size=8,
    max_width=50 * mm
):
    lines = []
    cur = ""

    for word in str(text).split():

        test = (
            word
            if not cur
            else cur + " " + word
        )

        if stringWidth(
            test,
            font,
            size
        ) <= max_width:

            cur = test

        else:

            if cur:
                lines.append(cur)

            cur = word

    if cur:
        lines.append(cur)

    return lines


def draw_wrapped(
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
        lines = lines[:max_lines]

    c.setFillColor(color)
    c.setFont(font, size)

    for i, line in enumerate(lines):

        yy = y - i * leading

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

    return len(lines) * leading


def box(
    c,
    x,
    y,
    w,
    h,
    fill=WHITE,
    stroke=None,
    r=7
):
    c.setFillColor(fill)
    c.setStrokeColor(
        stroke or fill
    )

    c.setLineWidth(.7)

    c.roundRect(
        x,
        y,
        w,
        h,
        r,
        fill=1,
        stroke=1 if stroke else 0
    )


def pill(
    c,
    x,
    y,
    w,
    h,
    text,
    fill=GREEN,
    tc=WHITE,
    size=7
):
    c.setFillColor(fill)

    c.roundRect(
        x,
        y,
        w,
        h,
        h / 2,
        fill=1,
        stroke=0
    )

    c.setFillColor(tc)
    c.setFont(
        "Helvetica-Bold",
        size
    )

    c.drawCentredString(
        x + w / 2,
        y + h / 2 - size * .36,
        text
    )


def sun(c, x, y, s=28):

    c.setFillColor(YELLOW)
    c.circle(
        x,
        y,
        s * .27,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(YELLOW)
    c.setLineWidth(1.5)

    for i in range(8):

        a = math.radians(i * 45)

        c.line(
            x + math.cos(a) * s * .42,
            y + math.sin(a) * s * .42,
            x + math.cos(a) * s * .76,
            y + math.sin(a) * s * .76
        )


def pv_scene(c, x, y, w, h):

    box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        None,
        9
    )

    c.setFillColor(PALE_GREEN)

    c.roundRect(
        x + 2 * mm,
        y + 2 * mm,
        w - 4 * mm,
        h - 4 * mm,
        6 * mm,
        fill=1,
        stroke=0
    )

    sun(
        c,
        x + w - 15 * mm,
        y + h - 12 * mm,
        25
    )

    c.setFillColor(
        colors.HexColor("#DCEFE4")
    )

    c.ellipse(
        x + 6 * mm,
        y + 5 * mm,
        x + w - 6 * mm,
        y + 18 * mm,
        fill=1,
        stroke=0
    )

    hx = x + 12 * mm
    hy = y + 10 * mm
    hw = 53 * mm
    hh = 27 * mm

    c.setFillColor(WHITE)

    c.roundRect(
        hx,
        hy,
        hw,
        hh,
        1.7 * mm,
        fill=1,
        stroke=0
    )

    p = c.beginPath()

    p.moveTo(
        hx - 5 * mm,
        hy + hh
    )

    p.lineTo(
        hx + hw / 2,
        hy + hh + 18 * mm
    )

    p.lineTo(
        hx + hw + 5 * mm,
        hy + hh
    )

    p.close()

    c.setFillColor(DARK_GREEN)
    c.drawPath(
        p,
        fill=1,
        stroke=0
    )

    c.saveState()

    c.translate(
        hx + 9 * mm,
        hy + hh + 3.5 * mm
    )

    c.rotate(18)

    c.setFillColor(
        colors.HexColor("#183B5A")
    )

    c.roundRect(
        0,
        0,
        36 * mm,
        11.5 * mm,
        1.2 * mm,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(
        colors.HexColor("#7895A9")
    )

    c.setLineWidth(.3)

    for xx in [
        9 * mm,
        18 * mm,
        27 * mm
    ]:
        c.line(
            xx,
            0,
            xx,
            11.5 * mm
        )

    for yy in [
        11.5 * mm / 3,
        23 * mm / 3
    ]:
        c.line(
            0,
            yy,
            36 * mm,
            yy
        )

    c.restoreState()

    for wx in [
        hx + 6.5 * mm,
        hx + 23 * mm
    ]:
        c.setFillColor(
            colors.HexColor("#BFE5E6")
        )

        c.roundRect(
            wx,
            hy + 14 * mm,
            10.5 * mm,
            7.5 * mm,
            .7 * mm,
            fill=1,
            stroke=0
        )

    c.setFillColor(DARK_GREEN)

    c.roundRect(
        hx + 39 * mm,
        hy,
        8.5 * mm,
        17 * mm,
        1 * mm,
        fill=1,
        stroke=0
    )

    bx = x + w - 28 * mm
    by = y + 8 * mm
    bw = 14 * mm
    bh = 25 * mm

    c.setFillColor(WHITE)

    c.roundRect(
        bx,
        by,
        bw,
        bh,
        2 * mm,
        fill=1,
        stroke=0
    )

    c.setStrokeColor(
        colors.HexColor("#C4D2CB")
    )

    c.roundRect(
        bx,
        by,
        bw,
        bh,
        2 * mm,
        fill=0,
        stroke=1
    )

    c.setFillColor(GREEN)

    c.roundRect(
        bx + 2.5 * mm,
        by + 5 * mm,
        bw - 5 * mm,
        14 * mm,
        1.2 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)
    c.setFont(
        "Helvetica-Bold",
        4.2
    )

    c.drawCentredString(
        bx + bw / 2,
        by + 10.7 * mm,
        "ENERGY"
    )


def header(c, title, subtitle=""):

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

    c.setFont(
        "Helvetica-Bold",
        19.5
    )

    c.drawString(
        18 * mm,
        PAGE_H - 13.5 * mm,
        title
    )

    if subtitle:

        c.setFont(
            "Helvetica",
            9
        )

        c.drawRightString(
            PAGE_W - 18 * mm,
            PAGE_H - 13.5 * mm,
            subtitle
        )


def footer(c, page):

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(.4)

    c.line(
        15 * mm,
        11.5 * mm,
        PAGE_W - 15 * mm,
        11.5 * mm
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7.1
    )

    c.drawString(
        15 * mm,
        6.8 * mm,
        "ENERGIA GIUSTA  •  Partner ENI Plenitude"
    )

    c.drawRightString(
        PAGE_W - 15 * mm,
        6.8 * mm,
        f"{page:02d}"
    )


def section(c, title, sub, y):

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        22
    )

    c.drawString(
        18 * mm,
        y,
        title
    )

    draw_wrapped(
        c,
        sub,
        18 * mm,
        y - 8 * mm,
        PAGE_W - 36 * mm,
        "Helvetica",
        9.5,
        11.5,
        GREY,
        2
    )


def kpi(
    c,
    x,
    y,
    w,
    h,
    label,
    value,
    accent=GREEN,
    icon=""
):

    box(
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

    c.roundRect(
        x,
        y + h - 10 * mm,
        w,
        10 * mm,
        7,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        8
    )

    c.drawString(
        x + 5 * mm,
        y + h - 6.6 * mm,
        (icon + "  " + label).upper()
    )

    fs = 18

    while (
        fs > 10
        and stringWidth(
            value,
            "Helvetica-Bold",
            fs
        ) > w - 10 * mm
    ):
        fs -= 1

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        fs
    )

    c.drawCentredString(
        x + w / 2,
        y + 8 * mm,
        value
    )


def monthly_chart(
    c,
    x,
    y,
    w,
    h,
    monthly
):

    vals = [
        float(
            v.get("E_m", 0)
            if isinstance(v, dict)
            else v
        )
        for v in (monthly or [])[:12]
    ]

    vals += [0] * (12 - len(vals))

    labels = [
        "GEN", "FEB", "MAR", "APR",
        "MAG", "GIU", "LUG", "AGO",
        "SET", "OTT", "NOV", "DIC"
    ]

    box(
        c,
        x,
        y,
        w,
        h,
        LIGHT_GREY,
        None,
        5
    )

    c.setFillColor(DARK)
    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        x + 7 * mm,
        y + h - 10 * mm,
        "Produzione fotovoltaica mensile"
    )

    cx = x + 9 * mm
    cy = y + 14 * mm
    cw = w - 18 * mm
    ch = h - 34 * mm
    mx = max(vals + [1])
    slot = cw / 12
    bw = slot * .58

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(.35)

    for i in range(5):

        c.line(
            cx,
            cy + ch * i / 4,
            cx + cw,
            cy + ch * i / 4
        )

    for i, (lab, v) in enumerate(
        zip(labels, vals)
    ):

        bx = (
            cx
            + i * slot
            + (slot - bw) / 2
        )

        bh = ch * v / mx

        c.setFillColor(GREEN)

        c.roundRect(
            bx,
            cy,
            bw,
            max(
                bh,
                .8 * mm
            ),
            1.2 * mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(GREY)
        c.setFont(
            "Helvetica",
            6.8
        )

        c.drawCentredString(
            bx + bw / 2,
            cy - 4.5 * mm,
            lab
        )

        if v:

            c.setFillColor(DARK)
            c.setFont(
                "Helvetica",
                5.7
            )

            c.drawCentredString(
                bx + bw / 2,
                cy + bh + 2 * mm,
                number(v)
            )


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

    a = 360 * self_used / total

    c.setFillColor(GREEN)

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

    c.setFillColor(YELLOW)

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

    c.setFillColor(WHITE)

    c.circle(
        cx,
        cy,
        r * .58,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK)

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


def economic_chart(
    c,
    x,
    y,
    w,
    h,
    years,
    net_cost,
    payback_years
):

    box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        10.5
    )

    c.drawString(
        x + 8 * mm,
        y + h - 10 * mm,
        "Beneficio cumulato e punto di pareggio"
    )

    vals = [
        z["cumulative"]
        for z in years
    ]

    mx = max(
        vals + [net_cost, 1]
    )

    left = x + 13 * mm
    bottom = y + 16 * mm
    cw = w - 20 * mm
    ch = h - 34 * mm

    c.setStrokeColor(MID_GREY)
    c.setLineWidth(.35)

    for i in range(4):

        c.line(
            left,
            bottom + ch * i / 3,
            left + cw,
            bottom + ch * i / 3
        )

    pts = []

    for i, v in enumerate(vals):

        px = left + cw * i / 24
        py = bottom + ch * v / mx

        pts.append(
            (px, py)
        )

    c.setStrokeColor(GREEN)
    c.setLineWidth(2.2)

    for a, b in zip(
        pts,
        pts[1:]
    ):

        c.line(
            a[0],
            a[1],
            b[0],
            b[1]
        )

    if net_cost:

        by = (
            bottom
            + ch * min(
                net_cost,
                mx
            ) / mx
        )

        c.saveState()

        c.setStrokeColor(YELLOW)
        c.setDash(5, 3)

        c.line(
            left,
            by,
            left + cw,
            by
        )

        c.restoreState()

        c.setFillColor(DARK)
        c.setFont(
            "Helvetica-Bold",
            6.3
        )

        c.drawString(
            left + 3 * mm,
            by + 2 * mm,
            "INVESTIMENTO NETTO "
            + euro(net_cost)
        )

    if (
        payback_years is not None
        and payback_years <= 25
        and net_cost
    ):

        px = (
            left
            + cw * payback_years / 24
        )

        py = (
            bottom
            + ch * net_cost / mx
        )

        c.setFillColor(YELLOW)

        c.circle(
            px,
            py,
            3.5,
            fill=1,
            stroke=0
        )

        c.setFillColor(DARK_GREEN)

        c.circle(
            px,
            py,
            1.7,
            fill=1,
            stroke=0
        )

        c.setFont(
            "Helvetica-Bold",
            6.5
        )

        c.drawString(
            min(
                px + 4 * mm,
                left + cw - 35 * mm
            ),
            min(
                py + 5 * mm,
                bottom + ch - 5 * mm
            ),
            "Rientro"
        )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        6
    )

    for i in [
        0, 4, 9, 14, 19, 24
    ]:

        c.drawCentredString(
            pts[i][0],
            bottom - 8,
            str(i + 1)
        )

    c.drawCentredString(
        left + cw / 2,
        bottom - 12 * mm,
        "anni"
    )


def service_card(
    c,
    x,
    y,
    w,
    h,
    title,
    text
):

    box(
        c,
        x,
        y,
        w,
        h,
        WHITE,
        MID_GREY,
        6
    )

    c.setFillColor(LIGHT_GREEN)

    c.circle(
        x + 11 * mm,
        y + h / 2,
        7 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK_GREEN)

    c.setFont(
        "Helvetica-Bold",
        13
    )

    c.drawCentredString(
        x + 11 * mm,
        y + h / 2 - 4,
        "✓"
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9.2
    )

    c.drawString(
        x + 23 * mm,
        y + h - 10 * mm,
        title
    )

    draw_wrapped(
        c,
        text,
        x + 23 * mm,
        y + h - 18 * mm,
        w - 29 * mm,
        "Helvetica",
        7.3,
        9,
        GREY,
        3
    )


# ============================================================
# PDF
# ============================================================

@app.post("/api/pdf")
def generate_pdf():

    d = request.get_json(force=True) or {}

    try:
        values = calculate_values(d)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 400

    address = str(
        d.get(
            "address",
            "ABITAZIONE"
        )
    ).upper()

    kwp = float(
        d.get("kwp", 0)
    )

    battery = float(
        d.get("battery", 0)
    )

    angle = float(
        d.get("angle", 30)
    )

    aspect = normalize_aspect(
        d.get("aspect", 0)
    )

    loss = float(
        d.get("loss", 14)
    )

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

    monthly = (
        d.get("monthly")
        if isinstance(
            d.get("monthly"),
            list
        )
        and len(d.get("monthly")) == 12
        else []
    )

    profile = {
        "giorno": "Prevalenza diurna",
        "equilibrato": "Equilibrato",
        "sera": "Prevalenza serale"
    }.get(
        d.get("profile"),
        "Equilibrato"
    )

    if not monthly:

        seasonal = [
            .055, .060, .075, .090,
            .105, .115, .120, .115,
            .095, .080, .050, .040
        ]

        s = sum(seasonal)

        monthly = [
            production * v / s
            for v in seasonal
        ]

    buf = io.BytesIO()

    c = canvas.Canvas(
        buf,
        pagesize=A4
    )

    c.setTitle(
        "Analisi Fotovoltaica - Energia Giusta"
    )

    # ========================================================
    # PAGE 1
    # ========================================================

    c.setFillColor(PALE_GREEN)

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
        PAGE_H - 86 * mm,
        PAGE_W,
        86 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK_GREEN)

    c.circle(
        PAGE_W + 12 * mm,
        PAGE_H - 5 * mm,
        55 * mm,
        fill=1,
        stroke=0
    )

    sun(
        c,
        PAGE_W - 40 * mm,
        PAGE_H - 30 * mm,
        42
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        25
    )

    c.drawString(
        18 * mm,
        PAGE_H - 30 * mm,
        "ENERGIA GIUSTA"
    )

    c.setFont(
        "Helvetica",
        8.5
    )

    c.drawString(
        18 * mm,
        PAGE_H - 39 * mm,
        "Partner ENI Plenitude  •  Soluzioni per l'energia"
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        28
    )

    c.drawString(
        18 * mm,
        PAGE_H - 108 * mm,
        "Analisi Fotovoltaica"
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        10.5
    )

    c.drawString(
        18 * mm,
        PAGE_H - 120 * mm,
        "Il tuo progetto, spiegato in modo semplice."
    )

    pv_scene(
        c,
        101 * mm,
        110 * mm,
        92 * mm,
        65 * mm
    )

    box(
        c,
        18 * mm,
        66 * mm,
        PAGE_W - 36 * mm,
        26 * mm,
        WHITE,
        MID_GREY,
        8
    )

    pill(
        c,
        26 * mm,
        83 * mm,
        40 * mm,
        6.5 * mm,
        "ABITAZIONE"
    )

    draw_wrapped(
        c,
        address,
        26 * mm,
        75.5 * mm,
        PAGE_W - 52 * mm,
        "Helvetica-Bold",
        10,
        11.5,
        DARK,
        2
    )

    kpi(
        c,
        18 * mm,
        32 * mm,
        53 * mm,
        26 * mm,
        "Potenza FV",
        f"{decimal(kwp)} kWp",
        GREEN,
        "PV"
    )

    kpi(
        c,
        78 * mm,
        32 * mm,
        53 * mm,
        26 * mm,
        "Accumulo",
        f"{decimal(battery)} kWh",
        YELLOW,
        "B"
    )

    kpi(
        c,
        138 * mm,
        32 * mm,
        53 * mm,
        26 * mm,
        "Risparmio annuo",
        euro(values["annual_saving"]),
        GREEN,
        "€"
    )

    footer(c, 1)

    c.showPage()

    # ========================================================
    # PAGE 2
    # ========================================================

    header(
        c,
        "Il tuo impianto",
        "DATI TECNICI"
    )

    section(
        c,
        "Configurazione del sistema",
        "I parametri utilizzati per stimare produzione e prestazioni.",
        PAGE_H - 38 * mm
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

    for i, (lab, val) in enumerate(cards):

        col = i % 3
        row = i // 3

        x = (
            18 * mm
            + col * 59 * mm
        )

        y = (
            PAGE_H
            - 72 * mm
            - row * 29 * mm
        )

        box(
            c,
            x,
            y,
            55 * mm,
            23 * mm,
            WHITE,
            MID_GREY,
            7
        )

        c.setFillColor(GREY)

        c.setFont(
            "Helvetica",
            7.3
        )

        c.drawString(
            x + 5 * mm,
            y + 14 * mm,
            lab
        )

        c.setFillColor(DARK)

        c.setFont(
            "Helvetica-Bold",
            14
        )

        c.drawString(
            x + 5 * mm,
            y + 5.3 * mm,
            val
        )

    monthly_chart(
        c,
        18 * mm,
        53 * mm,
        PAGE_W - 36 * mm,
        94 * mm,
        monthly
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7
    )

    c.drawString(
        18 * mm,
        48.5 * mm,
        "Produzione stimata tramite PVGIS 5.3, strumento della Commissione Europea – Joint Research Centre (JRC)."
    )

    c.setFont(
        "Helvetica-Bold",
        7
    )

    c.setFillColor(GREEN)

    c.drawCentredString(
        PAGE_W / 2,
        39 * mm,
        f"ORIENTAMENTO: {aspect_label(aspect).upper()}  •  "
        f"INCLINAZIONE: {decimal(angle)}°  •  "
        f"PROFILO: {profile}"
    )

    footer(c, 2)

    c.showPage()

    # ========================================================
    # PAGE 3
    # ========================================================

    header(
        c,
        "Come viene utilizzata l'energia",
        "AUTOCONSUMO"
    )

    section(
        c,
        "Autoconsumo e accumulo",
        "La simulazione utilizza un'ipotesi prudenziale di energia acquistata dalla rete.",
        PAGE_H - 38 * mm
    )

    donut(
        c,
        70 * mm,
        145 * mm,
        35 * mm,
        values["self_used"],
        values["export"]
    )

    box(
        c,
        112 * mm,
        123 * mm,
        80 * mm,
        45 * mm,
        WHITE,
        MID_GREY,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        10
    )

    c.drawString(
        120 * mm,
        158 * mm,
        "Ripartizione produzione"
    )

    c.setFillColor(GREEN)

    c.setFont(
        "Helvetica-Bold",
        12
    )

    c.drawString(
        120 * mm,
        147 * mm,
        "Autoconsumo"
    )

    c.setFillColor(DARK)

    c.drawRightString(
        184 * mm,
        147 * mm,
        number(values["self_used"]) + " kWh"
    )

    c.setFillColor(YELLOW)

    c.setFont(
        "Helvetica-Bold",
        12
    )

    c.drawString(
        120 * mm,
        137 * mm,
        "Energia immessa"
    )

    c.setFillColor(DARK)

    c.drawRightString(
        184 * mm,
        137 * mm,
        number(values["export"]) + " kWh"
    )

    box(
        c,
        18 * mm,
        70 * mm,
        PAGE_W - 36 * mm,
        38 * mm,
        LIGHT_YELLOW,
        None,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        11
    )

    c.drawString(
        26 * mm,
        96 * mm,
        "Ipotesi prudenziale"
    )

    draw_wrapped(
        c,
        f"La simulazione mantiene circa il {GRID_PURCHASE_PCT * 100:.0f}% del consumo annuo come acquisto dalla rete. Questo evita di sovrastimare l'autoconsumo e tiene conto del fatto che produzione e consumi non coincidono sempre.",
        26 * mm,
        88 * mm,
        PAGE_W - 52 * mm,
        "Helvetica",
        8.2,
        10,
        GREY,
        3
    )

    kpi(
        c,
        18 * mm,
        32 * mm,
        53 * mm,
        27 * mm,
        "Autoconsumo",
        f"{values['self_consumption_pct']:.0f}%",
        GREEN,
        ""
    )

    kpi(
        c,
        78 * mm,
        32 * mm,
        53 * mm,
        27 * mm,
        "Acquisto rete",
        f"{values['grid_purchase_pct']:.0f}%",
        YELLOW,
        ""
    )

    kpi(
        c,
        138 * mm,
        32 * mm,
        53 * mm,
        27 * mm,
        "Energia immessa",
        number(values["export"]) + " kWh",
        GREEN,
        ""
    )

    footer(c, 3)

    c.showPage()

    # ========================================================
    # PAGE 4
    # ========================================================

    header(
        c,
        "Il risultato economico",
        "ROI"
    )

    section(
        c,
        "Investimento e rientro",
        "La stima considera il costo netto dopo la detrazione indicata.",
        PAGE_H - 38 * mm
    )

    kpi(
        c,
        18 * mm,
        206 * mm,
        53 * mm,
        27 * mm,
        "Investimento netto",
        euro(values["net_cost"]),
        GREEN,
        "€"
    )

    kpi(
        c,
        78 * mm,
        206 * mm,
        53 * mm,
        27 * mm,
        "Risparmio annuo",
        euro(values["annual_saving"]),
        YELLOW,
        "€"
    )

    pb = (
        "n.d."
        if values["payback_years"] is None
        else f"{values['payback_years']:.1f} anni"
    )

    kpi(
        c,
        138 * mm,
        206 * mm,
        53 * mm,
        27 * mm,
        "Rientro stimato",
        pb,
        GREEN,
        ""
    )

    economic_chart(
        c,
        18 * mm,
        72 * mm,
        PAGE_W - 36 * mm,
        120 * mm,
        values["years"],
        values["net_cost"],
        values["payback_years"]
    )

    c.setFillColor(GREY)

    c.setFont(
        "Helvetica",
        7.2
    )

    c.drawString(
        18 * mm,
        57 * mm,
        "Il risultato è una simulazione commerciale: tariffe, consumi reali, ombreggiamenti e condizioni di ritiro possono modificarlo."
    )

    footer(c, 4)

    c.showPage()

    # ========================================================
    # PAGE 5
    # ========================================================

    header(
        c,
        "Proiezione nel tempo",
        "25 ANNI"
    )

    section(
        c,
        "Beneficio economico cumulato",
        "Produzione -0,5% annuo; prezzo dell'energia acquistata +2% annuo nella simulazione.",
        PAGE_H - 38 * mm
    )

    box(
        c,
        18 * mm,
        172 * mm,
        PAGE_W - 36 * mm,
        24 * mm,
        LIGHT_GREEN,
        None,
        8
    )

    c.setFillColor(DARK_GREEN)

    c.setFont(
        "Helvetica-Bold",
        11
    )

    c.drawString(
        26 * mm,
        185 * mm,
        "Beneficio cumulato a 25 anni"
    )

    c.setFont(
        "Helvetica-Bold",
        18
    )

    c.drawRightString(
        PAGE_W - 26 * mm,
        183.5 * mm,
        euro(values["gross_25"])
    )

    milestones = [
        1, 5, 10, 15, 20, 25
    ]

    y = 150 * mm

    c.setFillColor(GREEN)

    c.rect(
        18 * mm,
        y,
        PAGE_W - 36 * mm,
        9 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        7.5
    )

    c.drawString(
        23 * mm,
        y + 3 * mm,
        "ANNO"
    )

    c.drawString(
        48 * mm,
        y + 3 * mm,
        "BENEFICIO ANNUO"
    )

    c.drawString(
        105 * mm,
        y + 3 * mm,
        "CUMULATO"
    )

    for j, yr in enumerate(milestones):

        item = values["years"][yr - 1]

        yy = y - (j + 1) * 13 * mm

        c.setFillColor(
            WHITE
            if j % 2 == 0
            else LIGHT_GREY
        )

        c.rect(
            18 * mm,
            yy,
            PAGE_W - 36 * mm,
            13 * mm,
            fill=1,
            stroke=0
        )

        c.setFillColor(DARK)

        c.setFont(
            "Helvetica-Bold",
            8
        )

        c.drawString(
            23 * mm,
            yy + 4.5 * mm,
            f"{yr}° anno"
        )

        c.drawString(
            48 * mm,
            yy + 4.5 * mm,
            euro(item["benefit"])
        )

        c.drawString(
            105 * mm,
            yy + 4.5 * mm,
            euro(item["cumulative"])
        )

    box(
        c,
        18 * mm,
        35 * mm,
        PAGE_W - 36 * mm,
        28 * mm,
        LIGHT_YELLOW,
        None,
        8
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        9.5
    )

    c.drawString(
        26 * mm,
        53 * mm,
        "Nota sulla proiezione"
    )

    draw_wrapped(
        c,
        "La proiezione non costituisce una previsione finanziaria. Serve a visualizzare l'effetto di produzione, autoconsumo e variazione ipotizzata del prezzo dell'energia nel tempo.",
        26 * mm,
        45 * mm,
        PAGE_W - 52 * mm,
        "Helvetica",
        7.6,
        9,
        GREY,
        2
    )

    footer(c, 5)

    c.showPage()

    # ========================================================
    # PAGE 6
    # ========================================================

    header(
        c,
        "Cosa include l'offerta",
        "SERVIZI"
    )

    section(
        c,
        "Un progetto seguito dall'inizio alla fine",
        "Le principali attività comprese nella gestione della soluzione fotovoltaica.",
        PAGE_H - 38 * mm
    )

    services = [
        (
            "Sopralluogo",
            "Verifica tecnica dell'immobile, degli spazi disponibili e delle condizioni di installazione."
        ),
        (
            "Progettazione",
            "Dimensionamento del sistema in funzione dei consumi e delle caratteristiche dell'abitazione."
        ),
        (
            "Installazione",
            "Installatori specializzati e gestione delle attività necessarie alla messa in servizio."
        ),
        (
            "Pratiche GSE",
            "Supporto nella gestione delle pratiche relative all'energia immessa."
        ),
        (
            "Detrazione fiscale",
            "Supporto documentale per la pratica collegata alla detrazione indicata nell'offerta."
        ),
        (
            "Monitoraggio",
            "App e strumenti di monitoraggio per controllare produzione e funzionamento."
        ),
        (
            "Garanzie",
            "Pannelli: garanzia prodotto 25 anni e garanzia di potenza 30 anni. Inverter 12 anni; batterie 11 anni."
        ),
        (
            "Fine vita",
            "Gestione del corretto conferimento a fine vita di moduli fotovoltaici e inverter."
        )
    ]

    for i, (t, txt) in enumerate(services):

        col = i % 2
        row = i // 2

        service_card(
            c,
            18 * mm + 91 * mm * col,
            184 * mm - row * 34 * mm,
            84 * mm,
            28 * mm,
            t,
            txt
        )

    footer(c, 6)

    c.showPage()

    # ========================================================
    # PAGE 7
    # ========================================================

    c.setFillColor(PALE_GREEN)

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
        PAGE_H - 72 * mm,
        PAGE_W,
        72 * mm,
        fill=1,
        stroke=0
    )

    c.setFillColor(DARK_GREEN)

    c.circle(
        PAGE_W - 5 * mm,
        PAGE_H - 2 * mm,
        43 * mm,
        fill=1,
        stroke=0
    )

    sun(
        c,
        PAGE_W - 37 * mm,
        PAGE_H - 27 * mm,
        38
    )

    c.setFillColor(WHITE)

    c.setFont(
        "Helvetica-Bold",
        24
    )

    c.drawString(
        18 * mm,
        PAGE_H - 28 * mm,
        "Parliamone insieme"
    )

    c.setFont(
        "Helvetica",
        9.5
    )

    c.drawString(
        18 * mm,
        PAGE_H - 38 * mm,
        "Per informazioni sul progetto e per approfondire la soluzione proposta."
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        25
    )

    c.drawString(
        18 * mm,
        196 * mm,
        "Simone Alfarano"
    )

    c.setFillColor(GREEN)

    c.setFont(
        "Helvetica-Bold",
        11
    )

    c.drawString(
        18 * mm,
        187 * mm,
        "RESPONSABILE COMMERCIALE"
    )

    pill(
        c,
        18 * mm,
        171 * mm,
        72 * mm,
        7 * mm,
        "ENERGIA GIUSTA",
        GREEN,
        WHITE,
        7
    )

    c.setFillColor(DARK)

    c.setFont(
        "Helvetica-Bold",
        12
    )

    c.drawString(
        18 * mm,
        151 * mm,
        "Contatti"
    )

    rows = [
        (
            "Telefono / WhatsApp",
            "351.7478652"
        ),
        (
            "Email",
            "simone.alfarano@energiagiusta.it"
        ),
        (
            "Web",
            "www.energiagiusta.it"
        )
    ]

    for i, (lab, val) in enumerate(rows):

        yy = 137 * mm - i * 18 * mm

        box(
            c,
            18 * mm,
            yy - 5 * mm,
            PAGE_W - 36 * mm,
            13 * mm,
            WHITE,
            MID_GREY,
            6
        )

        c.setFillColor(GREY)

        c.setFont(
            "Helvetica",
            7
        )

        c.drawString(
            26 * mm,
            yy + 1 * mm,
            lab.upper()
        )

        c.setFillColor(DARK_GREEN)

        c.setFont(
            "Helvetica-Bold",
            10
        )

        c.drawString(
            70 * mm,
            yy + 1 * mm,
            val
        )

    box(
        c,
        18 * mm,
        50 * mm,
        PAGE_W - 36 * mm,
        31 * mm,
        LIGHT_GREEN,
        None,
        8
    )

    c.setFillColor(DARK_GREEN)

    c.setFont(
        "Helvetica-Bold",
        11
    )

    c.drawString(
        26 * mm,
        69 * mm,
        "ENERGIA GIUSTA • Partner ENI Plenitude"
    )

    draw_wrapped(
        c,
        "Soluzioni per fotovoltaico, accumulo e servizi per l'energia. Il presente documento è una stima commerciale basata sui dati inseriti.",
        26 * mm,
        60 * mm,
        PAGE_W - 52 * mm,
        "Helvetica",
        8,
        10,
        GREY,
        2
    )

    footer(c, 7)

    c.showPage()

    # ========================================================
    # SALVATAGGIO PDF
    # ========================================================

    c.save()

    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="Analisi_Fotovoltaica_Energia_Giusta.pdf"
    )


# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
