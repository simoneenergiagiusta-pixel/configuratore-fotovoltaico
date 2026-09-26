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
    req = Request(url, headers={"User-Agent": "ConfiguratoreFotovoltaico/1.0"})
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
    return jsonify({
        "annual_kwh": yearly["E_y"],
        "monthly_kwh": [x["E_m"] for x in fixed],
        "raw": data
    })

# =========================================================
# CALCOLI - INVARIATI
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
    self_consumption_pct = min(0.95, direct_pct + battery_boost)

    self_used = min(production * self_consumption_pct, consumption)
    export = max(0, production - self_used)
    grid = max(0, consumption - self_used)

    annual_saving = self_used * price + export * export_price
    net_cost = max(0, cost - deduction)

    degradation = 0.005
    energy_price_growth = 0.02

    years = []
    cumulative_benefit = 0
    payback = None

    for y in range(1, 26):
        production_y = production * ((1 - degradation) ** (y - 1))
        self_used_y = min(production_y * self_consumption_pct, consumption)
        export_y = max(0, production_y - self_used_y)
        energy_price_y = price * ((1 + energy_price_growth) ** (y - 1))
        benefit = self_used_y * energy_price_y + export_y * export_price
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
    net_25 = max(0, gross_25 - net_cost)

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
# PALETTE
# =========================================================

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

# =========================================================
# HELPERS
# =========================================================

def euro(value):
    return "€ " + f"{value:,.0f}".replace(",", ".")

def number(value):
    return f"{value:,.0f}".replace(",", ".")

def decimal(value):
    return f"{value:.2f}".replace(".", ",")

def wrap_text(text, font="Helvetica", size=8, max_width=50*mm):
    words = str(text).split()
    lines, current = [], ""
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

def draw_wrapped_text(c, text, x, y, max_width, font="Helvetica", size=8,
                      leading=11, color=DARK, max_lines=None, align="left"):
    lines = wrap_text(text, font, size, max_width)
    if max_lines:
        lines = lines[:max_lines]
    c.setFillColor(color)
    c.setFont(font, size)
    for i, line in enumerate(lines):
        yy = y - i * leading
        if align == "center":
            c.drawCentredString(x, yy, line)
        elif align == "right":
            c.drawRightString(x, yy, line)
        else:
            c.drawString(x, yy, line)
    return len(lines) * leading

def round_box(c, x, y, w, h, fill=WHITE, stroke=None, radius=7, lw=0.7):
    c.setFillColor(fill)
    c.setStrokeColor(stroke if stroke else fill)
    c.setLineWidth(lw)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1 if stroke else 0)

def pill(c, x, y, w, h, text, fill=GREEN, text_color=WHITE, size=7.5):
    c.setFillColor(fill)
    c.roundRect(x, y, w, h, h/2, fill=1, stroke=0)
    c.setFillColor(text_color)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(x+w/2, y+h/2-2.4, text)

def draw_sun(c, x, y, size=24):
    c.setStrokeColor(YELLOW)
    c.setFillColor(YELLOW)
    c.setLineWidth(1.4)
    c.circle(x, y, size*0.27, fill=1, stroke=0)
    for i in range(8):
        a = math.radians(i*45)
        c.line(x+math.cos(a)*size*.42, y+math.sin(a)*size*.42,
               x+math.cos(a)*size*.72, y+math.sin(a)*size*.72)

def draw_solar_house(c, x, y, scale=1.0):
    w, h = 62*scale, 40*scale
    c.setFillColor(LIGHT_GREEN)
    c.setStrokeColor(GREEN)
    c.setLineWidth(1.3)
    c.rect(x, y, w, h, fill=1, stroke=1)
    roof = c.beginPath()
    roof.moveTo(x-6*scale, y+h)
    roof.lineTo(x+w/2, y+h+27*scale)
    roof.lineTo(x+w+6*scale, y+h)
    roof.close()
    c.setFillColor(DARK_GREEN)
    c.drawPath(roof, fill=1, stroke=0)

    # pannelli sul tetto
    c.setFillColor(DARK)
    c.rect(x+8*scale, y+h+4*scale, w*.70, 12*scale, fill=1, stroke=0)
    c.setStrokeColor(WHITE)
    c.setLineWidth(.35)
    for i in range(1,4):
        xx = x+8*scale+i*(w*.70/4)
        c.line(xx, y+h+4*scale, xx, y+h+16*scale)
    c.line(x+8*scale, y+h+10*scale, x+8*scale+w*.70, y+h+10*scale)

    # porta e finestre
    c.setFillColor(WHITE)
    c.rect(x+w*.42, y, w*.18, h*.55, fill=1, stroke=0)
    c.rect(x+w*.12, y+h*.40, w*.18, h*.18, fill=1, stroke=0)
    c.rect(x+w*.70, y+h*.40, w*.18, h*.18, fill=1, stroke=0)

