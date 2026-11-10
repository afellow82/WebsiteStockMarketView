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
DB_CONFIG_PATH = "/etc/stockmarketview.json"


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
def index():
    return send_from_directory(BASE_PATH, "index.html")


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
# Positionen: gemeinsame Hilfsfunktionen
# ============================================

def position_eingabe_pruefen(daten):
    """Eingabedaten fuer INSERT und UPDATE validieren."""
    if not isinstance(daten, dict):
        return None, "Ungültige JSON-Eingabe."

    besitzer_id = daten.get("besitzer_id")
    wertpapier_id = daten.get("wertpapier_id")

    for feldname, feldwert, bezeichnung in (
        ("besitzer_id", besitzer_id, "Besitzer"),
        ("wertpapier_id", wertpapier_id, "Wertpapier")
    ):
        if (
            isinstance(feldwert, bool)
            or not isinstance(feldwert, int)
            or feldwert <= 0
        ):
            return None, f"Bitte einen gültigen {bezeichnung} auswählen."

    def dezimal_pruefen(rohwert, stellen, maximum, feldname):
        if isinstance(rohwert, bool) or rohwert is None:
            raise ValueError(f"{feldname} fehlt oder ist ungültig.")
        try:
            wert = Decimal(str(rohwert))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{feldname} ist keine gültige Zahl.")
        if not wert.is_finite() or wert < 0:
            raise ValueError(f"{feldname} muss eine nicht-negative Zahl sein.")
        schritt = Decimal("1").scaleb(-stellen)
        try:
            gerundet = wert.quantize(schritt)
        except InvalidOperation:
            raise ValueError(f"{feldname} liegt außerhalb des zulässigen Wertebereichs.")
        if wert != gerundet:
            raise ValueError(f"{feldname} darf höchstens {stellen} Nachkommastellen haben.")
        if gerundet > maximum:
            raise ValueError(f"{feldname} überschreitet den zulässigen Höchstwert.")
        return gerundet

    try:
        anzahl = dezimal_pruefen(
            daten.get("anzahl"), 6, Decimal("999999999999.999999"), "Anzahl"
        )
        investition = dezimal_pruefen(
            daten.get("investition"), 2, Decimal("999999999999.99"), "Investition"
        )
    except ValueError as exc:
        return None, str(exc)

    hinweis = daten.get("investition_hinweis")
    if hinweis is not None and not isinstance(hinweis, str):
        return None, "Der Hinweis zur Investition ist ungültig."
    hinweis = hinweis.strip() or None if isinstance(hinweis, str) else None

    ist_altersvorsorge = daten.get("ist_altersvorsorge")
    if not isinstance(ist_altersvorsorge, bool):
        return None, "Die Angabe zur Altersvorsorge muss Ja oder Nein sein."

    return {
        "besitzer_id": besitzer_id,
        "wertpapier_id": wertpapier_id,
        "anzahl": anzahl,
        "investition": investition,
        "investition_hinweis": hinweis,
        "ist_altersvorsorge": ist_altersvorsorge
    }, None


def position_select_sql():
    return """
        SELECT
            p.position_id,
            p.besitzer_id,
            b.kuerzel,
            b.ist_eigen,
            p.wertpapier_id,
            w.isin,
            w.bezeichnung,
            w.art,
            w.link_url,
            p.anzahl,
            p.investition,
            p.investition_hinweis,
            p.ist_altersvorsorge
        FROM public.position AS p
        JOIN public.besitzer AS b
          ON b.besitzer_id = p.besitzer_id
        JOIN public.wertpapier AS w
          ON w.wertpapier_id = p.wertpapier_id
    """


def position_als_json(zeile):
    return {
        "id": int(zeile[0]),
        "besitzer_id": int(zeile[1]),
        "besitzer": db_text(zeile[2]),
        "ist_eigen": bool(zeile[3]),
        "wertpapier_id": int(zeile[4]),
        "isin": db_text(zeile[5]).strip(),
        "bezeichnung": db_text(zeile[6]),
        "art": db_text(zeile[7]),
        "link_url": db_text(zeile[8]),
        "anzahl": format(zeile[9], ".6f"),
        "investition": format(zeile[10], ".2f"),
        "investition_hinweis": db_text(zeile[11]),
        "ist_altersvorsorge": bool(zeile[12])
    }


# ============================================
# Positionen: GET
# ============================================

