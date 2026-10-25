"""Painel local de uso de tokens de agentes de IA: Claude Code, Copilot CLI, Codex CLI, Gemini CLI e Antigravity.

Lê só arquivos locais das ferramentas e, para cotas, as APIs que as próprias ferramentas usam.
Nada é enviado para terceiros; o servidor escuta apenas em 127.0.0.1.
"""
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude" / "projects"
COPILOT_DB = HOME / ".copilot" / "session-store.db"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
CODEX_SESSIONS = CODEX_HOME / "sessions"
CODEX_AUTH = CODEX_HOME / "auth.json"
GEMINI_TMP = HOME / ".gemini" / "tmp"
ANTIGRAVITY_DIR = HOME / ".gemini" / "antigravity"
AGY_DIR = HOME / ".gemini" / "antigravity-cli"  # Antigravity CLI (agy)
AGY_CONVERSATIONS = AGY_DIR / "conversations"
AGY_HISTORY = AGY_DIR / "history.jsonl"
AGY = shutil.which("agy") or str(HOME / ".local" / "bin" / "agy")  # systemd não tem ~/.local/bin no PATH
AGY_TTL = 300  # o /usage do agy leva alguns segundos; consulta com menos frequência
HERE = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", int(os.environ.get("TOKEN_USAGE_PORT", "8777"))
CLAUDE_STATE = HOME / ".claude.json"
CLAUDE_CREDS = HOME / ".claude" / ".credentials.json"
COPILOT_STATE = HOME / ".copilot" / "session-state"
LIMITS_TTL = 60  # segundos entre consultas às APIs de cota
LIMITS_MIN = 10  # nem o botão de atualizar consulta as APIs mais que isso
BACKOFF_MAX = 3600  # teto da espera depois de 429 seguidos
# previsão de quando a cota acaba: ritmo da última hora, com um mínimo de dados para não alarmar à toa
FORECAST_LOOKBACK = 3600  # só as leituras desta janela de tempo contam para o ritmo
FORECAST_SPAN = 600  # precisa de pelo menos 10 min entre a primeira e a última leitura
FORECAST_MIN_DELTA = 2  # e de pelo menos 2 pontos percentuais consumidos nesse intervalo
GH = shutil.which("gh") or "/snap/bin/gh"  # systemd não tem /snap/bin no PATH
# só estes Host são aceitos: bloqueia DNS rebinding de páginas abertas no navegador
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; "
                               "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# cache por arquivo: path -> (mtime, size, {dedupe_key: record})
_file_cache: dict[str, tuple[float, int, dict]] = {}
_records_lock = threading.Lock()