def draw_battery(c, x, y, w=26, h=43):
    c.setStrokeColor(GREEN)
    c.setLineWidth(1.4)
    c.roundRect(x, y, w, h, 4, fill=0, stroke=1)
    c.rect(x+w*.35, y+h, w*.30, 4, fill=0, stroke=1)
    for i in range(3):
        c.setFillColor(MID_GREEN if i < 2 else LIGHT_GREEN)
        c.roundRect(x+5, y+6+i*11, w-10, 7, 2, fill=1, stroke=0)

def draw_header(c, title, subtitle=""):
    c.setFillColor(GREEN)
    c.rect(0, PAGE_H-22*mm, PAGE_W, 22*mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18*mm, PAGE_H-13.5*mm, title)
    if subtitle:
        c.setFont("Helvetica", 7.5)
        c.drawRightString(PAGE_W-18*mm, PAGE_H-13.5*mm, subtitle)

def draw_footer(c, page):
    c.setStrokeColor(MID_GREY)
    c.setLineWidth(.4)
    c.line(15*mm, 11.5*mm, PAGE_W-15*mm, 11.5*mm)
    c.setFillColor(GREY)
    c.setFont("Helvetica", 6.3)
    c.drawString(15*mm, 6.8*mm, "ENERGIA GIUSTA  •  Partner ENI Plenitude")
    c.drawRightString(PAGE_W-15*mm, 6.8*mm, f"{page:02d}")

def section_title(c, title, subtitle, y):
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(18*mm, y, title)
    if subtitle:
        draw_wrapped_text(c, subtitle, 18*mm, y-8*mm,
                           PAGE_W-36*mm, "Helvetica", 7.8, 9.5, GREY, 2)

def kpi(c, x, y, w, h, label, value, accent=GREEN, icon=None):
    round_box(c, x, y, w, h, WHITE, MID_GREY, 7)
    c.setFillColor(accent)
    c.roundRect(x, y+h-10*mm, w, 10*mm, 7, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(x+5*mm, y+h-6.6*mm, (icon + "  ") if icon else "")
    c.drawString(x+14*mm if icon else x+5*mm, y+h-6.6*mm, label.upper())

    fs = 15
    if stringWidth(value, "Helvetica-Bold", fs) > w-10*mm:
        fs = 12
    if stringWidth(value, "Helvetica-Bold", fs) > w-10*mm:
        fs = 10
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", fs)
    c.drawCentredString(x+w/2, y+8*mm, value)

def stat_line(c, x, y, label, value, color=DARK):
    c.setFillColor(GREY)
    c.setFont("Helvetica", 7.2)
    c.drawString(x, y, label)
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 8.2)
    c.drawRightString(x+48*mm, y, value)

def monthly_chart(c, x, y, w, h, monthly):
    round_box(c, x, y, w, h, WHITE, MID_GREY, 8)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x+8*mm, y+h-11*mm, "Produzione fotovoltaica mensile")
    c.setFillColor(GREY)
    c.setFont("Helvetica", 6.5)
    c.drawRightString(x+w-8*mm, y+h-11*mm, "kWh")

    left, bottom = x+12*mm, y+14*mm
    cw, ch = w-18*mm, h-31*mm
    maximum = max(max(monthly or [1]), 1)
    c.setStrokeColor(MID_GREY)
    c.setLineWidth(.35)
    for i in range(4):
        gy = bottom+ch*i/3
        c.line(left, gy, left+cw, gy)

    months = ["Gen","Feb","Mar","Apr","Mag","Giu","Lug","Ago","Set","Ott","Nov","Dic"]
    slot = cw/12
    bw = slot*.55
    for i, val in enumerate(monthly[:12]):
        bx = left+i*slot+(slot-bw)/2
        bh = ch*val/maximum
        c.setFillColor(GREEN if i not in (5,6,7) else MID_GREEN)
        c.roundRect(bx, bottom, bw, bh, 2, fill=1, stroke=0)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 5.7)
        c.drawCentredString(bx+bw/2, bottom-8, months[i])