@app.route("/api/positionen", methods=["GET"])
def positionen_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(position_select_sql() + """
                    ORDER BY
                        b.ist_eigen DESC,
                        b.kachel_reihenfolge ASC NULLS LAST,
                        b.besitzer_id ASC,
                        w.bezeichnung ASC,
                        p.position_id ASC
                """)
                daten = [position_als_json(zeile) for zeile in cur.fetchall()]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Positionen")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Positionen."}), 500



# ============================================
# Datenstände: GET
# ============================================

@app.route("/api/datenstand", methods=["GET"])
def datenstand_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        MAX(p.anteile_geaendert_am)
                            FILTER (WHERE b.ist_eigen IS TRUE),
                        MAX(p.investition_geaendert_am)
                            FILTER (WHERE b.ist_eigen IS TRUE),
                        MAX(p.anteile_geaendert_am)
                            FILTER (WHERE b.ist_eigen IS FALSE),
                        MAX(p.investition_geaendert_am)
                            FILTER (WHERE b.ist_eigen IS FALSE)
                    FROM public.position AS p
                    JOIN public.besitzer AS b
                      ON b.besitzer_id = p.besitzer_id
                """)

                zeile = cur.fetchone()

        def datum_iso(wert):
            if wert is None:
                return None
            if hasattr(wert, "date"):
                wert = wert.date()
            return wert.isoformat()

        response = jsonify({
            "anteile_eigen": datum_iso(zeile[0]),
            "investition_eigen": datum_iso(zeile[1]),
            "anteile_fremd": datum_iso(zeile[2]),
            "investition_fremd": datum_iso(zeile[3])
        })

        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Datenstände")
        return jsonify({
            "fehler": "Datenbankfehler beim Lesen der Datenstände."
        }), 500


# ============================================
# Positionen: POST
# ============================================

@app.route("/api/positionen", methods=["POST"])
def position_anlegen():
    daten, fehler = position_eingabe_pruefen(request.get_json(silent=True))
    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.position (
                        besitzer_id,
                        wertpapier_id,
                        anzahl,
                        investition,
                        investition_hinweis,
                        ist_altersvorsorge
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING position_id
                """, (
                    daten["besitzer_id"],
                    daten["wertpapier_id"],
                    daten["anzahl"],
                    daten["investition"],
                    daten["investition_hinweis"],
                    daten["ist_altersvorsorge"]
                ))
                position_id = cur.fetchone()[0]

                cur.execute(position_select_sql() + """
                    WHERE p.position_id = %s
                """, (position_id,))
                gespeicherte_position = position_als_json(cur.fetchone())

        response = jsonify(gespeicherte_position)
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except psycopg.errors.UniqueViolation:
        return jsonify({
            "fehler": "Für diesen Besitzer existiert dieses Wertpapier bereits als Position."
        }), 409

    except psycopg.errors.ForeignKeyViolation:
        return jsonify({
            "fehler": "Der ausgewählte Besitzer oder das Wertpapier existiert nicht mehr. Bitte die Liste neu laden."
        }), 409

    except psycopg.Error:
        app.logger.exception("Fehler beim Anlegen einer Position")
        return jsonify({"fehler": "Datenbankfehler beim Anlegen der Position."}), 500


# ============================================
# Positionen: PUT
# ============================================

@app.route("/api/positionen/<int:position_id>", methods=["PUT"])
def position_aendern(position_id):
    daten, fehler = position_eingabe_pruefen(request.get_json(silent=True))
    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE public.position
                    SET
                        besitzer_id = %s,
                        wertpapier_id = %s,
                        anzahl = %s,
                        investition = %s,
                        investition_hinweis = %s,
                        ist_altersvorsorge = %s
                    WHERE position_id = %s
                """, (
                    daten["besitzer_id"],
                    daten["wertpapier_id"],
                    daten["anzahl"],
                    daten["investition"],
                    daten["investition_hinweis"],
                    daten["ist_altersvorsorge"],
                    position_id
                ))

                if cur.rowcount == 0:
                    return jsonify({"fehler": "Die Position wurde nicht gefunden."}), 404

                cur.execute(position_select_sql() + """
                    WHERE p.position_id = %s
                """, (position_id,))
                gespeicherte_position = position_als_json(cur.fetchone())

        response = jsonify(gespeicherte_position)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.errors.UniqueViolation:
        return jsonify({
            "fehler": "Für diesen Besitzer existiert dieses Wertpapier bereits als Position."
        }), 409

    except psycopg.errors.ForeignKeyViolation:
        return jsonify({
            "fehler": "Der ausgewählte Besitzer oder das Wertpapier existiert nicht mehr. Bitte die Liste neu laden."
        }), 409

    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern einer Position")
        return jsonify({"fehler": "Datenbankfehler beim Ändern der Position."}), 500


# ============================================
# Positionen: DELETE
# ============================================

@app.route("/api/positionen/<int:position_id>", methods=["DELETE"])
def position_loeschen(position_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.position
                    WHERE position_id = %s
                """, (position_id,))

                if cur.rowcount == 0:
                    return jsonify({"fehler": "Die Position wurde nicht gefunden."}), 404

        response = jsonify({"ok": True, "id": position_id})
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Löschen einer Position")
        return jsonify({"fehler": "Datenbankfehler beim Löschen der Position."}), 500


