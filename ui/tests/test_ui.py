from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not EDGE.exists() and sys.platform != "darwin" and not sys.platform.startswith("linux"), reason="Microsoft Edge or Chromium is required")
def test_lazy_profile_flow_on_desktop_and_mobile() -> None:
    port = _free_port()
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mk_tracking.ui_app.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        env={**os.environ, "MK_WORK_DATA_BACKEND": "json"},
    )
    base_url = f"http://127.0.0.1:{port}"

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{base_url}/api/health", timeout=0.2)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("Uvicorn did not start")

        requested_paths: list[str] = []
        with sync_playwright() as playwright:
            launch_kwargs = {"headless": True}
            if EDGE.exists():
                launch_kwargs["executable_path"] = str(EDGE)
            browser = playwright.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("https://he.wikipedia.org/**", lambda route: route.abort())
            page.on("request", lambda request: requested_paths.append(request.url))
            page.add_init_script("localStorage.setItem('mk_tracking_welcome_dismissed_v1', 'true');")
            page.goto(base_url, wait_until="networkidle")

            assert page.locator("[data-member-name]").count() == 124
            assert not any("/data/" in url for url in requested_paths)

            page.locator('[data-topic-id="judiciary"]').click()
            page.locator("#relevantOnly").check()
            lapid = page.locator('[data-member-name="יאיר לפיד"]')
            assert lapid.count() == 1
            lapid.click()

            page.locator("#profileDrawer.open").wait_for()
            page.locator(".profile").wait_for()
            assert any("/api/mks/x-yairlapid/issues" in url for url in requested_paths)
            assert not any("/api/mks/x-yairlapid/posts" in url for url in requested_paths)
            assert not any("/data/" in url for url in requested_paths)

            source_button = page.locator("[data-source-key]").first
            assert source_button.count() == 1
            source_button.click()
            page.locator("#sourceDialog[open]").wait_for()
            assert page.locator("#sourceDialog a[href^='https://x.com/']").count() == 1
            page.locator("#closeSource").click()
            page.locator("#closeDrawer").click()

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="networkidle")
            no_horizontal_overflow = page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth + 1"
            )
            assert no_horizontal_overflow
            assert page.locator("[data-member-name]").count() == 124
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(not EDGE.exists() and sys.platform != "darwin" and not sys.platform.startswith("linux"), reason="Microsoft Edge or Chromium is required")
def test_welcome_and_walkthrough_tour_flow() -> None:
    port = _free_port()
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mk_tracking.ui_app.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        env={**os.environ, "MK_WORK_DATA_BACKEND": "json"},
    )
    base_url = f"http://127.0.0.1:{port}"

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{base_url}/api/health", timeout=0.2)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("Uvicorn did not start")

        with sync_playwright() as playwright:
            launch_kwargs = {"headless": True}
            if EDGE.exists():
                launch_kwargs["executable_path"] = str(EDGE)
            browser = playwright.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("https://he.wikipedia.org/**", lambda route: route.abort())

            # Load app fresh - Welcome modal should appear
            page.goto(base_url, wait_until="networkidle")
            welcome_card = page.locator(".welcome-modal-card")
            assert welcome_card.is_visible()

            # Click start tour button in welcome modal
            start_tour_btn = page.locator(".welcome-btn-primary")
            start_tour_btn.click()

            # Step 1: Controls Header
            tour_card = page.locator(".walkthrough-card")
            tour_card.wait_for()
            assert "פילטרים וחיפוש" in tour_card.text_content()

            # Advance to Step 2
            next_btn = page.locator(".tour-btn-primary")
            next_btn.click()

            # Step 2: Status Summary
            assert "שורת סיכום" in tour_card.text_content()

            # Advance to Step 3
            next_btn.click()

            # Step 3: Party Board
            assert "כרטיסי חברי הכנסת" in tour_card.text_content()

            # Advance to Step 4 (Profile Drawer)
            next_btn.click()

            # Step 4: Profile Drawer
            assert "פרופיל מפורט" in tour_card.text_content()
            drawer = page.locator("#profileDrawer.open")
            drawer.wait_for()
            assert drawer.is_visible()

            # Finish Tour
            finish_btn = page.locator(".tour-btn-primary")
            assert "סיום" in finish_btn.text_content()
            finish_btn.click()

            # Tour should be closed
            page.locator(".walkthrough-card").wait_for(state="detached")
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

