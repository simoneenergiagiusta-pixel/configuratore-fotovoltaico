
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

    # Simplified sales-model simulation. For a detailed hourly model,
    # replace this block with PVGIS seriescalc + hourly load profile.
    direct_pct = {"giorno": 0.55, "equilibrato": 0.40, "sera": 0.28}.get(profile, 0.40)
    battery_boost = min(0.25, battery / 30.0)
    self_consumption_pct = min(0.95, direct_pct + battery_boost)
    self_used = min(production * self_consumption_pct, consumption)
    export = max(0, production - self_used)
    grid = max(0, consumption - self_used)
    annual_saving = self_used * price + export * export_price
    net_cost = max(0, cost - deduction)
    payback = net_cost / annual_saving if annual_saving else None

    years = []
    cumulative = -net_cost
    degradation = 0.005
    for y in range(1, 26):
        benefit = annual_saving * ((1-degradation) ** (y-1))
        cumulative += benefit
        years.append({"year": y, "benefit": benefit, "cumulative": cumulative})

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
