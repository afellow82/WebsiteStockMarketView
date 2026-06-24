from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.request


class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):

        # ============================================
        # Tagesanfangskurs über ConsorsID
        # URL:
        # /api/chart/12345678
        # ============================================

        if self.path.startswith("/api/chart/"):


            consors_id = self.path.replace(
                "/api/chart/",
                ""
            )


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



            self.proxy_request(url)





        # ============================================
        # Kursdaten über ISIN
        # URL:
        # /api/IE00B4L5Y983
        # ============================================

        elif self.path.startswith("/api/"):


            isin = self.path.replace(
                "/api/",
                ""
            )


            url = (
                "https://www.consorsbank.de/"
                "web-financialinfo-service/api/marketdata/stocks"
                f"?id={isin}"
                "&field=BasicV1"
                "&field=ExchangesV2"
            )



            self.proxy_request(url)




        else:

            # normale Dateien ausliefern

            super().do_GET()







    # ============================================
    # Gemeinsame Proxy-Funktion
    # ============================================

    def proxy_request(self, url):


        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )


        try:


            with urllib.request.urlopen(req) as response:

                data = response.read()



            self.send_response(200)



            self.send_header(
                "Content-Type",
                "application/json"
            )


            self.send_header(
                "Access-Control-Allow-Origin",
                "*"
            )


            self.end_headers()



            self.wfile.write(data)





        except Exception as e:



            self.send_response(500)



            self.send_header(
                "Content-Type",
                "text/plain"
            )


            self.end_headers()



            self.wfile.write(
                str(e).encode()
            )







print("Server läuft:")
print("http://localhost:8000/etf.html")



HTTPServer(
    ("localhost", 8000),
    Handler
).serve_forever()