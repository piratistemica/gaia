"""Sessao do Control Center: cookie HttpOnly derivado do token do painel."""

from __future__ import annotations

from gaia.serve.session import (
    COOKIE_NAME,
    ControlSessions,
    clear_cookie,
    cookie_value,
    segredo_igual,
    set_cookie,
)


def test_grant_e_valid():
    sessoes = ControlSessions()
    nonce = sessoes.grant(now=1000.0)
    assert nonce
    assert sessoes.valid(nonce, now=1000.0)
    assert sessoes.valid(nonce, now=1000.0 + 60)
    assert sessoes.count() == 1


def test_nonce_expira():
    sessoes = ControlSessions(ttl=10)
    nonce = sessoes.grant(now=1000.0)
    assert sessoes.valid(nonce, now=1009.0)
    assert not sessoes.valid(nonce, now=1011.0)
    assert sessoes.count() == 0


def test_nonce_inventado_nao_vale():
    sessoes = ControlSessions()
    assert not sessoes.valid("")
    assert not sessoes.valid("inventado")


def test_cada_login_tem_nonce_proprio():
    sessoes = ControlSessions()
    a = sessoes.grant()
    b = sessoes.grant()
    assert a != b
    assert sessoes.count() == 2


def test_revoke():
    sessoes = ControlSessions()
    nonce = sessoes.grant()
    assert sessoes.revoke(nonce) is True
    assert not sessoes.valid(nonce)
    assert sessoes.revoke(nonce) is False


def test_teto_de_sessoes_descarta_a_mais_antiga():
    sessoes = ControlSessions(max_sessions=2)
    primeira = sessoes.grant(now=1.0)
    segunda = sessoes.grant(now=2.0)
    terceira = sessoes.grant(now=3.0)
    assert not sessoes.valid(primeira, now=4.0)
    assert sessoes.valid(segunda, now=4.0)
    assert sessoes.valid(terceira, now=4.0)
    assert sessoes.count() == 2


def test_cookie_de_sessao_e_httponly_e_restrito():
    cabecalho = set_cookie("nonce123")
    assert cabecalho.startswith(f"{COOKIE_NAME}=nonce123;")
    assert "HttpOnly" in cabecalho
    assert "SameSite=Strict" in cabecalho
    assert "Path=/control" in cabecalho
    assert "Max-Age=0" not in cabecalho


def test_cookie_de_saida_apaga():
    cabecalho = clear_cookie()
    assert f"{COOKIE_NAME}=;" in cabecalho
    assert "Max-Age=0" in cabecalho


def test_cookie_value_le_o_header():
    assert cookie_value("a=1; gaia_control=xyz; b=2") == "xyz"
    assert cookie_value("gaia_control=") == ""
    assert cookie_value("") == ""
    assert cookie_value("outro=1") == ""


def test_segredo_igual_aceita_unicode_sem_explodir():
    assert segredo_igual("abc", "abc") is True
    assert segredo_igual("abc", "abd") is False
    assert segredo_igual("", "abc") is False
    assert segredo_igual("tok", "") is False
    # `compare_digest` levanta TypeError com nao-ASCII em str: aqui nao pode.
    assert segredo_igual("são", "sao") is False


def test_cookie_do_chat_e_do_painel_sao_distintos():
    from gaia.serve.session import (
        CHAT_COOKIE_NAME,
        CHAT_COOKIE_PATH,
        COOKIE_PATH,
        chat_clear_cookie,
        chat_set_cookie,
    )

    chat = chat_set_cookie("abc")
    painel = set_cookie("abc")
    assert chat.startswith(f"{CHAT_COOKIE_NAME}=abc;")
    assert f"Path={CHAT_COOKIE_PATH}" in chat
    assert f"Path={COOKIE_PATH};" in painel
    assert CHAT_COOKIE_NAME != COOKIE_NAME
    assert "HttpOnly" in chat and "SameSite=Strict" in chat
    assert "Max-Age=0" in chat_clear_cookie()
    # Ler um cookie nao devolve o outro: quem procura pelo nome certo acha.
    header = f"{COOKIE_NAME}=painel; {CHAT_COOKIE_NAME}=chat"
    assert cookie_value(header, COOKIE_NAME) == "painel"
    assert cookie_value(header, CHAT_COOKIE_NAME) == "chat"


def test_nonce_do_chat_nao_vale_no_conjunto_do_painel():
    """Mesmo tipo de objeto, conjuntos separados: e a separacao de poderes."""
    from gaia.serve.session import Sessions

    chat = Sessions()
    painel = ControlSessions()
    nonce = chat.grant()
    assert chat.valid(nonce)
    assert not painel.valid(nonce)
