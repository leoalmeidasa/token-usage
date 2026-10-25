# token-usage

[English](README.md) · **Português**

Painel local que mostra quantos tokens você gastou nos agentes de IA de terminal e quanto ainda resta das cotas de cada plano.

- **Só biblioteca padrão do Python** no painel: nenhuma dependência para instalar. O ícone opcional na bandeja precisa de dois pacotes.
- **100% local**: lê os arquivos que as próprias ferramentas já gravam no seu disco. O servidor escuta apenas em `127.0.0.1` e a página não carrega nada de fora.
- **Mostra só o que você usa**: ferramentas não instaladas não aparecem na interface.

## Ferramentas suportadas

| Ferramenta | Histórico de tokens | Cota restante |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | API OAuth do Claude Code (fallback: cache em `~/.claude.json`) |
| GitHub Copilot CLI | `~/.copilot/session-store.db` | API do GitHub via `gh auth token` (fallback: `~/.copilot/session-state`) |
| OpenAI Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` (respeita `CODEX_HOME`) | API do ChatGPT usada pelo `/status` (fallback: último `rate_limits` gravado nos rollouts) |
| Gemini CLI | `~/.gemini/tmp/*/chats/*.json` e `*.jsonl` | — |
| Google Antigravity | Antigravity CLI (`agy`): `~/.gemini/antigravity-cli/conversations/*.db`. O app não grava conversas legíveis | language server local do Antigravity, só com o app aberto; sem o app, `agy -p /usage --output-format json` (comando local, não gasta tokens) |

O que o painel mostra:

- **Quanto resta** de cada janela de cota (sessão de 5 h, semanal, premium requests do mês, cota por modelo do Antigravity), com tempo até renovar.
- **Consumo** por dia, por modelo, por ferramenta e por projeto (diretório de trabalho), separando input novo, output, cache lido e cache gravado.

## Requisitos

- Linux, macOS ou Windows. A cota do app Antigravity depende de `/proc` e `ss`, então só funciona no Linux.
- Python 3.10+, a menos que você use os [executáveis prontos](#executáveis-prontos).
- Opcional: [`gh`](https://cli.github.com/) autenticado, para a cota do Copilot ao vivo.
- No macOS, o Claude Code guarda o token no Keychain. Nesse caso, a cota do Claude vem do último valor salvo em `~/.claude.json`.

## Instalação

Os scripts de instalação criam um virtualenv próprio (`~/.local/share/token-usage` ou `%LOCALAPPDATA%\token-usage`) e colocam o comando `token-usage` no PATH.

**Linux e macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.sh | sh
```

**Windows** (PowerShell)

```powershell
irm https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.ps1 | iex
```

Opções:

| `install.sh` | `install.ps1` | O que faz |
|---|---|---|
| `--tray` | `-Tray` | Instala também o [ícone na bandeja](#ícone-na-barra-superior--bandeja). No Windows, cria o atalho **Token Usage** no menu Iniciar. |
| `--autostart` | `-Autostart` | Inicia com a sessão: o ícone com `--tray`; senão, o painel. |
| `--uninstall` | `-Uninstall` | Remove tudo o que o script instalou. |

Para passar opções pelo pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.sh | sh -s -- --tray --autostart
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.ps1))) -Tray -Autostart
```

No Linux, `--tray` precisa do PyGObject com AppIndicator da distro. Se faltar, o script diz qual pacote instalar (Ubuntu/Debian: `sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1`).

### Com pipx ou uv

```bash
pipx install "token-usage[tray] @ https://github.com/leoalmeidasa/token-usage/archive/refs/heads/main.zip"
# ou
uv tool install "token-usage[tray] @ https://github.com/leoalmeidasa/token-usage/archive/refs/heads/main.zip"
```

Tire o `[tray]` se quiser só o painel. No Linux, o ícone precisa do PyGObject da distro: use `pipx install --system-site-packages ...`.

### Executáveis prontos

Cada [release](https://github.com/leoalmeidasa/token-usage/releases) traz executáveis que não precisam de Python:

- `token-usage-linux-x86_64`: só o painel. Para o ícone no Linux, use `install.sh --tray`.
- `token-usage-macos-arm64`: painel e ícone (`token-usage-macos-arm64 tray`).
- `token-usage-windows-x86_64.zip`: `token-usage.exe` (linha de comando) e `token-usage-tray.exe` (ícone, sem janela de console).

Os executáveis não são assinados. No macOS, rode `xattr -d com.apple.quarantine token-usage-macos-arm64` antes da primeira execução. No Windows, o SmartScreen pode pedir confirmação.

### A partir do clone

```bash
git clone https://github.com/leoalmeidasa/token-usage.git
cd token-usage
python3 server.py --open
```

## Uso

```bash
token-usage --open          # painel em http://127.0.0.1:8777/, abre o navegador
token-usage tray            # ícone na bandeja / barra superior
token-usage autostart       # inicia o painel com a sessão (--tray para o ícone, --remove para desfazer)
```

Para outra porta: `TOKEN_USAGE_PORT=9000 token-usage`.

A página atualiza sozinha a cada 60 s (cotas a cada 30 s).

No Windows, as pastas `~/.claude`, `~/.copilot` etc. ficam em `%USERPROFILE%`.

O `token-usage autostart` usa um serviço systemd de usuário para o painel e `~/.config/autostart` para o ícone no Linux, um LaunchAgent no macOS e a chave de registro `HKCU\...\Run` no Windows. Vale a partir do próximo login.

### Ícone na barra superior / bandeja

`token-usage tray` coloca um ícone na barra superior (GNOME, KDE), na área de notificação do Windows ou na barra de menus do macOS. O anel mostra a menor cota restante entre todas as ferramentas. Ao clicar, abre um menu com cada cota, os tokens de hoje e do mês por ferramenta e **Abrir painel detalhado**. Se o painel não estiver rodando, o ícone sobe o painel no mesmo processo. **Avisar quando a cota apertar** (ligado por padrão) manda uma notificação do sistema quando uma cota passa de 80% e de 95% usados e quando a previsão diz que ela acaba antes de renovar, uma vez por janela. Os limites ficam em `notify_at` no `tray.json`.

Em **Mostrar na barra** você escolhe uma cota para aparecer em texto ao lado do ícone (por exemplo `Claude 2%`), em % usado ou restante. A escolha fica em `~/.config/token-usage/tray.json` (`%APPDATA%\token-usage\tray.json` no Windows). No Windows, onde a bandeja não aceita texto, o número aparece dentro do anel.

Precisa de `pystray` e Pillow (`token-usage[tray]`). No Linux também precisa do PyGObject com AppIndicator; no GNOME, ative a extensão AppIndicator (já vem ativa no Ubuntu). No Windows, o menu abre com o botão direito; o clique esquerdo abre o painel.

## Como os números são calculados

- **Input novo** não inclui cache. Copilot, Codex e Gemini informam input com cache embutido; o painel subtrai para ficar comparável ao Claude.
- **Output** do Gemini soma os tokens de raciocínio (`thoughts`). No Codex, `output_tokens` já inclui o raciocínio.
- **Duplicatas** são removidas: o Claude Code grava várias linhas por resposta, sessões retomadas repetem mensagens e o Codex repete eventos `token_count`.
- **Premium Copilot** conta só turnos iniciados pelo usuário no Copilot CLI. A cota mensal também inclui uso na IDE e na web.
- **Previsão** ("nesse ritmo acaba às…"): o servidor guarda as leituras de cota em memória e ajusta uma reta ao % usado da última hora. Só aparece com pelo menos 3 leituras, 10 min de intervalo e 2 pontos percentuais consumidos, e só quando a cota acabaria antes de renovar. As leituras vêm de quem consulta `/api/limits` (página aberta ou ícone na bandeja); depois de reiniciar o servidor, leva uns 10 min para voltar. Enquanto a API de cota estiver limitada (429), não há leituras novas, e a previsão daquela ferramenta pode não aparecer.

## Privacidade e segurança

- Nenhum dado de uso sai da máquina. As únicas requisições externas são as consultas de cota, feitas para os mesmos endpoints que cada ferramenta já usa, com as credenciais que ela já guardou:
  - `api.anthropic.com` (Claude Code)
  - `api.github.com` (Copilot)
  - `chatgpt.com` (Codex)
  
  O painel só consulta ferramentas que estão instaladas.
- As credenciais são lidas a cada consulta, nunca copiadas, registradas em log ou devolvidas pela API local.
- O servidor:
  - escuta só em `127.0.0.1` e recusa requisições com `Host` diferente de `127.0.0.1`/`localhost` (proteção contra DNS rebinding);
  - envia `Content-Security-Policy` restritiva, `X-Frame-Options: DENY` e `nosniff`;
  - escapa todo texto vindo de disco ou rede antes de colocar no HTML.
- Qualquer processo local do seu usuário consegue ler `/api/usage`, que inclui caminhos de projetos. Não exponha a porta na rede (por exemplo, com proxy reverso ou `ssh -R`).
- Os endpoints de cota **não são APIs públicas documentadas**. Podem mudar sem aviso; nesse caso o painel cai para o último valor salvo localmente. Use por sua conta e verifique os termos de uso de cada serviço.

## Limitações

- Codex, Gemini e Antigravity foram implementados a partir dos formatos conhecidos dessas ferramentas. Se uma versão nova mudar o formato, abra uma issue com um trecho anonimizado do arquivo.
- O suporte a macOS e Windows segue os caminhos padrão das ferramentas, mas foi testado só no Linux.
- Gemini CLI antigo grava só o hash do projeto; nesse caso o projeto aparece como `gemini:<hash>`.
- O app Antigravity não grava tokens em formato legível, então só a cota aparece. O Antigravity CLI (`agy`) grava um protobuf por resposta em SQLite; o painel decodifica os campos de uso sem schema (mesmo layout `ModelUsageStats` do language server do Windsurf/Codeium), então uma versão nova do `agy` pode quebrar a leitura. O projeto vem do `history.jsonl`; conversas que não estão lá aparecem como `antigravity:<projeto do agy>`.

## Licença

[MIT](LICENSE). Chart.js 4.4.1 (MIT) está incluído em `token_usage/vendor/`. Veja `token_usage/vendor/LICENSE-chartjs.md`.