def donut(c, cx, cy, r, self_used, export):
    total=max(self_used+export,1)
    a=360*self_used/total
    c.setFillColor(GREEN)
    c.wedge(cx-r,cy-r,cx+r,cy+r,0,a,fill=1,stroke=0)
    c.setFillColor(YELLOW)
    c.wedge(cx-r,cy-r,cx+r,cy+r,a,360-a,fill=1,stroke=0)
    c.setFillColor(WHITE)
    c.circle(cx,cy,r*.58,fill=1,stroke=0)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold",14)
    c.drawCentredString(cx,cy+2,f"{self_used/total*100:.0f}%")
    c.setFont("Helvetica",6.5)
    c.drawCentredString(cx,cy-8,"autoconsumo")

def economic_chart(c, x, y, w, h, years):
    round_box(c,x,y,w,h,WHITE,MID_GREY,8)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold",10)
    c.drawString(x+8*mm,y+h-11*mm,"Beneficio cumulato nel tempo")
    vals=[z["cumulative"] for z in years]
    mx=max(max(vals or [1]),1)
    left,bottom=x+13*mm,y+15*mm
    cw,ch=w-20*mm,h-31*mm
    c.setStrokeColor(MID_GREY); c.setLineWidth(.35)
    for i in range(4):
        gy=bottom+ch*i/3
        c.line(left,gy,left+cw,gy)
    pts=[]
    for i,val in enumerate(vals):
        px=left+cw*i/24
        py=bottom+ch*val/mx
        pts.append((px,py))
    c.setStrokeColor(GREEN); c.setLineWidth(2)
    for p1,p2 in zip(pts,pts[1:]):
        c.line(p1[0],p1[1],p2[0],p2[1])
    for i in [0,4,9,14,19,24]:
        px,py=pts[i]
        c.setFillColor(GREEN); c.circle(px,py,2.2,fill=1,stroke=0)
        c.setFillColor(GREY); c.setFont("Helvetica",5.8)
        c.drawCentredString(px,bottom-8,str(i+1))

