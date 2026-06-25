from flask import Flask, Response, send_from_directory, jsonify
import urllib.request
import os


app = Flask(__name__)

BASE_PATH = os.path.dirname(os.path.abspath(__file__))



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


        with urllib.request.urlopen(
            req,
            timeout=15
        ) as response:

            data = response.read()


        return Response(
            data,
            status=200,
            mimetype="application/json"
        )


    except Exception as e:

        return jsonify(
            {
                "fehler": str(e),
                "url": url
            }
        ), 500





# ============================================
# Kursdaten über ISIN
# Beispiel:
# /api/IE00B4L5Y983
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
# Beispiel:
# /api/chart/123456
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
# Startseite
# ============================================

@app.route("/")
def index():

    return send_from_directory(
        BASE_PATH,
        "smv.html"
    )





# ============================================
# Direkter Aufruf
# /smv.html
# ============================================

@app.route("/smv.html")
def smv():

    return send_from_directory(
        BASE_PATH,
        "smv.html"
    )





# ============================================
# Lokaler Start
# ============================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8000
    )