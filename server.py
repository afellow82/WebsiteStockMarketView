from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
import urllib.request


class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):

        # API Weiterleitung
        if self.path.startswith("/api/"):

            isin = self.path.replace("/api/", "")

            url = (
                "https://www.consorsbank.de/"
                "web-financialinfo-service/api/marketdata/stocks"
                f"?id={isin}&field=BasicV1&field=ExchangesV2"
            )


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

                self.end_headers()

                self.wfile.write(data)


            except Exception as e:

                self.send_response(500)

                self.end_headers()

                self.wfile.write(
                    str(e).encode()
                )


        else:

            # normale Dateien ausliefern
            super().do_GET()



print("Server läuft:")
print("http://localhost:8000/etf.html")


HTTPServer(
    ("localhost", 8000),
    Handler
).serve_forever()