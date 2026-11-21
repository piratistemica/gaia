from __future__ import annotations

from pathlib import Path

import pytest

from gaia.audiobook import extract as extract_mod
from gaia.audiobook import jobs as jobs_mod
from gaia.audiobook.config import AudiobookConfig
from gaia.audiobook.extract import Extracted, extract_text
from gaia.audiobook.jobs import AudiobookError, AudiobookJob, build_audiobook
from gaia.audiobook.normalize import (
    chapters,
    clean_text,
    prepare,
    split_for_tts,
)
from gaia.audiobook.service import pending, process_inbox, text_job


def _cfg(tmp_path: Path, **overrides) -> AudiobookConfig:
    data = {
        "voice": "pt-BR-ThalitaNeural",
        "chunk_chars": 400,
        "sentence_pause_ms": 0,
    }
    data.update(overrides)
    return AudiobookConfig(**data)


def _fake_audio(text: str, target: Path) -> None:
    target.write_bytes(b"\xff\xfb\x90\x00" + text.encode()[:40])


@pytest.fixture
def fake_tts(monkeypatch):
    calls: list[tuple[str, Path]] = []

    async def synthesize(cfg, text, target):
        calls.append((text, target))
        _fake_audio(text, target)

    monkeypatch.setattr(jobs_mod, "_synthesize_chunk", synthesize)
    return calls


def test_clean_text_remove_markdown_urls_e_quebras():
    text = (
        "# Título\n\n"
        "Uma palavra-\nquebrada e um [link](http://exemplo.com).\n"
        "Veja https://site.com/x e **negrito**.\n\n\n\n"
        "1\n\n2\n"
    )
    cleaned = clean_text(text)
    assert "#" not in cleaned
    assert "http" not in cleaned
    assert "palavraquebrada" in cleaned
    assert "negrito" in cleaned
    assert "link" in cleaned


def test_clean_text_junta_linhas_do_pdf_num_paragrafo():
    text = "Era uma vez um rato que\nvivia numa biblioteca\nantiga.\n\nOutro paragrafo."
    cleaned = clean_text(text)
    assert "voce" not in cleaned
    assert "um rato que vivia numa biblioteca antiga." in cleaned


def test_clean_text_remove_cabecalho_repetido():
    page = "O RATO E A LUA\n\n" + "A historia continua aqui com bastante texto. " * 3
    text = "\n\n".join([page] * 4)
    cleaned = clean_text(text)
    assert cleaned.count("O RATO E A LUA") <= 1


def test_clean_text_expande_abreviacoes():
    assert "pagina 10" in clean_text("pág. 10")
    assert "etcetera" in clean_text("livros, revistas, etc.")


def test_split_for_tts_respeita_limite_e_fim_de_frase():
    text = "Primeira frase. Segunda frase. Terceira frase. Quarta frase."
    chunks = split_for_tts(text, 30)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_split_for_tts_texto_curto_fica_num_treco():
    assert split_for_tts("Oi.", 100) == ["Oi."]
    assert split_for_tts("", 100) == []


def test_chapters_sem_cabecalho_fica_um_so():
    text = "Era uma vez um rato que vivia feliz. " * 20
    result = chapters(text, "Meu Livro")
    assert result == [("Meu Livro", text.strip())]


def test_chapters_divide_por_capitulo():
    text = (
        "Prefacio curto de abertura.\n\n"
        "Capitulo 1\n\n"
        + "Primeiro capitulo com bastante texto. " * 10
        + "\n\nCapitulo 2\n\n"
        + "Segundo capitulo com bastante texto. " * 10
    )
    sections = chapters(text, "Livro")
    titles = [title for title, _ in sections]
    assert titles == ["Abertura", "Capitulo 1", "Capitulo 2"]
    assert all(body for _, body in sections)


def test_prepare_descarta_texto_que_nao_sobra():
    extracted = Extracted(text="###", pages=1, chars=3)
    assert prepare(extracted, "x") == []


def test_extract_text_arquivo_txt(tmp_path):
    path = tmp_path / "livro.txt"
    path.write_text("Primeira linha.\nSegunda linha.", encoding="utf-8")
    extracted = extract_text(path)
    assert extracted.chars > 0
    assert "Primeira linha" in extracted.text


