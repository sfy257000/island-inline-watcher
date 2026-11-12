"""Windows GUI entry point, with an offline packaged-app smoke test for CI."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import traceback
from urllib.parse import quote


def smoke_test(report):
    # Do not touch a real user's settings or connect to the booking/mail services.
    assert getattr(sys, "frozen", False), "Run this check against the packaged executable"
    with tempfile.TemporaryDirectory(prefix="island-smoke-") as folder:
        os.environ["ISLAND_DATA_DIR"] = folder
        import island_inline_watcher as app
        from browser_errors import BrowserError, BrowserTimeout

        assert importlib.util.find_spec("playwright") is None, "Playwright was bundled unexpectedly"
        assert issubclass(BrowserTimeout, BrowserError)
        assert app.SETTINGS_ENV.parent == Path(folder)
        ssl.create_default_context()
        profile = app.BookingProfile("測試", "使用者", "0912345678", "", app.ANY_TABLE)
        profile.save()
        assert app.BookingProfile.load() == profile
        trace = app.QueryTrace()
        trace.record("smoke", "離線測試")
        trace.close()
        assert trace.path.is_file()
        root = app.tk.Tk()
        root.withdraw()
        try:
            window = app.WatcherWindow(root)
            root.update()
            assert window.booking_profile == profile
        finally:
            root.destroy()

        browser = app.launch_query_browser(headless=True)
        try:
            page = browser.pages[0]
            html = "<input id='name'><select id='count'><option>2</option><option>3</option></select><button id='go' onclick='document.title=\"done\"'>Go</button>"
            page.goto("data:text/html," + quote(html, safe=""))
            page.locator("#name").fill("offline-test")
            page.locator("#count").select_option(value="3")
            page.locator("#go").click()
            assert page.locator("#name").input_value() == "offline-test"
            assert page.locator("#count").input_value() == "3"
            assert page.evaluate("document.title") == "done"
        finally:
            browser.close()
        payload = {"ok": True, "version": app.APP_VERSION,
                   "checks": ["tkinter", "settings", "logs", "tls", "native_chrome", "no_playwright"]}
    Path(report).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", metavar="REPORT_JSON", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        try:
            smoke_test(args.smoke_test)
        except Exception:
            Path(args.smoke_test).write_text(json.dumps({"ok": False, "error": traceback.format_exc()}), encoding="utf-8")
            sys.exit(1)
        return
    import island_inline_watcher as app
    app.main()


if __name__ == "__main__":
    main()
