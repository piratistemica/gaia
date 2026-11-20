"""Transporte do `zaya serve`: rotas, autorização e framing SSE."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

import pytest

from zaya.interface.remote import RemoteUI
from zaya.serve.server import ServeConfigError, ServeServer


class FakeTarget:
    def __init__(self) -> None:
        self.turnos: list[str] = []
        self.decisoes: list[tuple[str, str]] = []
        self.abortos = 0
        self._n = 0
        self.busy = False

    def health(self) -> dict[str, Any]:
        return {"model": "deepseek/flash", "db": "/tmp/zaya.db"}

    def sessions(self) -> list[dict[str, Any]]:
        return [{"id": 1, "channel": "cli", "mode": "assistente"}]

    def turn(self, text: str) -> str:
        if self.busy:
            raise RuntimeError("turno em andamento")
        self._n += 1
        self.turnos.append(text)
        return f"r_{self._n}"

    def decide(self, request_id: str, decision: str) -> None:
        self.decisoes.append((request_id, decision))

    def abort(self) -> None:
        self.abortos += 1


@pytest.fixture()
def servidor():
    ui = RemoteUI()
    alvo = FakeTarget()
    srv = ServeServer(alvo, ui, host="127.0.0.1", port=0)
    url = srv.start()
    yield srv, alvo, ui, url
    srv.shutdown()


def _porta(url: str) -> int:
    return int(url.rsplit(":", 1)[1])


def _pedido(
    url: str,
    metodo: str,
    caminho: str,
    corpo: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", _porta(url), timeout=5)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    cabecalhos = {"Content-Type": "application/json"}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    conn.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conn.getresponse()
    bruto = resposta.read()
    conn.close()
    try:
        payload = json.loads(bruto or b"{}")
    except ValueError:
        payload = {"_bruto": bruto.decode(errors="replace")}
    return resposta.status, payload


def test_health_responde_modelo_e_banco(servidor):
    _, _, _, url = servidor
    status, corpo = _pedido(url, "GET", "/health")
    assert status == 200
    assert corpo["ok"] is True
    assert corpo["model"] == "deepseek/flash"


def test_sessions_lista_conversas(servidor):
    _, _, _, url = servidor
    status, corpo = _pedido(url, "GET", "/sessions")
    assert status == 200
    assert corpo["sessions"][0]["channel"] == "cli"


def test_turn_agenda_e_devolve_run(servidor):
    _, alvo, _, url = servidor
    status, corpo = _pedido(url, "POST", "/turn", {"text": "oi"})
    assert status == 202
    assert corpo["run"] == "r_1"
    assert alvo.turnos == ["oi"]


def test_turn_vazio_e_recusado(servidor):
    _, alvo, _, url = servidor
    status, corpo = _pedido(url, "POST", "/turn", {"text": "   "})
    assert status == 400
    assert corpo["error"] == "empty_text"
    assert alvo.turnos == []


def test_turn_ocupado_devolve_409(servidor):
    _, alvo, _, url = servidor
    alvo.busy = True
    status, corpo = _pedido(url, "POST", "/turn", {"text": "oi"})
    assert status == 409
    assert corpo["error"] == "busy"


def test_permission_aceita_as_tres_decisoes(servidor):
    _, alvo, _, url = servidor
    for decisao in ("once", "always", "reject"):
        status, _ = _pedido(url, "POST", "/permission/p1", {"decision": decisao})
        assert status == 200
    assert alvo.decisoes == [("p1", "once"), ("p1", "always"), ("p1", "reject")]


def test_permission_recusa_decisao_invalida(servidor):
    _, alvo, _, url = servidor
    status, corpo = _pedido(url, "POST", "/permission/p1", {"decision": "sempre"})
    assert status == 400
    assert corpo["error"] == "bad_decision"
    assert alvo.decisoes == []


def test_abort_chama_o_motor(servidor):
    _, alvo, _, url = servidor
    status, _ = _pedido(url, "POST", "/abort")
    assert status == 200
    assert alvo.abortos == 1


def test_json_invalido_devolve_400(servidor):
    _, _, _, url = servidor
    conn = http.client.HTTPConnection("127.0.0.1", _porta(url), timeout=5)
    conn.request("POST", "/turn", body=b"{nao json", headers={"Content-Type": "application/json"})
    assert conn.getresponse().status == 400
    conn.close()


def test_rota_desconhecida_devolve_404(servidor):
    _, _, _, url = servidor
    status, corpo = _pedido(url, "GET", "/nao-existe")
    assert status == 404
    assert corpo["error"] == "not_found"


def test_token_obrigatorio_quando_configurado():
    ui = RemoteUI()
    srv = ServeServer(FakeTarget(), ui, host="127.0.0.1", port=0, token="segredo")
    url = srv.start()
    try:
        assert _pedido(url, "GET", "/sessions")[0] == 401
        assert _pedido(url, "GET", "/sessions", token="errado")[0] == 401
        assert _pedido(url, "GET", "/sessions", token="segredo")[0] == 200
        assert _pedido(url, "POST", "/turn", {"text": "oi"})[0] == 401
    finally:
        srv.shutdown()


def test_bind_na_rede_sem_token_e_recusado():
    with pytest.raises(ServeConfigError, match="ZAYA_SERVE_TOKEN"):
        ServeServer(FakeTarget(), RemoteUI(), host="100.66.45.57", port=0)


def _ler_evento(resposta, limite: int = 12) -> dict | None:
    """Le um bloco SSE (id / event / data / linha vazia) e devolve o data."""
    for _ in range(limite):
        linha = resposta.fp.readline().decode()
        if not linha:
            return None
        if linha.startswith("data: "):
            return json.loads(linha[len("data: ") :])
    return None


def test_sse_entrega_evento_com_id_e_nome(servidor):
    _, _, ui, url = servidor
    conn = http.client.HTTPConnection("127.0.0.1", _porta(url), timeout=5)
    conn.request("GET", "/events")
    resposta = conn.getresponse()
    assert resposta.status == 200
    assert resposta.getheader("Content-Type", "").startswith("text/event-stream")

    ui.info("primeiro")
    linhas: list[str] = []
    while len(linhas) < 4:
        linha = resposta.fp.readline().decode()
        if not linha:
            break
        linhas.append(linha.rstrip("\n"))
    conn.close()

    campos = dict(linha.split(": ", 1) for linha in linhas if ": " in linha)
    assert campos["event"] == "notice"
    assert campos["id"] == "1"
    assert json.loads(campos["data"])["text"] == "primeiro"


def test_sse_reconecta_com_since(servidor):
    _, _, ui, url = servidor
    ui.info("perdido")
    ui.info("tambem perdido")
    conn = http.client.HTTPConnection("127.0.0.1", _porta(url), timeout=5)
    conn.request("GET", "/events?since=2")
    resposta = conn.getresponse()
    ui.info("novo")
    evento = _ler_evento(resposta)
    conn.close()
    assert evento is not None
    assert evento["text"] == "novo"
    assert evento["seq"] == 3


def test_dois_clientes_sse_recebem_o_mesmo_evento(servidor):
    _, _, ui, url = servidor
    conns = []
    for _ in range(2):
        conn = http.client.HTTPConnection("127.0.0.1", _porta(url), timeout=5)
        conn.request("GET", "/events")
        conns.append((conn, conn.getresponse()))
    ui.info("broadcast")

    for conn, resposta in conns:
        evento = _ler_evento(resposta)
        assert evento is not None
        assert evento["text"] == "broadcast"
        conn.close()


def test_porta_ja_em_uso_levanta_oserror(servidor):
    srv, _, _, _url = servidor
    host, porta = srv.address
    with pytest.raises(OSError):
        ServeServer(FakeTarget(), RemoteUI(), host=host, port=porta)


def test_nao_aceita_conexao_apos_shutdown(servidor):
    srv, _, _, url = servidor
    srv.shutdown()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", _porta(url)), timeout=1).close()


def test_health_quebrado_devolve_json_e_nao_resposta_vazia(servidor):
    """Regressão: `mode_name` era chamado como função e /health sumia."""
    _, alvo, _, url = servidor

    def explodir() -> dict[str, Any]:
        raise TypeError("'str' object is not callable")

    alvo.health = explodir  # type: ignore[method-assign]
    status, corpo = _pedido(url, "GET", "/health")
    assert status == 500
    assert corpo["error"] == "health_failed"
    assert "not callable" in corpo["message"]
