"""
Record an annotated demo video of the Vakantie BV agent using Playwright.
All annotations are injected directly via page.evaluate with inline JS.
"""

import os
import time
import signal
import subprocess
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_SCRIPT = PROJECT_DIR / "vakantie_rdf_backend.py"
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"

FRONTEND_URL = (PROJECT_DIR / "vakantie-agent.html").as_uri()
VIDEO_DIR = str(PROJECT_DIR / "docs")

KLANT_MSG = (
    "Boek voor een nieuwe klant: Aag van der Zee (aag@vanderzee.nl). "
    "Zij wil een reis boeken naar Carlton Beach Knokke, een vijfsterren hotel "
    "in Knokke, België. Kamers kosten 850 EUR per nacht. "
    "Aag wil op 1 juli aankomen en veertien dagen verblijven voor 2 personen."
)

ADMIN_MSG = (
    "Maak alles aan wat nodig is en boek de reis: "
    "klant Aag van der Zee (aag@vanderzee.nl), "
    "bestemming Knokke (België, Gematigd klimaat), "
    "hotel Carlton Beach Knokke (5 sterren, 850 EUR/nacht, 40 kamers), "
    "boeking 1 juli t/m 15 juli 2025 voor 2 personen."
)


def setup_styles(page):
    """Inject animation keyframes once."""
    page.evaluate("""
    (() => {
        if (document.getElementById('demo-styles')) return;
        const s = document.createElement('style');
        s.id = 'demo-styles';
        s.textContent = `
            @keyframes demoFadeIn { from {opacity:0;transform:translate(-50%,-50%) translateY(12px)} to {opacity:1;transform:translate(-50%,-50%) translateY(0)} }
            @keyframes demoFadeOut { from {opacity:1} to {opacity:0} }
            @keyframes demoCardIn { from {opacity:0} to {opacity:1} }
        `;
        document.head.appendChild(s);
    })();
    """)


def show_title(page, title, subtitle, duration=4):
    setup_styles(page)
    page.evaluate("""(args) => {
        document.getElementById('demo-title')?.remove();
        const card = document.createElement('div');
        card.id = 'demo-title';
        card.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(6,11,20,0.92);color:#f9fafb;font-family:system-ui,sans-serif;animation:demoCardIn 0.5s ease-out;';
        const h = document.createElement('div');
        h.textContent = args[0];
        h.style.cssText = 'font-size:36px;font-weight:800;margin-bottom:12px;';
        card.appendChild(h);
        const sub = document.createElement('div');
        sub.textContent = args[1];
        sub.style.cssText = 'font-size:20px;color:#9ca3af;max-width:700px;text-align:center;';
        card.appendChild(sub);
        document.body.appendChild(card);
    }""", [title, subtitle])
    time.sleep(duration)
    page.evaluate("""() => {
        const c = document.getElementById('demo-title');
        if (c) { c.style.animation = 'demoFadeOut 0.5s forwards'; setTimeout(() => c.remove(), 500); }
    }""")
    time.sleep(0.6)


def show_banner(page, text, color="#f97316", duration=None):
    setup_styles(page)
    page.evaluate("""(args) => {
        document.getElementById('demo-banner')?.remove();
        const bar = document.createElement('div');
        bar.id = 'demo-banner';
        bar.textContent = args[0];
        bar.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:99999;padding:14px 28px;border-radius:12px;font-family:system-ui,sans-serif;font-size:20px;font-weight:600;line-height:1.5;text-align:center;color:#fff;pointer-events:none;box-shadow:0 8px 32px rgba(0,0,0,0.5);animation:demoFadeIn 0.4s ease-out;background:linear-gradient(135deg,' + args[1] + ',' + args[1] + 'cc);border:1px solid ' + args[1] + ';';
        document.body.appendChild(bar);
    }""", [text, color])
    if duration:
        time.sleep(duration)


def hide_banner(page):
    page.evaluate("""() => {
        const b = document.getElementById('demo-banner');
        if (b) { b.style.animation = 'demoFadeOut 0.3s forwards'; setTimeout(() => b.remove(), 300); }
    }""")
    time.sleep(0.4)


def spotlight(page, button_text, color="#f97316"):
    page.evaluate("""(args) => {
        document.getElementById('demo-spot')?.remove();
        const btns = Array.from(document.querySelectorAll('button'));
        const el = btns.find(b => b.textContent.includes(args[0]));
        if (!el) return;
        const r = el.getBoundingClientRect();
        const spot = document.createElement('div');
        spot.id = 'demo-spot';
        spot.style.cssText = 'position:fixed;z-index:99998;pointer-events:none;border:3px solid ' + args[1] + ';border-radius:8px;box-shadow:0 0 0 4000px rgba(0,0,0,0.45),0 0 20px ' + args[1] + '80;transition:all 0.3s;top:' + (r.top-4) + 'px;left:' + (r.left-4) + 'px;width:' + (r.width+8) + 'px;height:' + (r.height+8) + 'px;';
        document.body.appendChild(spot);
    }""", [button_text, color])


def hide_spotlight(page):
    page.evaluate("() => document.getElementById('demo-spot')?.remove()")
    time.sleep(0.3)


def wait_for_response(page, timeout=120000):
    time.sleep(1.5)
    page.wait_for_function("() => !document.querySelector('.dot')", timeout=timeout)
    time.sleep(1)


def kill_existing_server():
    """Stop any existing server on port 8000."""
    try:
        out = subprocess.check_output(["lsof", "-ti", ":8000"], text=True).strip()
        for pid in out.split("\n"):
            if pid:
                os.kill(int(pid), signal.SIGTERM)
                print(f"Stopped existing server (PID {pid})")
        time.sleep(1)
    except (subprocess.CalledProcessError, ProcessLookupError):
        pass


