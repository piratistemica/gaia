from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from zaya.agenda.client import AgendaClient, _load_env_file
from zaya.agent.errors import ToolError
from zaya.agent.tools import ToolContext, build_tools
from zaya.agent.tools.agenda import build_agenda_tools
from zaya.config import AgendaConfig


def test_env_file_e_cliente_habilitado(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://exemplo.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "AGENDA_USER_ID=user-1\n",
        encoding="utf-8",
    )
    config = AgendaConfig(env_file=str(env_file))
    client = AgendaClient(config)
    assert client.enabled is True
    assert _load_env_file(env_file)["AGENDA_USER_ID"] == "user-1"


def test_cliente_desabilitado_sem_env(tmp_path):
    config = AgendaConfig(env_file=str(tmp_path / "nao-existe.env"))
    client = AgendaClient(config)
    assert client.enabled is False


def test_agenda_desabilitada_por_config(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://exemplo.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "AGENDA_USER_ID=user-1\n",
        encoding="utf-8",
    )
    config = AgendaConfig(env_file=str(env_file), enabled=False)
    assert AgendaClient(config).enabled is False


# ------------------------------------------------------------------ ferramentas


def _env_agenda(tmp_path) -> str:
    env_file = tmp_path / "agenda.env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://exemplo.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "AGENDA_USER_ID=user-1\n",
        encoding="utf-8",
    )
    return str(env_file)


def test_tools_de_agenda_so_existem_com_credencial(tmp_path):
    sem = build_tools(ToolContext(Path(".")))
    assert "agenda_dia" not in sem.names()

    cfg = AgendaConfig(env_file=_env_agenda(tmp_path))
    com = build_tools(ToolContext(Path("."), True, cfg))
    assert {"agenda_dia", "agenda_paginas", "agenda_nova_tarefa"} <= set(com.names())
    assert com.get("agenda_nova_tarefa").requires_approval is True
    assert com.get("agenda_dia").requires_approval is False


class _FakeClient:
    """Troca o AgendaClient por um roteiro de respostas."""

    enabled = True
    created: ClassVar[list[dict]] = []
    marcados: ClassVar[list[tuple[str, bool]]] = []

    def __init__(self, cfg):
        self.cfg = cfg

    async def notes_on(self, date):
        return [
            {"id": "a", "text": "fio dental", "is_checkbox": True, "completed": False},
            {"id": "b", "text": "", "is_checkbox": True, "completed": False},
            {"id": "c", "text": "pilates", "is_checkbox": True, "completed": True},
            {"id": "r", "text": "alongar", "is_checkbox": True, "completed": True,
             "routine_id": "rot"},
            {"id": "r2", "text": "protetor", "is_checkbox": True, "completed": False,
             "routine_id": "rot"},
        ]

    async def routines(self):
        return [
            {"id": "rot", "text": "Rotina matinal", "type": "weekly",
             "frequency_config": {"days": [1, 3]}},
        ]

    async def pages_meta(self, limit=20):
        return [{"id": "p1", "title": "compras"}]

    async def pages(self, limit=20):
        return [{"id": "p1", "title": "compras", "content": "arroz\nfeijão"}]

    async def create_task(self, text, *, date=None, parent_id="", is_checkbox=True):
        self.created.append({"text": text, "date": date, "parent_id": parent_id})
        return {"id": "novo-1", "text": text, "date": date}

    async def set_completed(self, note_id, completed=True):
        self.marcados.append((note_id, completed))
        return {"id": note_id, "completed": completed}


@pytest.fixture
def tools_agenda(tmp_path, monkeypatch):
    import zaya.agent.tools.agenda as modulo

    monkeypatch.setattr(modulo, "AgendaClient", _FakeClient)
    _FakeClient.created = []
    _FakeClient.marcados = []
    cfg = AgendaConfig(env_file=_env_agenda(tmp_path))
    return {tool.name: tool for tool in build_agenda_tools()}, ToolContext(
        tmp_path, True, cfg
    )


