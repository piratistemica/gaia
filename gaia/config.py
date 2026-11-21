"""Configuracao da Gaia.

Precedencia (maior vence):
  1. variaveis de ambiente GAIA_*
  2. arquivo TOML (`~/.config/gaia/config.toml` ou GAIA_CONFIG)
  3. defaults deste modulo

Segredos nunca sao gravados no config: use `~/.config/gaia/.env` ou variaveis
de ambiente.
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaia.audiobook.config import AudiobookConfig

APP_NAME = "gaia"
_LEGACY_PREFIX = "ZAYA_"
_ENV_PREFIX = f"{APP_NAME.upper()}_"


class AgentConfig(BaseModel):
    """Motor proprio da Gaia: provedor, modelo e ferramentas."""

    model_config = ConfigDict(extra="ignore")

    # deepseek | openai | openrouter | ollama | anthropic | custom
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    # Vazio => usa o endpoint padrao do provider.
    base_url: str = ""
    # Segredo: prefira GAIA_AGENT_API_KEY no ambiente/.env.
    api_key: str = ""
    # Diretorio em que o agente trabalha (vazio = diretorio atual).
    directory: str = ""
    max_tokens: int = 4096
    temperature: float | None = 0.3
    max_steps: int = 32
    resume_session: bool = True
    request_timeout: float = 900.0
    # Sem nenhum evento nesse tempo (e ainda em execucao) => considera travado.
    stall_timeout: float = 90.0
    auto_approve: bool = False
    # Ferramentas de leitura e escrita no computador.
    tools: bool = True
    allow_outside_project: bool = True
    # Modelo pequeno para pedidos curtos (vazio = sempre o modelo principal).
    fast_model: str = ""
    # Marca o prefixo estavel do prompt para cache (Anthropic).
    cache_prompt: bool = True
    # Depois desse numero de mensagens, o historico antigo vira um resumo.
    max_history_messages: int = 20
    # Teto de caracteres do historico que vai ao modelo (0 = sem teto).
    max_history_chars: int = 24000
    summary_chars: int = 1200
    # Medir tokens por turno (usa stream_options em APIs compativeis).
    usage: bool = True
    # Teto de caracteres de um resultado de ferramenta guardado no historico.
    # 0 = sem teto. O modelo ve o resultado inteiro no turno atual.
    tool_result_chars: int = 4000
    # Como resumir o historico antigo: local (extrativo) ou llm (semantico).
    # O modo llm cai automaticamente no local se a chamada falhar.
    history_summary: str = "local"
    # Modelo do resumo (vazio = modelo principal).
    history_summary_model: str = ""

    def model_slug(self) -> str:
        return f"{self.provider}/{self.model}"

    def apply_model(self, provider: str, model: str) -> None:
        """Aponta a configuracao para outro modelo (vale no proximo turno)."""
        self.provider = provider.strip()
        self.model = model.strip()


class STTConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "small"
    language: str = "pt"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 1
    vad_filter: bool = True
    # Se false, a transcricao e enviada direto, sem pedir correcao.
    confirm_transcript: bool = False
    # Legendas parciais enquanto a pessoa fala (transcricao final continua completa).
    partial_transcription: bool = True
    partial_interval_seconds: float = 1.5
    partial_window_seconds: float = 10.0


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    provider: str = "edge"  # edge | say | none
    edge_voice: str = "pt-BR-FranciscaNeural"
    say_voice: str = "Luciana"
    rate: str = "+0%"
    max_chars: int = 1200
    # Pausa extra entre frases (ms). 0 = sem pausa adicional.
    sentence_pause_ms: int = 120


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sample_rate: int = 16000
    channels: int = 1
    # Silencio necessario para encerrar uma fala (5s = pausa confortavel).
    silence_ms: int = 5000
    min_speech_ms: int = 250
    preroll_ms: int = 300
    # 0 = sem limite de duracao para uma fala.
    max_seconds: float = 0.0
    # Probabilidade do Silero VAD acima da qual o bloco conta como fala.
    # So vale quando o onnxruntime esta disponivel (o normal).
    vad_threshold: float = 0.5
    # Reserva sem Silero: limiar de RMS em dB.
    speech_threshold_db: float = -42.0
    input_device: str = ""
    # Escuta continua: microfone aberto, fala -> executa -> responde -> volta a ouvir.
    continuous: bool = True
    # Depois desse tempo sem fala, desliga o microfone (0 = nunca).
    auto_mute_seconds: float = 30.0


class SecondBrainConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = "~/SecondBrain"
    index_db: str = ""
    auto_context: bool = True
    # 2 a 3 trechos curtos bastam: mais que isso infla o prompt sem ganho.
    context_results: int = 2
    # Limite de caracteres por nota injetada no contexto.
    context_chars: int = 480
    # Teto total do bloco de contexto recuperado.
    context_max_chars: int = 1200
    # Criar nota e aditivo: por padrao nao pergunta (mais rapido).
    confirm_create: bool = False
    # Editar/excluir sao destrutivos: por padrao continuam exigindo confirmacao.
    require_confirmation: bool = True
    # Reindexar a cada inicio pode custar caro em bases grandes.
    reindex_on_start: bool = False
    backup_before_write: bool = True
    default_folder: str = "Inbox"
    # Embeddings: vazio = desligado, a busca segue lexical (padrao).
    # Ex.: "text-embedding-3-small" (OpenAI) ou "nomic-embed-text" (Ollama).
    embed_model: str = ""
    embed_base_url: str = "https://api.openai.com/v1"
    # A chave vai no .env como GAIA_SB_EMBED_API_KEY, nunca no TOML.
    embed_api_key: str = ""
    # Cosseno minimo para considerar que "ressoa". Depende do modelo: medido
    # com paraphrase-multilingual no mini, relacionado ~0.40-0.60 e sem
    # relacao ~0.08-0.37. Ajuste se trocar de modelo de embedding.
    embed_min_score: float = 0.35
    # CPU (mini PC) demora: lote pequeno e timeout folgado evitam timeouts.
    embed_batch_size: int = 8
    embed_timeout: float = 120.0
    # Vazio => <secondbrain.path>/preferences.md
    preferences_file: str = ""
    exclude: list[str] = Field(
        default_factory=lambda: [".obsidian", ".git", ".trash", ".gaia", "node_modules"]
    )


class InterfaceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    show_banner: bool = True
    speak_tool_activity: bool = False
    steering: bool = True
    # Pede respostas diretas ao agente, reduzindo exploracao desnecessaria.
    concise: bool = True
    # Nivel de humor da persona: off | leve | medio | alto.
    humor: str = "medio"
    # Mostrar raciocinio e diffs das ferramentas no terminal.
    show_reasoning: bool = True
    show_diffs: bool = True
    # Mostrar o painel de metas ao iniciar.
    show_metas_on_start: bool = True
    # Painéis de metas e lua começam recolhidos (F5 continua ocultando).
    dashboard_collapsed: bool = True
    # Interface: terminal (padrão) ou textual.
    ui: str = "textual"
    # Selecao de texto ligada ao abrir: arrastar destaca e copia (F4 alterna).
    selection_mode: bool = False
    # Modo rápido: sem busca automática e respostas mais curtas.
    fast: bool = False
    # Saudações/small talk respondem na hora, sem chamar o modelo.
    fast_smalltalk: bool = True
    # Eco em chinês (caracteres + pinyin) para cada mensagem.
    chinese_echo: bool = False
    # Respostas renderizadas como Markdown (títulos, ênfase, código).
    markdown_render: bool = True
    # Modo inicial: pesquisadora | trabalhadora | investidora | assistente.
    default_mode: str = "assistente"


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Consolida os turnos do dia numa nota de memoria no Second Brain.
    enabled: bool = True
    # Horario local (HH:MM) em que o dia e consolidado enquanto a Gaia roda.
    at: str = "23:00"
    # Ao encerrar depois desse horario, consolida o que ainda estiver pendente.
    on_exit: bool = True
    # Resumo pelo modelo; se falhar, cai no resumo local deterministico.
    use_llm: bool = True
    folder: str = "Memoria"
    # Sonho noturno: quando ociosa de madrugada, cruza a memoria do dia com
    # as memorias antigas e escreve uma nota de sonho no Second Brain.
    dream_enabled: bool = True
    dream_at: str = "02:00"
    # Fim da janela do sonho (a janela pode virar a meia-noite).
    dream_until: str = "08:00"
    # Os sonhos ficam em pasta propria, separada do diario consolidado.
    dream_folder: str = "Sonhos"
    # Notas de andaime/exemplo que nunca devem entrar no sonho: elas falam de
    # tudo e de nada, e a busca por sentido as traz para o topo sempre.
    dream_ignore: list[str] = Field(
        default_factory=lambda: ["README.md", "Inbox/Boas-vindas.md"]
    )
    # Aviso no dia seguinte: uma linha sobre o sonho, sem segunda chamada ao
    # modelo. Vai pelo canal em uso (TUI ou Telegram), fora das quiet hours.
    dream_notify: bool = True
    dream_notify_at: str = "08:00"
    # Espaco entre tentativas quando o aviso do sonho nao consegue sair.
    retry_minutes: float = 10.0
    max_turns: int = 120
    max_chars: int = 16000
    llm_timeout_seconds: float = 90.0


class BriefingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Bom dia com tarefas/habitos e resumo do meio-dia, sem usar o modelo.
    enabled: bool = True
    morning_at: str = "08:00"
    noon_at: str = "12:00"
    # Depois desse horario o resumo do meio-dia nao e mais enviado.
    noon_until: str = "18:00"
    # Trecho do nome da rotina da Agenda (sem acento, case-insensitive).
    morning_routine: str = "matinal"
    max_tasks: int = 15
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Comentario pessoal do modelo no briefing (uma frase feita pra ela hoje).
    personalize: bool = False
    # Quais briefings ganham o comentario: morning | noon.
    personalize_slots: list[str] = Field(default_factory=lambda: ["morning"])
    personalize_timeout_seconds: float = 20.0
    # Fecho da noite no Telegram: o que foi feito, o que nao foi e um insight.
    evening_enabled: bool = True
    evening_at: str = "18:00"
    # Depois desse horario nao envia mais (quiet hours comecam as 23:00).
    evening_until: str = "23:00"
    # Insight curto gerado pelo modelo junto com a lista (false = so a lista).
    evening_insight: bool = True
    evening_timeout_seconds: float = 60.0
    # Resumo da carteira (watchlist) nos dias escolhidos, sem usar o modelo.
    market_enabled: bool = True
    # Dias da semana com 0 = segunda (seg, qua, sex por padrao).
    market_days: list[int] = Field(default_factory=lambda: [0, 2, 4])
    market_at: str = "12:00"
    # Depois desse horario o resumo da carteira nao e mais enviado.
    market_until: str = "18:00"


class CareerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
    # entrega no Telegram. O metodo vive em ~/SecondBrain/cv/playbook-vagas.md.
    enabled: bool = True
    # Dias da semana com 0 = segunda (segunda por padrao).
    days: list[int] = Field(default_factory=lambda: [0])
    at: str = "09:00"
    # Depois desse horario a rotina nao e mais enviada.
    until: str = "18:00"
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Vagas por mensagem; o resto fica registrado no estado.
    max_items: int = 8
    # Vazio => <data_dir>/career/state.json
    state_file: str = ""


class ProactiveConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    startup_greeting: bool = True
    # Saudacao inicial instantanea (sem LLM). Ligue para personalizar via modelo.
    greeting_via_llm: bool = False
    idle_checkin: bool = True
    idle_minutes: float = 45.0
    min_interval_minutes: float = 60.0
    daily_max: int = 4
    quiet_hours: str = "23:00-08:00"
    check_interval_seconds: float = 30.0
    reminders: bool = True


class WhatsAppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    phone_number_id: str = ""
    # Segredos: apenas via ambiente/.env, nunca no TOML.
    access_token: str = ""
    verify_token: str = ""
    recipient: str = ""
    api_version: str = "v21.0"
    graph_base_url: str = "https://graph.facebook.com"
    # Porta local do webhook (recebe mensagens da Meta).
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8765


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    path: str = "~/.config/gaia/focus.json"
    # Perguntar sobre o foco nas fases principais da lua.
    prompt_on_moon: bool = True


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Perfil persistente: guarda cookies/logins entre execucoes.
    profile_dir: str = ""
    headless: bool = True
    timeout_ms: int = 30000


class AgendaConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # .env do projeto Agenda (Supabase). Nada e copiado para o repo da Gaia.
    env_file: str = "~/agenda-git/agenda/.env"
    poll_minutes: float = 30.0
    max_items: int = 5
    # Avisar proativamente sobre tarefas pendentes do dia.
    notify_pending: bool = False
    # Checagem automatica da pagina "tarefas pra gaia" (0 = desligada).
    tasks_poll_seconds: float = 0.0


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Segredo: apenas via ambiente/.env.
    bot_token: str = ""
    # Chat autorizado (você). Vazio => o primeiro chat que falar com o bot.
    allowed_chat_id: str = ""
    api_base_url: str = "https://api.telegram.org"
    poll_timeout: int = 25
    # Responder também com áudio (gera mp3 local e envia como áudio).
    voice_replies: bool = False
    # O Telegram não tem terminal interativo para aprovar ferramentas. Com
    # isto ligado, as ferramentas que pedem confirmação são aprovadas
    # automaticamente (exige allowed_chat_id fixado). Desligado, toda
    # ferramenta que pede confirmação é negada.
    auto_approve: bool = False
    # Shell e escrita de arquivo pelo chat remoto: opt-in explícito, e só vale
    # junto de auto_approve + allowed_chat_id. Desligado (padrão) o bot acessa
    # a Agenda pelas ferramentas agenda_* e não consegue rodar comando na
    # máquina.
    shell_access: bool = False
    # Mostra o raciocínio do modelo no chat (uma mensagem editada ao vivo).
    # Alterna no próprio chat com /pensando on|off.
    show_reasoning: bool = False


class ServeConfig(BaseModel):
    """`gaia serve`: uma inteligencia, varias telas (ver docs/plans/2026-11-19).

    O servidor e o dono unico do estado: banco, Second Brain e proatividade. Os
    clientes (`gaia --remote`, web) so desenham.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Loopback por padrao: nada escuta na rede sem pedido explicito.
    host: str = "127.0.0.1"
    port: int = 8787
    # Segredo: apenas via ambiente/.env (GAIA_SERVE_TOKEN).
    token: str = ""
    # Bind fora do loopback (ex.: IP do tailnet) exige token. Ver allow_remote.
    allow_remote: bool = False
    # Sobe o poller do Telegram junto do servidor (um processo so).
    telegram: bool = True

    def remote_bind_without_token(self) -> bool:
        """True = escutaria na rede sem segredo; o servidor deve recusar."""
        if self.host in {"", "127.0.0.1", "localhost", "::1"}:
            return False
        return not self.allow_remote or not self.token.strip()


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent: AgentConfig = Field(default_factory=AgentConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    secondbrain: SecondBrainConfig = Field(default_factory=SecondBrainConfig)
    interface: InterfaceConfig = Field(default_factory=InterfaceConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    career: CareerConfig = Field(default_factory=CareerConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    agenda: AgendaConfig = Field(default_factory=AgendaConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    serve: ServeConfig = Field(default_factory=ServeConfig)
    audiobook: AudiobookConfig = Field(default_factory=AudiobookConfig)
    data_dir: str = ""

    # Caminho de onde o config foi carregado (informativo).
    config_file: str = ""

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _env_path(suffix: str) -> str:
        for key in (f"{_ENV_PREFIX}{suffix}", f"{_LEGACY_PREFIX}{suffix}"):
            value = os.environ.get(key)
            if value:
                return value
        return ""

    @staticmethod
    def default_config_path() -> Path:
        env = Config._env_path("CONFIG")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / "config.toml"

    @staticmethod
    def default_dotenv_path() -> Path:
        env = Config._env_path("DOTENV")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / ".env"

    @staticmethod
    def default_data_dir() -> Path:
        env = Config._env_path("DATA_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".local" / "share" / APP_NAME

    def resolved_data_dir(self) -> Path:
        base = Path(self.data_dir).expanduser() if self.data_dir else self.default_data_dir()
        return base.resolve()

    def db_path(self) -> Path:
        path = self.resolved_data_dir() / f"{APP_NAME}.db"
        legacy = path.with_name("zaya.db")
        if not path.exists() and legacy.exists():
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(legacy) + suffix)
                if source.exists():
                    source.replace(Path(str(path) + suffix))
        return path

    def index_path(self) -> Path:
        if self.secondbrain.index_db:
            return Path(self.secondbrain.index_db).expanduser().resolve()
        return self.resolved_data_dir() / "sb_index.db"

    def log_dir(self) -> Path:
        return self.resolved_data_dir() / "logs"

    def browser_profile_dir(self) -> Path:
        if self.browser.profile_dir:
            return Path(self.browser.profile_dir).expanduser().resolve()
        return self.resolved_data_dir() / "browser"

    def second_brain_path(self) -> Path:
        return Path(self.secondbrain.path).expanduser().resolve()

    def preferences_path(self) -> Path:
        if self.secondbrain.preferences_file:
            return Path(self.secondbrain.preferences_file).expanduser().resolve()
        return self.second_brain_path() / "preferences.md"

    def project_directory(self) -> Path:
        if self.agent.directory:
            return Path(self.agent.directory).expanduser().resolve()
        return Path.cwd().resolve()

    def audiobook_inbox(self) -> Path:
        return self.audiobook.inbox_path(self.resolved_data_dir())

    def audiobook_outbox(self) -> Path:
        return self.audiobook.outbox_path(self.resolved_data_dir())

    def ensure_dirs(self) -> None:
        dirs = [self.resolved_data_dir(), self.log_dir()]
        if self.audiobook.enabled:
            # A pasta de entrada precisa existir para a pessoa simplesmente
            # arrastar PDFs para ela.
            dirs += [self.audiobook_inbox(), self.audiobook_outbox()]
        for path in dirs:
            path.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("agent", {}).get("api_key"):
            data["agent"]["api_key"] = "***"
        for field in ("access_token", "verify_token"):
            if data.get("whatsapp", {}).get(field):
                data["whatsapp"][field] = "***"
        if data.get("telegram", {}).get("bot_token"):
            data["telegram"]["bot_token"] = "***"
        if data.get("secondbrain", {}).get("embed_api_key"):
            data["secondbrain"]["embed_api_key"] = "***"
        if data.get("serve", {}).get("token"):
            data["serve"]["token"] = "***"
        return data

    # ------------------------------------------------------------------ load
    @classmethod
    def load(
        cls, config_path: str | Path | None = None, env: dict[str, str] | None = None
    ) -> Config:
        real_env = env is None
        if real_env:
            _migrate_legacy_locations()
        env = dict(os.environ if env is None else env)
        _load_dotenv(cls.default_dotenv_path(), env)
        _alias_legacy_env(env)
        if real_env:
            # Deixa o que veio do .env visivel para subprocessos (ferramenta
            # de shell, CLIs filhas) sem sobrescrever o ambiente existente.
            _export_env(env)

        path = Path(config_path).expanduser() if config_path else cls.default_config_path()
        raw: dict[str, Any] = {}
        if path.is_file():
            with path.open("rb") as fh:
                raw = tomllib.load(fh)

        _migrate_legacy_config(raw)
        _apply_env(raw, env)

        cfg = cls.model_validate(raw)
        cfg.config_file = str(path)
        cfg.ensure_dirs()
        return cfg


def _migrate_legacy_locations() -> None:
    """Copia config e dados do nome antigo (``zaya``) na primeira execucao."""
    home = Path.home()
    for old, new in (
        (home / ".config" / "zaya", home / ".config" / APP_NAME),
        (home / ".local" / "share" / "zaya", home / ".local" / "share" / APP_NAME),
    ):
        if old.is_dir() and not new.exists():
            try:
                shutil.copytree(old, new)
            except OSError:
                continue
    config_dir = home / ".config" / APP_NAME
    for name in ("config.toml", ".env"):
        target = config_dir / name
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            continue
        renamed = text.replace(_LEGACY_PREFIX, _ENV_PREFIX)
        if renamed != text:
            target.write_text(renamed, encoding="utf-8")


def _alias_legacy_env(env: dict[str, str]) -> None:
    """Espelha ``ZAYA_*`` para ``GAIA_*`` quando o nome novo nao existe."""
    for key, value in list(env.items()):
        if key.startswith(_LEGACY_PREFIX):
            env.setdefault(_ENV_PREFIX + key[len(_LEGACY_PREFIX) :], value)


def _export_env(env: dict[str, str]) -> None:
    """Espelha o .env no ambiente do processo, sem sobrescrever o que ja existe."""
    for key, value in env.items():
        os.environ.setdefault(key, value)


def _load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Carrega KEY=VALUE simples sem sobrescrever o ambiente existente."""
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    except OSError:
        return


_ENV_MAP: dict[str, tuple[str, str]] = {
    "GAIA_AGENDA_ENABLED": ("agenda", "enabled"),
    "GAIA_AGENDA_ENV_FILE": ("agenda", "env_file"),
    "GAIA_AGENDA_MAX_ITEMS": ("agenda", "max_items"),
    "GAIA_AGENDA_NOTIFY_PENDING": ("agenda", "notify_pending"),
    "GAIA_AGENDA_POLL_MINUTES": ("agenda", "poll_minutes"),
    "GAIA_AGENDA_TASKS_POLL_SECONDS": ("agenda", "tasks_poll_seconds"),
    "GAIA_AUDIO_AUTO_MUTE_SECONDS": ("audio", "auto_mute_seconds"),
    "GAIA_AUDIO_CONTINUOUS": ("audio", "continuous"),
    "GAIA_AUDIO_INPUT_DEVICE": ("audio", "input_device"),
    "GAIA_AUDIO_MAX_SECONDS": ("audio", "max_seconds"),
    "GAIA_AUDIO_SILENCE_MS": ("audio", "silence_ms"),
    "GAIA_AUDIO_VAD_THRESHOLD": ("audio", "vad_threshold"),
    "GAIA_BROWSER_ENABLED": ("browser", "enabled"),
    "GAIA_BROWSER_HEADLESS": ("browser", "headless"),
    "GAIA_BROWSER_PROFILE_DIR": ("browser", "profile_dir"),
    "GAIA_BROWSER_TIMEOUT_MS": ("browser", "timeout_ms"),
    "GAIA_FOCUS_ENABLED": ("focus", "enabled"),
    "GAIA_FOCUS_PATH": ("focus", "path"),
    "GAIA_FOCUS_PROMPT_ON_MOON": ("focus", "prompt_on_moon"),
    "GAIA_CHINESE": ("interface", "chinese_echo"),
    "GAIA_FAST": ("interface", "fast"),
    "GAIA_FAST_SMALLTALK": ("interface", "fast_smalltalk"),
    "GAIA_UI_DASHBOARD_COLLAPSED": ("interface", "dashboard_collapsed"),
    "GAIA_UI_SHOW_DIFFS": ("interface", "show_diffs"),
    "GAIA_UI_SHOW_METAS_ON_START": ("interface", "show_metas_on_start"),
    "GAIA_UI_SHOW_REASONING": ("interface", "show_reasoning"),
    "GAIA_TELEGRAM_SHOW_REASONING": ("telegram", "show_reasoning"),
    "GAIA_SERVE_ENABLED": ("serve", "enabled"),
    "GAIA_SERVE_HOST": ("serve", "host"),
    "GAIA_SERVE_PORT": ("serve", "port"),
    "GAIA_SERVE_TOKEN": ("serve", "token"),
    "GAIA_SERVE_ALLOW_REMOTE": ("serve", "allow_remote"),
    "GAIA_SERVE_TELEGRAM": ("serve", "telegram"),
    "GAIA_UI_SELECTION_MODE": ("interface", "selection_mode"),
    "GAIA_UI_MARKDOWN": ("interface", "markdown_render"),
    "GAIA_UI": ("interface", "ui"),
    "GAIA_AGENT_ALLOW_OUTSIDE_PROJECT": ("agent", "allow_outside_project"),
    "GAIA_AGENT_API_KEY": ("agent", "api_key"),
    "GAIA_AGENT_AUTO_APPROVE": ("agent", "auto_approve"),
    "GAIA_AGENT_BASE_URL": ("agent", "base_url"),
    "GAIA_AGENT_CACHE_PROMPT": ("agent", "cache_prompt"),
    "GAIA_AGENT_DIRECTORY": ("agent", "directory"),
    "GAIA_AGENT_FAST_MODEL": ("agent", "fast_model"),
    "GAIA_AGENT_HISTORY_SUMMARY": ("agent", "history_summary"),
    "GAIA_AGENT_HISTORY_SUMMARY_MODEL": ("agent", "history_summary_model"),
    "GAIA_AGENT_MAX_HISTORY_CHARS": ("agent", "max_history_chars"),
    "GAIA_AGENT_MAX_HISTORY_MESSAGES": ("agent", "max_history_messages"),
    "GAIA_AGENT_MAX_STEPS": ("agent", "max_steps"),
    "GAIA_AGENT_MAX_TOKENS": ("agent", "max_tokens"),
    "GAIA_AGENT_MODEL": ("agent", "model"),
    "GAIA_AGENT_PROVIDER": ("agent", "provider"),
    "GAIA_AGENT_REQUEST_TIMEOUT": ("agent", "request_timeout"),
    "GAIA_AGENT_RESUME_SESSION": ("agent", "resume_session"),
    "GAIA_AGENT_STALL_TIMEOUT": ("agent", "stall_timeout"),
    "GAIA_AGENT_SUMMARY_CHARS": ("agent", "summary_chars"),
    "GAIA_AGENT_TEMPERATURE": ("agent", "temperature"),
    "GAIA_AGENT_TOOLS": ("agent", "tools"),
    "GAIA_AGENT_TOOL_RESULT_CHARS": ("agent", "tool_result_chars"),
    "GAIA_AGENT_USAGE": ("agent", "usage"),
    "GAIA_MEMORY_AT": ("memory", "at"),
    "GAIA_MEMORY_DREAM_AT": ("memory", "dream_at"),
    "GAIA_MEMORY_DREAM_ENABLED": ("memory", "dream_enabled"),
    "GAIA_MEMORY_DREAM_FOLDER": ("memory", "dream_folder"),
    "GAIA_MEMORY_DREAM_NOTIFY": ("memory", "dream_notify"),
    "GAIA_MEMORY_DREAM_NOTIFY_AT": ("memory", "dream_notify_at"),
    "GAIA_MEMORY_DREAM_UNTIL": ("memory", "dream_until"),
    "GAIA_MEMORY_ENABLED": ("memory", "enabled"),
    "GAIA_MEMORY_FOLDER": ("memory", "folder"),
    "GAIA_MEMORY_ON_EXIT": ("memory", "on_exit"),
    "GAIA_MEMORY_RETRY_MINUTES": ("memory", "retry_minutes"),
    "GAIA_MEMORY_USE_LLM": ("memory", "use_llm"),
    "GAIA_BRIEFING_ENABLED": ("briefing", "enabled"),
    "GAIA_BRIEFING_MORNING_AT": ("briefing", "morning_at"),
    "GAIA_BRIEFING_NOON_AT": ("briefing", "noon_at"),
    "GAIA_BRIEFING_NOON_UNTIL": ("briefing", "noon_until"),
    "GAIA_BRIEFING_MORNING_ROUTINE": ("briefing", "morning_routine"),
    "GAIA_BRIEFING_MAX_TASKS": ("briefing", "max_tasks"),
    "GAIA_BRIEFING_EVENING_AT": ("briefing", "evening_at"),
    "GAIA_BRIEFING_EVENING_ENABLED": ("briefing", "evening_enabled"),
    "GAIA_BRIEFING_EVENING_INSIGHT": ("briefing", "evening_insight"),
    "GAIA_BRIEFING_EVENING_UNTIL": ("briefing", "evening_until"),
    "GAIA_BRIEFING_MARKET_ENABLED": ("briefing", "market_enabled"),
    "GAIA_BRIEFING_MARKET_AT": ("briefing", "market_at"),
    "GAIA_BRIEFING_MARKET_UNTIL": ("briefing", "market_until"),
    "GAIA_CAREER_ENABLED": ("career", "enabled"),
    "GAIA_CAREER_AT": ("career", "at"),
    "GAIA_CAREER_UNTIL": ("career", "until"),
    "GAIA_CAREER_MAX_ITEMS": ("career", "max_items"),
    "GAIA_CAREER_STATE_FILE": ("career", "state_file"),
    "GAIA_PROACTIVE_CHECK_INTERVAL_SECONDS": ("proactive", "check_interval_seconds"),
    "GAIA_PROACTIVE_DAILY_MAX": ("proactive", "daily_max"),
    "GAIA_PROACTIVE_ENABLED": ("proactive", "enabled"),
    "GAIA_PROACTIVE_GREETING_VIA_LLM": ("proactive", "greeting_via_llm"),
    "GAIA_PROACTIVE_IDLE_CHECKIN": ("proactive", "idle_checkin"),
    "GAIA_PROACTIVE_IDLE_MINUTES": ("proactive", "idle_minutes"),
    "GAIA_PROACTIVE_MIN_INTERVAL_MINUTES": ("proactive", "min_interval_minutes"),
    "GAIA_PROACTIVE_QUIET_HOURS": ("proactive", "quiet_hours"),
    "GAIA_PROACTIVE_REMINDERS": ("proactive", "reminders"),
    "GAIA_PROACTIVE_STARTUP_GREETING": ("proactive", "startup_greeting"),
    "GAIA_SB_AUTO_CONTEXT": ("secondbrain", "auto_context"),
    "GAIA_SB_CONFIRM_CREATE": ("secondbrain", "confirm_create"),
    "GAIA_SB_CONTEXT_CHARS": ("secondbrain", "context_chars"),
    "GAIA_SB_CONTEXT_MAX_CHARS": ("secondbrain", "context_max_chars"),
    "GAIA_SB_CONTEXT_RESULTS": ("secondbrain", "context_results"),
    "GAIA_SECOND_BRAIN_INDEX": ("secondbrain", "index_db"),
    "GAIA_SECOND_BRAIN_PATH": ("secondbrain", "path"),
    "GAIA_SB_EMBED_API_KEY": ("secondbrain", "embed_api_key"),
    "GAIA_SB_EMBED_BASE_URL": ("secondbrain", "embed_base_url"),
    "GAIA_SB_EMBED_BATCH_SIZE": ("secondbrain", "embed_batch_size"),
    "GAIA_SB_EMBED_MIN_SCORE": ("secondbrain", "embed_min_score"),
    "GAIA_SB_EMBED_TIMEOUT": ("secondbrain", "embed_timeout"),
    "GAIA_SB_EMBED_MODEL": ("secondbrain", "embed_model"),
    "GAIA_SB_PREFERENCES_FILE": ("secondbrain", "preferences_file"),
    "GAIA_SB_REINDEX_ON_START": ("secondbrain", "reindex_on_start"),
    "GAIA_SB_REQUIRE_CONFIRMATION": ("secondbrain", "require_confirmation"),
    "GAIA_STT_CONFIRM_TRANSCRIPT": ("stt", "confirm_transcript"),
    "GAIA_STT_LANGUAGE": ("stt", "language"),
    "GAIA_STT_MODEL": ("stt", "model"),
    "GAIA_STT_PARTIAL_INTERVAL_SECONDS": ("stt", "partial_interval_seconds"),
    "GAIA_STT_PARTIAL_TRANSCRIPTION": ("stt", "partial_transcription"),
    "GAIA_STT_PARTIAL_WINDOW_SECONDS": ("stt", "partial_window_seconds"),
    "GAIA_TELEGRAM_ALLOWED_CHAT_ID": ("telegram", "allowed_chat_id"),
    "GAIA_TELEGRAM_API_BASE_URL": ("telegram", "api_base_url"),
    "GAIA_TELEGRAM_AUTO_APPROVE": ("telegram", "auto_approve"),
    "GAIA_TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "GAIA_TELEGRAM_ENABLED": ("telegram", "enabled"),
    "GAIA_TELEGRAM_POLL_TIMEOUT": ("telegram", "poll_timeout"),
    "GAIA_TELEGRAM_SHELL_ACCESS": ("telegram", "shell_access"),
    "GAIA_TELEGRAM_VOICE_REPLIES": ("telegram", "voice_replies"),
    "GAIA_AUDIOBOOK_ENABLED": ("audiobook", "enabled"),
    "GAIA_AUDIOBOOK_INBOX": ("audiobook", "inbox"),
    "GAIA_AUDIOBOOK_VOICE": ("audiobook", "voice"),
    "GAIA_AUDIOBOOK_WATCH_MINUTES": ("audiobook", "watch_minutes"),
    "GAIA_TTS_VOICE": ("tts", "edge_voice"),
    "GAIA_TTS_ENABLED": ("tts", "enabled"),
    "GAIA_TTS_PROVIDER": ("tts", "provider"),
    "GAIA_TTS_SENTENCE_PAUSE_MS": ("tts", "sentence_pause_ms"),
    "GAIA_WHATSAPP_ACCESS_TOKEN": ("whatsapp", "access_token"),
    "GAIA_WHATSAPP_API_VERSION": ("whatsapp", "api_version"),
    "GAIA_WHATSAPP_ENABLED": ("whatsapp", "enabled"),
    "GAIA_WHATSAPP_PHONE_NUMBER_ID": ("whatsapp", "phone_number_id"),
    "GAIA_WHATSAPP_RECIPIENT": ("whatsapp", "recipient"),
    "GAIA_WHATSAPP_VERIFY_TOKEN": ("whatsapp", "verify_token"),
    "GAIA_WHATSAPP_WEBHOOK_HOST": ("whatsapp", "webhook_host"),
    "GAIA_WHATSAPP_WEBHOOK_PORT": ("whatsapp", "webhook_port"),
}


def _field_is_bool(section: str, field: str) -> bool:
    """True se o campo do modelo e booleano (parse dedicado de env)."""
    section_field = Config.model_fields.get(section)
    if section_field is None:
        return False
    field_info = getattr(section_field.annotation, "model_fields", {}).get(field)
    return field_info is not None and field_info.annotation is bool


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


# Variaveis do formato antigo (OpenCode). Continuam aceitas enquanto a
# configuracao nao migra para GAIA_AGENT_*.
_LEGACY_ENV_MAP: dict[str, str] = {
    "GAIA_OPENCODE_AUTO_APPROVE": "auto_approve",
    "GAIA_OPENCODE_DIRECTORY": "directory",
    "GAIA_OPENCODE_MODEL": "model",
}

# Modelos do catalogo antigo que nao existem na API direta da DeepSeek.
_PROVIDER_API_KEYS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "commandcode": "COMMAND_CODE_API_KEY",
    "commandcode-anthropic": "COMMAND_CODE_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
}

_LEGACY_MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "deepseek/deepseek-flash": ("deepseek", "deepseek-flash"),
    "deepseek/deepseek-v4.1-flash": ("deepseek", "deepseek-flash"),
}


def _migrate_legacy_config(raw: dict[str, Any]) -> None:
    """Move ``[opencode]`` para ``[agent]`` quando a config ainda e antiga."""
    legacy = raw.pop("opencode", None)
    if not isinstance(legacy, dict):
        return
    agent = raw.setdefault("agent", {})
    if not isinstance(agent, dict):
        return
    for field in (
        "auto_approve",
        "directory",
        "request_timeout",
        "resume_session",
        "stall_timeout",
        "temperature",
        "max_tokens",
    ):
        if field in legacy and field not in agent:
            agent[field] = legacy[field]
    slug = str(legacy.get("model") or "").strip()
    if slug and "model" not in agent:
        provider, _, model = slug.partition("/")
        if provider and model:
            alias = _LEGACY_MODEL_ALIASES.get(slug)
            if alias:
                provider, model = alias
            agent.setdefault("provider", provider)
            agent["model"] = model
        else:
            agent["model"] = slug


def _apply_env(raw: dict[str, Any], env: dict[str, str]) -> None:
    for key, (section, field) in _ENV_MAP.items():
        value = env.get(key)
        if value is None or value == "":
            continue
        if _field_is_bool(section, field):
            raw.setdefault(section, {})[field] = _parse_bool(value)
        else:
            raw.setdefault(section, {})[field] = value

    agent = raw.setdefault("agent", {})
    if isinstance(agent, dict):
        for key, field in _LEGACY_ENV_MAP.items():
            value = env.get(key)
            if value and field not in agent:
                if field == "auto_approve":
                    agent[field] = _parse_bool(value)
                elif field == "model" and "/" in value:
                    provider, _, model = value.partition("/")
                    alias = _LEGACY_MODEL_ALIASES.get(value)
                    if alias:
                        provider, model = alias
                    agent.setdefault("provider", provider)
                    agent["model"] = model
                else:
                    agent[field] = value

    # A chave lida do .env precisa chegar ao agente: `resolve_api_key` consulta
    # o ambiente do processo, mas o .env e carregado numa copia local.
    if isinstance(agent, dict) and not str(agent.get("api_key") or "").strip():
        provider = str(agent.get("provider") or "deepseek").strip().lower()
        provider_key = _PROVIDER_API_KEYS.get(provider, "")
        for key in (
            "GAIA_AGENT_API_KEY",
            provider_key,
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "COMMAND_CODE_API_KEY",
            "COMMANDCODE_API_KEY",
            "OPENCODE_API_KEY",
            "OPENCODE_ZEN_API_KEY",
            "GROQ_API_KEY",
            "MISTRAL_API_KEY",
            "XAI_API_KEY",
        ):
            if key and env.get(key):
                agent["api_key"] = env[key]
                break

    if env.get("GAIA_DATA_DIR"):
        raw["data_dir"] = env["GAIA_DATA_DIR"]


_DEFAULT_CONFIG_TEMPLATE = """\
# Configuracao da Gaia. Segredos NAO devem ficar aqui: use ~/.config/gaia/.env

[agent]
# Trocar de provedor: rode /modelo (lista ao vivo + escolha numerada).
# Presets: deepseek | openai | openrouter | ollama | anthropic |
#          commandcode | commandcode-anthropic | opencode | groq | mistral |
#          xai | custom (qualquer um com base_url proprio)
provider = "deepseek"
model = "deepseek-flash"
# Vazio = endpoint padrao do provider.
base_url = ""
# Diretorio de trabalho (vazio = diretorio atual).
directory = ""
max_tokens = 4096
temperature = 0.3
max_steps = 32
resume_session = true
request_timeout = 900.0
# Auto-aprovar ferramentas (perigoso; padrao false).
auto_approve = false
tools = true
allow_outside_project = true
# Modelo pequeno para pedidos curtos (vazio = sempre o principal).
fast_model = ""
# Cacheia o prefixo estavel do prompt (persona + preferencias).
cache_prompt = true
# Depois disso o historico antigo vira um resumo compacto.
max_history_messages = 20
max_history_chars = 24000
summary_chars = 1200
# Mede tokens por turno (e mostra no /status).
usage = true
# Resultado de ferramenta no historico (o turno atual ve tudo; 0 = sem teto).
tool_result_chars = 4000
# Resumo do historico: local (extrativo) | llm (semantico, com fallback local).
history_summary = "local"
# Modelo do resumo (vazio = modelo principal).
history_summary_model = ""
# A chave da API NAO fica aqui: use GAIA_AGENT_API_KEY em ~/.config/gaia/.env

[stt]
model = "small"
language = "pt"
device = "cpu"
compute_type = "int8"
beam_size = 1
vad_filter = true
confirm_transcript = false      # envia a transcricao direto, sem pedir correcao
partial_transcription = true    # legendas parciais enquanto fala
partial_interval_seconds = 1.5
partial_window_seconds = 10.0

[tts]
enabled = true
provider = "edge"          # edge | say | none
edge_voice = "pt-BR-FranciscaNeural"
say_voice = "Luciana"
rate = "+0%"
max_chars = 1200
sentence_pause_ms = 120      # pausa extra entre frases (0 = nenhuma)

[audiobook]
enabled = true
inbox = ""                 # vazio => <data_dir>/audiobooks/inbox (coloque PDFs aqui)
outbox = ""                # vazio => <data_dir>/audiobooks/outbox (MP3 pronto)
tmp = ""                   # vazio => <data_dir>/audiobooks/tmp
voice = "pt-BR-ThalitaNeural"
rate = "-4%"
pitch = "+0Hz"
sentence_pause_ms = 220
chunk_chars = 3500
watch_minutes = 0.0        # >0 = checa a pasta de entrada sozinho (ex.: 5)
notify = true              # avisa no Telegram quando fica pronto
resume = true

[audio]
sample_rate = 16000
channels = 1
silence_ms = 5000               # pausa de 5s encerra a fala
min_speech_ms = 250
preroll_ms = 300
max_seconds = 0.0               # 0 = sem limite para uma fala
vad_threshold = 0.5             # probabilidade minima do Silero para contar fala
speech_threshold_db = -42.0     # reserva: limiar de RMS se faltar onnxruntime
input_device = ""
continuous = true               # microfone aberto; fala -> executa -> volta a ouvir
auto_mute_seconds = 30          # silencio que desliga o microfone (0 = nunca)

[secondbrain]
path = "~/SecondBrain"
# Embeddings para busca por sentido e para o sonho. Vazio = desligado.
embed_model = ""            # ex.: text-embedding-3-small | nomic-embed-text
embed_base_url = "https://api.openai.com/v1"
context_results = 2            # 2-3 trechos curtos bastam
context_chars = 480            # por trecho
context_max_chars = 1200       # teto do bloco inteiro
index_db = ""
auto_context = true
confirm_create = false          # criar nota nao pergunta (aditivo)
require_confirmation = true     # editar/excluir ainda pergunta
reindex_on_start = false        # ligue para reindexar a cada inicio
backup_before_write = true
default_folder = "Inbox"
preferences_file = ""          # vazio => <secondbrain.path>/preferences.md

[interface]
show_banner = true
speak_tool_activity = false
steering = true
concise = true
# Humor da persona: off | leve | medio | alto (alto = brinca e conta piadas).
humor = "medio"
show_reasoning = true
show_diffs = true
show_metas_on_start = true
dashboard_collapsed = true   # metas e lua começam recolhidos
ui = "textual"             # textual (padrão) | terminal
selection_mode = false     # true = seleção de texto ligada: arraste destaca e copia
fast = false               # modo rápido: sem busca automática, respostas curtas
fast_smalltalk = true      # saudações respondem na hora, sem chamar o modelo
chinese_echo = false       # mostra chinês + pinyin de cada mensagem
markdown_render = true     # respostas em Markdown (títulos, ênfase, código)
default_mode = "assistente"     # pesquisadora | trabalhadora | investidora | assistente

[whatsapp]
enabled = false
phone_number_id = ""
recipient = ""
api_version = "v21.0"
webhook_host = "127.0.0.1"
webhook_port = 8765
# access_token e verify_token vão apenas no .env/ambiente.

[browser]
enabled = true
profile_dir = ""          # vazio => <data_dir>/browser
headless = true
timeout_ms = 30000

[focus]
enabled = true
path = "~/.config/gaia/focus.json"
prompt_on_moon = true

[agenda]
enabled = true
env_file = "~/agenda-git/agenda/.env"
poll_minutes = 30
max_items = 5
notify_pending = false
tasks_poll_seconds = 0      # 0 = nao checa sozinho

[telegram]
enabled = false
allowed_chat_id = ""
api_base_url = "https://api.telegram.org"
poll_timeout = 25
voice_replies = false
auto_approve = false      # true = aprova as ferramentas no chat autorizado
shell_access = false      # true = libera write/edit/bash no chat (evite)
show_reasoning = false    # mostra o raciocinio no chat (/pensando on|off)
# bot_token vai apenas no .env/ambiente.

[memory]
# Consolida o dia em uma nota de memoria no Second Brain.
enabled = true
at = "23:00"
on_exit = true
use_llm = true
folder = "Memoria"
# Sonho noturno: quando ociosa de madrugada, cruza o dia com memorias antigas.
dream_enabled = true
dream_at = "02:00"          # janela noturna: pode virar a meia-noite
dream_until = "08:00"
dream_folder = "Sonhos"     # pasta propria, fora do diario consolidado
dream_notify = true         # avisa no dia seguinte (uma linha)
dream_notify_at = "08:00"
retry_minutes = 10.0        # espaco entre tentativas se o aviso falhar

[briefing]
# Bom dia, meio-dia e fecho da noite no Telegram, direto da Agenda.
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"
morning_routine = "matinal"
max_tasks = 15
# Comentario pessoal do modelo em cima da lista (uma frase feita pra ela hoje).
personalize = false
# Briefings que ganham o comentario: morning | noon.
personalize_slots = ["morning"]
personalize_timeout_seconds = 20.0
# Fecho da noite: o que foi feito, o que ficou e um insight seu.
evening_enabled = true
evening_at = "18:00"
evening_until = "23:00"
evening_insight = true

[career]
# Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
# entrega no Telegram. O metodo esta em ~/SecondBrain/cv/playbook-vagas.md.
enabled = true
days = [0]                  # 0=seg, 1=ter, 2=qua, 3=qui, 4=sex
at = "09:00"
until = "18:00"             # depois disso nao envia mais (recupera na proxima)
max_items = 8               # vagas por mensagem; o resto fica no estado

[proactive]
enabled = true
startup_greeting = true
greeting_via_llm = false   # true = saudação personalizada pelo modelo (mais lenta)
idle_checkin = true
idle_minutes = 45.0
min_interval_minutes = 60.0
daily_max = 4
quiet_hours = "23:00-08:00"
check_interval_seconds = 30.0
reminders = true

[serve]
# `gaia serve`: o mini processa e guarda tudo; as telas so desenham.
# Ver docs/plans/2026-11-19-serve-cliente-fino.md.
enabled = false
host = "127.0.0.1"    # loopback; para o tailnet use o IP 100.x e allow_remote
port = 8787
# Segredo: apenas no .env (GAIA_SERVE_TOKEN). Sem token nao escuta na rede.
allow_remote = false
telegram = true       # sobe o poller do Telegram junto (um processo so)
"""


def write_default_config(path: Path | None = None, force: bool = False) -> Path:
    target = path or Config.default_config_path()
    target = Path(target).expanduser()
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target


def _agent_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Intervalo (inicio do corpo, fim do corpo) da secao ``[agent]``."""
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if start is not None:
                return start, index
            if stripped == "[agent]":
                start = index + 1
    if start is None:
        return None
    return start, len(lines)


def _replace_agent_key(lines: list[str], key: str, value: str, start: int, end: int) -> bool:
    pattern = re.compile(rf"^(\s*{key}\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^#\n]*)(.*)$")
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match is None:
            continue
        comment = match.group(2)
        if comment and not comment.lstrip().startswith("#"):
            comment = ""
        lines[index] = f'{match.group(1)}"{value}"{(" " + comment.strip()) if comment.strip() else ""}'
        return True
    return False


def save_agent_model(
    provider: str,
    model: str,
    path: Path | None = None,
) -> Path:
    """Grava ``agent.provider``/``agent.model`` no TOML preservando o resto.

    Edicao linha a linha de proposito: o arquivo tem comentarios explicativos
    que um dump de TOML destruiria. Uma copia ``.bak`` fica ao lado.
    """
    target = Path(path or Config.default_config_path()).expanduser()
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider or not model:
        raise ValueError("provider e model são obrigatórios")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        original = target.read_text(encoding="utf-8")
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_text(original, encoding="utf-8")
        lines = original.splitlines()
    else:
        original = ""
        lines = []
    bounds = _agent_section_bounds(lines)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[agent]", f'provider = "{provider}"', f'model = "{model}"'])
    else:
        start, end = bounds
        missing: list[tuple[str, str]] = []
        for key, value in (("provider", provider), ("model", model)):
            if not _replace_agent_key(lines, key, value, start, end):
                missing.append((key, value))
        if missing:
            insert_at = end
            while insert_at > start and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines[insert_at:insert_at] = [f'{key} = "{value}"' for key, value in missing]
    updated = "\n".join(lines).rstrip("\n") + "\n"
    if updated != original:
        target.write_text(updated, encoding="utf-8")
    return target


def model_env_override(env: dict[str, str] | None = None) -> list[str]:
    """Variaveis que sobrescrevem o modelo do TOML na proxima inicializacao."""
    source = os.environ if env is None else env
    return [
        name
        for name in ("GAIA_AGENT_PROVIDER", "GAIA_AGENT_MODEL")
        if str(source.get(name) or "").strip()
    ]
