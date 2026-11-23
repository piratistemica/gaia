# Web do `gaia serve`: instâncias, sessão unificada e paridade com a TUI

Status: **implementado**. Documento de decisão; o código é a verdade final e o
`README.md`/`AGENTS.md` foram atualizados na mesma mudança. Testes:
`tests/test_serve_instances.py` e `tests/test_control_session.py`.

## Problema

A página `/` (chat) e o `/control` (painel) nasceram separadas e cada uma
resolveu credencial do seu jeito: o chat pede o `GAIA_SERVE_TOKEN` a cada visita
(fica em `localStorage`, um por origem) e o painel já tem sessão de cookie.
Além disso, o web só mostra o essencial do chat: nada de instâncias, modos,
sessões anteriores, automações, provedor ou chave mascarada — coisas que a TUI
já faz.

## Decisões

1. **`instances.json` em `GAIA_DATA_DIR` é a fonte das instâncias.**
   Lista manual (`id`, `name`, `url`, `serve_token`, `control_token`). Arquivo
   ausente ou inválido = lista vazia (a tela funciona; só não mostra seletor).
   O servidor **nunca** devolve os tokens: a rota pública de apresentação
   (`Instance.public()`) só tem `id`, `name`, `url`, `current`, `has_*_token`.
   Os tokens existem no arquivo para outra instância poder ser usada como
   cliente; não são usados para autenticar ninguém neste processo.

2. **Status online/offline é medido pelo servidor, não pelo navegador.**
   `GET /instances` faz um `GET <url>/health` por instância, em paralelo, com
   timeout curto (`0.8 s`) e `Authorization` da instância. Motivo: evita CORS
   no navegador e não vaza token para o front. Falha = `online: false` (nunca
   500).

3. **Trocar de instância é navegação, não fetch cross-origin.**
   Cookie de sessão é `SameSite=Strict` (por origem). Em vez de abrir CORS com
   credenciais, o seletor navega para a URL da instância escolhida. O
   `instances.json` continua útil para o servidor atual listar/provar as outras.

4. **Sessão de cookie unificada, com `Bearer` como fallback.**
   `POST /session` (chat) troca `Authorization: Bearer <GAIA_SERVE_TOKEN>` por
   um nonce opaco em cookie `HttpOnly` (`gaia_chat`, `Path=/`,
   `SameSite=Strict`). `DELETE /session` encerra; `GET /session` responde
   `{authenticated}`. O cookie do painel (`gaia_control`, `Path=/control`)
   continua separado: um não vale no outro (separação de poderes). O `Bearer`
   segue aceito em todas as rotas — é o que o cliente fino e o `curl` usam.
   Os nonces vivem em memória (reiniciar o `serve` derruba as sessões).

5. **“Lembrar token neste navegador” é opt-in e explícito.**
   Marcado, o token daquele origem vai para o `localStorage` e a tela refaz a
   troca em silêncio quando o cookie vence (12 h) — que é o atrito real de
   pedir token "toda vez". Desmarcado (padrão), nada do token é guardado: só o
   cookie de sessão. O nonce nunca é legível pelo JS.

6. **Paridade com a TUI por rotas pequenas, no mesmo contrato do chat.**
   `GET /engine` (modelo, provedor, chave mascarada, modo), `GET /modes` +
   `POST /mode`, `GET /sessions` (já existia) + `POST /session/open` +
   `POST /session/new` + `GET /history`, `GET /automations`. Tudo exige a
   mesma prova do chat (cookie ou `Bearer`). Nada disso mostra segredo inteiro.

7. **Front compartilhado, sem framework nem build.**
   `gaia/serve/static/shared.js` (exposto em `/static/shared.js`) concentra
   armazenamento de tokens, login/logout, chamadas à API, seletor de instância
   e cabeçalho. `index.html` e `control.js` consomem o mesmo módulo; o painel
   mantém as abas e a lógica de desenho que já existia.

## Fluxo

1. A página abre, chama `GET /session`. Sem sessão, tenta refazer a troca com o
   token lembrado (se houver); senão mostra o modal.
2. Com sessão, carrega `/instances`, `/engine`, `/modes`, `/health` e desenha o
   cabeçalho (instância, modelo, provedor, chave mascarada, modo).
3. `POST /session` com `Bearer` cria o nonce; o `Set-Cookie` faz o resto. As
   chamadas seguintes usam só o cookie (sem cabeçalho) até ele vencer.
4. Turno, busca, permissão, modelo, modo, sessões: tudo pelo mesmo `api()`.
5. Trocar de instância no seletor navega para a outra URL; lá o ciclo recomeça
   (com cookie ou token lembrado daquele origem).

## Riscos e não-objetivos

- **Não** há CORS com credenciais, nem `SameSite=None`: custo de segurança alto
  para um ganho que a navegação resolve.
- **Não** há edição de `instances.json` pela tela (o arquivo é manual, chmod
  600, contém tokens). Uma fase futura pode escrever via Control Center.
- `GET /health` continua **público**: nada de chave mascarada nem de detalhe
  administrativo nele. O cabeçalho lê `/engine`, que é autenticado.
- O probe de `GET /instances` é best-effort: offline não é erro de rota.

## Testes

`tests/test_serve_instances.py`: leitura do arquivo (ausente/inválido/filtro),
probe (monkeypatched, sem rede), `/instances` sem token/omissão de segredos,
`current` por `serve.instance_id`, `POST/GET/DELETE /session` (cookie
`HttpOnly`, `Bearer` ainda vale, cookie do painel não vale no chat e vice-versa)
e as rotas novas com alvo falso. Os testes existentes de `test_serve.py` e
`test_control_api.py` continuam valendo sem alteração de contrato.