# ============================================
# Gemeinsame Hilfsfunktionen für die drei Planungstabellen
# ============================================

def _smv_json_objekt():
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict):
        raise ValueError("Ungültige JSON-Eingabe.")
    return daten


def _smv_int(daten, feld, bezeichnung, minimum=None, maximum=None):
    wert = daten.get(feld)
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise ValueError(f"{bezeichnung} muss eine ganze Zahl sein.")
    if minimum is not None and wert < minimum:
        raise ValueError(f"{bezeichnung} muss mindestens {minimum} sein.")
    if maximum is not None and wert > maximum:
        raise ValueError(f"{bezeichnung} darf höchstens {maximum} sein.")
    return wert


def _smv_decimal(daten, feld, bezeichnung, precision, scale, nullable=False):
    rohwert = daten.get(feld)
    if rohwert is None:
        if nullable:
            return None
        raise ValueError(f"{bezeichnung} fehlt.")
    if isinstance(rohwert, bool):
        raise ValueError(f"{bezeichnung} ist ungültig.")
    try:
        wert = Decimal(str(rohwert))
        if not wert.is_finite():
            raise ValueError()
        quantum = Decimal(1).scaleb(-scale)
        gerundet = wert.quantize(quantum)
        if wert != gerundet:
            raise ValueError()
        if abs(wert) >= Decimal(10) ** (precision - scale):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"{bezeichnung} darf höchstens {scale} Nachkommastellen und "
            f"insgesamt {precision} Stellen haben."
        )
    return wert


def _smv_text(daten, feld, bezeichnung, maximum):
    wert = daten.get(feld)
    if not isinstance(wert, str):
        raise ValueError(f"{bezeichnung} fehlt oder ist ungültig.")
    wert = wert.strip()
    if not wert or len(wert) > maximum:
        raise ValueError(f"{bezeichnung} darf nicht leer sein und höchstens {maximum} Zeichen enthalten.")
    return wert


def _smv_datum(daten, feld, bezeichnung):
    rohwert = daten.get(feld)
    if not isinstance(rohwert, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rohwert):
        raise ValueError(f"{bezeichnung} muss im Format JJJJ-MM-TT angegeben werden.")
    try:
        return date.fromisoformat(rohwert)
    except ValueError:
        raise ValueError(f"{bezeichnung} ist kein gültiges Kalenderdatum.")


def _smv_eigene_position_pruefen(cur, position_id):
    cur.execute("""
        SELECT 1
        FROM public.position AS p
        JOIN public.besitzer AS b
          ON b.besitzer_id = p.besitzer_id
        WHERE p.position_id = %s
          AND b.ist_eigen = true
    """, (position_id,))
    if cur.fetchone() is None:
        raise ValueError("Die ausgewählte Position ist nicht vorhanden oder gehört nicht zum eigenen Bestand.")


def _smv_decimal_json(wert):
    return None if wert is None else format(wert, "f")


def _smv_sparplan_json(zeile):
    return {
        "sparplan_id": zeile[0],
        "position_id": zeile[1],
        "betrag": _smv_decimal_json(zeile[2]),
        "rhythmus": db_text(zeile[3]),
        "tag_im_monat": zeile[4]
    }


def _smv_geplanter_kauf_json(zeile):
    return {
        "kauf_id": zeile[0],
        "position_id": zeile[1],
        "zeitpunkt_text": db_text(zeile[2]),
        "zeitpunkt_datum": zeile[3].isoformat(),
        "kommentar": db_text(zeile[4]),
        "berechnungsart": db_text(zeile[5]),
        "kaufpreis": _smv_decimal_json(zeile[6]),
        "anzahl_fix": _smv_decimal_json(zeile[7]),
        "wert_fix": _smv_decimal_json(zeile[8]),
        "faktor_auf_kaufpreis": _smv_decimal_json(zeile[9])
    }