def test_agenda_dia_ignora_nota_em_branco(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_dia"].handler({"data": "2026-11-16"}, ctx)
    assert saida.ok
    assert "fio dental" in saida.content
    assert "Pendentes (1)" in saida.content
    assert "pilates" in saida.content
    assert "Rotina do dia: 1/2" in saida.content


def test_agenda_dia_lista_pendencia_de_rotina_com_id(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_dia"].handler({}, ctx)
    assert "protetor (rotina) [id: r2]" in saida.content
    assert "alongar (rotina)" not in saida.content  # ja foi feita


def test_agenda_dia_nao_lista_nota_vazia_como_pendente(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_dia"].handler({}, ctx)
    assert "[id: b]" not in saida.content


def test_agenda_rotinas_mostra_dias(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_rotinas"].handler({}, ctx)
    assert "Rotina matinal" in saida.content
    assert "seg, qua" in saida.content


def test_agenda_paginas_lista_e_le(tools_agenda):
    tools, ctx = tools_agenda
    lista = tools["agenda_paginas"].handler({}, ctx)
    assert "compras" in lista.content
    pagina = tools["agenda_paginas"].handler({"titulo": "comp"}, ctx)
    assert "arroz" in pagina.content


def test_agenda_nova_tarefa_exige_confirmacao(tools_agenda):
    tools, ctx = tools_agenda
    pedido = tools["agenda_nova_tarefa"].handler({"texto": "pagar boleto"}, ctx)
    assert pedido.ok is False
    assert "Confirmacao necessaria" in pedido.content
    assert _FakeClient.created == []

    feito = tools["agenda_nova_tarefa"].handler(
        {"texto": "pagar boleto", "confirm": True}, ctx
    )
    assert feito.ok is True
    assert _FakeClient.created == [
        {"text": "pagar boleto", "date": feito.content.split("Agenda de ")[1][:10],
         "parent_id": ""}
    ]


def test_agenda_nova_tarefa_recusa_texto_vazio(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_nova_tarefa"].handler(
        {"texto": "   ", "confirm": True}, ctx
    )
    assert saida.ok is False


def test_agenda_marcar_por_texto_e_por_id(tools_agenda):
    tools, ctx = tools_agenda
    pendente = tools["agenda_marcar_tarefa"].handler({"texto": "fio dental"}, ctx)
    assert pendente.ok is False and "Confirmacao necessaria" in pendente.content

    feito = tools["agenda_marcar_tarefa"].handler(
        {"texto": "fio dental", "confirm": True}, ctx
    )
    assert feito.ok is True
    assert _FakeClient.marcados == [("a", True)]

    reabrir = tools["agenda_marcar_tarefa"].handler(
        {"id": "c", "feito": False, "confirm": True}, ctx
    )
    assert reabrir.ok is True
    assert _FakeClient.marcados[-1] == ("c", False)


def test_agenda_marcar_nao_encontrada(tools_agenda):
    tools, ctx = tools_agenda
    saida = tools["agenda_marcar_tarefa"].handler(
        {"texto": "inexistente", "confirm": True}, ctx
    )
    assert saida.ok is False
    assert "nao achei" in saida.content


def test_agenda_tool_sem_credencial_avisa(tmp_path):
    """Sem .env da Agenda a ferramenta explica o que falta, não estoura solta."""
    from zaya.agent.tools.agenda import agenda_dia

    ctx = ToolContext(tmp_path, True, AgendaConfig(env_file=str(tmp_path / "nao.env")))
    with pytest.raises(ToolError, match="Agenda"):
        agenda_dia({}, ctx)


def test_agenda_dia_nao_mostra_hashtags(tools_agenda, monkeypatch):
    import zaya.agent.tools.agenda as modulo

    notas = [
        {"id": "a", "text": "fio dental #saude", "is_checkbox": True, "completed": False},
        {"id": "b", "text": "#importante", "is_checkbox": True, "completed": False},
    ]

    class _ComTags(_FakeClient):
        async def notes_on(self, date):
            return notas

    monkeypatch.setattr(modulo, "AgendaClient", _ComTags)
    tools, ctx = tools_agenda
    saida = tools["agenda_dia"].handler({}, ctx)
    assert "#" not in saida.content
    assert "fio dental" in saida.content
    assert "Pendentes (1)" in saida.content


def test_agenda_nova_tarefa_confirmacao_sem_hashtag(tools_agenda):
    tools, ctx = tools_agenda
    pedido = tools["agenda_nova_tarefa"].handler({"texto": "pagar boleto #financas"}, ctx)
    assert pedido.ok is False
    assert "#" not in pedido.content
    assert "pagar boleto" in pedido.content
