"""Configuracao efetiva com origem e edicao cirurgica do TOML.

Duas funcoes, uma responsabilidade cada:

* ``effective`` diz, para cada campo, **qual valor vale** e **de onde ele veio**
  (``default`` | ``toml`` | ``env``). Sem isso o painel mentiria: a pessoa
  editaria e nada mudaria, porque o ``.env`` vence o TOML.
* ``update`` grava **uma** chave no TOML preservando comentarios, faz backup e
  confere relendo o arquivo inteiro. Se a releitura nao bater, restaura o
  backup e levanta ``ValueError`` — erro nunca persiste.

Por que TOML e nao uma tabela de overrides no SQLite: o repo ja sofreu com tres
fontes de verdade (config do `.zshrc`, `.env` e default). Uma fonte so, com a
origem visivel na tela, e mais simples de auditar.
"""

from __future__ import annotations

import ast
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gaia.config import _ENV_MAP, Config

# Campos que NUNCA sao editaveis pelo painel: ou sao segredo, ou sao caminho de
# maquina, ou mudariam o proprio servidor por baixo dos pés.
_SENSITIVE_FIELDS = {
    ("agent", "api_key"),
    ("secondbrain", "embed_api_key"),
    ("telegram", "bot_token"),
    ("whatsapp", "access_token"),
    ("whatsapp", "verify_token"),
    ("serve", "token"),
    ("serve", "host"),
    ("serve", "port"),
    ("serve", "control_token"),
}

# Seccoes que o painel mostra. Ordem = ordem na tela.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("agent", "Agente e modelos"),
    ("proactive", "Proatividade"),
    ("briefing", "Briefings do dia"),
    ("career", "Rotina de vagas"),
    ("memory", "Memoria e sonho"),
    ("agenda", "Agenda"),
    ("estudos", "Estudos e concursos"),
    ("telegram", "Telegram"),
    ("secondbrain", "Second Brain"),
    ("interface", "Interface"),
    ("focus", "Foco"),
    ("serve", "Servidor"),
    ("audiobook", "Audiobook"),
)


def _env_map_reverse() -> dict[tuple[str, str], list[str]]:
    invertido: dict[tuple[str, str], list[str]] = {}
    for chave, (secao, campo) in _ENV_MAP.items():
        invertido.setdefault((secao, campo), []).append(chave)
    return invertido


def _toml_raw(path: Path) -> dict[str, Any]:
    try:
        with Path(path).expanduser().open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _sub_model(cfg: Config, secao: str) -> BaseModel | None:
    valor = getattr(cfg, secao, None)
    return valor if isinstance(valor, BaseModel) else None


