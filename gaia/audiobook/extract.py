"""Extracao de texto de PDF (biblioteca padrao, sem dependencia externa).

Um PDF e uma sequencia de objetos; o texto visivel fica nos streams de conteudo
entre ``BT``/``ET``, nas operacoes ``Tj``/``TJ``. Isso cobre a maioria dos PDFs
gerados por Word/LaTeX/exportadores, que usam fontes simples. PDFs escaneados
(imagem, sem camada de texto) nao saem daqui: a funcao devolve pouco texto e o
chamador trata o erro pedindo OCR.

Limite conhecido: PDFs com fontes Type0/CID (CJK) saem como lixo;, por isso o
resultado passa por uma checagem de "texto legivel" antes de virar audio.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

_OBJ = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.S)
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_TEXT_OP = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|\bT[Jj]\b|\bT[dD*]\b|\bTL\b|\bTf\b|\bET\b|\bBT\b", re.S)
_LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
_HEX = re.compile(rb"<([0-9A-Fa-f\s]+)>")
_ESCAPES = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}


@dataclass
class Extracted:
    text: str
    pages: int
    chars: int

    @property
    def readable(self) -> bool:
        """Texto com cara de prosa (para descartar PDF escaneado/CID)."""
        sample = self.text[:4000]
        if len(sample.strip()) < 40:
            return False
        letters = sum(c.isalpha() or c.isspace() for c in sample)
        return letters / max(1, len(sample)) > 0.7


def _unescape(raw: bytes) -> str:
    out = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index : index + 1]
        if char != b"\\":
            out += char
            index += 1
            continue
        nxt = raw[index + 1 : index + 2]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt]
            index += 2
        elif nxt.isdigit():
            octal = raw[index + 1 : index + 4]
            match = re.match(rb"[0-7]{1,3}", octal)
            if match:
                out.append(int(match.group(0), 8) & 0xFF)
                index += 1 + len(match.group(0))
            else:
                out += nxt
                index += 2
        elif nxt in {b"\n", b"\r"}:
            index += 2
        else:
            out += nxt
            index += 2
    return out.decode("utf-8", errors="replace")


def _decode_stream(raw: bytes) -> bytes:
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return b""


def _stream_text(content: bytes) -> str:
    """Texto de um stream de conteudo: junta Tj/TJ e quebra linha em Td/TD/T*."""
    parts: list[str] = []
    for token in _TEXT_OP.findall(content):
        if token.startswith(b"("):
            parts.append(_unescape(token[1:-1]))
        elif token.startswith(b"<"):
            digits = _HEX.match(token)
            if digits:
                clean = re.sub(rb"\s+", b"", digits.group(1))
                if len(clean) % 2:
                    clean += b"0"
                try:
                    parts.append(bytes.fromhex(clean.decode("ascii")).decode("utf-16-be", "ignore"))
                except ValueError:
                    pass
        elif token in {b"Td", b"TD", b"T*", b"ET"}:
            parts.append("\n")
        elif token == b"TJ":
            parts.append(" ")
    return "".join(parts)


def _strip_garbage(text: str) -> str:
    text = text.replace("\u0000", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text


def extract_pdf(path: Path) -> Extracted:
    data = path.read_bytes()
    chunks: list[str] = []
    for match in _OBJ.finditer(data):
        body = match.group(3)
        if b"stream" not in body:
            continue
        stream = _STREAM.search(body)
        if not stream:
            continue
        content = stream.group(1)
        if b"FlateDecode" in body[: stream.start()]:
            content = _decode_stream(content)
        if b"BT" not in content:
            continue
        chunks.append(_stream_text(content))
    text = _normalize(_strip_garbage("\n".join(chunks)))
    text = _trim_tail_garbage(text)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    if not pages:
        pages = max(1, text.count("\f") + 1)
    return Extracted(text=text, pages=pages, chars=len(text))


def extract_text(path: Path, text: str = "") -> Extracted:
    """Texto colado ou arquivo .txt/.md: normaliza e conta pagina estimada."""
    if not text:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = _normalize(text)
    pages = max(1, len(text) // 1800)
    return Extracted(text=text, pages=pages, chars=len(text))


def _trim_tail_garbage(text: str) -> str:
    """Corta o lixo no fim (stream de fonte/imagem decodificado como texto).

    O lixo costuma vir colado na ultima linha, entao procura-se a primeira
    sequencia longa de caracteres ilegiveis e corta dali em diante.
    """
    marker = re.search(r"(?:\ufffd[^\n]{20,})|(?:[\x00-\x08\x0b\x0c\x0e-\x1f]{4,})", text)
    if marker and marker.start() > len(text) * 0.2:
        text = text[: marker.start()]
    lines = text.split("\n")
    while lines and _garbage_ratio(lines[-1]) > 0.4:
        lines.pop()
    return "\n".join(lines).strip()


def _garbage_ratio(line: str) -> float:
    visible = [c for c in line if not c.isspace()]
    if not visible:
        return 0.0
    bad = sum(1 for c in visible if c == "\ufffd" or ord(c) < 32)
    return bad / len(visible)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(path: Path) -> Extracted:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in {".txt", ".md", ".markdown", ".text"}:
        return extract_text(path)
    raise ValueError(f"formato nao suportado: {suffix or path.name} (use .pdf ou .txt)")
