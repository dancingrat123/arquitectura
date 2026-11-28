"""Captura las evidencias de interfaz de SIRIF contra el stack local.

Uso:
    pip install playwright httpx
    python docs/evidencias/capturar_evidencias.py

Requiere el stack activo (`docker compose --compatibility up -d --build`) en
https://localhost:9443 y Chrome instalado (Playwright lo usa como canal).
Las capturas sobrescriben los archivos de este directorio.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = os.getenv("SIRIF_URL", "https://localhost:9443")
SALIDA = Path(__file__).resolve().parent
USUARIO = os.getenv("SIRIF_USUARIO", "admin")
CLAVE = os.getenv("SIRIF_CLAVE", "sirif2026")

INIT_TEMA_OSCURO = "try{localStorage.setItem('sirif_tema','dark')}catch(e){}"


def guardar(page, nombre: str) -> None:
    ruta = SALIDA / nombre
    page.screenshot(path=str(ruta))
    print(f"[ok] {ruta.name}")


def main() -> int:
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="chrome", headless=True)

        contexto = navegador.new_context(
            viewport={"width": 1600, "height": 1000},
            ignore_https_errors=True,
            locale="es-CL",
            reduced_motion="reduce",
        )
        page = contexto.new_page()
        page.add_init_script(INIT_TEMA_OSCURO)
        page.set_default_timeout(45000)

        page.goto(BASE, wait_until="load")
        page.wait_for_timeout(7000)
        page.add_style_tag(
            content="*{animation-duration:0s !important;transition-duration:0s !important}"
        )
        guardar(page, "01-mapa-publico.png")

        page.click("#login-open-btn")
        page.wait_for_timeout(1200)
        guardar(page, "02-login.png")

        page.fill("#login-user", USUARIO)
        page.fill("#login-password", CLAVE)
        page.click("#login-submit")
        page.wait_for_timeout(7000)
        page.evaluate(
            "document.querySelectorAll('#toasts .toast').forEach((t) => t.remove())"
        )
        guardar(page, "03-diagnostico.png")

        token = page.evaluate("localStorage.getItem('sirif_token')")

        page.click('.view-btn[data-view="tabla"]')
        page.wait_for_timeout(3500)
        guardar(page, "04-ranking.png")

        page.click('.view-btn[data-view="mapa"]')
        page.click("#tab-simulador")
        page.wait_for_timeout(2500)
        boton_simular = page.locator("#sim-body button", has_text="Simular escenario")
        if boton_simular.count():
            boton_simular.first.click()
            page.wait_for_timeout(3500)
        guardar(page, "05-simulador.png")

        page.click("#tab-asistente")
        page.wait_for_timeout(800)
        chip = page.locator("#chat-chips button").first
        if chip.count():
            chip.click()
            page.wait_for_timeout(4000)
        guardar(page, "06-asistente.png")

        page.click("#tab-operacion")
        page.wait_for_timeout(2500)
        guardar(page, "07-operacion.png")

        if page.locator("#notif-btn").is_visible():
            page.click("#notif-btn")
            page.wait_for_timeout(1500)
            guardar(page, "08-alertas.png")
            page.click("#notif-close")
            page.wait_for_timeout(600)

        contexto.close()

        # Ficha PDF real del endpoint con la misma sesión autenticada.
        if token:
            try:
                with httpx.Client(verify=False, timeout=60) as cliente:
                    pdf = cliente.get(
                        f"{BASE}/api/reportes/quilpue/pdf",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                pdf.raise_for_status()
                destino = SALIDA / "ficha-riesgo-quilpue.pdf"
                destino.write_bytes(pdf.content)
                print(f"[ok] {destino.name} ({len(pdf.content) // 1024} KB)")
            except Exception as error:  # noqa: BLE001
                print(f"[warn] no se pudo descargar el PDF: {error}", file=sys.stderr)

        # Vista móvil (invitado).
        movil = navegador.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            ignore_https_errors=True,
            locale="es-CL",
            reduced_motion="reduce",
        )
        pagina_movil = movil.new_page()
        pagina_movil.add_init_script(INIT_TEMA_OSCURO)
        pagina_movil.goto(BASE, wait_until="load")
        pagina_movil.wait_for_timeout(7000)
        guardar(pagina_movil, "09-movil-mapa.png")
        movil.close()

        navegador.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
