"""Testes do deploy por git no mini (`deploy/gaia-deploy.sh`).

O script e shell puro e roda no mini; aqui ele roda num repo temporario
(bare remote em `tmp_path`, nada de rede, nada de SSH). O Python do "venv"
e um stub que importa `gaia` do proprio repo — e assim que o script valida
o codigo antes de reiniciar. O `systemctl` tambem e stub: ele so anota as
chamadas num arquivo, para provar quando o servico reinicia (ou nao).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="precisa de git e bash",
)

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "gaia-deploy.sh"


def _git(cwd: Path, *args: str) -> str:
    saida = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return saida.stdout.strip()


def _escrever(caminho: Path, conteudo: str, executavel: bool = False) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")
    if executavel:
        caminho.chmod(0o755)
    return caminho


@pytest.fixture
def mundo(tmp_path: Path) -> dict[str, Path]:
    """remote bare + clone do Mac (empurra) + clone do mini (recebe)."""
    remoto = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", remoto], check=True)

    mac = tmp_path / "mac"
    subprocess.run(["git", "clone", "-q", str(remoto), str(mac)], check=True)
    _git(mac, "checkout", "-q", "-b", "main")
    _git(mac, "config", "user.name", "Teste")
    _git(mac, "config", "user.email", "teste@exemplo.com")
    _escrever(mac / ".gitignore", ".venv/\ndata/\nconfig/\n.env\n")
    _escrever(mac / "gaia" / "__init__.py", "VERSAO = 1\n")
    _git(mac, "add", "-A")
    _git(mac, "commit", "-qm", "primeiro")
    _git(mac, "push", "-q", "origin", "main")

    mini = tmp_path / "mini"
    subprocess.run(["git", "clone", "-q", str(remoto), str(mini)], check=True)

    # stub do venv: "import gaia" a partir do repo (cwd e o repo).
    _escrever(
        mini / ".venv" / "bin" / "python",
        "#!/usr/bin/env bash\nexec python3 -c 'import gaia'\n",
        executavel=True,
    )
    servico = tmp_path / "systemctl.log"

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'printf "%s\\n" "$*" >> "$1"', "_", str(servico), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    return {"remoto": remoto, "mac": mac, "mini": mini, "servico": servico}


def _commit_no_mac(mac: Path, conteudo: str, mensagem: str = "mudanca") -> str:
    _escrever(mac / "gaia" / "__init__.py", conteudo)
    _git(mac, "add", "-A")
    _git(mac, "commit", "-qm", mensagem)
    _git(mac, "push", "-q", "origin", "main")
    return _git(mac, "rev-parse", "HEAD")


def _rodar(mundo: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    mini = mundo["mini"]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(mundo["mini"].parent),
        "GAIA_DEPLOY_REPO": str(mini),
        "GAIA_DEPLOY_REMOTE": "origin",
        "GAIA_DEPLOY_BRANCH": "main",
        # gaia-serve "ativo": o script deve reiniciar SO ele.
        "GAIA_DEPLOY_SYSTEMCTL": str(mundo["mini"].parent / "systemctl"),
    }
    sistema = mundo["mini"].parent / "systemctl"
    _escrever(
        sistema,
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2 $3" = "--user is-active --quiet" ]; then\n'
        '  [ "$4" = "gaia-serve.service" ] && exit 0\n'
        "  exit 3\n"
        "fi\n"
        'printf "%s\\n" "$*" >> "$GAIA_DEPLOY_LOG"\n'
        "exit 0\n",
        executavel=True,
    )
    env["GAIA_DEPLOY_LOG"] = str(mundo["servico"])
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=mini.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _reinicios(mundo: dict[str, Path]) -> list[str]:
    log = mundo["servico"]
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_sem_mudanca_sai_zero_e_nao_reinicia(mundo: dict[str, Path]) -> None:
    antes = _git(mundo["mini"], "rev-parse", "HEAD")
    r = _rodar(mundo)
    assert r.returncode == 0, r.stderr
    assert _git(mundo["mini"], "rev-parse", "HEAD") == antes
    assert _reinicios(mundo) == []


def test_dry_run_nao_muda_head(mundo: dict[str, Path]) -> None:
    antes = _git(mundo["mini"], "rev-parse", "HEAD")
    novo = _commit_no_mac(mundo["mac"], "VERSAO = 2\n")
    assert novo != antes

    r = _rodar(mundo, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert _git(mundo["mini"], "rev-parse", "HEAD") == antes
    assert _reinicios(mundo) == []


def test_commit_novo_atualiza_head_e_reinicia_so_o_serve(mundo: dict[str, Path]) -> None:
    novo = _commit_no_mac(mundo["mac"], "VERSAO = 2\n")

    r = _rodar(mundo)
    assert r.returncode == 0, r.stderr
    assert _git(mundo["mini"], "rev-parse", "HEAD") == novo
    assert _git(mundo["mini"], "rev-parse", "origin/main") == novo
    # um processo escritor so: reinicia o serve, nunca liga o telegram.
    assert _reinicios(mundo) == ["--user restart gaia-serve.service"]


def test_import_quebrado_volta_ao_sha_anterior_e_nao_reinicia(
    mundo: dict[str, Path],
) -> None:
    antes = _git(mundo["mini"], "rev-parse", "HEAD")
    _commit_no_mac(mundo["mac"], "isto nao e python(\n")

    r = _rodar(mundo)
    assert r.returncode != 0
    assert _git(mundo["mini"], "rev-parse", "HEAD") == antes
    assert _reinicios(mundo) == []


def test_arvore_suja_nao_impede_o_reset(mundo: dict[str, Path]) -> None:
    novo = _commit_no_mac(mundo["mac"], "VERSAO = 3\n")
    (mundo["mini"] / "gaia" / "__init__.py").write_text("lixo local = 1\n")
    _escrever(mundo["mini"] / "sobresalente.txt", "nao versionado\n")

    r = _rodar(mundo)
    assert r.returncode == 0, r.stderr
    assert _git(mundo["mini"], "rev-parse", "HEAD") == novo
    # git clean -fd (sem -x) some com o lixo, e nao toca no .venv ignorado.
    assert not (mundo["mini"] / "sobresalente.txt").exists()
    assert (mundo["mini"] / ".venv" / "bin" / "python").exists()
