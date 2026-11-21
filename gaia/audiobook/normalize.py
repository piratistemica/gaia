"""Limpeza deterministica do texto antes de virar narracao.

Remove o que soa mal na voz e nao carrega conteudo: Markdown, URLs,
numeracao de pagina, cabecalhos repetidos e ruido de extracao. Nao chama o
modelo: e rapido e barato, roda por trecho durante a geracao.
"""

from __future__ import annotations

import re
from collections import Counter

from gaia.audiobook.extract import Extracted

# Expansoes para o TTS ler direito (a voz nao conhece essas grafias).
_ABBREVIATIONS = {
    "ex.": "exemplo",
    "etc.": "etcetera",
    "pág.": "pagina",
    "pag.": "pagina",
    "nº": "numero",
    "n°": "numero",
    "art.": "artigo",
    "cap.": "capitulo",
    "sr.": "senhor",
    "sra.": "senhora",
    "dr.": "doutor",
    "dra.": "doutora",
    "vs.": "versus",
    "a.C.": "antes de Cristo",
    "d.C.": "depois de Cristo",
    "km": "quilometros",
    "kg": "quilos",
    "a.m.": "da manha",
    "p.m.": "da tarde",
}

# Numero solto no meio/fim da pagina (rodape). "pág. N" sozinho pode ser uma
# referencia no texto, entao so sai quando o texto inteiro e so isso.
_PAGE_NUMBERS = re.compile(r"^\s*\d{1,4}\s*(?:[-\u2013/]\s*\d{1,4})?\s*$")
# URL para a voz: nao engole o \n (senao juntaria paragrafos inteiros).
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]+")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^\s*[-*+•]\s+", re.M)
_MD_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.M)
_MD_EMPHASIS = re.compile(r"[*_~`]{1,3}")
# Quebra visual do PDF: "livr-\no" -> "livro" (traco colado ao fim da linha).
_HYPHEN_BREAK = re.compile(r"([^\s\-])-\n([^\s\-])")
_INLINE_WS = re.compile(r"[ \t]+")
_MULTI_NL = re.compile(r"\n{3,}")
_REPEATED_WORDS = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.I)


def _fix_hyphenation(text: str) -> str:
    """``trans-\nformar`` -> ``transformar`` (quebra de linha do PDF)."""
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def _drop_page_numbers(text: str) -> str:
    lines = [line for line in text.split("\n") if not _PAGE_NUMBERS.match(line)]
    return "\n".join(lines)


def _drop_repeated_headers(text: str) -> str:
    """Linhas curtas repetidas 3+ vezes (cabecalho/rodape de cada pagina)."""
    lines = text.split("\n")
    short = [line.strip() for line in lines if 0 < len(line.strip()) <= 60]
    counts = Counter(short)
    headers = {line for line, n in counts.items() if n >= 3}
    if not headers:
        return text
    return "\n".join(line for line in lines if line.strip() not in headers)


def _join_wrapped_lines(text: str) -> str:
    """Junta a quebra visual do PDF: cada linha vira paragrafo continuo.

    Cabecalhos e linhas em branco continuam separando blocos, senao um titulo
    de capitulo seria colado na primeira frase e viraria fala confusa.
    """
    paragraphs_out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            paragraphs_out.append(" ".join(current))
            current.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            paragraphs_out.append("")
            continue
        if _HEADING.match(stripped):
            flush()
            paragraphs_out.append(stripped)
            paragraphs_out.append("")
            continue
        current.append(stripped)
    flush()
    return "\n".join(paragraphs_out)


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")  # soft hyphen do PDF
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _EMAIL.sub("", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_NUMBERED.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _fix_hyphenation(text)
    text = _drop_page_numbers(text)
    text = _drop_repeated_headers(text)
    text = _INLINE_WS.sub(" ", text)
    text = _join_wrapped_lines(text)
    text = re.sub(r" *\n *", "\n", text)
    text = _MULTI_NL.sub("\n\n", text)
    text = _REPEATED_WORDS.sub(r"\1", text)
    text = _apply_abbreviations(text)
    return text.strip()


def _apply_abbreviations(text: str) -> str:
    for short, long in _ABBREVIATIONS.items():
        text = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", long, text, flags=re.I)
    return text


# $ em modo multiline sai antes do \n; o \n* final cobre o titulo seguido de
# linha em branco (que e como ele aparece depois da limpeza).
_HEADING = re.compile(
    r"^[ \t]*(?:(?i:cap[ií]tulo|parte)[ \t]+[0-9IVXLCDM]+\b|([A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9][A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9 :,\-]{3,60}))[ \t\n]*",
    re.M,
)


def paragraphs(text: str) -> list[str]:
    """Paragrafos, juntando linhas duras do PDF numa frase corrida."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        out.append(" ".join(lines))
    return out


def chapters(text: str, fallback_title: str = "Audiolivro") -> list[tuple[str, str]]:
    """Divide por cabecalhos de capitulo; sem cabecalho, um capitulo unico.

    Reconhece ``Capitulo N``/``Capítulo N``, ``Parte N`` e titulos em CAPS
    curtos. Cada capitulo sai como uma faixa do MP3 final.
    """
    # group(1) e o titulo em CAPS; None = "Capitulo/Parte N".
    # Titulos em CAPS so sao estrutura quando ha pelo menos 2: um unico
    # "titulo" pode ser so uma frase no meio do texto.
    marks = [m for m in _HEADING.finditer(text) if _is_structural(m)]
    if not any(m.group(1) is None for m in marks) and len(marks) < 2:
        return [(fallback_title, text.strip())]

    result: list[tuple[str, str]] = []
    # Texto antes do primeiro cabecalho (prefacio, epigrafe).
    preface = text[: marks[0].start()].strip()
    if preface:
        result.append(("Abertura", preface))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        title = " ".join(mark.group(0).split())[:80]
        body = text[mark.end() : end].strip()
        if body:
            result.append((title, body))
    return result or [(fallback_title, text.strip())]


def _is_structural(match: re.Match[str]) -> bool:
    """Capitulo/Parte numerado sempre vale; titulo em CAPS precisa 2+ palavras."""
    if match.group(1) is None:
        return True
    words = match.group(0).split()
    return 2 <= len(words) <= 8


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Divide em trechos de ate max_chars, cortando em fim de frase."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
            window.rfind("\n"),
        )
        if cut <= max_chars // 3:
            cut = max_chars
        chunks.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


def prepare(extracted: Extracted, fallback_title: str) -> list[tuple[str, str]]:
    """Extracted -> capitulos prontos para narracao (limpos)."""
    cleaned = clean_text(extracted.text)
    result = [(title, clean_text(body)) for title, body in chapters(cleaned, fallback_title)]
    return [(title, body) for title, body in result if body]