def start_server():
    """Start a fresh backend server and wait until it's ready."""
    kill_existing_server()
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
    proc = subprocess.Popen(
        [python, str(BACKEND_SCRIPT)],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("Starting fresh backend server...")
    for _ in range(30):
        try:
            urllib.request.urlopen("http://localhost:8000/health", timeout=1)
            print("Backend server ready.")
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Backend server did not start within 15 seconds")


def run():
    server = start_server()
    try:
        _run_recording()
    finally:
        server.terminate()
        server.wait(timeout=5)
        print("Backend server stopped.")


def _run_recording():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=VIDEO_DIR,
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto(FRONTEND_URL)
        time.sleep(2)

        # ─── Title ──────────────────────────────────────────
        show_title(page,
            "Vakantie BV \u2014 Slimme Agent, Strakke Regels",
            "Demo: Wat mag een medewerker w\u00e9l en niet? En een beheerder?",
            duration=4)

        # ─── Step 1: Klant ──────────────────────────────────
        show_banner(page,
            "\U0001f464 De medewerker probeert een NIEUWE klant te boeken bij een NIEUW hotel",
            "#f97316")
        time.sleep(2)

        spotlight(page, "Klant", "#f97316")
        time.sleep(2)
        hide_spotlight(page)

        print("Step 1: Klant tries to book...")
        page.locator("textarea").click()
        page.locator("textarea").press_sequentially(KLANT_MSG, delay=12)
        time.sleep(0.5)

        hide_banner(page)
        show_banner(page,
            "\u23f3 De agent verwerkt het verzoek \u2014 het systeem checkt de bedrijfsregels...",
            "#6b7280")

        page.locator("textarea").press("Enter")
        wait_for_response(page)

        hide_banner(page)
        show_banner(page,
            "\U0001f6ab GEWEIGERD \u2014 Een medewerker mag geen klanten of hotels aanmaken. Dat is voorbehouden aan beheerders.",
            "#dc2626", duration=4)
        hide_banner(page)
        time.sleep(1)

        # ─── Step 2: Switch to Admin ────────────────────────
        show_banner(page,
            "\U0001f504 We schakelen over naar de BEHEERDER...",
            "#8b5cf6")
        time.sleep(2)

        spotlight(page, "Admin", "#ef4444")
        time.sleep(2)

        print("Step 2: Switching to Admin...")
        page.locator("button", has_text="Admin").click()
        time.sleep(1)

        hide_spotlight(page)
        hide_banner(page)

        show_banner(page,
            "\U0001f527 Beheerder actief \u2014 deze rol mag w\u00e9l klanten, hotels en bestemmingen aanmaken",
            "#ef4444", duration=3)
        hide_banner(page)

        # ─── Step 3: Admin books ────────────────────────────
        show_banner(page,
            "\U0001f527 De beheerder geeft opdracht om alles aan te maken en de reis te boeken",
            "#ef4444")

        print("Step 3: Admin creating and booking...")
        page.locator("textarea").click()
        page.locator("textarea").press_sequentially(ADMIN_MSG, delay=12)
        time.sleep(0.5)

        hide_banner(page)
        show_banner(page,
            "\u26a1 De agent voert het uit \u2014 het systeem valideert elke stap automatisch",
            "#6b7280")

        page.locator("textarea").press("Enter")
        wait_for_response(page, timeout=180000)

        hide_banner(page)
        show_banner(page,
            "\u2705 GELUKT \u2014 Alles aangemaakt en boeking bevestigd. De bedrijfsregels zijn nageleefd.",
            "#16a34a", duration=4)
        hide_banner(page)
        time.sleep(1)

        # ─── Step 4: Database ───────────────────────────────
        show_banner(page,
            "\U0001f5c4\ufe0f Bewijs: de gegevens staan in de database",
            "#3b82f6")

        print("Step 4: Checking database view...")
        page.locator("button", has_text="Database").click()
        time.sleep(2)
        hide_banner(page)

        for icon, table, label in [
            ("\U0001f4cb", "boekingen", "\U0001f4cb Nieuwe boeking staat in het systeem"),
            ("\U0001f464", "klanten",   "\U0001f464 Aag van der Zee is aangemaakt als klant"),
            ("\U0001f3e8", "hotels",    "\U0001f3e8 Carlton Beach Knokke is toegevoegd als hotel"),
        ]:
            page.locator("button", has_text=f"{icon} {table}").click()
            show_banner(page, label, "#3b82f6", duration=3)
            hide_banner(page)

        # ─── Step 5: Ontologie ──────────────────────────────
        show_banner(page,
            "\U0001f578\ufe0f Onder de motorkap: de OWL ontologie bepaalt alles",
            "#a855f7")

        print("Step 5: Showing ontology view...")
        page.locator("button", has_text="Ontologie").click()
        time.sleep(2)
        hide_banner(page)

        show_banner(page,
            "\U0001f4dc De Turtle-serialisatie bevat klassen, relaties, ActionTypes en precondities\n"
            "De agent leest hier dynamisch uit wat hij per rol mag doen",
            "#a855f7", duration=5)
        hide_banner(page)
        time.sleep(1)

        # ─── End ────────────────────────────────────────────
        show_title(page,
            "Bedrijfsregels ingebouwd, niet ingetypt",
            "De ontologie is de single source of truth \u2014 geen handmatige prompts, geen verrassingen",
            duration=5)

        print("Recording complete!")
        time.sleep(1)
        context.close()
        browser.close()
        print(f"Video saved to {VIDEO_DIR}/")


if __name__ == "__main__":
    run()