def effective(
    cfg: Config,
    env: dict[str, str] | None = None,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Lista de campos por secao, com valor efetivo e origem.

    ``env`` e o ambiente que **de fato** influenciou o Config (o ``.env``
    carregado), nao ``os.environ`` puro: uma variavel exportada depois da carga
    nao muda nada ate reiniciar.
    """
    import os

    fonte_env = dict(os.environ if env is None else env)
    caminho = Path(path or cfg.config_file or Config.default_config_path())
    bruto = _toml_raw(caminho)
    invertido = _env_map_reverse()
    resultado: list[dict[str, Any]] = []

    for secao, rotulo in SECTIONS:
        modelo = _sub_model(cfg, secao)
        if modelo is None:
            continue
        campos: list[dict[str, Any]] = []
        for nome in type(modelo).model_fields:
            if (secao, nome) in _SENSITIVE_FIELDS:
                continue
            valor = getattr(modelo, nome)
            chaves_env = invertido.get((secao, nome), [])
            env_key = ""
            for candidata in chaves_env:
                if fonte_env.get(candidata, "") not in {"", None}:
                    env_key = candidata
                    break
            origem = "default"
            if env_key:
                origem = "env"
            elif isinstance(bruto.get(secao), dict) and nome in bruto[secao]:
                origem = "toml"
            campos.append(
                {
                    "key": nome,
                    "value": _plain(valor),
                    "type": _tipo(valor, modelo, nome),
                    "source": origem,
                    "env_key": env_key,
                    "default": _plain(type(modelo).model_fields[nome].default),
                    "editable": origem != "env",
                    "options": _options(valor),
                }
            )
        resultado.append({"section": secao, "label": rotulo, "fields": campos})
    return resultado


def _plain(valor: Any) -> Any:
    """Valor serializavel para JSON (enum/dataclass viram primitivos)."""
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    if isinstance(valor, (list, tuple)):
        return [_plain(item) for item in valor]
    if isinstance(valor, dict):
        return {str(k): _plain(v) for k, v in valor.items()}
    return str(valor)


def _tipo(valor: Any, modelo: BaseModel, nome: str) -> str:
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, int):
        return "int"
    if isinstance(valor, float):
        return "float"
    if isinstance(valor, list):
        return "list"
    anotacao = str(type(modelo).model_fields[nome].annotation)
    if "str" in anotacao:
        # Campos de horario tem formato fixo e a UI usa <input type=time>.
        if nome == "at" or nome.endswith("_at") or nome.endswith("_until"):
            return "time"
        return "str"
    return "str"


def _options(valor: Any) -> list[Any]:
    """Valores aceitos quando o campo e um enum disfarcado de str."""
    if isinstance(valor, str) and valor in {"off", "leve", "medio", "alto"}:
        return ["off", "leve", "medio", "alto"]
    return []


def _format(value: Any, tipo: str) -> str:
    """Serializa no formato TOML, em uma linha."""
    if tipo == "bool":
        return "true" if bool(value) else "false"
    if tipo == "int":
        return str(int(value))
    if tipo == "float":
        numero = float(value)
        return str(int(numero)) + ".0" if numero.is_integer() else repr(numero)
    if tipo == "list":
        if not isinstance(value, list):
            raise ValueError(f"esperava lista, veio {type(value).__name__}")
        itens = []
        for item in value:
            if isinstance(item, bool):
                itens.append("true" if item else "false")
            elif isinstance(item, int):
                itens.append(str(item))
            elif isinstance(item, float):
                itens.append(repr(item))
            else:
                texto = str(item)
                if '"' in texto:
                    raise ValueError("item de lista com aspas nao suportado")
                itens.append(f'"{texto}"')
        return "[" + ", ".join(itens) + "]"
    texto = str(value)
    if "\n" in texto or "\r" in texto:
        raise ValueError("valor nao pode conter quebra de linha")
    return '"' + texto.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _section_bounds(lines: list[str], secao: str) -> tuple[int, int] | None:
    """Intervalo (inicio do corpo, fim do corpo) de ``[secao]``."""
    inicio: int | None = None
    for indice, linha in enumerate(lines):
        limpa = linha.strip()
        if limpa.startswith("[") and limpa.endswith("]"):
            if inicio is not None:
                return inicio, indice
            if limpa == f"[{secao}]":
                inicio = indice + 1
    if inicio is None:
        return None
    return inicio, len(lines)


_KEY_LINE = re.compile(
    r"^(?P<lead>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*(?P<comment>#.*)?$"
)


def _replace_key(
    lines: list[str], secao: str, chave: str, valor_toml: str
) -> bool:
    limites = _section_bounds(lines, secao)
    if limites is None:
        return False
    inicio, fim = limites
    for indice in range(inicio, fim):
        achado = _KEY_LINE.match(lines[indice])
        if achado is None or achado.group("key") != chave:
            continue
        comentario = achado.group("comment") or ""
        linha = f"{achado.group('lead')}{chave} = {valor_toml}"
        if comentario:
            linha += f"  {comentario}"
        lines[indice] = linha
        return True
    return False


def _create_section(lines: list[str], secao: str, chave: str, valor_toml: str) -> None:
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"[{secao}]")
    lines.append(f"{chave} = {valor_toml}")


def _validar_tipo(cfg: Config, secao: str, chave: str, valor: Any) -> Any:
    """Coage o valor pelo modelo pydantic da secao (valida faixa/tipo)."""
    modelo = _sub_model(cfg, secao)
    if modelo is None:
        raise ValueError(f"secao desconhecida: {secao}")
    campos = type(modelo).model_fields
    if chave not in campos:
        raise ValueError(f"campo desconhecido: {secao}.{chave}")
    parcial = {chave: valor}
    try:
        validado = type(modelo).model_validate({**modelo.model_dump(), **parcial})
    except ValidationError as exc:
        raise ValueError(_mensagem_validacao(exc)) from exc
    return getattr(validado, chave)


def _mensagem_validacao(exc: ValidationError) -> str:
    partes = []
    for erro in exc.errors():
        campo = ".".join(str(p) for p in erro.get("loc", ()))
        partes.append(f"{campo}: {erro.get('msg', '')}")
    return "; ".join(partes) or "valor invalido"


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_QUIET = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def _validar_semantica(section: str, key: str, valor: Any, tipo: str) -> None:
    """Regras que o modelo pydantic nao cobre (faixa, formato de horario).

    O pydantic valida tipo, nao intencao: ``daily_max = -5`` e um int valido e
    ``at = "25:99"`` e uma str valida. O painel recusa os dois onde eles
    significariam uma rotina impossivel.
    """
    if tipo == "time":
        if not isinstance(valor, str) or not _HHMM.match(valor):
            raise ValueError(f"{section}.{key} deve ser HH:MM (00:00 a 23:59)")
    if key == "quiet_hours":
        if not isinstance(valor, str) or not _QUIET.match(valor):
            raise ValueError("quiet_hours deve ser HH:MM-HH:MM")
    if tipo == "int" and key.endswith("_max"):
        if int(valor) < 1:
            raise ValueError(f"{section}.{key} deve ser >= 1")
    if tipo == "int" and key in {"max_steps", "max_tokens", "max_items"}:
        if int(valor) < 1:
            raise ValueError(f"{section}.{key} deve ser >= 1")
    if tipo in {"float", "int"} and key.endswith("_minutes"):
        if float(valor) < 0:
            raise ValueError(f"{section}.{key} nao pode ser negativo")
    if tipo in {"float", "int"} and key.endswith("_seconds"):
        if float(valor) < 0:
            raise ValueError(f"{section}.{key} nao pode ser negativo")
    if tipo == "float" and key == "temperature":
        if not 0.0 <= float(valor) <= 2.0:
            raise ValueError("temperature deve ficar entre 0.0 e 2.0")
    if tipo == "list" and key in {"days", "market_days", "study_days"}:
        if any(int(item) < 0 or int(item) > 6 for item in valor):
            raise ValueError(f"{section}.{key} usa 0=segunda ... 6=domingo")


def _coerce(value: Any, tipo: str) -> Any:
    if tipo == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if tipo == "int":
        return int(value)
    if tipo == "float":
        return float(value)
    if tipo == "list":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            texto = value.strip()
            if not texto:
                return []
            try:
                dados = ast.literal_eval(texto)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(f"lista invalida: {value!r}") from exc
            if not isinstance(dados, (list, tuple)):
                raise ValueError(f"lista invalida: {value!r}")
            return list(dados)
        raise ValueError("esperava lista")
    return str(value)


def update(
    path: Path,
    section: str,
    key: str,
    value: Any,
    *,
    cfg: Config | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Grava uma chave no TOML com validacao e rollback.

    Devolve ``{"section", "key", "value", "path", "backup"}``. Levanta
    ``ValueError`` quando o valor nao passa na validacao do modelo ou quando a
    releitura do arquivo nao confirma a gravacao (nesse caso o ``.bak`` ja foi
    restaurado).
    """
    import os

    caminho = Path(path).expanduser()
    if (section, key) in _SENSITIVE_FIELDS:
        raise ValueError(f"campo sensivel nao e editavel pelo painel: {section}.{key}")

    base = cfg or Config.load(config_path=caminho, env=dict(env or {}))
    modelo = _sub_model(base, section)
    if modelo is None:
        raise ValueError(f"secao desconhecida: {section}")
    if key not in type(modelo).model_fields:
        raise ValueError(f"campo desconhecido: {section}.{key}")

    fonte_env = dict(os.environ if env is None else env)
    for chave_env, (secao_env, campo_env) in _ENV_MAP.items():
        if (secao_env, campo_env) == (section, key) and fonte_env.get(chave_env):
            raise ValueError(
                f"{section}.{key} vem de {chave_env} no ambiente/.env; "
                "remova o override antes de editar o TOML"
            )

    tipo = _tipo(getattr(modelo, key), modelo, key)
    coerced = _coerce(value, tipo)
    _validar_semantica(section, key, coerced, tipo)
    validado = _validar_tipo(base, section, key, coerced)
    literal = _format(validado, tipo)

    caminho.parent.mkdir(parents=True, exist_ok=True)
    original = ""
    backup: Path | None = None
    if caminho.is_file():
        original = caminho.read_text(encoding="utf-8")
        backup = caminho.with_suffix(caminho.suffix + ".bak")
        shutil.copy2(caminho, backup)
        lines = original.splitlines()
    else:
        lines = []

    if not _replace_key(lines, section, key, literal):
        if _section_bounds(lines, section) is None:
            _create_section(lines, section, key, literal)
        else:
            limites = _section_bounds(lines, section)
            assert limites is not None
            inicio, fim = limites
            inserir = fim
            while inserir > inicio and not lines[inserir - 1].strip():
                inserir -= 1
            lines.insert(inserir, f"{key} = {literal}")

    atualizado = "\n".join(lines).rstrip("\n") + "\n"

    def _restaurar() -> None:
        if backup is not None:
            shutil.copy2(backup, caminho)
        elif not original:
            caminho.unlink(missing_ok=True)

    try:
        if atualizado != original:
            caminho.write_text(atualizado, encoding="utf-8")
        conferido = _toml_raw(caminho)
        if not isinstance(conferido.get(section), dict):
            raise ValueError(f"secao [{section}] nao encontrada apos a gravacao")
        if key not in conferido[section]:
            raise ValueError(f"{section}.{key} nao encontrado apos a gravacao")
        relido = type(modelo).model_validate(
            {**modelo.model_dump(), **conferido[section]}
        )
        if getattr(relido, key) != validado:
            raise ValueError(
                f"releitura divergiu: gravado {validado!r}, lido "
                f"{getattr(relido, key)!r}"
            )
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        _restaurar()
        raise ValueError(f"gravacao abortada: {exc}") from exc
    except OSError as exc:
        _restaurar()
        raise ValueError(f"nao foi possivel gravar {caminho}: {exc}") from exc

    return {
        "section": section,
        "key": key,
        "value": _plain(validado),
        "path": str(caminho),
        "backup": str(backup) if backup is not None else "",
        "source": "toml",
    }


def remove_env_override(
    env_path: Path, section: str, key: str
) -> dict[str, Any]:
    """Comenta no ``.env`` a variavel que travava o campo do TOML."""
    from gaia.control.secrets import remove_secret

    alvos = [
        chave
        for chave, (secao, campo) in _ENV_MAP.items()
        if (secao, campo) == (section, key)
    ]
    if not alvos:
        raise ValueError(f"{section}.{key} nao tem variavel de ambiente associada")
    removidos: list[str] = []
    for chave in alvos:
        try:
            resultado = remove_secret(env_path, chave)
        except FileNotFoundError:
            continue
        if resultado.get("removed"):
            removidos.append(chave)
    return {
        "section": section,
        "key": key,
        "removed": removidos,
        "note": (
            "override comentado no .env; reinicie o gaia serve para valer"
            if removidos
            else "nenhuma variavel correspondente encontrada no .env"
        ),
    }


__all__ = [
    "SECTIONS",
    "effective",
    "remove_env_override",
    "update",
]
