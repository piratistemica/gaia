"""Credenciais no ``.env``: leitura mascarada, escrita atomica e remocao.

Regra do repo que este modulo respeita sem excecao: **segredo so vive no
``.env``/ambiente, nunca no TOML, nunca no log, nunca inteiro na tela.**

O painel nunca recebe o valor completo. Ele ve ``sk-…1234`` e pode:

* gravar/rotacionar (backup ``.env.bak``, ``chmod 600``, releitura conferida);
* remover (a linha e comentada, nao apagada — da para voltar atras).

Como o ambiente vence o TOML em ``Config.load``, "remover" aqui significa
**remover o override**, e nao apagar a credencial do processo em execucao.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gaia.agent.providers import PRESETS

# Chaves que o painel pode escrever. Qualquer outra e recusada: um campo livre
# aqui viraria um jeito de sobrescrever PATH ou LD_PRELOAD no .env.
_FIXED_KEYS = (
    "GAIA_AGENT_API_KEY",
    "GAIA_AGENT_PROVIDER",
    "GAIA_AGENT_MODEL",
    "GAIA_SERVE_TOKEN",
    "GAIA_CONTROL_TOKEN",
    "GAIA_TELEGRAM_BOT_TOKEN",
    "GAIA_TELEGRAM_ALLOWED_CHAT_ID",
    "GAIA_SB_EMBED_API_KEY",
    "GAIA_WHATSAPP_ACCESS_TOKEN",
    "GAIA_WHATSAPP_VERIFY_TOKEN",
)


def allowed_keys() -> tuple[str, ...]:
    """Allowlist = chaves fixas da Gaia + chaves de ambiente dos presets."""
    chaves: list[str] = list(_FIXED_KEYS)
    for preset in PRESETS.values():
        for nome in preset.env_keys:
            if nome not in chaves:
                chaves.append(nome)
    return tuple(sorted(chaves))


@dataclass(frozen=True)
class SecretEntry:
    key: str
    masked: str
    present: bool
    # linha do arquivo (1-based) para a remocao cirurgica
    line: int = 0
    commented: bool = False


_LINE = re.compile(r"^(?P<lead>\s*)(?P<comment>#\s*)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")


def mask(value: str) -> str:
    """Mostra o suficiente para reconhecer a chave, nunca o suficiente para usar.

    Valores curtos (tokens de 8-12 chars) revelam so os 4 ultimos; chaves longas
    mostram o prefixo do provedor (``sk-…1234``), que e o que ajuda a pessoa a
    saber qual credencial esta ali.
    """
    texto = (value or "").strip().strip('"').strip("'")
    if not texto:
        return ""
    if len(texto) <= 4:
        return "…"
    if len(texto) <= 10:
        return f"…{texto[-4:]}"
    return f"{texto[:3]}…{texto[-4:]}"


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def list_secrets(
    env_path: Path, env: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Chaves conhecidas com valor mascarado e origem.

    ``source`` e ``env`` quando a variavel ja existe no ambiente do processo
    (ai remover do arquivo nao muda nada ate reiniciar) e ``dotenv`` quando
    vem so do arquivo.
    """
    caminho = Path(env_path).expanduser()
    linhas = _read_lines(caminho)
    ambiente = dict(os.environ if env is None else env)
    permitidas = set(allowed_keys())
    itens: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for indice, linha in enumerate(linhas, start=1):
        achado = _LINE.match(linha)
        if achado is None:
            continue
        chave = achado.group("key")
        if chave not in permitidas or chave in vistos:
            continue
        vistos.add(chave)
        valor = achado.group("value")
        comentada = bool(achado.group("comment"))
        do_arquivo = "" if comentada else valor
        do_ambiente = ambiente.get(chave, "").strip()
        efetivo = do_ambiente or do_arquivo
        itens.append(
            {
                "key": chave,
                "masked": mask(efetivo),
                "present": bool(efetivo),
                "source": "env" if do_ambiente else ("dotenv" if do_arquivo else ""),
                "line": indice,
                "commented": comentada,
                "editable": True,
            }
        )

    for chave in sorted(permitidas - vistos):
        do_ambiente = ambiente.get(chave, "").strip()
        if not do_ambiente:
            continue
        itens.append(
            {
                "key": chave,
                "masked": mask(do_ambiente),
                "present": True,
                "source": "env",
                "line": 0,
                "commented": False,
                "editable": False,
            }
        )
    return itens


