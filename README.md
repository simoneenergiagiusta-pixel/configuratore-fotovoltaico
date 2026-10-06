
# Configuratore Fotovoltaico – prototipo web

## Cosa fa
- Inserisce dati impianto/cliente.
- Interroga PVGIS 5.3 lato server, evitando chiamate AJAX dirette dal browser.
- Calcola produzione annuale e mensile.
- Stima autoconsumo, energia immessa, risparmio annuo e payback.
- Prevede batteria, profilo consumi, prezzo energia, valore energia immessa e detrazione.

## Avvio
Python 3.10+ consigliato.

    pip install flask
    python app.py

Aprire:
    http://127.0.0.1:5000

Il computer deve avere accesso a Internet per interrogare PVGIS.

## Nota tecnica
La documentazione ufficiale JRC indica che le API PVGIS accettano GET e che l'accesso AJAX/CORS diretto non è consentito. Per questo il prototipo fa la chiamata dal backend Flask.

## Prossimo step consigliato
1. Geocoding automatico dell'indirizzo -> lat/lon.
2. Simulazione oraria PVGIS + profilo di carico + stato di carica batteria.
3. Generazione automatica del PDF cliente con grafici.
4. Archivio clienti/preventivi.
