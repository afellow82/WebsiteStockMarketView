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


def db_text(wert):
    """SQL_ASCII-Textwerte aus PostgreSQL als UTF-8-Text zurückgeben."""
    if isinstance(wert, bytes):
        return wert.decode("utf-8")
    return wert


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
# Besitzer: GET
# ============================================

@app.route("/api/besitzer", methods=["GET"])
def besitzer_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        besitzer_id,
                        kuerzel,
                        ist_eigen,
                        kachel_reihenfolge
                    FROM public.besitzer
                    ORDER BY
                        ist_eigen DESC,
                        kachel_reihenfolge ASC NULLS LAST,
                        besitzer_id ASC
                """)

                daten = [
                    {
                        "id": zeile[0],
                        "kuerzel": db_text(zeile[1]),
                        "ist_eigen": zeile[2],
                        "kachel_reihenfolge": zeile[3]
                    }
                    for zeile in cur.fetchall()
                ]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Besitzer")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Besitzer."}), 500


# ============================================
# Besitzer: Kachelreihenfolge speichern
# ============================================

@app.route("/api/besitzer/kachel-reihenfolge", methods=["PUT"])
def besitzer_kachel_reihenfolge_speichern():
    daten = request.get_json(silent=True)

    if not isinstance(daten, dict) or not isinstance(daten.get("reihenfolge"), list):
        return jsonify({"fehler": "Ungültige JSON-Eingabe."}), 400

    auswahl = daten["reihenfolge"]

    if len(auswahl) != 3:
        return jsonify({
            "fehler": "Es müssen genau drei Fremdbesitzer angegeben werden."
        }), 400

    ids = []
    positionen = []
    updates = []

    for eintrag in auswahl:
        if not isinstance(eintrag, dict):
            return jsonify({"fehler": "Ungültiger Eintrag in der Reihenfolge."}), 400

        besitzer_id = eintrag.get("id")
        position = eintrag.get("kachel_reihenfolge")

        if (
            isinstance(besitzer_id, bool)
            or not isinstance(besitzer_id, int)
            or besitzer_id <= 0
        ):
            return jsonify({"fehler": "Ungültige Besitzer-ID."}), 400

        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or position not in (1, 2, 3)
        ):
            return jsonify({
                "fehler": "Jeder Fremdbesitzer muss eine Kachelposition von 1 bis 3 erhalten."
            }), 400

        ids.append(besitzer_id)
        positionen.append(position)
        updates.append((position, besitzer_id))

    if len(set(ids)) != 3:
        return jsonify({"fehler": "Ein Besitzer wurde mehrfach angegeben."}), 400

    if set(positionen) != {1, 2, 3}:
        return jsonify({
            "fehler": "Die Kachelpositionen 1, 2 und 3 müssen jeweils genau einmal vergeben werden."
        }), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT besitzer_id
                    FROM public.besitzer
                    WHERE ist_eigen = false
                    ORDER BY besitzer_id
                """)
                vorhandene_ids = {zeile[0] for zeile in cur.fetchall()}

                if len(vorhandene_ids) != 3 or set(ids) != vorhandene_ids:
                    return jsonify({
                        "fehler": "Die Besitzerliste hat sich geändert. Bitte das Fenster neu öffnen."
                    }), 409

                cur.executemany("""
                    UPDATE public.besitzer
                    SET kachel_reihenfolge = %s
                    WHERE besitzer_id = %s
                      AND ist_eigen = false
                """, updates)

                cur.execute("""
                    SELECT
                        besitzer_id,
                        kuerzel,
                        ist_eigen,
                        kachel_reihenfolge
                    FROM public.besitzer
                    ORDER BY
                        ist_eigen DESC,
                        kachel_reihenfolge ASC NULLS LAST,
                        besitzer_id ASC
                """)

                gespeicherte_daten = [
                    {
                        "id": zeile[0],
                        "kuerzel": db_text(zeile[1]),
                        "ist_eigen": zeile[2],
                        "kachel_reihenfolge": zeile[3]
                    }
                    for zeile in cur.fetchall()
                ]

        response = jsonify({
            "ok": True,
            "besitzer": gespeicherte_daten
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Speichern der Kachelreihenfolge")
        return jsonify({
            "fehler": "Datenbankfehler beim Speichern der Kachelreihenfolge."
        }), 500


# ============================================
# Fremdanlagen-Positionen: GET
# ============================================

@app.route("/api/fremdanlagen", methods=["GET"])
def fremdanlagen_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        b.kuerzel,
                        w.isin,
                        w.bezeichnung,
                        w.link_url,
                        p.anzahl,
                        p.investition,
                        p.investition_hinweis
                    FROM public.position AS p
                    JOIN public.besitzer AS b
                      ON b.besitzer_id = p.besitzer_id
                    JOIN public.wertpapier AS w
                      ON w.wertpapier_id = p.wertpapier_id
                    WHERE b.ist_eigen = false
                    ORDER BY
                        b.kachel_reihenfolge ASC NULLS LAST,
                        b.besitzer_id ASC,
                        w.bezeichnung ASC
                """)

                daten = [
                    {
                        "besitzer": db_text(zeile[0]),
                        "isin": db_text(zeile[1]).strip(),
                        "bezeichnung": db_text(zeile[2]),
                        "link": db_text(zeile[3]),
                        "anzahl": format(zeile[4], "f"),
                        "investition": format(zeile[5], "f"),
                        "investitionHinweis": db_text(zeile[6])
                    }
                    for zeile in cur.fetchall()
                ]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Fremdanlagen")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Fremdanlagen."}), 500


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
# Wertpapiere: gemeinsame Hilfsfunktionen
# ============================================

def wertpapier_eingabe_pruefen(daten):
    """Eingabedaten fuer INSERT und UPDATE normalisieren und validieren."""
    if not isinstance(daten, dict):
        return None, "Ungültige JSON-Eingabe."

    isin = daten.get("isin")
    bezeichnung = daten.get("bezeichnung")
    art = daten.get("art")
    link_url = daten.get("link_url")

    if not isinstance(isin, str):
        return None, "Bitte eine gültige ISIN mit 12 Zeichen eingeben."
    isin = isin.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return None, "Bitte eine gültige ISIN mit 12 Zeichen eingeben (z. B. IE00B4L5Y983)."

    if not isinstance(bezeichnung, str):
        return None, "Bitte eine Bezeichnung eingeben."
    bezeichnung = bezeichnung.strip()
    if not bezeichnung or len(bezeichnung) > 150:
        return None, "Die Bezeichnung muss zwischen 1 und 150 Zeichen lang sein."

    if not isinstance(art, str) or art not in ("ETF", "Aktie", "Fonds"):
        return None, "Bitte als Art ETF, Aktie oder Fonds auswählen."

    if link_url is not None:
        if not isinstance(link_url, str):
            return None, "Der Link ist ungültig."
        link_url = link_url.strip() or None
        if link_url and not re.match(r"^https?://", link_url, flags=re.IGNORECASE):
            return None, "Der Link muss mit http:// oder https:// beginnen."

    return {
        "isin": isin,
        "bezeichnung": bezeichnung,
        "art": art,
        "link_url": link_url
    }, None


def wertpapier_select_sql():
    return """
        SELECT
            w.wertpapier_id,
            w.isin,
            w.bezeichnung,
            w.art,
            w.link_url,
            (
                SELECT COUNT(*)
                FROM public.position AS p
                WHERE p.wertpapier_id = w.wertpapier_id
            ) AS verwendungsanzahl
        FROM public.wertpapier AS w
    """


def wertpapier_als_json(zeile):
    return {
        "id": zeile[0],
        "isin": db_text(zeile[1]).strip(),
        "bezeichnung": db_text(zeile[2]),
        "art": db_text(zeile[3]),
        "link_url": db_text(zeile[4]),
        "verwendungsanzahl": int(zeile[5])
    }


# ============================================
# Wertpapiere: GET
# ============================================

@app.route("/api/wertpapiere", methods=["GET"])
def wertpapiere_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(wertpapier_select_sql() + """
                    ORDER BY w.bezeichnung ASC, w.wertpapier_id ASC
                """)
                daten = [wertpapier_als_json(zeile) for zeile in cur.fetchall()]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Wertpapiere")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Wertpapiere."}), 500


# ============================================
# Wertpapiere: POST
# ============================================

@app.route("/api/wertpapiere", methods=["POST"])
def wertpapier_anlegen():
    daten, fehler = wertpapier_eingabe_pruefen(request.get_json(silent=True))
    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.wertpapier
                        (isin, bezeichnung, art, link_url)
                    VALUES (%s, %s, %s, %s)
                    RETURNING wertpapier_id
                """, (daten["isin"], daten["bezeichnung"], daten["art"], daten["link_url"]))
                wertpapier_id = cur.fetchone()[0]

                cur.execute(wertpapier_select_sql() + """
                    WHERE w.wertpapier_id = %s
                """, (wertpapier_id,))
                gespeichertes_wertpapier = wertpapier_als_json(cur.fetchone())

        response = jsonify(gespeichertes_wertpapier)
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except psycopg.errors.UniqueViolation:
        return jsonify({"fehler": "Diese ISIN existiert bereits."}), 409

    except psycopg.Error:
        app.logger.exception("Fehler beim Anlegen eines Wertpapiers")
        return jsonify({"fehler": "Datenbankfehler beim Anlegen des Wertpapiers."}), 500


# ============================================
# Wertpapiere: PUT
# ============================================

@app.route("/api/wertpapiere/<int:wertpapier_id>", methods=["PUT"])
def wertpapier_aendern(wertpapier_id):
    daten, fehler = wertpapier_eingabe_pruefen(request.get_json(silent=True))
    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE public.wertpapier
                    SET
                        isin = %s,
                        bezeichnung = %s,
                        art = %s,
                        link_url = %s
                    WHERE wertpapier_id = %s
                """, (
                    daten["isin"], daten["bezeichnung"], daten["art"],
                    daten["link_url"], wertpapier_id
                ))

                if cur.rowcount == 0:
                    return jsonify({"fehler": "Das Wertpapier wurde nicht gefunden."}), 404

                cur.execute(wertpapier_select_sql() + """
                    WHERE w.wertpapier_id = %s
                """, (wertpapier_id,))
                gespeichertes_wertpapier = wertpapier_als_json(cur.fetchone())

        response = jsonify(gespeichertes_wertpapier)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.errors.UniqueViolation:
        return jsonify({"fehler": "Diese ISIN existiert bereits."}), 409

    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern eines Wertpapiers")
        return jsonify({"fehler": "Datenbankfehler beim Ändern des Wertpapiers."}), 500


# ============================================
# Wertpapiere: DELETE
# ============================================

@app.route("/api/wertpapiere/<int:wertpapier_id>", methods=["DELETE"])
def wertpapier_loeschen(wertpapier_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # FOR UPDATE sperrt den Wertpapier-Datensatz bis zum Ende
                # der Transaktion. So kann waehrend der anschliessenden
                # Verwendungspruefung keine neue FK-Position dazwischenkommen.
                cur.execute("""
                    SELECT wertpapier_id
                    FROM public.wertpapier
                    WHERE wertpapier_id = %s
                    FOR UPDATE
                """, (wertpapier_id,))

                if cur.fetchone() is None:
                    return jsonify({"fehler": "Das Wertpapier wurde nicht gefunden."}), 404

                cur.execute("""
                    SELECT COUNT(*)
                    FROM public.position
                    WHERE wertpapier_id = %s
                """, (wertpapier_id,))
                verwendungsanzahl = int(cur.fetchone()[0])

                if verwendungsanzahl > 0:
                    return jsonify({
                        "fehler": "Löschen nicht möglich: Das Wertpapier wird in "
                                  f"{verwendungsanzahl} Position(en) verwendet.",
                        "verwendungsanzahl": verwendungsanzahl
                    }), 409

                cur.execute("""
                    DELETE FROM public.wertpapier
                    WHERE wertpapier_id = %s
                """, (wertpapier_id,))

        response = jsonify({"ok": True, "id": wertpapier_id})
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Löschen eines Wertpapiers")
        return jsonify({"fehler": "Datenbankfehler beim Löschen des Wertpapiers."}), 500


# ============================================
# Lokaler Start
# ============================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8001
    )