def _validar(chave: str, valor: str) -> str:
    nome = (chave or "").strip()
    if nome not in allowed_keys():
        raise ValueError(
            f"chave nao permitida pelo painel: {nome!r} "
            f"(use {', '.join(allowed_keys()[:4])}...)"
        )
    limpo = (valor or "").strip().strip('"').strip("'")
    if not limpo:
        raise ValueError("valor vazio: use remove para tirar a credencial")
    if "\n" in limpo or "\r" in limpo:
        raise ValueError("valor nao pode conter quebra de linha")
    return limpo


def _backup(caminho: Path) -> Path | None:
    if not caminho.is_file():
        return None
    destino = caminho.with_name(caminho.name + ".bak")
    shutil.copy2(caminho, destino)
    try:
        destino.chmod(0o600)
    except OSError:  # pragma: no cover - FS sem chmod
        pass
    return destino


def _write_secure(caminho: Path, linhas: list[str]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    texto = "\n".join(linhas).rstrip("\n") + "\n"
    caminho.write_text(texto, encoding="utf-8")
    try:
        caminho.chmod(0o600)
    except OSError:  # pragma: no cover - FS sem chmod
        pass


def _quote(valor: str) -> str:
    return f'"{valor}"'


def set_secret(env_path: Path, key: str, value: str) -> dict[str, Any]:
    """Grava/rotaciona uma credencial. Devolve o item mascarado."""
    caminho = Path(env_path).expanduser()
    limpo = _validar(key, value)
    nome = key.strip()
    _backup(caminho)
    linhas = _read_lines(caminho)
    nova = f"{nome}={_quote(limpo)}"

    atualizado = False
    for indice, linha in enumerate(linhas):
        achado = _LINE.match(linha)
        if achado is None or achado.group("key") != nome:
            continue
        linhas[indice] = nova
        atualizado = True
        break
    if not atualizado:
        if linhas and linhas[-1].strip():
            linhas.append("")
        linhas.append(nova)

    _write_secure(caminho, linhas)

    # Confere que o arquivo continua legivel e que a chave esta la.
    conferido = {}
    for linha in _read_lines(caminho):
        achado = _LINE.match(linha)
        if achado is not None and achado.group("key") == nome:
            conferido[nome] = achado.group("value")
    if conferido.get(nome, "").strip('"').strip("'") != limpo:
        raise ValueError("falha ao gravar a credencial: releitura divergiu")

    return {
        "key": nome,
        "masked": mask(limpo),
        "present": True,
        "source": "dotenv",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def remove_secret(env_path: Path, key: str) -> dict[str, Any]:
    """Comenta a linha do ``.env`` (nao apaga) para remover o override."""
    caminho = Path(env_path).expanduser()
    nome = (key or "").strip()
    if nome not in allowed_keys():
        raise ValueError(f"chave nao permitida pelo painel: {nome!r}")
    if not caminho.is_file():
        raise FileNotFoundError(f"{caminho} nao existe")
    _backup(caminho)
    linhas = _read_lines(caminho)
    alterado = False
    for indice, linha in enumerate(linhas):
        achado = _LINE.match(linha)
        if achado is None or achado.group("key") != nome or achado.group("comment"):
            continue
        linhas[indice] = f"# {linha.lstrip()}"
        alterado = True
    if alterado:
        _write_secure(caminho, linhas)
    return {
        "key": nome,
        "removed": alterado,
        "note": (
            "linha comentada no .env; reinicie o gaia serve para o processo "
            "esquecer o valor"
            if alterado
            else "chave nao estava no .env"
        ),
    }


def describe_secrets(env_path: Path) -> dict[str, Any]:
    """Payload da rota: itens, caminho e allowlist (nunca valores)."""
    caminho = Path(env_path).expanduser()
    itens = list_secrets(caminho)
    return {
        "path": str(caminho),
        "exists": caminho.is_file(),
        "writable": _writable(caminho),
        "allowed": list(allowed_keys()),
        "secrets": itens,
    }


def _writable(caminho: Path) -> bool:
    if caminho.is_file():
        return os.access(caminho, os.W_OK)
    pai = caminho.parent
    return pai.is_dir() and os.access(pai, os.W_OK)


__all__ = [
    "SecretEntry",
    "allowed_keys",
    "describe_secrets",
    "list_secrets",
    "mask",
    "remove_secret",
    "set_secret",
]
