"""Cliente web: uma pagina HTML, sem build, servida pelo proprio `gaia serve`.

Sem framework e sem bundler de proposito: o navegador e so mais uma tela do
mesmo motor (`GET /events` + `POST /turn`), como o `gaia --remote`. O contrato
dos eventos esta na §4.1 de `docs/plans/2026-11-19-serve-cliente-fino.md`.

O token vive no `localStorage` e vai no cabecalho `Authorization` — por isso o
SSE e lido com `fetch` + `ReadableStream` em vez de `EventSource` (que nao
permite cabecalho proprio). Token na URL vazaria em log e historico.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PAGE_PATH = Path(__file__).with_name("static") / "index.html"


@lru_cache(maxsize=1)
def page() -> bytes:
    """HTML da pagina. Lido uma vez por processo (arquivo versionado)."""
    return PAGE_PATH.read_bytes()


def content_type() -> str:
    return "text/html; charset=utf-8"