# ============================================
# Jahresanfangswerte: GET
# ============================================

@app.route("/api/position-jahresanfang", methods=["GET"])
def position_jahresanfang_lesen():
    jahr_roh = request.args.get("jahr")
    if jahr_roh is not None:
        if not re.fullmatch(r"\d{4}", jahr_roh):
            return jsonify({"fehler": "Ungültiges Jahr."}), 400
        jahr = int(jahr_roh)
        if jahr < 2000 or jahr > 2100:
            return jsonify({"fehler": "Das Jahr muss zwischen 2000 und 2100 liegen."}), 400
    else:
        jahr = None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if jahr is None:
                    cur.execute("""
                        SELECT ja.position_id, ja.jahr, ja.anzahl, ja.kurs
                        FROM public.position_jahresanfang AS ja
                        JOIN public.position AS p
                          ON p.position_id = ja.position_id
                        JOIN public.besitzer AS b
                          ON b.besitzer_id = p.besitzer_id
                        WHERE b.ist_eigen = true
                        ORDER BY ja.jahr DESC, ja.position_id ASC
                    """)
                else:
                    cur.execute("""
                        SELECT ja.position_id, ja.jahr, ja.anzahl, ja.kurs
                        FROM public.position_jahresanfang AS ja
                        JOIN public.position AS p
                          ON p.position_id = ja.position_id
                        JOIN public.besitzer AS b
                          ON b.besitzer_id = p.besitzer_id
                        WHERE b.ist_eigen = true
                          AND ja.jahr = %s
                        ORDER BY ja.position_id ASC
                    """, (jahr,))

                daten = [
                    {
                        "position_id": zeile[0],
                        "jahr": zeile[1],
                        "anzahl": _smv_decimal_json(zeile[2]),
                        "kurs": _smv_decimal_json(zeile[3])
                    }
                    for zeile in cur.fetchall()
                ]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Jahresanfangswerte")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Jahresanfangswerte."}), 500


# ============================================
# Jahresanfangswerte: POST (neu erfassen)
# ============================================

@app.route("/api/position-jahresanfang", methods=["POST"])
def position_jahresanfang_einfuegen():
    try:
        daten = _smv_json_objekt()
        position_id = _smv_int(daten, "position_id", "Positions-ID", 1)
        jahr = _smv_int(daten, "jahr", "Jahr", 2000, 2100)
        anzahl = _smv_decimal(daten, "anzahl", "Anzahl", 18, 6)
        kurs = _smv_decimal(daten, "kurs", "Kurs", 14, 4)
        if anzahl < 0:
            raise ValueError("Die Anzahl darf nicht negativ sein.")
        if kurs <= 0:
            raise ValueError("Der Kurs muss größer als 0 sein.")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _smv_eigene_position_pruefen(cur, position_id)
                cur.execute("""
                    INSERT INTO public.position_jahresanfang
                        (position_id, jahr, anzahl, kurs)
                    VALUES (%s, %s, %s, %s)
                    RETURNING position_id, jahr, anzahl, kurs
                """, (position_id, jahr, anzahl, kurs))
                zeile = cur.fetchone()

        response = jsonify({
            "position_id": zeile[0],
            "jahr": zeile[1],
            "anzahl": _smv_decimal_json(zeile[2]),
            "kurs": _smv_decimal_json(zeile[3])
        })
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.errors.UniqueViolation:
        return jsonify({"fehler": "Für diese Position und dieses Jahr existieren bereits Jahresanfangswerte."}), 409
    except psycopg.Error:
        app.logger.exception("Fehler beim Einfügen der Jahresanfangswerte")
        return jsonify({"fehler": "Datenbankfehler beim Speichern der Jahresanfangswerte."}), 500


# ============================================
# Jahresanfangswerte: PUT (ändern; kein DELETE vorgesehen)
# ============================================

