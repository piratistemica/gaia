"""Sessao do Control Center: cookie ``HttpOnly`` em cima do token do painel.

O token continua sendo a unica credencial (``GAIA_CONTROL_TOKEN``), mas obrigar
a digitacao a cada visita e atrito sem ganho de seguranca: o ``localStorage`` e
por origem, entao o mesmo painel aberto por ``gaia:8787``, pelo IP do tailnet ou
por ``localhost`` sao tres armazenamentos diferentes — e basta trocar de aba,
usar janela anonima ou esperar a limpeza automatica do navegador para o modal
voltar.

Aqui o token e trocado por um nonce opaco que o navegador guarda num cookie
``HttpOnly``: o JS do painel nao le a credencial de sessao (XSS no painel nao
rouba o segredo guardado) e a URL nunca carrega credencial. O ``Bearer``
continua aceito — e o que o cliente fino e o ``curl`` usam.

O nonce vive so na memoria do processo: reiniciar o ``gaia serve`` derruba todas
as sessoes e o painel refaz o login se tiver o token guardado. Nada vai para
disco e o nonce nao e derivado do token (vazamento de um nao revela o outro).
"""

from __future__ import annotations

import secrets
import threading
import time

COOKIE_NAME = "gaia_control"
# Validade do nonce no servidor (memoria). Curta porque renovar e barato.
SESSION_TTL_SECONDS = 12 * 60 * 60
# Validade do cookie no navegador. Maior que o TTL de proposito: com o cookie
# vencido/estranho o front tenta reautenticar sozinho em vez de pedir o token.
COOKIE_MAX_AGE = 30 * 24 * 60 * 60
# Teto de sessoes vivas: navegadores esquecidos nao crescem sem limite.
MAX_SESSIONS = 64

# `Path=/control` cobre a pagina e a API e nada fora delas.
COOKIE_PATH = "/control"

# Sessao do CHAT (`GAIA_SERVE_TOKEN`). Cookie e path separados do painel de
# proposito: o token de conversa nao vale em /control/api e vice-versa. O path
# ``/`` cobre chat, SSE, busca e as rotas novas de leitura.
CHAT_COOKIE_NAME = "gaia_chat"
CHAT_COOKIE_PATH = "/"


def segredo_igual(dado: str, esperado: str) -> bool:
    """Compara segredos em tempo constante, tolerando unicode no header."""
    if not dado or not esperado:
        return False
    return secrets.compare_digest(dado.encode("utf-8"), esperado.encode("utf-8"))


def cookie_value(header: str, nome: str = COOKIE_NAME) -> str:
    """Valor de um cookie do header ``Cookie`` (vazio se ausente)."""
    for parte in (header or "").split(";"):
        chave, _, valor = parte.partition("=")
        if chave.strip() == nome:
            return valor.strip()
    return ""


def _cookie(nonce: str, *, name: str, path: str, max_age: int) -> str:
    return (
        f"{name}={nonce}; Path={path}; Max-Age={max_age}; "
        "HttpOnly; SameSite=Strict"
    )


def set_cookie(
    nonce: str, *, name: str = COOKIE_NAME, path: str = COOKIE_PATH
) -> str:
    """Cabecalho ``Set-Cookie`` da sessao do painel (default).

    ``HttpOnly`` (JS nao le), ``SameSite=Strict`` (outro site nao usa a sessao
    em requisicao nenhuma) e ``Path=/control``. Sem ``Secure`` porque o painel
    e servido em HTTP na LAN/tailnet por padrao; atras de HTTPS o cookie ja vai
    cifrado pelo transporte.
    """
    return _cookie(nonce, name=name, path=path, max_age=COOKIE_MAX_AGE)


def clear_cookie(*, name: str = COOKIE_NAME, path: str = COOKIE_PATH) -> str:
    """``Set-Cookie`` que apaga a sessao do navegador."""
    return _cookie("", name=name, path=path, max_age=0)


def chat_set_cookie(nonce: str) -> str:
    """Sessao do chat: ``gaia_chat`` em ``Path=/``."""
    return set_cookie(nonce, name=CHAT_COOKIE_NAME, path=CHAT_COOKIE_PATH)


def chat_clear_cookie() -> str:
    """Apaga a sessao do chat (logout)."""
    return clear_cookie(name=CHAT_COOKIE_NAME, path=CHAT_COOKIE_PATH)


class Sessions:
    """Nonces ativos de uma credencial. Thread-safe: o serve tem varias threads.

    Usado duas vezes, com instancias separadas: sessoes do painel
    (``GAIA_CONTROL_TOKEN``) e do chat (``GAIA_SERVE_TOKEN``). O nonce e o
    mesmo tipo de objeto, mas um nunca e valido no conjunto do outro.
    """

    def __init__(
        self,
        ttl: float = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._ttl = float(ttl)
        self._max = max(1, int(max_sessions))
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def grant(self, now: float | None = None) -> str:
        """Cria uma sessao e devolve o nonce (nunca o token do painel)."""
        agora = time.time() if now is None else now
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._limpar(agora)
            while len(self._nonces) >= self._max:
                # Descarta a sessao mais antiga (dict preserva ordem de insercao).
                self._nonces.pop(next(iter(self._nonces)))
            self._nonces[nonce] = agora + self._ttl
        return nonce

    def valid(self, nonce: str, now: float | None = None) -> bool:
        """True se o nonce existe e nao venceu."""
        if not nonce:
            return False
        agora = time.time() if now is None else now
        with self._lock:
            vence = self._nonces.get(nonce)
            if vence is None:
                return False
            if vence <= agora:
                self._nonces.pop(nonce, None)
                return False
            return True

    def revoke(self, nonce: str) -> bool:
        """Encerra uma sessao. False = nonce desconhecido (ja expirado)."""
        if not nonce:
            return False
        with self._lock:
            return self._nonces.pop(nonce, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._nonces.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._nonces)

    def _limpar(self, agora: float) -> None:
        vencidos = [n for n, vence in self._nonces.items() if vence <= agora]
        for nonce in vencidos:
            self._nonces.pop(nonce, None)


# Nome historico: o painel foi o primeiro consumidor. O comportamento e o mesmo
# (nonces opacos, em memoria, com TTL e teto).
ControlSessions = Sessions


__all__ = [
    "CHAT_COOKIE_NAME",
    "CHAT_COOKIE_PATH",
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "COOKIE_PATH",
    "SESSION_TTL_SECONDS",
    "ControlSessions",
    "Sessions",
    "chat_clear_cookie",
    "chat_set_cookie",
    "clear_cookie",
    "cookie_value",
    "segredo_igual",
    "set_cookie",
]