def service_card(c, x, y, w, h, num, title, text):
    round_box(c,x,y,w,h,WHITE,MID_GREY,8)
    c.setFillColor(GREEN); c.circle(x+10*mm,y+h-11*mm,6*mm,fill=1,stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold",7)
    c.drawCentredString(x+10*mm,y+h-13.2*mm,num)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",8.3)
    c.drawString(x+20*mm,y+h-8.5*mm,title)
    draw_wrapped_text(c,text,x+20*mm,y+h-16*mm,w-25*mm,"Helvetica",6.7,8.5,GREY,4)

# =========================================================
# PDF
# =========================================================

@app.post("/api/pdf")
def generate_pdf():
    d=request.get_json(force=True)
    values=calculate_values(d)

    address=d.get("address","Abitazione")
    kwp=float(d.get("kwp",0))
    battery=float(d.get("battery",0))
    angle=float(d.get("angle",30))
    aspect=float(d.get("aspect",0))
    loss=float(d.get("loss",14))
    consumption=float(d.get("consumption",0))
    production=float(d.get("production",0))
    cost=float(d.get("cost",0))
    deduction=float(d.get("deduction",0))
    energy_price=float(d.get("energy_price",0.25))
    export_price=float(d.get("export_price",0.10))
    monthly=d.get("monthly",[]) or [production/12 for _ in range(12)]
    profile=d.get("profile","equilibrato")

    profile_labels={"giorno":"Prevalenza diurna","equilibrato":"Equilibrato","sera":"Prevalenza serale"}
    profile_label=profile_labels.get(profile,"Equilibrato")

    buf=io.BytesIO()
    c=canvas.Canvas(buf,pagesize=A4)
    c.setTitle("Analisi Fotovoltaica - Energia Giusta")

    # -----------------------------------------------------
    # 1 COPERTINA
    # -----------------------------------------------------
    c.setFillColor(PALE_GREEN); c.rect(0,0,PAGE_W,PAGE_H,fill=1,stroke=0)
    c.setFillColor(GREEN); c.rect(0,PAGE_H-86*mm,PAGE_W,86*mm,fill=1,stroke=0)

    # pattern decorativo
    c.setFillColor(DARK_GREEN)
    c.circle(PAGE_W+12*mm,PAGE_H-5*mm,55*mm,fill=1,stroke=0)
    draw_sun(c,PAGE_W-40*mm,PAGE_H-30*mm,23)

    c.setFillColor(WHITE); c.setFont("Helvetica-Bold",24)
    c.drawString(18*mm,PAGE_H-30*mm,"ENERGIA GIUSTA")
    c.setFont("Helvetica",8)
    c.drawString(18*mm,PAGE_H-39*mm,"Partner ENI Plenitude  •  Soluzioni per l'energia")

    c.setFillColor(DARK); c.setFont("Helvetica-Bold",26)
    c.drawString(18*mm,PAGE_H-112*mm,"Analisi Fotovoltaica")
    c.setFillColor(GREY); c.setFont("Helvetica",10)
    c.drawString(18*mm,PAGE_H-123*mm,"Il tuo progetto, spiegato in modo semplice.")

    draw_solar_house(c,PAGE_W-91*mm,PAGE_H-171*mm,.82)
    draw_battery(c,PAGE_W-34*mm,PAGE_H-167*mm,23,40)

    round_box(c,18*mm,67*mm,PAGE_W-36*mm,28*mm,WHITE,MID_GREY,8)
    pill(c,26*mm,84*mm,40*mm,6.5*mm,"ABITAZIONE",GREEN,WHITE,6.2)
    draw_wrapped_text(c,address,26*mm,76.5*mm,PAGE_W-52*mm,
                      "Helvetica-Bold",10,12,DARK,2)

    kpi(c,18*mm,33*mm,53*mm,25*mm,"Potenza FV",f"{decimal(kwp)} kWp",GREEN,"☀")
    kpi(c,77*mm,33*mm,53*mm,25*mm,"Accumulo",f"{decimal(battery)} kWh",YELLOW,"B")
    kpi(c,136*mm,33*mm,53*mm,25*mm,"Risparmio annuo",euro(values["annual_saving"]),GREEN,"€")
    draw_footer(c,1); c.showPage()

    # -----------------------------------------------------
    # 2 IMPIANTO
    # -----------------------------------------------------
    draw_header(c,"Il tuo impianto","DATI TECNICI")
    section_title(c,"Configurazione del sistema",
                  "I parametri utilizzati per stimare produzione e prestazioni.",
                  PAGE_H-38*mm)

    cards=[
        ("Potenza fotovoltaica",f"{decimal(kwp)} kWp"),
        ("Batteria di accumulo",f"{decimal(battery)} kWh"),
        ("Consumo annuo",f"{number(consumption)} kWh"),
        ("Produzione stimata",f"{number(production)} kWh"),
        ("Inclinazione",f"{decimal(angle)}°"),
        ("Perdite di sistema",f"{decimal(loss)}%")
    ]
    for i,(lab,val) in enumerate(cards):
        col=i%3; row=i//3
        x=18*mm+col*59*mm; y=PAGE_H-73*mm-row*28*mm
        round_box(c,x,y,55*mm,22*mm,WHITE,MID_GREY,7)
        c.setFillColor(GREY); c.setFont("Helvetica",6.4); c.drawString(x+5*mm,y+13*mm,lab)
        c.setFillColor(DARK); c.setFont("Helvetica-Bold",12.5); c.drawString(x+5*mm,y+5*mm,val)

    monthly_chart(c,18*mm,55*mm,PAGE_W-36*mm,78*mm,monthly)
    c.setFillColor(GREY); c.setFont("Helvetica",6.7)
    c.drawString(18*mm,46*mm,"Stima della produzione elaborata tramite PVGIS in base a localizzazione e parametri inseriti.")
    draw_footer(c,2); c.showPage()

    # -----------------------------------------------------
    # 3 AUTOCONSUMO
    # -----------------------------------------------------
    draw_header(c,"La tua energia","AUTOCONSUMO")
    section_title(c,"Dove finisce l'energia prodotta?",
                  "La simulazione separa l'energia utilizzata direttamente dall'abitazione da quella immessa in rete.",
                  PAGE_H-38*mm)

    donut(c,67*mm,PAGE_H-99*mm,32*mm,values["self_used"],values["export"])

    # legenda
    c.setFillColor(GREEN); c.rect(111*mm,PAGE_H-84*mm,6*mm,6*mm,fill=1,stroke=0)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",8.5); c.drawString(122*mm,PAGE_H-82.5*mm,"Autoconsumo")
    c.setFillColor(GREY); c.setFont("Helvetica",7.5); c.drawString(122*mm,PAGE_H-91*mm,f"{number(values['self_used'])} kWh/anno")

    c.setFillColor(YELLOW); c.rect(111*mm,PAGE_H-104*mm,6*mm,6*mm,fill=1,stroke=0)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",8.5); c.drawString(122*mm,PAGE_H-102.5*mm,"Energia immessa")
    c.setFillColor(GREY); c.setFont("Helvetica",7.5); c.drawString(122*mm,PAGE_H-111*mm,f"{number(values['export'])} kWh/anno")

    kpi(c,18*mm,88*mm,53*mm,25*mm,"Autoconsumo",f"{values['self_used']/max(production,1)*100:.0f}%",GREEN,"A")
    kpi(c,77*mm,88*mm,53*mm,25*mm,"Energia immessa",f"{values['export']/max(production,1)*100:.0f}%",YELLOW,"I")
    kpi(c,136*mm,88*mm,53*mm,25*mm,"Energia da rete",f"{number(values['grid_purchase'])} kWh",GREEN,"R")

    round_box(c,18*mm,45*mm,PAGE_W-36*mm,31*mm,LIGHT_GREEN,None,8)
    c.setFillColor(DARK_GREEN); c.setFont("Helvetica-Bold",9.5)
    c.drawString(27*mm,60*mm,"In parole semplici")
    draw_wrapped_text(c,
        "L'energia prodotta viene prima utilizzata dall'abitazione. "
        "L'eventuale eccedenza viene immessa in rete e valorizzata "
        "secondo il valore inserito nella simulazione.",
        27*mm,52*mm,PAGE_W-54*mm,"Helvetica",7.5,9.5,DARK,3)
    draw_footer(c,3); c.showPage()

    # -----------------------------------------------------
    # 4 ECONOMIA
    # -----------------------------------------------------
    draw_header(c,"Il ritorno dell'investimento","ANALISI ECONOMICA")
    section_title(c,"I numeri principali",
                  "Una lettura immediata dell'investimento e del beneficio stimato.",
                  PAGE_H-38*mm)

    payback=f"{values['payback']} anni" if values["payback"] is not None else "> 25 anni"
    kpi(c,18*mm,PAGE_H-86*mm,55*mm,29*mm,"Investimento netto",euro(values["net_cost"]),GREEN,"€")
    kpi(c,78*mm,PAGE_H-86*mm,55*mm,29*mm,"Rientro stimato",payback,YELLOW,"T")
    kpi(c,138*mm,PAGE_H-86*mm,53*mm,29*mm,"Beneficio 25 anni",euro(values["gross_25"]),GREEN,"€")

    round_box(c,18*mm,96*mm,PAGE_W-36*mm,32*mm,WHITE,MID_GREY,8)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",9.5)
    c.drawString(27*mm,118*mm,"Composizione dell'investimento")
    stat_line(c,27*mm,107*mm,"Costo complessivo",euro(cost))
    stat_line(c,105*mm,107*mm,"Detrazione totale",euro(deduction),GREEN)
    c.setFillColor(LIGHT_GREEN); c.roundRect(27*mm,99*mm,158*mm,4*mm,2,fill=1,stroke=0)
    if cost>0:
        dw=min(158*mm,158*mm*deduction/cost)
        c.setFillColor(GREEN); c.roundRect(27*mm,99*mm,dw,4*mm,2,fill=1,stroke=0)

    economic_chart(c,18*mm,24*mm,PAGE_W-36*mm,62*mm,values["years"])
    draw_footer(c,4); c.showPage()

    # -----------------------------------------------------
    # 5 PROIEZIONE
    # -----------------------------------------------------
    draw_header(c,"Proiezione economica","ORIZZONTE 25 ANNI")
    section_title(c,"Come evolve il beneficio nel tempo",
                  "La simulazione considera un degrado della produzione dello 0,5% annuo e una crescita del prezzo dell'energia del 2% annuo.",
                  PAGE_H-38*mm)

    x=18*mm; y=PAGE_H-78*mm; tw=PAGE_W-36*mm; rh=9*mm
    c.setFillColor(GREEN); c.roundRect(x,y,tw,rh,3,fill=1,stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold",7.5)
    c.drawString(x+5*mm,y+3.1*mm,"PERIODO")
    c.drawString(x+45*mm,y+3.1*mm,"BENEFICIO ANNUO")
    c.drawString(x+105*mm,y+3.1*mm,"BENEFICIO CUMULATO")

    for i,yr in enumerate([1,5,10,15,20,25]):
        item=values["years"][yr-1]; ry=y-(i+1)*rh
        c.setFillColor(LIGHT_GREY if i%2==0 else WHITE)
        c.rect(x,ry,tw,rh,fill=1,stroke=0)
        c.setFillColor(DARK); c.setFont("Helvetica-Bold",7.5)
        c.drawString(x+5*mm,ry+3.1*mm,f"{yr}° anno")
        c.drawString(x+45*mm,ry+3.1*mm,euro(item["benefit"]))
        c.setFillColor(GREEN); c.drawString(x+105*mm,ry+3.1*mm,euro(item["cumulative"]))

    round_box(c,18*mm,68*mm,PAGE_W-36*mm,32*mm,LIGHT_GREEN,None,8)
    c.setFillColor(DARK_GREEN); c.setFont("Helvetica-Bold",8.8)
    c.drawString(27*mm,89*mm,"BENEFICIO CUMULATO STIMATO A 25 ANNI")
    c.setFillColor(GREEN); c.setFont("Helvetica-Bold",19)
    c.drawString(27*mm,77*mm,euro(values["gross_25"]))
    c.setFillColor(DARK); c.setFont("Helvetica",7.5)
    c.drawRightString(PAGE_W-27*mm,79*mm,"Beneficio netto: "+euro(values["net_25"]))

    c.setFillColor(DARK); c.setFont("Helvetica-Bold",8.5)
    c.drawString(18*mm,57*mm,"Assunzioni della simulazione")
    assumptions=[
        "Degrado produzione: 0,5% annuo",
        "Crescita prezzo energia acquistata: 2% annuo",
        f"Prezzo energia acquistata: {decimal(energy_price)} €/kWh",
        f"Valore energia immessa: {decimal(export_price)} €/kWh",
        f"Profilo consumi: {profile_label}"
    ]
    yy=50*mm
    for txt in assumptions:
        c.setFillColor(GREY); c.setFont("Helvetica",6.8)
        c.drawString(18*mm,yy,"• "+txt); yy-=4.7*mm
    draw_footer(c,5); c.showPage()

    # -----------------------------------------------------
    # 6 COSA COMPRENDE
    # -----------------------------------------------------
    draw_header(c,"Cosa comprende la soluzione","SERVIZI E GARANZIE")
    section_title(c,"Un progetto completo",
                  "Dalla prima analisi alla gestione dell'impianto nel tempo.",
                  PAGE_H-38*mm)

    services=[
        ("01","Sopralluogo","Verifica degli spazi, della copertura e delle condizioni di installazione."),
        ("02","Progettazione","Dimensionamento dell'impianto in funzione dei consumi e della produzione stimata."),
        ("03","Installazione","Installatori specializzati, messa in servizio e avviamento dell'impianto."),
        ("04","Pratiche GSE","Gestione delle pratiche necessarie per l'impianto e la valorizzazione dell'energia."),
        ("05","Detrazione fiscale","Supporto nella gestione della documentazione relativa alla detrazione prevista."),
        ("06","Monitoraggio","App per controllare produzione, consumi e funzionamento dell'impianto."),
        ("07","Garanzie","Pannelli: prodotto fino a 25 anni e prestazione fino a 30 anni; inverter fino a 12 anni."),
        ("08","Fine vita","Gestione dello smaltimento dei moduli fotovoltaici e dell'inverter a fine vita.")
    ]
    for i,(num,title,txt) in enumerate(services):
        col=i%2; row=i//2
        service_card(c,18*mm+col*88*mm,PAGE_H-74*mm-row*36*mm,82*mm,30*mm,num,title,txt)

    round_box(c,18*mm,29*mm,PAGE_W-36*mm,25*mm,LIGHT_YELLOW,None,8)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",8.7)
    c.drawString(27*mm,46*mm,"Garanzie indicative")
    c.setFont("Helvetica",6.8)
    c.drawString(27*mm,38*mm,"✓ Pannelli: prodotto fino a 25 anni    ✓ Prestazione pannelli fino a 30 anni")
    c.drawString(27*mm,33*mm,"✓ Inverter fino a 12 anni               ✓ Batterie fino a 11 anni")
    draw_footer(c,6); c.showPage()

    # -----------------------------------------------------
    # 7 CONTATTI
    # -----------------------------------------------------
    c.setFillColor(GREEN); c.rect(0,0,PAGE_W,PAGE_H,fill=1,stroke=0)
    c.setFillColor(DARK_GREEN); c.circle(PAGE_W+15*mm,PAGE_H-10*mm,66*mm,fill=1,stroke=0)
    draw_sun(c,PAGE_W-43*mm,PAGE_H-37*mm,22)

    c.setFillColor(WHITE); c.setFont("Helvetica-Bold",25)
    c.drawString(18*mm,PAGE_H-48*mm,"La tua energia.")
    c.drawString(18*mm,PAGE_H-62*mm,"Il tuo risparmio.")
    c.setFont("Helvetica",8.5)
    c.drawString(18*mm,PAGE_H-76*mm,"Parliamone insieme e costruiamo la soluzione più adatta.")

    round_box(c,18*mm,69*mm,PAGE_W-36*mm,67*mm,WHITE,None,10)
    c.setFillColor(DARK); c.setFont("Helvetica-Bold",17)
    c.drawString(29*mm,118*mm,"Simone Alfarano")
    c.setFillColor(GREEN); c.setFont("Helvetica-Bold",8)
    c.drawString(29*mm,108*mm,"RESPONSABILE COMMERCIALE")
    c.setFillColor(GREY); c.setFont("Helvetica",7.8)

    contact_lines=[
        "Energia Giusta",
        "Partner ENI Plenitude",
        "Tel. / WhatsApp: 351.7478652",
        "simone.alfarano@energiagiusta.it",
        "www.energiagiusta.it"
    ]
    yy=97*mm
    for line in contact_lines:
        c.drawString(29*mm,yy,line); yy-=7*mm

    pill(c,29*mm,48*mm,67*mm,12*mm,"CONTATTAMI PER INFO",YELLOW,DARK,8.5)

    c.setFillColor(WHITE); c.setFont("Helvetica",6.2)
    draw_wrapped_text(c,
        "Stima commerciale. Il risultato dipende da tariffe, profilo reale dei consumi, "
        "condizioni di scambio/ritiro, ombreggiamento e altri fattori. Non è un preventivo finanziario.",
        18*mm,22*mm,PAGE_W-36*mm,"Helvetica",6.2,8,WHITE,4)

    c.save()
    buf.seek(0)
    return send_file(buf,mimetype="application/pdf",as_attachment=True,
                     download_name="Analisi_Fotovoltaica_Energia_Giusta.pdf")

# =========================================================
# AVVIO
# =========================================================

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
