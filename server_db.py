from flask import Flask, Response, send_from_directory, jsonify, request
from decimal import Decimal, InvalidOperation
from datetime import date
import urllib.request
import json
import os
import re
import psycopg

app = Flask(__name__)

BASE_PATH = os.path.dirname(os.path.abspath(__file__))
DB_CONFIG_PATH = "/etc/stockmarketview-db.json"


# ============================================
# PostgreSQL-Verbindung
# ============================================

def get_db_config():
    with open(DB_CONFIG_PATH, "r") as f:
        return json.load(f)


def get_db_connection():
    return psycopg.connect(**get_db_config())


# ============================================
# Consors API Proxy
# ============================================

def proxy_request(url):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()

        return Response(
            data,
            status=200,
            mimetype="application/json"
        )

    except Exception as e:
        return jsonify({
            "fehler": str(e),
            "url": url
        }), 500


# ============================================
# Kursdaten über ISIN
# ============================================

@app.route("/api/<isin>")
def api_kurs(isin):
    url = (
        "https://www.consorsbank.de/"
        "web-financialinfo-service/api/marketdata/stocks"
        f"?id={isin}"
        "&field=BasicV1"
        "&field=ExchangesV2"
    )
    return proxy_request(url)


# ============================================
# Tagesanfangskurs
# ============================================

@app.route("/api/chart/<consors_id>")
def api_chart(consors_id):
    url = (
        "https://www.consorsbank.de/"
        "web-financialinfo-service/api/marketdata/securities"
        f"?id={consors_id}"
        "&field=ChartHistoryV1"
        "&idTypeOffset=51"
        "&historySince=0"
        "&resolution=15m"
        "&pagesize=2000"
        "&sortorder=DATETIME_FIRST"
    )
    return proxy_request(url)


# ============================================
# Test-Webseite
# ============================================

@app.route("/")
@app.route("/index.html")
@app.route("/index_db.html")
def index_db():
    return send_from_directory(BASE_PATH, "index_db.html")


# ============================================
# Altersvorsorge-Historie: GET
# ============================================

@app.route("/api/altersvorsorge-historie", methods=["GET"])
def altersvorsorge_historie_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT startdatum, historische_summe
                    FROM public.altersvorsorge_historie
                    ORDER BY startdatum ASC
                """)

                daten = [
                    {
                        "startdatum": zeile[0].isoformat(),
                        "historische_summe": format(zeile[1], ".2f")
                    }
                    for zeile in cur.fetchall()
                ]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except Exception:
        app.logger.exception("Fehler beim Lesen der Altersvorsorge-Historie")
        return jsonify({"fehler": "Datenbankfehler beim Lesen."}), 500


# ============================================
# Altersvorsorge-Historie: POST
# ============================================

@app.route("/api/altersvorsorge-historie", methods=["POST"])
def altersvorsorge_historie_einfuegen():
    daten = request.get_json(silent=True)

    if not isinstance(daten, dict):
        return jsonify({"fehler": "Ungültige JSON-Eingabe."}), 400

    startdatum = daten.get("startdatum")
    betrag_roh = daten.get("historische_summe")

    # Datum im ISO-Format prüfen
    if not isinstance(startdatum, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", startdatum
    ):
        return jsonify({"fehler": "Ungültiges Startdatum."}), 400

    try:
        date.fromisoformat(startdatum)
    except ValueError:
        return jsonify({"fehler": "Ungültiges Kalenderdatum."}), 400

    # Betrag prüfen
    if isinstance(betrag_roh, bool) or betrag_roh is None:
        return jsonify({"fehler": "Historische Summe fehlt oder ist ungültig."}), 400

    try:
        betrag = Decimal(str(betrag_roh))

        if not betrag.is_finite() or betrag < 0:
            raise ValueError()

        betrag_gerundet = betrag.quantize(Decimal("0.01"))

        if betrag != betrag_gerundet:
            raise ValueError()

        if betrag > Decimal("999999999999.99"):
            raise ValueError()

    except (InvalidOperation, ValueError):
        return jsonify({
            "fehler": "Bitte eine gültige nicht-negative Summe mit höchstens zwei Nachkommastellen eingeben."
        }), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.altersvorsorge_historie
                        (startdatum, historische_summe)
                    VALUES (%s, %s)
                    RETURNING startdatum, historische_summe
                """, (startdatum, betrag_gerundet))

                zeile = cur.fetchone()

        response = jsonify({
            "startdatum": zeile[0].isoformat(),
            "historische_summe": format(zeile[1], ".2f")
        })
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except psycopg.errors.UniqueViolation:
        return jsonify({
            "fehler": "Für dieses Startdatum existiert bereits ein Eintrag."
        }), 409

    except psycopg.Error:
        app.logger.exception("Fehler beim Speichern der Altersvorsorge-Historie")
        return jsonify({"fehler": "Datenbankfehler beim Speichern."}), 500


# ============================================
# Lokaler Start
# ============================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8001
    )