def test_extract_formato_nao_suportado(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_mod.extract(path)


@pytest.mark.asyncio
async def test_build_audiobook_txt_gera_mp3_com_id3(tmp_path, fake_tts):
    source = tmp_path / "livro.txt"
    source.write_text("Capitulo 1\n\n" + "Uma historia muito longa. " * 40, encoding="utf-8")
    job = AudiobookJob(
        source=source,
        outbox=tmp_path / "out",
        cfg=_cfg(tmp_path),
        tmp_dir=tmp_path / "tmp",
    )
    progress: list[tuple[int, int]] = []
    output = await build_audiobook(job, progress=lambda d, t: progress.append((d, t)))

    assert output.is_file()
    assert output.name == "livro.mp3"
    data = output.read_bytes()
    assert data.startswith(b"ID3\x03")
    assert b"TIT2" in data and b"CHAP" in data
    assert b"Uma historia muito longa." in data
    assert progress[-1][0] == progress[-1][1] > 0
    # cache de trechos e merged apagados no fim
    assert not any(job.workdir.glob("*.mp3"))


@pytest.mark.asyncio
async def test_build_audiobook_retoma_do_cache(tmp_path, monkeypatch):
    source = tmp_path / "livro.txt"
    source.write_text("Uma historia muito longa. " * 60, encoding="utf-8")
    cfg = _cfg(tmp_path, chunk_chars=300)
    job = AudiobookJob(source=source, outbox=tmp_path / "out", cfg=cfg, tmp_dir=tmp_path / "tmp")

    generated: list[Path] = []

    async def first(cfg_, text, target):
        generated.append(target)
        _fake_audio(text, target)

    monkeypatch.setattr(jobs_mod, "_synthesize_chunk", first)
    await build_audiobook(job)

    # Segunda execucao com o mesmo source: cache zerado, entao regenera.
    # Para testar a retomada, guardamos um trecho antes de rodar de novo.
    job2 = AudiobookJob(source=source, outbox=tmp_path / "out", cfg=cfg, tmp_dir=tmp_path / "tmp")
    job2.workdir.mkdir(parents=True, exist_ok=True)
    (job2.workdir / "00000.mp3").write_bytes(b"\xff\xfb\x90\x00cached")

    calls: list[str] = []

    async def second(cfg_, text, target):
        calls.append(text)
        _fake_audio(text, target)

    monkeypatch.setattr(jobs_mod, "_synthesize_chunk", second)
    output = await build_audiobook(job2)
    assert b"cached" in output.read_bytes()
    assert len(calls) == len(generated) - 1


@pytest.mark.asyncio
async def test_build_audiobook_texto_curto_apos_limpeza_falha(tmp_path, fake_tts):
    source = tmp_path / "vazio.txt"
    source.write_text("###\n\n1\n2\n", encoding="utf-8")
    job = AudiobookJob(source=source, outbox=tmp_path / "out", cfg=_cfg(tmp_path), tmp_dir=tmp_path / "tmp")
    with pytest.raises(AudiobookError):
        await build_audiobook(job)


@pytest.mark.asyncio
async def test_build_audiobook_pdf_ilegivel_falha(tmp_path, fake_tts):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    job = AudiobookJob(source=source, outbox=tmp_path / "out", cfg=_cfg(tmp_path), tmp_dir=tmp_path / "tmp")
    with pytest.raises(AudiobookError):
        await build_audiobook(job)


@pytest.mark.asyncio
async def test_process_inbox_arquiva_sucesso_e_erro(tmp_path, fake_tts):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    good = inbox / "bom.txt"
    good.write_text("Uma historia longa o suficiente. " * 30, encoding="utf-8")
    bad = inbox / "ruim.pdf"
    bad.write_bytes(b"%PDF-1.4\n%%EOF")

    result = await process_inbox(
        _cfg(tmp_path), inbox, tmp_path / "out", tmp_path / "tmp", _logger()
    )

    assert [p.name for p in result.done] == ["bom.mp3"]
    assert [p.name for p, _ in result.failed] == ["ruim.pdf"]
    assert (inbox / "processados" / "bom.txt").is_file()
    assert (inbox / "erros" / "ruim.pdf").is_file()
    assert not good.exists() and not bad.exists()


@pytest.mark.asyncio
async def test_process_inbox_desligado_nao_faz_nada(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("texto", encoding="utf-8")
    result = await process_inbox(
        _cfg(tmp_path, enabled=False), inbox, tmp_path / "out", tmp_path / "tmp", _logger()
    )
    assert result.empty


def test_process_inbox_permanece_enquanto_cresce(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    path = inbox / "a.txt"
    path.write_text("a" * 10, encoding="utf-8")
    assert [p.name for p in pending(_cfg(tmp_path), inbox)] == ["a.txt"]


def test_text_job_titulo_padrao(tmp_path):
    job = text_job(_cfg(tmp_path), tmp_path / "out", tmp_path / "tmp", "texto qualquer")
    assert job.direct_text is True
    assert job.title == "Texto colado"


def _logger():
    import logging

    return logging.getLogger("gaia.audiobook.test")
