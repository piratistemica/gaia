from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from zaya.agent.errors import ToolError
from zaya.agent.tools.base import ToolContext
from zaya.agent.tools.planilhas import (
    _coluna,
    _intervalo,
    _letra,
    _serial_para_data,
    abrir,
    build_planilha_tools,
    linhas,
    planilha,
)

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _criar_xlsx(destino: Path) -> Path:
    """Monta um .xlsx mínimo e válido à mão: zip de XML, sem openpyxl."""
    conteudo = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="{MIME}.sheet.main+xml"/>
 <Override PartName="/xl/sharedStrings.xml" ContentType="{MIME}.sharedStrings+xml"/>
 <Override PartName="/xl/styles.xml" ContentType="{MIME}.styles+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>
  <sheet name="ok 6am" sheetId="1" r:id="rId1"/>
  <sheet name="Dieta" sheetId="2" r:id="rId2"/>
 </sheets>
</workbook>"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""
    # índice 0 = "segunda", 1 = café, 2 = texto com rich text (duas runs)
    shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
 <si><t>segunda</t></si>
 <si><t>café</t></si>
 <si><r><t>proteína</t></r><r><t> 30g</t></r></si>
 <si><t>DIETA</t></si>
</sst>"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>
 <cellXfs count="2">
  <xf numFmtId="0"/>
  <xf numFmtId="164"/>
 </cellXfs>
</styleSheet>"""
    # A1 shared(0)=segunda ; B1 número ; A2 data com estilo 1 ; B2 sem valor (linha vazia some)
    sheet1 = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c><c r="C1" t="s"><v>2</v></c></row>
  <row r="2"><c r="A2" s="1"><v>45956</v></c></row>
  <row r="3"><c r="A3"/></row>
  <row r="5"><c r="A5" t="b"><v>1</v></c></row>
 </sheetData>
</worksheet>"""
    sheet2 = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>
  <row r="1"><c r="A1" t="s"><v>3</v></c></row>
  <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>200</v></c></row>
  <row r="3"><c r="A3" t="inlineStr"><is><t>almoço</t></is></c></row>
 </sheetData>
