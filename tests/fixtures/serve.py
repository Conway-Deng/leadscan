"""
tests/fixtures/serve.py
-----------------------
A tiny local website that imitates four real Singapore small-business sites.
It lets the whole pipeline be run end to end with no internet access and no
Google Places key, so the scoring can be checked against a known answer.

    python tests/fixtures/serve.py 8099

Then, in another terminal:

    python leadscan.py --input tests/fixtures/fixture_businesses.csv --include-cool
"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# 1. The perfect lead: a live Meta Pixel and a Google Ads tag, an Instagram
#    account, no form, no phone link, no viewport, and a slow-looking page.
HOT = """<!doctype html><html><head><title>Quiet Interiors</title>
<script>!function(f,b,e,v,n,t,s){f.fbq=n}(window);fbq('init','101010101010');</script>
<script src="https://www.googletagmanager.com/gtag/js?id=AW-987654321"></script>
</head><body>
<h1>Quiet Interiors</h1>
<p>Award-winning HDB and condo renovation. Book a free consultation today.</p>
<a href="https://www.instagram.com/quietinteriors.sg/">Instagram</a>
<a href="https://www.tiktok.com/@quietinteriors">TikTok</a>
<p>Drop by our showroom at 1 Ubi Road.</p>
</body></html>"""

# 2. Organic only: an Instagram account, no ad tag, no capture path.
WARM = """<!doctype html><html><head><title>Studio Lumen</title>
<meta name="viewport" content="width=device-width">
</head><body>
<h1>Studio Lumen</h1>
<p>Interior styling for small homes.</p>
<a href="https://www.instagram.com/studiolumen.sg/">Follow us</a>
</body></html>"""

# 3. Analytics only, and it has a real contact form. The old version called
#    this a HOT confirmed ad-spender. It is neither hot nor a lead.
SOLID = """<!doctype html><html><head><title>Northline Design</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://www.googletagmanager.com/gtag/js?id=G-ABCDE12345"></script>
</head><body>
<h1>Northline Design</h1>
<form action="/enquiry" method="post">
  <input type="text" name="name" placeholder="Your name">
  <input type="email" name="email" placeholder="Your email">
  <textarea name="message"></textarea>
  <button type="submit">Send</button>
</form>
<a href="tel:+6561234567">6123 4567</a>
</body></html>"""

# 4. Footer noise only. No real profile link anywhere, despite four links that
#    look like social links to a naive scanner.
NOISE = """<!doctype html><html xmlns:fb="http://www.facebook.com/2008/fbml">
<head><title>Elm and Line</title>
<meta name="viewport" content="width=device-width"></head><body>
<h1>Elm &amp; Line</h1>
<p>Bespoke carpentry. Free quote on request.</p>
<img src="https://www.facebook.com/tr?id=0&ev=PageView">
<a href="https://developers.facebook.com/docs/plugins/">plugin docs</a>
<a href="https://www.instagram.com/p/CxAbCdEf123/">a recent post</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">share</a>
</body></html>"""

PAGES = {"/hot": HOT, "/warm": WARM, "/solid": SOLID, "/noise": NOISE}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.rstrip("/") or "/hot")
        if body is None:
            self.send_error(404)
            return
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"Fixture site on http://127.0.0.1:{port}/  (hot, warm, solid, noise)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