def local_day(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")


def project_name(cwd: str | None, folder: str) -> str:
    if cwd:
        return cwd.replace(str(HOME), "~") or "~"
    return folder


def record(day, source, model, project, session, inp=0, outp=0, cr=0, cw=0, premium=0.0) -> dict:
    return {"day": day, "source": source, "model": model, "project": project, "session": session,
            "input": max(inp, 0), "output": outp, "cache_read": cr, "cache_write": cw,
            "requests": 1, "premium": premium}


def _stat_sig(path: Path) -> tuple[float, int]:
    st = path.stat()
    return st.st_mtime, st.st_size


def cached_records(paths, parser, seen: set, sig=_stat_sig) -> dict:
    """Reparseia só arquivos que mudaram; deduplica pelas chaves que o parser devolve."""
    out = {}
    for path in paths:
        p = str(path)
        seen.add(p)
        try:
            mtime, size = sig(path)
        except OSError:
            continue
        cached = _file_cache.get(p)
        if not cached or cached[0] != mtime or cached[1] != size:
            try:
                cached = (mtime, size, parser(path))
            except (OSError, ValueError, sqlite3.Error) as e:
                print(f"ignorando {p}: {e}", file=sys.stderr, flush=True)
                cached = (mtime, size, {})
            _file_cache[p] = cached
        out.update(cached[2])
    return out


def parse_claude_file(path: Path) -> dict:
    recs = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            model = msg.get("model")
            if not u or not model or model == "<synthetic>" or not d.get("timestamp"):
                continue
            # o Claude Code grava uma linha por bloco de conteúdo com o mesmo usage
            key = ("claude", msg.get("id"), d.get("requestId"))
            recs[key] = record(local_day(d["timestamp"]), "claude", model,
                               project_name(d.get("cwd"), path.parent.name), d.get("sessionId") or path.stem,
                               u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                               u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0)
    return recs


def claude_records(seen: set) -> list[dict]:
    if not CLAUDE_DIR.is_dir():
        return []
    # sessões retomadas/forks repetem mensagens em arquivos distintos
    return list(cached_records(CLAUDE_DIR.rglob("*.jsonl"), parse_claude_file, seen).values())


def parse_codex_file(path: Path) -> dict:
    """Rollout do Codex CLI: um evento token_count por resposta do modelo."""
    recs = {}
    session, cwd, model = path.stem, None, "codex"
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"session_meta"' not in line and '"turn_context"' not in line and '"token_count"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                session = p.get("id") or session
                cwd = p.get("cwd") or cwd
            elif d.get("type") == "turn_context":
                model = p.get("model") or model
                cwd = p.get("cwd") or cwd
            elif p.get("type") == "token_count" and p.get("info") and d.get("timestamp"):
                last = p["info"].get("last_token_usage") or {}
                total = p["info"].get("total_token_usage") or {}
                # o mesmo total acumulado reaparece em eventos repetidos e em sessões retomadas
                key = ("codex", session, total.get("total_tokens"), total.get("input_tokens"))
                cached = last.get("cached_input_tokens") or 0
                # input_tokens inclui o cache lido; output_tokens já inclui o raciocínio
                recs[key] = record(local_day(d["timestamp"]), "codex", model, project_name(cwd, "?"), session,
                                   (last.get("input_tokens") or 0) - cached, last.get("output_tokens") or 0, cached)
    return recs


def codex_records(seen: set) -> list[dict]:
    if not CODEX_SESSIONS.is_dir():
        return []
    return list(cached_records(CODEX_SESSIONS.rglob("rollout-*.jsonl"), parse_codex_file, seen).values())


def _gemini_project(chat_dir: Path) -> str:
    # versões novas gravam a raiz do projeto; as antigas só têm o hash sha256 do caminho
    root = chat_dir.parent / ".project_root"
    try:
        return project_name(root.read_text(encoding="utf-8").strip(), "")
    except OSError:
        name = chat_dir.parent.name
        return f"gemini:{name[:12]}" if re.fullmatch(r"[0-9a-f]{64}", name) else name


def _gemini_jsonl(path: Path) -> dict:
    """Formato novo: 1ª linha com metadados da sessão, depois uma linha por mensagem.
    A mesma mensagem é regravada quando atualizada; a última versão vale."""
    session, messages = path.stem, {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if "sessionId" in d and "id" not in d:
                session = d["sessionId"]
            elif d.get("id"):
                messages[d["id"]] = d
    return {"sessionId": session, "messages": list(messages.values())}


def parse_gemini_file(path: Path) -> dict:
    recs = {}
    d = _gemini_jsonl(path) if path.suffix == ".jsonl" else json.loads(path.read_text(encoding="utf-8", errors="replace"))
    session = d.get("sessionId") or path.stem
    project = _gemini_project(path.parent)
    for m in d.get("messages") or []:
        t = m.get("tokens")
        if m.get("type") != "gemini" or not t or not m.get("timestamp"):
            continue
        cached = t.get("cached") or 0
        # input do Gemini inclui o cache lido; thoughts é cobrado como output
        recs[("gemini", session, m.get("id") or m["timestamp"])] = record(
            local_day(m["timestamp"]), "gemini", m.get("model") or "gemini", project, session,
            (t.get("input") or 0) - cached, (t.get("output") or 0) + (t.get("thoughts") or 0), cached)
    return recs


def _gemini_chats():
    # versões antigas gravam um .json por sessão; as novas, .jsonl
    return [*GEMINI_TMP.glob("*/chats/*.json"), *GEMINI_TMP.glob("*/chats/*.jsonl")]


def gemini_records(seen: set) -> list[dict]:
    if not GEMINI_TMP.is_dir():
        return []
    return list(cached_records(_gemini_chats(), parse_gemini_file, seen).values())


def _pb_varint(b: bytes, i: int) -> tuple[int, int]:
    r = shift = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return r, i


def _pb_fields(b: bytes) -> dict:
    """Decodifica um nível de protobuf sem schema: {campo: [valores]} (int ou bytes)."""
    i, out = 0, {}
    try:
        while i < len(b):
            key, i = _pb_varint(b, i)
            wire = key & 7
            if wire == 0:
                v, i = _pb_varint(b, i)
            elif wire == 1:
                v, i = b[i:i + 8], i + 8
            elif wire == 5:
                v, i = b[i:i + 4], i + 4
            elif wire == 2:
                n, i = _pb_varint(b, i)
                v, i = b[i:i + n], i + n
            else:
                raise ValueError(f"wire type {wire}")
            out.setdefault(key >> 3, []).append(v)
    except IndexError as e:
        raise ValueError("protobuf truncado") from e
    return out


def _pb_get(msg: dict, *path, default=None):
    """Segue campos aninhados (primeira ocorrência de cada um)."""
    v = None
    for n, f in enumerate(path):
        if f not in msg:
            return default
        v = msg[f][0]
        if n < len(path) - 1:
            if not isinstance(v, bytes):
                return default
            msg = _pb_fields(v)
    return v


def _agy_workspaces() -> dict:
    """conversationId -> diretório de trabalho, a partir do histórico de prompts do agy."""
    out = {}
    try:
        with AGY_HISTORY.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("conversationId") and d.get("workspace"):
                    out[d["conversationId"]] = d["workspace"]
    except OSError:
        pass
    return out


def _agy_sig(path: Path) -> tuple[float, int]:
    # SQLite em modo WAL: as escritas recentes ficam no -wal antes do checkpoint
    mtime, size = _stat_sig(path)
    try:
        wal_mtime, wal_size = _stat_sig(path.with_name(path.name + "-wal"))
        return max(mtime, wal_mtime), size + wal_size
    except OSError:
        return mtime, size


def parse_agy_file(path: Path, workspaces: dict) -> dict:
    """Conversa do Antigravity CLI: SQLite com um protobuf por resposta em gen_metadata.

    Campos usados (mesmo ModelUsageStats do language server do Windsurf/Codeium):
      1.4  = uso: 2 input (sem cache), 3 output (inclui raciocínio), 4 cache gravado, 5 cache lido, 11 response id
      1.9.4.1 = horário da resposta (epoch s); 1.19 = id do modelo
    """
    recs = {}
    session = path.stem
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        meta = con.execute("select data from trajectory_metadata_blob").fetchone()
        rows = con.execute("select idx, data from gen_metadata order by idx").fetchall()
    finally:
        con.close()
    meta = _pb_fields(meta[0]) if meta and meta[0] else {}
    # respostas sem horário próprio herdam o da anterior; a primeira, o início da conversa
    last_ts = _pb_get(meta, 2, 1) or 0
    agy_project = (_pb_get(meta, 18) or b"").decode(errors="replace")
    project = project_name(workspaces.get(session), f"antigravity:{agy_project or '?'}")
    for idx, data in rows:
        gen = _pb_get(_pb_fields(data or b""), 1)
        if not isinstance(gen, bytes):
            continue
        gen = _pb_fields(gen)
        u = _pb_get(gen, 4)
        if not isinstance(u, bytes):
            continue
        u = _pb_fields(u)
        last_ts = _pb_get(gen, 9, 4, 1) or last_ts
        if not last_ts:
            continue
        num = {f: v if isinstance(v := _pb_get(u, f, default=0), int) else 0 for f in (2, 3, 4, 5)}
        model = (_pb_get(gen, 19) or _pb_get(gen, 21) or b"antigravity").decode(errors="replace")
        rid = (_pb_get(u, 11) or b"").decode(errors="replace") or f"{session}:{idx}"
        day = datetime.fromtimestamp(last_ts, timezone.utc).astimezone().strftime("%Y-%m-%d")
        recs[("antigravity", rid)] = record(day, "antigravity", model, project, session,
                                            num[2], num[3], num[5], num[4])
    return recs


def antigravity_records(seen: set) -> list[dict]:
    if not AGY_CONVERSATIONS.is_dir():
        return []
    workspaces = _agy_workspaces()
    return list(cached_records(AGY_CONVERSATIONS.glob("*.db"), lambda p: parse_agy_file(p, workspaces),
                               seen, sig=_agy_sig).values())


def copilot_records() -> list[dict]:
    if not COPILOT_DB.exists():
        return []
    # as_uri() gera file:///C:/... no Windows e escapa espaços e "?" no caminho
    con = sqlite3.connect(f"{COPILOT_DB.as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            """select u.created_at, u.model, coalesce(s.cwd, ''), u.session_id,
                      coalesce(u.input_tokens,0), coalesce(u.output_tokens,0),
                      coalesce(u.cache_read_tokens,0), coalesce(u.cache_write_tokens,0),
                      coalesce(u.request_multiplier,0), u.initiator
               from assistant_usage_events u left join sessions s on s.id = u.session_id"""
        ).fetchall()
    finally:
        con.close()
    # input_tokens do Copilot já inclui cache lido e gravado; separa para bater com o Claude
    # só turnos iniciados pelo usuário consomem premium request
    return [record(local_day(ts), "copilot", model, project_name(cwd, "?"), sess, inp - cr - cw, outp, cr, cw,
                   mult if initiator == "user" else 0.0)
            for ts, model, cwd, sess, inp, outp, cr, cw, mult, initiator in rows]


class RateLimited(Exception):
    """A API respondeu 429: não consulta de novo antes do horário combinado."""


# url -> (não consultar antes deste epoch, quantos 429 seguidos)
_backoff: dict[str, tuple[float, int]] = {}


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    """Segundos pedidos no Retry-After (número ou data HTTP), se houver."""
    v = e.headers.get("Retry-After") if e.headers else None
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    try:
        return (parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None


def _get_json(url: str, headers: dict, body: dict | None = None, context=None) -> dict:
    until, strikes = _backoff.get(url, (0.0, 0))
    if time.time() < until:
        raise RateLimited(f"API limitou as consultas (429); nova tentativa às {datetime.fromtimestamp(until):%H:%M}")
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"User-Agent": "token-usage-dashboard", "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if body is not None else {}),
                                          **headers})
    try:
        with urllib.request.urlopen(req, timeout=10, context=context) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        # insistir a cada LIMITS_TTL prolonga o bloqueio: dobra a espera a cada 429 seguido
        strikes += 1
        # Retry-After manda, mas com teto: uma data absurda não pode congelar a consulta
        wait = min(max(_retry_after(e) or 0, LIMITS_TTL * 2 ** strikes), BACKOFF_MAX)
        _backoff[url] = (time.time() + wait, strikes)
        raise RateLimited(f"API limitou as consultas (429); nova tentativa às "
                          f"{datetime.fromtimestamp(time.time() + wait):%H:%M}") from e
    _backoff.pop(url, None)
    return data


# o que fazer quando a credencial guardada pela ferramenta não vale mais
LOGIN_HINT = {
    "claude": "login do Claude Code vencido: abra o claude e rode /login",
    "copilot": "gh sem login válido: rode gh auth login",
    "codex": "login do Codex vencido: rode codex login",
}


def _api_err(source: str, e: Exception) -> str:
    code = getattr(e, "code", None)
    if code in (401, 403) or isinstance(e, subprocess.CalledProcessError):
        return f"{LOGIN_HINT[source]} ({_err(e)})"[:200]
    return _err(e)


def _err(e: Exception) -> str:
    # mensagens curtas; nunca incluem cabeçalhos (onde ficam os tokens)
    if isinstance(e, RateLimited):
        return str(e)[:200]
    return f"{type(e).__name__}: {e}"[:200]


def _claude_limits_from(util: dict) -> list[dict]:
    labels = {"session": "Sessão (5 h)", "weekly_all": "Semanal · todos os modelos",
              "weekly_opus": "Semanal · Opus", "weekly_sonnet": "Semanal · Sonnet"}
    out = []
    for lim in util.get("limits") or []:
        out.append({"label": labels.get(lim["kind"], lim["kind"]), "used_pct": lim.get("percent") or 0,
                    "resets_at": lim.get("resets_at"), "severity": lim.get("severity")})
    return out


_claude_last: tuple[float, dict] | None = None  # (fetched_at, resposta) da última leitura ao vivo


def claude_limits() -> dict:
    global _claude_last
    try:
        # o Claude Code renova o token e regrava o arquivo; relê a cada consulta
        token = json.loads(CLAUDE_CREDS.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
        util = _get_json("https://api.anthropic.com/api/oauth/usage",
                         {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        _claude_last = (time.time(), util)
        return {"live": True, "fetched_at": _claude_last[0], "limits": _claude_limits_from(util), "error": None}
    except Exception as e:  # noqa: BLE001
        err = _api_err("claude", e)
    # sem API: a leitura mais recente entre a última ao vivo e a que o próprio Claude Code guardou
    try:
        cached = json.loads(CLAUDE_STATE.read_text(encoding="utf-8")).get("cachedUsageUtilization") or {}
        fetched, util = (cached.get("fetchedAtMs") or 0) / 1000, cached.get("utilization") or {}
    except Exception as e:  # noqa: BLE001
        if not _claude_last:
            return {"live": False, "fetched_at": None, "limits": [], "error": f"{err}; cache: {_err(e)}"}
        fetched, util = 0, {}
    if _claude_last and _claude_last[0] > fetched:
        fetched, util = _claude_last
    return {"live": False, "fetched_at": fetched, "limits": _claude_limits_from(util), "error": err}


def _copilot_snapshot() -> dict | None:
    files = sorted(COPILOT_STATE.glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:10]:
        last = None
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"quotaSnapshots"' in line:
                    last = line
        if not last:
            continue
        d = json.loads(last)
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "quotaSnapshots" in o:
                    q = o["quotaSnapshots"].get("premium_interactions") or {}
                    ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                    return {"entitlement": q.get("entitlementRequests"), "used": q.get("usedRequests"),
                            "resets_at": q.get("resetDate"), "fetched_at": ts}
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    return None


def copilot_limits() -> dict:
    try:
        token = subprocess.run([GH, "auth", "token"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        d = _get_json("https://api.github.com/copilot_internal/user",
                      {"Authorization": f"token {token}", "X-GitHub-Api-Version": "2025-04-01"})
        q = d["quota_snapshots"]["premium_interactions"]
        ent = q.get("entitlement") or 0
        used = ent - (q.get("remaining") or 0)
        reset = d.get("quota_reset_date")
        snap = {"entitlement": ent, "used": used, "resets_at": f"{reset}T00:00:00Z" if reset else None,
                "fetched_at": time.time(), "unlimited": q.get("unlimited"), "plan": d.get("copilot_plan")}
        live, err = True, None
    except Exception as e:  # noqa: BLE001
        snap, live, err = _copilot_snapshot(), False, _api_err("copilot", e)
    if not snap:
        return {"live": False, "fetched_at": None, "limits": [], "error": err}
    ent, used = snap["entitlement"] or 0, snap["used"] or 0
    lim = {"label": "Premium requests (mês)", "used_pct": 0 if snap.get("unlimited") else (used / ent * 100 if ent else 100),
           "resets_at": snap["resets_at"], "used": used, "total": ent}
    return {"live": live, "fetched_at": snap["fetched_at"], "limits": [lim], "error": err, "plan": snap.get("plan")}


def _window_label(minutes) -> str:
    return {300: "Sessão (5 h)", 10080: "Semanal"}.get(minutes) or (f"Janela de {minutes / 60:g} h" if minutes else "Janela")


def _iso(epoch) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def _codex_snapshot() -> dict | None:
    """Último rate_limits que o Codex CLI gravou nos rollouts."""
    if not CODEX_SESSIONS.is_dir():
        return None
    files = sorted(CODEX_SESSIONS.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:10]:
        last = None
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"rate_limits"' in line:
                    last = line
        if not last:
            continue
        d = json.loads(last)
        rl = (d.get("payload") or {}).get("rate_limits") or {}
        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
        out = []
        for w in (rl.get("primary"), rl.get("secondary")):
            if not w:
                continue
            reset = w.get("resets_at") or (ts + w["resets_in_seconds"] if w.get("resets_in_seconds") is not None else None)
            out.append({"label": _window_label(w.get("window_minutes")), "used_pct": w.get("used_percent") or 0,
                        "resets_at": _iso(reset)})
        if out:
            return {"fetched_at": ts, "limits": out, "plan": rl.get("plan_type")}
    return None


def codex_limits() -> dict:
    try:
        # mesmo endpoint que o /status do Codex usa com login ChatGPT
        tok = json.loads(CODEX_AUTH.read_text(encoding="utf-8"))["tokens"]
        d = _get_json("https://chatgpt.com/backend-api/wham/usage",
                      {"Authorization": f"Bearer {tok['access_token']}", "ChatGPT-Account-Id": tok.get("account_id") or ""})
        rl = d.get("rate_limit") or {}
        out = []
        for w in (rl.get("primary_window"), rl.get("secondary_window")):
            if not w:
                continue
            secs = w.get("limit_window_seconds")
            reset = w.get("reset_at") or (time.time() + w["reset_after_seconds"] if w.get("reset_after_seconds") is not None else None)
            out.append({"label": _window_label(secs // 60 if secs else None), "used_pct": w.get("used_percent") or 0,
                        "resets_at": _iso(reset)})
        return {"live": True, "fetched_at": time.time(), "limits": out, "error": None, "plan": d.get("plan_type")}
    except Exception as e:  # noqa: BLE001
        err = _api_err("codex", e)
    snap = _codex_snapshot()
    if not snap:
        return {"live": False, "fetched_at": None, "limits": [], "error": err}
    return {"live": False, "error": err, **snap}


def _antigravity_server() -> tuple[int, str] | None:
    """PID e token CSRF do language server que o Antigravity sobe localmente."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            # processo de outro usuário: não é nosso Antigravity
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        cmd = b" ".join(args).decode(errors="replace")
        # o Windsurf usa o mesmo binário; filtra pelo caminho do Antigravity
        if "language_server" not in cmd or "antigravity" not in cmd.lower() or "--csrf_token" not in cmd:
            continue
        m = re.search(r"--csrf_token[ =](\S+)", cmd)
        if m:
            return int(proc.name), m.group(1)
    return None


def _listening_ports(pid: int) -> list[int]:
    out = subprocess.run(["ss", "-tlnpH"], capture_output=True, text=True, timeout=5).stdout
    ports = []
    for line in out.splitlines():
        if f"pid={pid}," in line:
            m = re.search(r"(?:127\.0\.0\.1|\[::1\]|\*|0\.0\.0\.0):(\d+)\s", line)
            if m:
                ports.append(int(m.group(1)))
    return ports


_agy_cache: tuple[float, dict] | None = None


def _agy_limits() -> dict:
    """Cota pelo `agy -p /usage --output-format json`: comando local, não gasta tokens.
    O agy guarda o token CSRF do language server só em memória, então não dá para ir direto nele."""
    global _agy_cache
    if _agy_cache and time.time() - _agy_cache[0] < AGY_TTL:
        return _agy_cache[1]
    try:
        out = subprocess.run([AGY, "-p", "/usage", "--output-format", "json"], capture_output=True, text=True,
                             timeout=60, check=True, stdin=subprocess.DEVNULL).stdout
        cmd = json.loads(out).get("command") or {}
        labels = {"weekly": "semanal", "5h": "sessão (5 h)"}
        lims = [{"label": f"{g.get('name') or '?'} · {labels.get(b.get('window'), b.get('name') or '?')}",
                 "used_pct": (1 - (b.get("remaining_fraction") or 0)) * 100, "resets_at": b.get("reset_time")}
                for g in (cmd.get("data") or {}).get("groups") or [] for b in g.get("buckets") or []]
        res = {"live": True, "fetched_at": time.time(), "limits": lims, "error": None if lims else "agy /usage sem cotas"}
    except Exception as e:  # noqa: BLE001
        res = {"live": False, "fetched_at": None, "limits": [], "error": f"agy /usage: {_err(e)}"}
    _agy_cache = (time.time(), res)
    return res


def antigravity_limits() -> dict:
    if not sys.platform.startswith("linux"):
        if os.access(AGY, os.X_OK):
            return _agy_limits()
        # a descoberta do language server usa /proc e ss
        return {"live": False, "fetched_at": None, "limits": [], "error": "cota do Antigravity só é lida no Linux"}
    srv = _antigravity_server()
    if not srv:
        if os.access(AGY, os.X_OK):
            return _agy_limits()
        return {"live": False, "fetched_at": None, "limits": [], "error": "Antigravity não está aberto"}
    pid, csrf = srv
    # o language server usa certificado autoassinado; a conexão nunca sai de 127.0.0.1
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    err = "nenhuma porta do language server respondeu"
    for port in _listening_ports(pid):
        for scheme, ctx in (("https", insecure), ("http", None)):
            try:
                d = _get_json(f"{scheme}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus",
                              {"X-Codeium-Csrf-Token": csrf, "Connect-Protocol-Version": "1"},
                              {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}, ctx)
            except Exception as e:  # noqa: BLE001
                err = _err(e)
                continue
            us = d.get("userStatus") or {}
            out = []
            for c in (us.get("cascadeModelConfigData") or {}).get("clientModelConfigs") or []:
                q = c.get("quotaInfo")
                if not q:
                    continue
                out.append({"label": c.get("label") or "?", "used_pct": (1 - (q.get("remainingFraction") or 0)) * 100,
                            "resets_at": q.get("resetTime")})
            plan = ((us.get("planStatus") or {}).get("planInfo") or {}).get("planName")
            return {"live": True, "fetched_at": time.time(), "limits": out, "error": None, "plan": plan}
    return {"live": False, "fetched_at": None, "limits": [], "error": err}


def _antigravity_app_installed() -> bool:
    return ANTIGRAVITY_DIR.is_dir() or (HOME / ".config" / "Antigravity").is_dir()


def installed() -> list[str]:
    """Ferramentas com dados nesta máquina; a interface só mostra estas."""
    found = {
        "claude": CLAUDE_DIR.is_dir() or CLAUDE_CREDS.exists(),
        "copilot": COPILOT_DB.exists() or COPILOT_STATE.is_dir(),
        "codex": CODEX_SESSIONS.is_dir() or CODEX_AUTH.exists(),
        # ~/.gemini também é criado pelo Antigravity; só conta com chats do Gemini CLI
        "gemini": GEMINI_TMP.is_dir() and bool(_gemini_chats()),
        "antigravity": _antigravity_app_installed() or AGY_DIR.is_dir(),
    }
    return [k for k, v in found.items() if v]


def _mark_stale(res: dict) -> dict:
    """Cota de cache cuja janela já renovou: o uso atual é desconhecido, não zero."""
    if not res.get("live"):
        now = time.time()
        for lim in res.get("limits") or []:
            try:
                reset = datetime.fromisoformat(lim["resets_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
            if reset < now:
                lim["stale"] = True
    return res


def _safe(fn) -> dict:
    # uma ferramenta com formato inesperado não pode derrubar as outras
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"erro em {fn.__name__}: {e!r}", file=sys.stderr, flush=True)
        return {"live": False, "fetched_at": None, "limits": [], "error": _err(e)}


def _epoch(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def _slope(pts: list[tuple[float, float]]) -> float:
    """Inclinação por mínimos quadrados: pontos percentuais por segundo."""
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mu = sum(u for _, u in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (u - mu) for t, u in pts) / den if den else 0.0


# "fonte|rótulo" -> leituras (quando, % usado, renovação em epoch); só em memória, refaz em ~10 min
_samples: dict[str, deque] = {}


def _forecast(source: str, res: dict) -> dict:
    """Guarda a leitura de cada cota e, havendo ritmo de consumo, projeta quando ela chega a 100%.

    Acrescenta rate_pct_h (ritmo da última hora) e, se a cota acabar antes de renovar, exhausts_at.
    """
    t, now = res.get("fetched_at"), time.time()
    for lim in res.get("limits") or []:
        # fontes que reaproveitam o resultado entre consultas não podem herdar previsão velha
        lim.pop("rate_pct_h", None)
        lim.pop("exhausts_at", None)
        reset, used = _epoch(lim.get("resets_at")), lim.get("used_pct")
        # cache de uma janela que já renovou não diz nada sobre o ritmo atual
        if not t or reset is None or reset < now or used is None:
            continue
        s = _samples.setdefault(f"{source}|{lim['label']}", deque(maxlen=240))
        # janela nova: a renovação mudou de lugar ou o uso caiu
        if s and (abs(s[-1][2] - reset) > 600 or used < s[-1][1] - 1):
            s.clear()
        if not s or t > s[-1][0]:
            s.append((t, used, reset))
        pts = [(ts, u) for ts, u, _ in s if ts >= t - FORECAST_LOOKBACK]
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < FORECAST_SPAN or pts[-1][1] - pts[0][1] < FORECAST_MIN_DELTA:
            continue
        rate = _slope(pts)
        if rate <= 0:
            continue
        lim["rate_pct_h"] = round(rate * 3600, 1)
        if used < 100 and (eta := t + (100 - used) / rate) < reset:
            lim["exhausts_at"] = _iso(eta)
    return res


LIMIT_SOURCES = {"claude": claude_limits, "copilot": copilot_limits, "codex": codex_limits, "antigravity": antigravity_limits}
_limits_lock = threading.Lock()
_limits_cache: tuple[float, dict] | None = None


def limits(force: bool = False) -> dict:
    global _limits_cache
    with _limits_lock:
        age = time.time() - _limits_cache[0] if _limits_cache else None
        if age is None or age > LIMITS_TTL or (force and age > LIMITS_MIN):
            # não consulta APIs de ferramentas que o usuário não usa
            _limits_cache = (time.time(), {k: _forecast(k, _mark_stale(_safe(LIMIT_SOURCES[k])))
                                           for k in installed() if k in LIMIT_SOURCES})
        return _limits_cache[1]


def aggregate() -> dict:
    agg = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                               "requests": 0, "premium": 0.0, "sessions": set()})
    seen: set[str] = set()
    # ThreadingHTTPServer: requisições simultâneas não podem mexer no cache ao mesmo tempo
    with _records_lock:
        recs = (claude_records(seen) + copilot_records() + codex_records(seen) + gemini_records(seen)
                + antigravity_records(seen))
        for p in set(_file_cache) - seen:
            del _file_cache[p]
    for r in recs:
        a = agg[(r["day"], r["source"], r["model"], r["project"])]
        for k in ("input", "output", "cache_read", "cache_write", "requests", "premium"):
            a[k] += r[k]
        a["sessions"].add(r["session"])
    rows = []
    for (day, source, model, project), a in agg.items():
        a["sessions"] = sorted(a["sessions"])
        rows.append({"day": day, "source": source, "model": model, "project": project, **a})
    rows.sort(key=lambda r: r["day"])
    return {"generated": datetime.now().astimezone().isoformat(timespec="seconds"), "sources": installed(), "rows": rows}


STATIC = {"/app.js": "app.js", "/vendor/chart.umd.min.js": "vendor/chart.umd.min.js"}


class Handler(BaseHTTPRequestHandler):
    server_version = "token-usage"
    sys_version = ""

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self._send(421, b"host not allowed", "text/plain")
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path in STATIC:
            self._send(200, (HERE / STATIC[url.path]).read_bytes(), "text/javascript; charset=utf-8")
        elif url.path == "/api/usage":
            try:
                self._send(200, json.dumps(aggregate()).encode(), "application/json")
            except Exception as e:  # noqa: BLE001
                print(f"erro em /api/usage: {e!r}", file=sys.stderr, flush=True)
                self._send(500, json.dumps({"error": _err(e)}).encode(), "application/json")
        elif url.path == "/api/limits":
            body = json.dumps(limits(force="force" in url.query)).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main(open_browser: bool = False):
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Token usage em {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    srv.serve_forever()