</worksheet>"""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w") as zf:
        zf.writestr("[Content_Types].xml", conteudo)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
    return destino


@pytest.fixture
def planilhas(tmp_path) -> tuple[Path, ToolContext]:
    raiz = tmp_path / "brain"
    arquivo = _criar_xlsx(raiz / "planilhas" / "planner-talita.xlsx")
    assert arquivo.is_file()
    return raiz, ToolContext(tmp_path, True, None, str(raiz))


# ------------------------------------------------------------------ leitura crua
def test_letra_e_coluna_ida_e_volta():
    assert [_letra(i) for i in range(4)] == ["A", "B", "C", "D"]
    assert _letra(25) == "Z"
    assert _letra(26) == "AA"
    assert _coluna("A1") == 0
    assert _coluna("AA7") == 26


def test_serial_para_data_e_numero():
    assert _serial_para_data(45956) == "2025-10-26"
    assert _serial_para_data(1) == "1899-12-31"
    assert _serial_para_data("abc") == "abc"


def test_intervalo_aceita_faixa_numero_e_vazio():
    assert _intervalo("10-25", 100) == (10, 25)
    assert _intervalo("", 100) == (1, 100)
    assert _intervalo("7", 100) == (7, 100)
    with pytest.raises(ToolError):
        _intervalo("abc-def", 10)


def test_abrir_lista_abas_e_shared_strings_com_rich_text(planilhas):
    raiz, _ = planilhas
    livro = abrir(raiz / "planilhas" / "planner-talita.xlsx")
    assert [aba.nome for aba in livro.abas] == ["ok 6am", "Dieta"]
    assert livro.compartilhadas[2] == "proteína 30g"
    assert 1 in livro.datas  # estilo 1 usa numFmt de data


def test_linhas_resolvem_shared_numero_data_booleano_e_inline(planilhas):
    raiz, _ = planilhas
    caminho = raiz / "planilhas" / "planner-talita.xlsx"
    livro = abrir(caminho)
    ok = next(aba for aba in livro.abas if aba.nome == "ok 6am")
    dados = dict(linhas(livro, caminho, ok))
    assert dados[1] == ["segunda", "42", "proteína 30g"]
    assert dados[2] == ["2025-10-26"]      # data convertida, não 45956
    assert 3 not in dados                  # linha só com célula vazia é ignorada
    assert dados[5] == ["VERDADEIRO"]

    dieta = next(aba for aba in livro.abas if aba.nome == "Dieta")
    linhas_dieta = dict(linhas(livro, caminho, dieta))
    assert linhas_dieta[3] == ["almoço"]   # inlineStr


# ------------------------------------------------------------------ ferramenta
def test_ferramenta_sem_parametros_lista_abas(planilhas):
    _, ctx = planilhas
    saida = planilha({}, ctx)
    assert saida.ok
    assert "planner-talita.xlsx" in saida.content
    assert "ok 6am: 3 linhas" in saida.content
    assert "Dieta: 3 linhas" in saida.content
    assert "aba='Dieta'" in saida.content


def test_ferramenta_mostra_faixa_de_linhas(planilhas):
    _, ctx = planilhas
    saida = planilha({"aba": "ok 6am", "linhas": "1-2"}, ctx)
    assert saida.ok
    assert "colunas: A | B | C" in saida.content
    assert "1: segunda | 42 | proteína 30g" in saida.content
    assert "2: 2025-10-26" in saida.content
    assert "5: VERDADEIRO" not in saida.content


def test_ferramenta_busca_case_insensitive(planilhas):
    _, ctx = planilhas
    saida = planilha({"arquivo": "planner", "busca": "CAFÉ"}, ctx)
    assert saida.ok
    assert "1 linha(s) com 'CAFÉ'" in saida.content
    assert "Dieta 2: café | 200" in saida.content


def test_ferramenta_busca_sem_resultado_nao_e_erro(planilhas):
    _, ctx = planilhas
    saida = planilha({"aba": "Dieta", "busca": "jaca"}, ctx)
    assert saida.ok is True
    assert "nenhuma linha com 'jaca'" in saida.content


def test_ferramenta_aba_inexistente_mostra_as_abas(planilhas):
    _, ctx = planilhas
    saida = planilha({"aba": "paleo"}, ctx)
    assert saida.ok is False
    assert "ok 6am, Dieta" in saida.content


def test_ferramenta_arquivo_inexistente_lista_disponiveis(planilhas):
    _, ctx = planilhas
    saida = planilha({"arquivo": "orcamento"}, ctx)
    assert saida.ok is False


def test_ferramenta_usa_arquivo_mais_recente(tmp_path):
    raiz = tmp_path / "brain"
    pasta = raiz / "planilhas"
    antigo = _criar_xlsx(pasta / "velha.xlsx")
    novo = _criar_xlsx(pasta / "nova.xlsx")
    import os
    import time

    os.utime(antigo, (time.time() - 86400, time.time() - 86400))
    ctx = ToolContext(tmp_path, True, None, str(raiz))
    saida = planilha({"aba": "ok 6am"}, ctx)
    assert novo.name in saida.content


def test_ferramenta_recusa_zip_invalido(tmp_path):
    raiz = tmp_path / "brain"
    pasta = raiz / "planilhas"
    pasta.mkdir(parents=True)
    (pasta / "quebrada.xlsx").write_bytes(b"nao sou zip")
    ctx = ToolContext(tmp_path, True, None, str(raiz))
    saida = planilha({}, ctx)
    assert saida.ok is False
    assert "não é uma planilha" in saida.content


def test_ferramenta_sem_secondbrain_avisa(tmp_path):
    with pytest.raises(ToolError, match="SecondBrain"):
        planilha({}, ToolContext(tmp_path))


def test_ferramenta_nao_pede_aprovacao():
    tool = build_planilha_tools()[0]
    assert tool.name == "planilha"
    assert tool.requires_approval is False
