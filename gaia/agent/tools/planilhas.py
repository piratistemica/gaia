"""Leitura de planilhas .xlsx sem dependência externa (zipfile + XML).

Motivo: no canal do Telegram o agente não tem shell (``telegram.shell_access``
é desligado de propósito) e o venv do mini não tem ``openpyxl``. Um ``.xlsx`` é
um zip de XML, então dá para ler com a biblioteca padrão — leitura pura, sem
tocar no arquivo e sem executar nada.

Limites conhecidos: fórmulas aparecem pelo valor em cache; células formatadas
como data viram AAAA-MM-DD; o resto numérico sai como número.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from gaia.agent.errors import ToolError
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, integer, schema, string
from gaia.briefing.compose import normalize

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

SUFIXOS = (".xlsx", ".xlsm")
MAX_LINHAS = 200
MAX_CELULA = 200
LINHAS_PADRAO = 30
LINHAS_BUSCA = 40

# numFmtId embutidos que o Excel trata como data/hora.
NUMFMT_DATA = {
    14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 30, 36, 45, 46, 47, 50, 57,
}


@dataclass(frozen=True)
class Aba:
    nome: str
    caminho: str


@dataclass
class Planilha:
    """Conteúdo lido: abas, strings compartilhadas e estilos de data."""

    abas: list[Aba]
    compartilhadas: list[str] = field(default_factory=list)
    datas: set[int] = field(default_factory=set)


def _tag(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1]


def _letra(indice: int) -> str:
    """0 → A, 25 → Z, 26 → AA (padrão de coluna do Excel: base 26 sem zero)."""
    letra = ""
    atual = indice + 1
    while atual > 0:
        atual, resto = divmod(atual - 1, 26)
        letra = chr(65 + resto) + letra
    return letra


def _coluna(referencia: str) -> int:
    '''"B7" → 1'''
    indice = 0
    for caractere in referencia:
        if not caractere.isalpha():
            break
        indice = indice * 26 + (ord(caractere.upper()) - 64)
    return max(0, indice - 1)


def _serial_para_data(valor: float) -> str:
    """Serial do Excel (dias desde 1899-12-30) → AAAA-MM-DD."""
    try:
        dias = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    base = date(1899, 12, 30)
    if dias < 0 or dias > 2_958_465:  # fora da faixa plausível
        return f"{dias:g}"
    momento = datetime.combine(base, datetime.min.time()) + timedelta(days=dias)
    if momento.hour or momento.minute or momento.second:
        return momento.strftime("%Y-%m-%d %H:%M")
    return momento.date().isoformat()


def _texto_estilo(formato: str) -> bool:
    """Heurística: o formato de número parece data?"""
    limpo = formato.split(";")[0].lower()
    for pedaco in ('"', "[", "]"):
        limpo = limpo.replace(pedaco, "")
    return any(marca in limpo for marca in ("yy", "dd", "mm/", "/mm", "h:m"))


def _estilos(zf: zipfile.ZipFile) -> set[int]:
    """Índices de estilo (cellXfs) que representam data."""
    formatos: dict[int, str] = {}
    try:
        raiz = ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return set()
    for elemento in raiz.iter():
        if _tag(elemento) == "numFmt":
            try:
                formatos[int(elemento.get("numFmtId", "0"))] = elemento.get(
                    "formatCode", ""
                )
            except ValueError:
                continue
    datas: set[int] = set()
    celulas = next(
        (elemento for elemento in raiz.iter() if _tag(elemento) == "cellXfs"), None
    )
    if celulas is None:
        return datas
    for indice, xf in enumerate(celulas):
        try:
            numfmt = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        if numfmt in NUMFMT_DATA or _texto_estilo(formatos.get(numfmt, "")):
            datas.add(indice)
    return datas


def abrir(caminho: Path) -> Planilha:
    """Lê workbook, relationships, sharedStrings e estilos de um .xlsx."""
    try:
        zf = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as exc:
        raise ToolError(f"{caminho.name} não é uma planilha .xlsx válida") from exc
    except OSError as exc:
        raise ToolError(f"não consegui abrir {caminho}: {exc}") from exc

    with zf:
        try:
            raiz = ET.fromstring(zf.read("xl/workbook.xml"))
        except KeyError as exc:
            raise ToolError(f"{caminho.name} sem xl/workbook.xml") from exc

        alvos: dict[str, str] = {}
        try:
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                alvos[rel.get("Id", "")] = rel.get("Target", "")
        except KeyError:
            pass

        abas: list[Aba] = []
        for elemento in raiz.iter():
            if _tag(elemento) != "sheet":
                continue
            alvo = alvos.get(elemento.get(f"{REL_NS}id", ""), "")
            if not alvo:
                continue
            caminho_rel = alvo.lstrip("/")
            if not caminho_rel.startswith("xl/"):
                caminho_rel = f"xl/{caminho_rel}"
            abas.append(Aba(str(elemento.get("name") or "?"), caminho_rel))

        compartilhadas: list[str] = []
        try:
            sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst:
                compartilhadas.append(
                    "".join(t.text or "" for t in si.iter() if _tag(t) == "t")
                )
        except KeyError:
            pass

        planilha = Planilha(abas=abas, compartilhadas=compartilhadas, datas=_estilos(zf))
    return planilha


def _valor(celula: ET.Element, planilha: Planilha) -> str:
    tipo = celula.get("t", "")
    bruto = ""
    inline = ""
    for filho in celula:
        nome = _tag(filho)
        if nome == "v":
            bruto = filho.text or ""
        elif nome == "is":
            inline = "".join(t.text or "" for t in filho.iter() if _tag(t) == "t")
    if tipo == "s":
        try:
            texto = planilha.compartilhadas[int(bruto)]
        except (ValueError, IndexError):
            texto = ""
    elif tipo == "inlineStr":
        texto = inline
    elif tipo == "b":
        texto = "VERDADEIRO" if bruto.strip() == "1" else "FALSO"
    elif tipo in {"str", "e"}:
        texto = bruto
    else:
        if bruto.strip() == "":
            return ""
        try:
            numero = float(bruto)
        except ValueError:
            return bruto
        estilo = celula.get("s")
        if estilo is not None:
            try:
                if int(estilo) in planilha.datas:
                    return _serial_para_data(numero)
            except ValueError:
                pass
        if numero.is_integer() and abs(numero) < 1e15:
            return str(int(numero))
        return f"{numero:g}"
    return " ".join(texto.split()) if "\n" in texto else texto


def linhas(planilha: Planilha, caminho: Path, aba: Aba) -> list[tuple[int, list[str]]]:
    """[(número da linha, [valores...])], na ordem das colunas do arquivo."""
    try:
        with zipfile.ZipFile(caminho) as zf:
            raiz = ET.fromstring(zf.read(aba.caminho))
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise ToolError(f"não consegui ler a aba {aba.nome}: {exc}") from exc

    saida: list[tuple[int, list[str]]] = []
    for linha in raiz.iter():
        if _tag(linha) != "row":
            continue
        try:
            numero = int(linha.get("r", "0"))
        except ValueError:
            numero = 0
        valores: dict[int, str] = {}
        for celula in linha:
            if _tag(celula) != "c":
                continue
            texto = _valor(celula, planilha)
            if texto:
                valores[_coluna(celula.get("r", "A"))] = texto
        if not valores:
            continue
        largura = max(valores) + 1
        linha_valores = [valores.get(i, "") for i in range(largura)]
        saida.append((numero, linha_valores))
    return saida


# ------------------------------------------------------------------ ferramenta
def _raiz(ctx: ToolContext) -> Path:
    if not str(getattr(ctx, "secondbrain", "") or "").strip():
        raise ToolError("SecondBrain não configurado (secondbrain.path)")
    return Path(str(ctx.secondbrain)).expanduser()


def _candidatas(raiz: Path) -> list[Path]:
    pasta = raiz / "planilhas"
    base = pasta if pasta.is_dir() else raiz
    arquivos = [
        p
        for p in base.rglob("*")
        if p.is_file() and p.suffix.lower() in SUFIXOS and not p.name.startswith("~$")
    ]
    return sorted(arquivos, key=lambda p: (-p.stat().st_mtime, str(p)))


def _escolher(raiz: Path, pedido: str) -> Path:
    disponiveis = _candidatas(raiz)
    if not disponiveis:
        raise ToolError(f"nenhuma planilha (.xlsx) em {raiz / 'planilhas'}")
    procurado = normalize(pedido)
    if not procurado:
        return disponiveis[0]
    for arquivo in disponiveis:
        if procurado in normalize(arquivo.stem):
            return arquivo
    nomes = ", ".join(arquivo.name for arquivo in disponiveis)
    raise ToolError(f"não achei planilha com '{pedido}'. Disponíveis: {nomes}")


def _curto(valor: str) -> str:
    if len(valor) <= MAX_CELULA:
        return valor
    return valor[: MAX_CELULA - 1] + "…"


def _linha_texto(numero: int, valores: list[str]) -> str:
    while valores and not valores[-1]:
        valores.pop()
    corpo = " | ".join(_curto(v) for v in valores)
    return f"{numero}: {corpo}"


def _intervalo(bruto: str, total: int) -> tuple[int, int]:
    """'10-25' → (10, 25); vazio → (1, total)."""
    texto = str(bruto or "").strip()
    if not texto:
        return 1, total
    if "-" in texto:
        inicio_txt, _, fim_txt = texto.replace(" ", "").partition("-")
        try:
            return max(1, int(inicio_txt)), max(1, int(fim_txt))
        except ValueError:
            raise ToolError(f"intervalo inválido: {texto} (use 10-25)") from None
    try:
        unico = max(1, int(texto))
    except ValueError:
        raise ToolError(f"intervalo inválido: {texto}") from None
    return unico, total


def planilha(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    raiz = _raiz(ctx)
    try:
        arquivo = _escolher(raiz, str(arguments.get("arquivo") or ""))
        livro = abrir(arquivo)
    except ToolError as exc:
        return ToolResult.failure(str(exc))

    pedido = normalize(str(arguments.get("aba") or ""))
    busca_bruta = " ".join(str(arguments.get("busca") or "").split())
    busca = busca_bruta.casefold()

    if not pedido and not busca:
        resumo = [f"Planilha: {arquivo.name}"]
        for aba in livro.abas:
            dados = linhas(livro, arquivo, aba)
            largura = max((len(valores) for _, valores in dados), default=0)
            resumo.append(f"- {aba.nome}: {len(dados)} linhas x {largura} colunas")
        resumo.append(
            "\nPasse `aba` para ver o conteúdo (ex.: aba='Dieta', linhas='1-30')."
        )
        return ToolResult.success("\n".join(resumo))

    if pedido:
        exatas = [a for a in livro.abas if normalize(a.nome) == pedido]
        parciais = [a for a in livro.abas if pedido in normalize(a.nome)]
        alvos = exatas or parciais
        if not alvos:
            nomes = ", ".join(a.nome for a in livro.abas)
            return ToolResult.failure(f"aba '{pedido}' não existe. Abas: {nomes}")
    else:
        # busca sem aba: procura em todas
        alvos = list(livro.abas)

    if busca:
        achados: list[str] = []
        total = 0
        for aba in alvos:
            for numero, valores in linhas(livro, arquivo, aba):
                if not any(busca in valor.casefold() for valor in valores if valor):
                    continue
                total += 1
                if total > LINHAS_BUSCA:
                    continue
                prefixo = f"{aba.nome} " if len(alvos) > 1 else ""
                achados.append(f"{prefixo}{_linha_texto(numero, list(valores))}")
        if not achados:
            onde = alvos[0].nome if len(alvos) == 1 else "nenhuma aba"
            return ToolResult.success(f"nenhuma linha com '{busca_bruta}' em {onde}")
        cabeca = f"{arquivo.name} | {total} linha(s) com '{busca_bruta}'"
        corpo = list(achados)
        if total > LINHAS_BUSCA:
            corpo.append(f"… e mais {total - LINHAS_BUSCA} linha(s)")
        return ToolResult.success("\n".join([cabeca, *corpo]))

    aba = alvos[0]
    try:
        dados = linhas(livro, arquivo, aba)
    except ToolError as exc:
        return ToolResult.failure(str(exc))
    try:
        limite = int(arguments.get("max_linhas") or LINHAS_PADRAO)
    except (TypeError, ValueError):
        limite = LINHAS_PADRAO
    limite = min(MAX_LINHAS, max(1, limite))
    try:
        inicio, fim = _intervalo(str(arguments.get("linhas") or ""), len(dados))
    except ToolError as exc:
        return ToolResult.failure(str(exc))
    selecionadas = [(n, v) for n, v in dados if inicio <= n <= fim][:limite]
    largura = max((len(valores) for _, valores in selecionadas), default=0)
    cabecalho = [
        f"{arquivo.name} | {aba.nome} | {len(dados)} linhas x {largura} colunas",
        "colunas: " + " | ".join(_letra(i) for i in range(largura)) if largura else "",
        f"linhas {inicio}-{fim}, {len(selecionadas)} com conteúdo",
    ]
    corpo = [_linha_texto(numero, valores) for numero, valores in selecionadas]
    if not corpo:
        corpo = ["(nenhuma linha neste intervalo)"]
    return ToolResult.success("\n".join([linha for linha in cabecalho if linha] + corpo))


PLANILHA_TOOL: dict[str, Any] = {
    "name": "planilha",
    "description": (
        "Le planilhas .xlsx do SecondBrain (pasta 'planilhas'). Sem parâmetros, "
        "lista as abas e o tamanho de cada uma. Com 'aba', devolve as linhas em "
        "texto (use 'linhas' = '1-30'); com 'busca', filtra linhas que contenham "
        "o termo. Somente leitura: nunca altera o arquivo."
    ),
    "parameters": schema(
        {
            "arquivo": string("Trecho do nome do arquivo; vazio = o mais recente"),
            "aba": string("Nome (ou trecho) da aba; vazio = listar abas"),
            "linhas": string("Intervalo de linhas, ex.: '1-30'; vazio = do começo"),
            "busca": string("Filtra linhas que contenham este termo"),
            "max_linhas": integer("Máximo de linhas a devolver (padrão 30, teto 200)"),
        }
    ),
    "handler": planilha,
    "requires_approval": False,
}


def build_planilha_tools() -> tuple[Tool, ...]:
    return (Tool(**PLANILHA_TOOL),)


__all__ = [
    "PLANILHA_TOOL",
    "Aba",
    "Planilha",
    "abrir",
    "build_planilha_tools",
    "linhas",
    "planilha",
]
