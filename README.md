# token-usage

**English** · [Português](README.pt-BR.md)

A local dashboard that shows how many tokens you spent on terminal AI coding agents and how much of each plan's quota is left.

- **Python standard library only** for the dashboard: no dependencies to install. The optional tray icon needs two packages.
- **100% local**: it reads the files the tools already write to your disk. The server listens only on `127.0.0.1`, and the page loads nothing from the internet.
- **Shows only what you use**: tools that are not installed do not appear in the UI.

## Supported tools

| Tool | Token history | Remaining quota |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | Claude Code OAuth API (fallback: cache in `~/.claude.json`) |
| GitHub Copilot CLI | `~/.copilot/session-store.db` | GitHub API via `gh auth token` (fallback: `~/.copilot/session-state`) |
| OpenAI Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` (honors `CODEX_HOME`) | ChatGPT API used by `/status` (fallback: last `rate_limits` saved in the rollouts) |
| Gemini CLI | `~/.gemini/tmp/*/chats/*.json` and `*.jsonl` | — |
| Google Antigravity | Antigravity CLI (`agy`): `~/.gemini/antigravity-cli/conversations/*.db`. The app does not store conversations in a readable format | Antigravity's local language server, only while the app is open; without the app, `agy -p /usage --output-format json` (local command, no tokens spent) |

What the dashboard shows:

- **Remaining quota** for each window (5-hour session, weekly, monthly premium requests, per-model Antigravity quota), with time until reset.
- **Usage** per day, model, tool and project (working directory), split into fresh input, output, cache read and cache write.

## Requirements

- Linux, macOS or Windows. The Antigravity app quota relies on `/proc` and `ss`, so it works on Linux only.
- Python 3.10+, unless you use the [prebuilt executables](#prebuilt-executables).
- Optional: an authenticated [`gh`](https://cli.github.com/) for the live Copilot quota.
- On macOS, Claude Code stores its token in the Keychain. In that case, the Claude quota comes from the last value saved in `~/.claude.json`.

## Install

The install scripts create a virtualenv of their own (`~/.local/share/token-usage` or `%LOCALAPPDATA%\token-usage`) and put the `token-usage` command on your PATH.

**Linux and macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.sh | sh
```

**Windows** (PowerShell)

```powershell
irm https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.ps1 | iex
```

Options:

| `install.sh` | `install.ps1` | What it does |
|---|---|---|
| `--tray` | `-Tray` | Also installs the [tray icon](#top-bar--system-tray-icon). On Windows it adds a **Token Usage** Start menu shortcut. |
| `--autostart` | `-Autostart` | Starts with your session: the icon with `--tray`, otherwise the dashboard. |
| `--uninstall` | `-Uninstall` | Removes everything the script installed. |

To pass options through the pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.sh | sh -s -- --tray --autostart
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/leoalmeidasa/token-usage/main/install.ps1))) -Tray -Autostart
```

On Linux, `--tray` needs PyGObject with AppIndicator from your distro. The script tells you which package to install if it is missing (Ubuntu/Debian: `sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1`).

### With pipx or uv

```bash
pipx install "token-usage[tray] @ https://github.com/leoalmeidasa/token-usage/archive/refs/heads/main.zip"
# or
uv tool install "token-usage[tray] @ https://github.com/leoalmeidasa/token-usage/archive/refs/heads/main.zip"
```

Drop `[tray]` if you only want the dashboard. On Linux, the icon needs the distro's PyGObject: use `pipx install --system-site-packages ...`.

### Prebuilt executables

Each [release](https://github.com/leoalmeidasa/token-usage/releases) has executables that do not need Python:

- `token-usage-linux-x86_64`: dashboard only. For the icon on Linux, use `install.sh --tray`.
- `token-usage-macos-arm64`: dashboard and icon (`token-usage-macos-arm64 tray`).
- `token-usage-windows-x86_64.zip`: `token-usage.exe` (command line) and `token-usage-tray.exe` (icon, no console window).

The executables are not signed. On macOS, run `xattr -d com.apple.quarantine token-usage-macos-arm64` before the first run. On Windows, SmartScreen may ask for confirmation.

### From a clone

```bash
git clone https://github.com/leoalmeidasa/token-usage.git
cd token-usage
python3 server.py --open
```

## Usage

```bash
token-usage --open          # dashboard at http://127.0.0.1:8777/, opens the browser
token-usage tray            # tray / top bar icon
token-usage autostart       # start the dashboard with the session (--tray for the icon, --remove to undo)
```

To use another port: `TOKEN_USAGE_PORT=9000 token-usage`.

The page refreshes by itself every 60 s (quotas every 30 s).

On Windows, the `~/.claude`, `~/.copilot` etc. folders live in `%USERPROFILE%`.

`token-usage autostart` uses a systemd user service for the dashboard and `~/.config/autostart` for the icon on Linux, a LaunchAgent on macOS and the `HKCU\...\Run` registry key on Windows. It takes effect at the next login.

### Top bar / system tray icon

`token-usage tray` adds an icon to the top bar (GNOME, KDE), the Windows notification area or the macOS menu bar. The ring shows the lowest remaining quota across all tools. Clicking it opens a menu with each quota, today's and this month's tokens per tool, and **Open detailed dashboard**. If the dashboard is not running, the icon starts it in the same process. **Avisar quando a cota apertar** (on by default) sends a system notification when a quota passes 80% and 95% used and when the forecast says it runs out before resetting, once per window. The thresholds are `notify_at` in `tray.json`.

**Show in bar** picks one quota to display as text next to the icon (for example `Claude 2%`), as % used or % remaining. The choice is saved in `~/.config/token-usage/tray.json` (`%APPDATA%\token-usage\tray.json` on Windows). On Windows, where the tray does not accept text, the number is drawn inside the ring.

It needs `pystray` and Pillow (`token-usage[tray]`). On Linux it also needs PyGObject with AppIndicator; on GNOME, enable the AppIndicator extension (already on by default in Ubuntu). On Windows, right-click the icon for the menu; left-click opens the dashboard.

## How the numbers are computed

- **Fresh input** excludes cache. Copilot, Codex and Gemini report input with cache included; the dashboard subtracts it so the numbers match Claude's.
- **Output** for Gemini adds the reasoning tokens (`thoughts`). In Codex, `output_tokens` already includes reasoning.
- **Duplicates** are removed: Claude Code writes several lines per response, resumed sessions repeat messages, and Codex repeats `token_count` events.
- **Copilot premium** counts only user-initiated turns in Copilot CLI. The monthly quota also includes IDE and web usage.
- **Forecast** ("at this pace it runs out at…"): the server keeps quota readings in memory and fits a line to the % used over the last hour. It only shows up with at least 3 readings spanning 10 minutes and 2 percentage points used, and only when the quota would run out before resetting. Readings come from whoever polls `/api/limits` (open page or tray icon); after a server restart it takes about 10 minutes to come back. While a quota API is rate limited (429), there are no new readings, so the forecast for that tool may not show up.

## Privacy and security

- No usage data leaves your machine. The only outbound requests are the quota checks. They go to the same endpoints each tool already uses, with the credentials the tool already stored:
  - `api.anthropic.com` (Claude Code)
  - `api.github.com` (Copilot)
  - `chatgpt.com` (Codex)

  The dashboard only queries tools that are installed.
- Credentials are read on each check. They are never copied, logged or returned by the local API.
- The server:
  - listens only on `127.0.0.1` and rejects requests whose `Host` is not `127.0.0.1`/`localhost` (DNS rebinding protection);
  - sends a strict `Content-Security-Policy`, `X-Frame-Options: DENY` and `nosniff`;
  - escapes all text that comes from disk or network before putting it into the HTML.
- Any local process running as your user can read `/api/usage`, which includes project paths. Do not expose the port to the network (for example, through a reverse proxy or `ssh -R`).
- The quota endpoints are **not documented public APIs**. They can change without notice; if they do, the dashboard falls back to the last value saved locally. Use at your own risk and check each service's terms of use.

## Limitations

- Codex, Gemini and Antigravity support is based on the known file formats of those tools. If a new version changes the format, open an issue with an anonymized snippet of the file.
- macOS and Windows support follows the tools' default paths, but it was tested on Linux only.
- Older Gemini CLI versions store only a hash of the project path; such projects show up as `gemini:<hash>`.
- The Antigravity app does not store tokens in a readable format, so only its quota is shown. The Antigravity CLI (`agy`) stores one protobuf per response in SQLite; the dashboard decodes the usage fields without a schema (same `ModelUsageStats` layout as the Windsurf/Codeium language server), so a new `agy` version may break it. The project comes from `history.jsonl`; conversations not listed there show up as `antigravity:<agy project>`.

## License

[MIT](LICENSE). Chart.js 4.4.1 (MIT) is bundled in `token_usage/vendor/`. See `token_usage/vendor/LICENSE-chartjs.md`.
