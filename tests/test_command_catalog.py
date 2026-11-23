"""Catalogo dos comandos: uma fonte so para terminal, web e `/ajuda`.

O teste de deriva le o `_handle_command` por AST: comando novo no despacho sem
entrada no catalogo (ou o contrario) falha aqui, e nao tres telas depois.
"""

from __future__ import annotations

import ast
from pathlib import Path

from gaia.controller.command_catalog import COMMANDS, as_dicts, as_rows

RAIZ = Path(__file__).resolve().parent.parent
COMMANDS_PY = RAIZ / "gaia" / "controller" / "commands.py"


def _nomes_do_despacho() -> set[str]:
    """Todos os literais de comando comparados em `_handle_command`."""
    arvore = ast.parse(COMMANDS_PY.read_text(encoding="utf-8"))
    alvo = next(
        no
        for no in ast.walk(arvore)
        if isinstance(no, ast.AsyncFunctionDef | ast.FunctionDef)
        and no.name == "_handle_command"
    )
    nomes: set[str] = set()
    for no in ast.walk(alvo):
        if isinstance(no, ast.Compare):
            for comparado in [no.left, *no.comparators]:
                if isinstance(comparado, ast.Constant) and isinstance(comparado.value, str):
                    if comparado.value.startswith("/"):
                        nomes.add(comparado.value)
        elif isinstance(no, ast.Set | ast.Tuple | ast.List):
            for item in no.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    if item.value.startswith("/"):
                        nomes.add(item.value)
    return nomes


def test_catalogo_e_despacho_cobrem_os_mesmos_comandos():
    catalogo = {nome for command in COMMANDS for nome in command.names}
    despacho = _nomes_do_despacho()
    # "/" abre a lista e cai em /ajuda; nao e comando proprio no catalogo.
    despacho = despacho - {"/"}
    assert catalogo - despacho == set(), "catalogo lista comando que ninguem trata"
    assert despacho - catalogo == set(), "despacho trata comando fora do catalogo"


def test_nomes_unicos():
    nomes = [nome for command in COMMANDS for nome in command.names]
    assert len(nomes) == len(set(nomes))


def test_linhas_da_ajuda_tem_sintaxe_e_descricao():
    for sintaxe, descricao in as_rows():
        assert sintaxe.startswith("/")
        assert descricao.strip()


def test_dicts_do_web_levam_nomes_e_argumento():
    dados = as_dicts()
    assert dados[0]["usage"] == "/ajuda"
    assert "/help" in dados[0]["names"]
    assert dados[0]["argument"] is False
    busca = next(item for item in dados if item["usage"].startswith("/buscar"))
    assert busca["argument"] is True