@app.route("/api/position-jahresanfang/<int:position_id>/<int:jahr>", methods=["PUT"])
def position_jahresanfang_aendern(position_id, jahr):
    try:
        daten = _smv_json_objekt()
        if daten.get("position_id", position_id) != position_id or daten.get("jahr", jahr) != jahr:
            raise ValueError("Positions-ID und Jahr im JSON müssen zur Adresse passen.")
        if jahr < 2000 or jahr > 2100:
            raise ValueError("Das Jahr muss zwischen 2000 und 2100 liegen.")
        anzahl = _smv_decimal(daten, "anzahl", "Anzahl", 18, 6)
        kurs = _smv_decimal(daten, "kurs", "Kurs", 14, 4)
        if anzahl < 0:
            raise ValueError("Die Anzahl darf nicht negativ sein.")
        if kurs <= 0:
            raise ValueError("Der Kurs muss größer als 0 sein.")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE public.position_jahresanfang AS ja
                    SET anzahl = %s,
                        kurs = %s
                    WHERE ja.position_id = %s
                      AND ja.jahr = %s
                      AND EXISTS (
                          SELECT 1
                          FROM public.position AS p
                          JOIN public.besitzer AS b
                            ON b.besitzer_id = p.besitzer_id
                          WHERE p.position_id = ja.position_id
                            AND b.ist_eigen = true
                      )
                    RETURNING ja.position_id, ja.jahr, ja.anzahl, ja.kurs
                """, (anzahl, kurs, position_id, jahr))
                zeile = cur.fetchone()
                if zeile is None:
                    return jsonify({"fehler": "Der Jahresanfangswert wurde nicht gefunden."}), 404

        response = jsonify({
            "position_id": zeile[0],
            "jahr": zeile[1],
            "anzahl": _smv_decimal_json(zeile[2]),
            "kurs": _smv_decimal_json(zeile[3])
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern der Jahresanfangswerte")
        return jsonify({"fehler": "Datenbankfehler beim Ändern der Jahresanfangswerte."}), 500


# ============================================
# Sparpläne: GET
# ============================================

@app.route("/api/sparplaene", methods=["GET"])
def sparplaene_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT s.sparplan_id, s.position_id, s.betrag,
                           s.rhythmus, s.tag_im_monat
                    FROM public.sparplan AS s
                    JOIN public.position AS p
                      ON p.position_id = s.position_id
                    JOIN public.besitzer AS b
                      ON b.besitzer_id = p.besitzer_id
                    WHERE b.ist_eigen = true
                    ORDER BY s.position_id ASC
                """)
                daten = [_smv_sparplan_json(z) for z in cur.fetchall()]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Sparpläne")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der Sparpläne."}), 500


# ============================================
# Sparpläne: POST (neu anlegen)
# ============================================

