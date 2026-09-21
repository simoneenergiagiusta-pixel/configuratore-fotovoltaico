
from flask import Flask, render_template, request, jsonify, send_file
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json, io, math

app = Flask(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"

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
    monthly = [x["E_m"] for x in fixed]
    return jsonify({
        "annual_kwh": yearly["E_y"],
        "monthly_kwh": monthly,
        "raw": data
    })

@app.post("/api/calculate")
def calculate():
    d = request.get_json(force=True)
    production = float(d["production"])
    consumption = float(d["consumption"])
    battery = float(d.get("battery", 0))
    price = float(d.get("energy_price", 0.25))
    export_price = float(d.get("export_price", 0.10))
    cost = float(d["cost"])
    deduction = float(d.get("deduction", 0))
    profile = d.get("profile", "equilibrato")

        # Modello economico semplificato ma più realistico.
    #
    # L'autoconsumo dipende dal profilo dei consumi e dalla capacità
    # della batteria. La produzione viene ridotta dello 0,5% ogni anno.
    #
    # Il prezzo dell'energia acquistata può aumentare nel tempo.
    # Il valore dell'energia immessa rimane separato.

    direct_pct = {
        "giorno": 0.55,
        "equilibrato": 0.40,
        "sera": 0.28
    }.get(profile, 0.40)

    # Contributo della batteria all'autoconsumo.
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

    # Risparmio del primo anno.
    annual_saving = (
        self_used * price
        + export * export_price
    )

    net_cost = max(
        0,
        cost - deduction
    )

    # Calcolo del rientro effettivo dell'investimento.
    # Verrà individuato l'anno in cui il beneficio cumulato
    # supera l'investimento netto.
    payback = None

    # ---------------------------------------------------------
    # PROIEZIONE ECONOMICA 25 ANNI
    # ---------------------------------------------------------

    degradation = 0.005
    energy_price_growth = 0.02
    years = []

    # Cumulato dei soli benefici economici annuali
    cumulative_benefit = 0

    for y in range(1, 26):

        # Produzione dell'anno considerando il degrado
        production_y = production * (
            (1 - degradation) ** (y - 1)
        )

        # Manteniamo la stessa percentuale di autoconsumo
        self_used_y = min(
            production_y * self_consumption_pct,
            consumption
        )

        export_y = max(
            0,
            production_y - self_used_y
        )

        # Prezzo energia acquistata nell'anno considerato
        energy_price_y = price * (
            (1 + energy_price_growth) ** (y - 1)
        )

        # Beneficio economico dell'anno
        benefit = (
            self_used_y * energy_price_y
            + export_y * export_price
        )

        # Cumulato dei benefici annuali
        cumulative_benefit += benefit

        # Primo anno in cui i benefici cumulati
        # recuperano l'investimento netto
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

    return jsonify({
        "self_used": self_used,
        "export": export,
        "grid_purchase": grid,
        "annual_saving": annual_saving,
        "net_cost": net_cost,
        "payback": payback,
        "years": years
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
