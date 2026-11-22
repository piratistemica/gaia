"""Paginas web servidas pelo proprio `gaia serve`.

Sem framework e sem bundler de proposito: o navegador e so mais uma tela do
mesmo motor. Duas paginas:

* ``/`` — o chat (`static/index.html`), contrato dos eventos na
  ``docs/plans/2026-11-19-serve-cliente-fino.md`` (§4.1).
* ``/control`` — o Agent Control Center (`static/control.html`), que fala com
  ``/control/api/*`` e exige ``GAIA_CONTROL_TOKEN``.

O token vive no ``localStorage`` e vai no cabecalho ``Authorization`` — por isso
o SSE do chat e lido com ``fetch`` + ``ReadableStream`` em vez de ``EventSource``
(que nao permite cabecalho proprio). Token na URL vazaria em log e historico.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).with_name("static")
PAGE_PATH = STATIC_DIR / "index.html"

# Arquivos do painel que podem ser servidos. Allowlist explicita: nada de
# resolver caminho vindo da URL (``..`` nunca chega ao disco).
CONTROL_ASSETS = ("control.html", "control.css", "control.js")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@lru_cache(maxsize=1)
def page() -> bytes:
    """HTML da pagina do chat. Lido uma vez por processo (arquivo versionado)."""
    return PAGE_PATH.read_bytes()


def content_type() -> str:
    return "text/html; charset=utf-8"


def control_page() -> bytes:
    """HTML do Control Center (publico; a API e que exige token)."""
    return (STATIC_DIR / "control.html").read_bytes()


def control_asset(name: str) -> tuple[bytes, str] | None:
    """Asset do painel por nome, ou None se nao estiver na allowlist."""
    if name not in CONTROL_ASSETS:
        return None
    caminho = STATIC_DIR / name
    if not caminho.is_file():
        return None
    tipo = _CONTENT_TYPES.get(caminho.suffix, "application/octet-stream")
    return caminho.read_bytes(), tipo
