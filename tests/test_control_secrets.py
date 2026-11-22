"""Testes do gerenciamento de credenciais no .env."""

from __future__ import annotations

import json

import pytest

from gaia.control.secrets import (
    allowed_keys,
    describe_secrets,
    list_secrets,
    mask,
    remove_secret,
    set_secret,
)


def test_mask_nao_expoe_o_valor():
    assert mask("sk-segredo-1234") == "sk-…1234"
    assert mask("") == ""
    assert mask("abc") == "…"


def test_allowlist_inclui_chaves_dos_presets():
    chaves = allowed_keys()
    assert "GAIA_CONTROL_TOKEN" in chaves
    assert "OPENAI_API_KEY" in chaves
    assert "DEEPSEEK_API_KEY" in chaves


def test_lista_mascara_sem_vazar_valor(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-segredo-1234\n")
    itens = list_secrets(env, env={})
    assert len(itens) == 1
    item = itens[0]
    assert item["key"] == "OPENAI_API_KEY"
    assert item["masked"] == "sk-…1234"
    assert item["source"] == "dotenv"
    assert "segredo-1234" not in json.dumps(item)


def test_lista_marca_origem_no_ambiente(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-arquivo-1111\n")
    itens = list_secrets(env, env={"OPENAI_API_KEY": "sk-ambiente-2222"})
    item = itens[0]
    assert item["source"] == "env"
    assert item["masked"] == "sk-…2222"


def test_lista_ignora_chaves_fora_da_allowlist(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AWS_SECRET=nao-vaza\nOPENAI_API_KEY=sk-ok-1234\n")
    chaves = [item["key"] for item in list_secrets(env, env={})]
    assert "AWS_SECRET" not in chaves
    assert "OPENAI_API_KEY" in chaves


def test_lista_traz_variavel_so_do_ambiente(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    itens = list_secrets(env, env={"GAIA_SERVE_TOKEN": "abcdefgh"})
    item = next(i for i in itens if i["key"] == "GAIA_SERVE_TOKEN")
    assert item["source"] == "env"
    assert item["editable"] is False
    assert item["masked"] == "…efgh"


def test_set_secret_grava_com_backup_e_chmod(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OUTRA=1\n")
    item = set_secret(env, "OPENAI_API_KEY", "sk-nova-9876")
    texto = env.read_text()
    assert 'OPENAI_API_KEY="sk-nova-9876"' in texto
    assert "OUTRA=1" in texto
    assert (tmp_path / ".env.bak").exists()
    assert oct(env.stat().st_mode)[-3:] == "600"
    assert item["masked"] == "sk-…9876"


def test_set_secret_cria_arquivo_quando_nao_existe(tmp_path):
    env = tmp_path / ".env"
    set_secret(env, "DEEPSEEK_API_KEY", "sk-nova-0001")
    assert env.is_file()
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_set_secret_rotaciona_sem_duplicar(tmp_path):
    env = tmp_path / ".env"
    set_secret(env, "OPENAI_API_KEY", "sk-primeira-1111")
    set_secret(env, "OPENAI_API_KEY", "sk-segunda-2222")
    texto = env.read_text()
    assert texto.count("OPENAI_API_KEY=") == 1
    assert "sk-segunda-2222" in texto
    assert "sk-primeira-1111" not in texto


def test_set_secret_fora_da_allowlist_falha(tmp_path):
    with pytest.raises(ValueError):
        set_secret(tmp_path / ".env", "LD_PRELOAD", "/tmp/evil.so")


def test_set_secret_valor_vazio_falha(tmp_path):
    with pytest.raises(ValueError):
        set_secret(tmp_path / ".env", "OPENAI_API_KEY", "   ")


def test_set_secret_com_quebra_de_linha_falha(tmp_path):
    with pytest.raises(ValueError):
        set_secret(tmp_path / ".env", "OPENAI_API_KEY", "sk-a\nsk-b")


def test_set_secret_aceita_valor_com_aspas(tmp_path):
    env = tmp_path / ".env"
    set_secret(env, "OPENAI_API_KEY", "sk-com-aspas-9999")
    itens = list_secrets(env, env={})
    assert itens[0]["masked"] == "sk-…9999"


def test_remove_secret_comenta_linha(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-1-abcdef\nPATH_X=2\n")
    resultado = remove_secret(env, "OPENAI_API_KEY")
    texto = env.read_text()
    assert resultado["removed"] is True
    assert "# OPENAI_API_KEY=sk-1-abcdef" in texto
    assert "PATH_X=2" in texto


def test_remove_secret_nao_apaga_de_novo(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# OPENAI_API_KEY=sk-1\n")
    resultado = remove_secret(env, "OPENAI_API_KEY")
    assert resultado["removed"] is False


def test_remove_secret_arquivo_ausente(tmp_path):
    with pytest.raises(FileNotFoundError):
        remove_secret(tmp_path / "nao-existe.env", "OPENAI_API_KEY")


def test_remove_secret_fora_da_allowlist(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LD_PRELOAD=/tmp/x.so\n")
    with pytest.raises(ValueError):
        remove_secret(env, "LD_PRELOAD")


def test_remove_secret_marca_como_comentada(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-1-abcd\n")
    remove_secret(env, "OPENAI_API_KEY")
    itens = list_secrets(env, env={})
    item = itens[0]
    assert item["commented"] is True
    assert item["present"] is False
    assert item["masked"] == ""


def test_describe_secrets_informa_caminho_e_allowlist(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-1-abcd\n")
    dados = describe_secrets(env)
    assert dados["path"] == str(env)
    assert dados["exists"] is True
    assert dados["writable"] is True
    assert "GAIA_CONTROL_TOKEN" in dados["allowed"]
    assert len(dados["secrets"]) == 1


def test_describe_secrets_sem_arquivo(tmp_path):
    dados = describe_secrets(tmp_path / ".env")
    assert dados["exists"] is False
    assert dados["secrets"] == []
    assert dados["writable"] is True
