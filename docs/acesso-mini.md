# Acesso ao mini a partir do MacBook

O mini (`zaya`) é headless na prática: o canal de uso é o
Telegram. Para administrar, tudo sai do MacBook por SSH com chave — sem senha,
sem abrir porta no roteador.

## O básico que já existe

`~/.ssh/config` no MacBook:

```sshconfig
Host zaya
    HostName 192.168.1.10   # IP do mini na sua LAN
    User talita
    IdentityFile ~/.ssh/id_ed25519
```

Daí sai `ssh zaya` e mais nada é necessário para entrar.

## `zmin` — o kit de administração

`scripts/zmin.sh`, symlinkado em `~/.local/bin/zmin` (que já está no `PATH`).

```bash
zmin doctor              # ssh, alias vs IP real, serviço, agenda, ferramentas, log
zmin logs -f             # journal do serviço (unidade de usuário: sem sudo)
zmin restart             # reinicia o serviço do Telegram
zmin deploy --dry-run    # mostra o que iria; depois `zmin deploy` envia e reinicia
zmin test                # smoke test: importa tudo, confere agenda e permissões
zmin agenda [AAAA-MM-DD] # exatamente o que a Zaya vê na Agenda
zmin ask "pergunta"      # conversa com a Zaya do mini sem passar pelo Telegram
zmin shell               # sessão `screen` persistente (sobrevive a queda de wifi)
zmin sudo <comando…>     # roda com sudo; a senha é digitada AQUI, no MacBook
```

`zmin deploy` sincroniza o repo menos o que não deve sair do MacBook
(`.git`, `.venv`, `data/`, `config/`, caches) e só reinicia o serviço se o código
importar no destino. Antes de reiniciar ele não faz nada destrutivo: não usa
`--delete`.

## Uma vez só: senha do sudo

`sudo` no mini pede senha interativa — mas isso **não** obriga a ir até lá:

```bash
zmin sudo -v                                   # valida o sudo por 15 min
zmin sudo systemctl restart avahi-daemon        # conserta o nome .local
zmin sudo apt install -y tmux fail2ban         # opcionais
```

## Itens de uma vez (e por que)

| Comando | Por quê |
|---|---|
| `sudo systemctl restart avahi-daemon` | o mDNS ainda anuncia o nome antigo (`talita-default-string.local`); depois disso `ssh zaya.local` funciona e o IP deixa de importar |
| `sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && sudo systemctl restart ssh` | fecha login por senha (hoje ninguém usa: 113 logins por chave, 0 por senha) |
| `sudo apt install -y tmux` | `screen` funciona, `tmux` é mais confortável para sessões longas |
| `sudo apt install -y fail2ban` | só faz sentido se algum dia o SSH ficar exposto |

## IP dinâmico

`enp1s0` está em DHCP, então o alias pode quebrar um dia.
`zmin doctor` compara o alias com o IP real e avisa. Três saídas, em ordem de
preferência:

1. **mDNS**: depois do restart do avahi, `HostName zaya.local` no
   `~/.ssh/config` — imune a mudança de IP.
2. **Reserva DHCP** no roteador, amarrando o MAC do mini ao IP atual.
3. `zmin doctor` + editar o `HostName` na mão quando avisar.

## Fora de casa

O SSH é só LAN. Para alcançar o mini de qualquer lugar sem abrir porta no
roteador, o caminho é uma VPN de malha:

```bash
# no MacBook e no mini (precisa de conta)
brew install --cask tailscale      # macOS
zmin sudo apt install -y tailscale && zmin sudo tailscale up
```

Depois disso o mini ganha um IP `100.x.y.z` estável, acessível de qualquer
rede, com ACL por máquina — e o SSH deixa de depender de estar em casa. Nada
de `iptables` manual nem de expor a 22 na internet.

## O que **não** fazer

- Port forwarding de SSH no roteador: é o caminho mais curto para brute force
  contínuo.
- `NOPASSWD: ALL` no sudoers: com `auto_approve` no agente, isso é root a um
  comando de distância. Se quiser tirar a senha de alguns comandos, use um
  drop-in em `/etc/sudoers.d/` com lista explícita (`systemctl restart ssh`,
  `journalctl`) — nunca `ALL`.
