# Acesso ao mini a partir do MacBook

O mini (`gaia`) é headless na prática. Em produção o `gaia serve` é o
**único** processo dono do banco, do Second Brain, dos workers e do Telegram;
Mac, navegador e celular são telas (`gaia --remote` ou a web em `GET /`). Para
administrar, tudo sai do MacBook por SSH com chave — sem senha, sem abrir porta
no roteador.

## O básico que já existe

`~/.ssh/config` no MacBook:

```sshconfig
Host gaia
    HostName 192.168.1.10   # IP do mini na sua LAN
    User talita
    IdentityFile ~/.ssh/id_ed25519
```

Daí sai `ssh gaia` e mais nada é necessário para entrar.

## `gmin` — o kit de administração

`scripts/gmin.sh`, symlinkado em `~/.local/bin/gmin` (que já está no `PATH`).

```bash
gmin doctor              # ssh, alias vs IP real, serviço, agenda, ferramentas, log
gmin logs -f             # journal do serviço (unidade de usuário: sem sudo)
gmin restart             # reinicia a unidade ativa (serve ou telegram)
gmin serve status        # /health no loopback e no tailnet
gmin deploy --dry-run    # mostra o que iria; depois `gmin deploy` envia e reinicia
gmin test                # smoke test: importa tudo, confere agenda e permissões
gmin agenda [AAAA-MM-DD] # exatamente o que a Gaia vê na Agenda
gmin ask "pergunta"      # conversa com a Gaia do mini sem passar pelo Telegram
gmin shell               # sessão `screen` persistente (sobrevive a queda de wifi)
gmin sudo <comando…>     # roda com sudo; a senha é digitada AQUI, no MacBook
```

`gmin deploy` sincroniza o repo menos o que não deve sair do MacBook
(`.git`, `.venv`, `data/`, `config/`, caches) e só reinicia o serviço se o código
importar no destino. Antes de reiniciar ele não faz nada destrutivo: não usa
`--delete`.

## Uma vez só: senha do sudo

`sudo` no mini pede senha interativa — mas isso **não** obriga a ir até lá:

```bash
gmin sudo -v                                   # valida o sudo por 15 min
gmin sudo systemctl restart avahi-daemon        # conserta o nome .local
gmin sudo apt install -y tmux fail2ban         # opcionais
```

## Itens de uma vez (e por que)

| Comando | Por quê |
|---|---|
| `sudo systemctl restart avahi-daemon` | o mDNS ainda anuncia o nome antigo (`talita-default-string.local`); depois disso `ssh gaia.local` funciona e o IP deixa de importar |
| `sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && sudo systemctl restart ssh` | fecha login por senha (hoje ninguém usa: 113 logins por chave, 0 por senha) |
| `sudo apt install -y tmux` | `screen` funciona, `tmux` é mais confortável para sessões longas |
| `sudo apt install -y fail2ban` | só faz sentido se algum dia o SSH ficar exposto |

## IP dinâmico

`enp1s0` está em DHCP, então o alias pode quebrar um dia.
`gmin doctor` compara o alias com o IP real e avisa. Três saídas, em ordem de
preferência:

1. **mDNS**: depois do restart do avahi, `HostName gaia.local` no
   `~/.ssh/config` — imune a mudança de IP.
2. **Reserva DHCP** no roteador, amarrando o MAC do mini ao IP atual.
3. `gmin doctor` + editar o `HostName` na mão quando avisar.

## Fora de casa

O SSH é só LAN. O acesso remoto é por VPN de malha (Tailscale), **já instalada**
no mini, no MacBook e no iPhone: o mini tem IP `100.66.45.57` (MagicDNS
`gaia.tail682866.ts.net`), e o `gaia serve` escuta nesse IP com token. O SSH e a
web deixam de depender de estar em casa, com ACL por máquina — nada de
`iptables` manual nem de expor a 22 na internet.

Se precisar reinstalar em outra máquina:

```bash
brew install --cask tailscale      # macOS
gmin sudo apt install -y tailscale && gmin sudo tailscale up
gmin serve install --tailnet       # exige GAIA_SERVE_TOKEN no .env
```

## O que **não** fazer

- Port forwarding de SSH no roteador: é o caminho mais curto para brute force
  contínuo.
- `NOPASSWD: ALL` no sudoers: com `auto_approve` no agente, isso é root a um
  comando de distância. Se quiser tirar a senha de alguns comandos, use um
  drop-in em `/etc/sudoers.d/` com lista explícita (`systemctl restart ssh`,
  `journalctl`) — nunca `ALL`.