@app.route("/api/sparplaene", methods=["POST"])
def sparplan_einfuegen():
    try:
        daten = _smv_json_objekt()
        position_id = _smv_int(daten, "position_id", "Positions-ID", 1)
        betrag = _smv_decimal(daten, "betrag", "Betrag", 12, 2)
        rhythmus = daten.get("rhythmus", "monatlich")
        tag = _smv_int(daten, "tag_im_monat", "Tag im Monat", 1, 28)
        if betrag <= 0:
            raise ValueError("Der Sparplanbetrag muss größer als 0 sein.")
        if rhythmus != "monatlich":
            raise ValueError("Der Rhythmus muss 'monatlich' sein.")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _smv_eigene_position_pruefen(cur, position_id)
                cur.execute("""
                    INSERT INTO public.sparplan
                        (position_id, betrag, rhythmus, tag_im_monat)
                    VALUES (%s, %s, %s, %s)
                    RETURNING sparplan_id, position_id, betrag, rhythmus, tag_im_monat
                """, (position_id, betrag, rhythmus, tag))
                zeile = cur.fetchone()

        response = jsonify(_smv_sparplan_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.errors.UniqueViolation:
        return jsonify({"fehler": "Für diese Position existiert bereits ein Sparplan."}), 409
    except psycopg.Error:
        app.logger.exception("Fehler beim Einfügen des Sparplans")
        return jsonify({"fehler": "Datenbankfehler beim Anlegen des Sparplans."}), 500


# ============================================
# Sparpläne: PUT (ändern)
# ============================================

@app.route("/api/sparplaene/<int:sparplan_id>", methods=["PUT"])
def sparplan_aendern(sparplan_id):
    try:
        daten = _smv_json_objekt()
        position_id = _smv_int(daten, "position_id", "Positions-ID", 1)
        betrag = _smv_decimal(daten, "betrag", "Betrag", 12, 2)
        rhythmus = daten.get("rhythmus", "monatlich")
        tag = _smv_int(daten, "tag_im_monat", "Tag im Monat", 1, 28)
        if betrag <= 0:
            raise ValueError("Der Sparplanbetrag muss größer als 0 sein.")
        if rhythmus != "monatlich":
            raise ValueError("Der Rhythmus muss 'monatlich' sein.")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _smv_eigene_position_pruefen(cur, position_id)
                cur.execute("""
                    UPDATE public.sparplan AS s
                    SET position_id = %s,
                        betrag = %s,
                        rhythmus = %s,
                        tag_im_monat = %s
                    WHERE s.sparplan_id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM public.position AS p
                          JOIN public.besitzer AS b
                            ON b.besitzer_id = p.besitzer_id
                          WHERE p.position_id = s.position_id
                            AND b.ist_eigen = true
                      )
                    RETURNING s.sparplan_id, s.position_id, s.betrag,
                              s.rhythmus, s.tag_im_monat
                """, (position_id, betrag, rhythmus, tag, sparplan_id))
                zeile = cur.fetchone()
                if zeile is None:
                    return jsonify({"fehler": "Der Sparplan wurde nicht gefunden."}), 404

        response = jsonify(_smv_sparplan_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.errors.UniqueViolation:
        return jsonify({"fehler": "Für diese Position existiert bereits ein Sparplan."}), 409
    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern des Sparplans")
        return jsonify({"fehler": "Datenbankfehler beim Ändern des Sparplans."}), 500


# ============================================
# Sparpläne: DELETE
# ============================================

@app.route("/api/sparplaene/<int:sparplan_id>", methods=["DELETE"])
def sparplan_loeschen(sparplan_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.sparplan AS s
                    WHERE s.sparplan_id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM public.position AS p
                          JOIN public.besitzer AS b
                            ON b.besitzer_id = p.besitzer_id
                          WHERE p.position_id = s.position_id
                            AND b.ist_eigen = true
                      )
                    RETURNING s.sparplan_id
                """, (sparplan_id,))
                zeile = cur.fetchone()
                if zeile is None:
                    return jsonify({"fehler": "Der Sparplan wurde nicht gefunden."}), 404

        response = jsonify({"ok": True, "sparplan_id": zeile[0]})
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Löschen des Sparplans")
        return jsonify({"fehler": "Datenbankfehler beim Löschen des Sparplans."}), 500


# ============================================
# Geplante Käufe: GET
# ============================================

@app.route("/api/geplante-kaeufe", methods=["GET"])
def geplante_kaeufe_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT g.kauf_id, g.position_id, g.zeitpunkt_text,
                           g.zeitpunkt_datum, g.kommentar, g.berechnungsart,
                           g.kaufpreis, g.anzahl_fix, g.wert_fix,
                           g.faktor_auf_kaufpreis
                    FROM public.geplanter_kauf AS g
                    JOIN public.position AS p
                      ON p.position_id = g.position_id
                    JOIN public.besitzer AS b
                      ON b.besitzer_id = p.besitzer_id
                    WHERE b.ist_eigen = true
                    ORDER BY g.zeitpunkt_datum ASC, g.kauf_id ASC
                """)
                daten = [_smv_geplanter_kauf_json(z) for z in cur.fetchall()]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der geplanten Käufe")
        return jsonify({"fehler": "Datenbankfehler beim Lesen der geplanten Käufe."}), 500


# ============================================
# Geplante Käufe: Eingabe prüfen
# ============================================

def _smv_geplanter_kauf_eingabe(daten):
    position_id = _smv_int(daten, "position_id", "Positions-ID", 1)
    zeitpunkt_text = _smv_text(daten, "zeitpunkt_text", "Zeitpunkt-Text", 30)
    zeitpunkt_datum = _smv_datum(daten, "zeitpunkt_datum", "Zeitpunkt-Datum")
    kommentar = _smv_text(daten, "kommentar", "Kommentar", 200)
    berechnungsart = daten.get("berechnungsart")
    if berechnungsart not in ("ANZAHL_FIX", "WERT_AUS_KAUFPREIS", "WERT_FIX"):
        raise ValueError("Ungültige Berechnungsart.")

    kaufpreis = _smv_decimal(daten, "kaufpreis", "Kaufpreis", 12, 2)
    anzahl_fix = _smv_decimal(daten, "anzahl_fix", "Feste Anzahl", 18, 6, nullable=True)
    wert_fix = _smv_decimal(daten, "wert_fix", "Fester Wert", 14, 2, nullable=True)
    faktor = _smv_decimal(daten, "faktor_auf_kaufpreis", "Faktor", 12, 10, nullable=True)

    if kaufpreis < 0:
        raise ValueError("Der Kaufpreis darf nicht negativ sein.")
    if berechnungsart == "ANZAHL_FIX":
        if anzahl_fix is None or anzahl_fix <= 0 or wert_fix is not None or faktor is not None:
            raise ValueError("Bei ANZAHL_FIX ist nur eine positive feste Anzahl zulässig.")
    elif berechnungsart == "WERT_FIX":
        if wert_fix is None or wert_fix <= 0 or anzahl_fix is not None or faktor is not None:
            raise ValueError("Bei WERT_FIX ist nur ein positiver fester Wert zulässig.")
    else:
        if faktor is None or faktor <= 0 or anzahl_fix is not None or wert_fix is not None:
            raise ValueError("Bei WERT_AUS_KAUFPREIS ist nur ein positiver Faktor zulässig.")

    return (
        position_id, zeitpunkt_text, zeitpunkt_datum, kommentar,
        berechnungsart, kaufpreis, anzahl_fix, wert_fix, faktor
    )


# ============================================
# Geplante Käufe: POST (neu anlegen)
# ============================================

@app.route("/api/geplante-kaeufe", methods=["POST"])
def geplanter_kauf_einfuegen():
    try:
        werte = _smv_geplanter_kauf_eingabe(_smv_json_objekt())
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _smv_eigene_position_pruefen(cur, werte[0])
                cur.execute("""
                    INSERT INTO public.geplanter_kauf
                        (position_id, zeitpunkt_text, zeitpunkt_datum, kommentar,
                         berechnungsart, kaufpreis, anzahl_fix, wert_fix,
                         faktor_auf_kaufpreis)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING kauf_id, position_id, zeitpunkt_text, zeitpunkt_datum,
                              kommentar, berechnungsart, kaufpreis, anzahl_fix,
                              wert_fix, faktor_auf_kaufpreis
                """, werte)
                zeile = cur.fetchone()

        response = jsonify(_smv_geplanter_kauf_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.Error:
        app.logger.exception("Fehler beim Einfügen des geplanten Kaufs")
        return jsonify({"fehler": "Datenbankfehler beim Anlegen des geplanten Kaufs."}), 500


# ============================================
# Geplante Käufe: PUT (ändern)
# ============================================

@app.route("/api/geplante-kaeufe/<int:kauf_id>", methods=["PUT"])
def geplanter_kauf_aendern(kauf_id):
    try:
        werte = _smv_geplanter_kauf_eingabe(_smv_json_objekt())
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _smv_eigene_position_pruefen(cur, werte[0])
                cur.execute("""
                    UPDATE public.geplanter_kauf AS g
                    SET position_id = %s,
                        zeitpunkt_text = %s,
                        zeitpunkt_datum = %s,
                        kommentar = %s,
                        berechnungsart = %s,
                        kaufpreis = %s,
                        anzahl_fix = %s,
                        wert_fix = %s,
                        faktor_auf_kaufpreis = %s
                    WHERE g.kauf_id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM public.position AS p
                          JOIN public.besitzer AS b
                            ON b.besitzer_id = p.besitzer_id
                          WHERE p.position_id = g.position_id
                            AND b.ist_eigen = true
                      )
                    RETURNING g.kauf_id, g.position_id, g.zeitpunkt_text,
                              g.zeitpunkt_datum, g.kommentar, g.berechnungsart,
                              g.kaufpreis, g.anzahl_fix, g.wert_fix,
                              g.faktor_auf_kaufpreis
                """, (*werte, kauf_id))
                zeile = cur.fetchone()
                if zeile is None:
                    return jsonify({"fehler": "Der geplante Kauf wurde nicht gefunden."}), 404

        response = jsonify(_smv_geplanter_kauf_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response

    except ValueError as e:
        return jsonify({"fehler": str(e)}), 400
    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern des geplanten Kaufs")
        return jsonify({"fehler": "Datenbankfehler beim Ändern des geplanten Kaufs."}), 500


# ============================================
# Geplante Käufe: DELETE
# ============================================

@app.route("/api/geplante-kaeufe/<int:kauf_id>", methods=["DELETE"])
def geplanter_kauf_loeschen(kauf_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.geplanter_kauf AS g
                    WHERE g.kauf_id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM public.position AS p
                          JOIN public.besitzer AS b
                            ON b.besitzer_id = p.besitzer_id
                          WHERE p.position_id = g.position_id
                            AND b.ist_eigen = true
                      )
                    RETURNING g.kauf_id
                """, (kauf_id,))
                zeile = cur.fetchone()
                if zeile is None:
                    return jsonify({"fehler": "Der geplante Kauf wurde nicht gefunden."}), 404

        response = jsonify({"ok": True, "kauf_id": zeile[0]})
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Löschen des geplanten Kaufs")
        return jsonify({"fehler": "Datenbankfehler beim Löschen des geplanten Kaufs."}), 500



# ============================================
# Notizen: gemeinsame Hilfsfunktionen
# ============================================

NOTIZ_KATEGORIEN = ("Anforderung", "Fehler")


def notiz_eingabe_pruefen(daten):
    if not isinstance(daten, dict):
        return None, "Ungültige JSON-Eingabe."

    kategorie = daten.get("kategorie")
    text = daten.get("text")

    if not isinstance(kategorie, str):
        return None, "Bitte eine Kategorie auswählen."

    kategorie = kategorie.strip()

    if kategorie not in NOTIZ_KATEGORIEN:
        return None, "Zulässig sind nur Anforderung und Fehler."

    if not isinstance(text, str):
        return None, "Bitte einen Notiztext eingeben."

    text = text.strip()

    if not text:
        return None, "Der Notiztext darf nicht leer sein."

    return {
        "kategorie": kategorie,
        "text": text
    }, None


def notiz_als_json(zeile):
    return {
        "id": int(zeile[0]),
        "zeitpunkt": zeile[1].isoformat(),
        "kategorie": db_text(zeile[2]),
        "text": db_text(zeile[3])
    }


# ============================================
# Notizen: GET
# ============================================

@app.route("/api/notiz", methods=["GET"])
def notizen_lesen():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, zeitpunkt, kategorie, text
                    FROM public.notiz
                    ORDER BY zeitpunkt DESC, id DESC
                """)

                daten = [
                    notiz_als_json(zeile)
                    for zeile in cur.fetchall()
                ]

        response = jsonify(daten)
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Lesen der Notizen")
        return jsonify({
            "fehler": "Datenbankfehler beim Lesen der Notizen."
        }), 500


# ============================================
# Notizen: POST
# ============================================

@app.route("/api/notiz", methods=["POST"])
def notiz_anlegen():
    daten, fehler = notiz_eingabe_pruefen(
        request.get_json(silent=True)
    )

    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.notiz (kategorie, text)
                    VALUES (%s, %s)
                    RETURNING id, zeitpunkt, kategorie, text
                """, (daten["kategorie"], daten["text"]))

                zeile = cur.fetchone()

        response = jsonify(notiz_als_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    except psycopg.Error:
        app.logger.exception("Fehler beim Anlegen einer Notiz")
        return jsonify({
            "fehler": "Datenbankfehler beim Anlegen der Notiz."
        }), 500


# ============================================
# Notizen: PUT
# ============================================

@app.route("/api/notiz/<int:notiz_id>", methods=["PUT"])
def notiz_aendern(notiz_id):
    daten, fehler = notiz_eingabe_pruefen(
        request.get_json(silent=True)
    )

    if fehler:
        return jsonify({"fehler": fehler}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE public.notiz
                    SET
                        kategorie = %s,
                        text = %s
                    WHERE id = %s
                    RETURNING id, zeitpunkt, kategorie, text
                """, (
                    daten["kategorie"],
                    daten["text"],
                    notiz_id
                ))

                zeile = cur.fetchone()

                if zeile is None:
                    return jsonify({
                        "fehler": "Die Notiz wurde nicht gefunden."
                    }), 404

        response = jsonify(notiz_als_json(zeile))
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Ändern einer Notiz")
        return jsonify({
            "fehler": "Datenbankfehler beim Ändern der Notiz."
        }), 500


# ============================================
# Notizen: DELETE
# ============================================

@app.route("/api/notiz/<int:notiz_id>", methods=["DELETE"])
def notiz_loeschen(notiz_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.notiz
                    WHERE id = %s
                    RETURNING id
                """, (notiz_id,))

                zeile = cur.fetchone()

                if zeile is None:
                    return jsonify({
                        "fehler": "Die Notiz wurde nicht gefunden."
                    }), 404

        response = jsonify({
            "ok": True,
            "id": zeile[0]
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    except psycopg.Error:
        app.logger.exception("Fehler beim Löschen einer Notiz")
        return jsonify({
            "fehler": "Datenbankfehler beim Löschen der Notiz."
        }), 500


# ============================================
# Lokaler Start
# ============================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8000
    )
