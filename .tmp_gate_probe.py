from mobile_ingestion import create_app
from mobile_ingestion.config import AppConfig
from werkzeug.serving import make_server
import threading
from playwright.sync_api import sync_playwright

app = create_app(AppConfig(testing=True, voice_wake_phrases=("ok jarvis", "hey atlas"), voice_idle_timeout_seconds=12))
server = make_server("127.0.0.1", 0, app)
port = server.socket.getsockname()[1]
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
url = f"http://127.0.0.1:{port}"
print("URL", url)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("console", lambda msg: print("console", msg.type, msg.text))
    page.goto(url, wait_until="networkidle")
    print("body class=", page.evaluate("document.body.className"))
    print("gate class=", page.evaluate("document.getElementById('gate-screen').className"))
    print("gate display=", page.evaluate("getComputedStyle(document.getElementById('gate-screen')).display"))
    browser.close()

server.shutdown()
