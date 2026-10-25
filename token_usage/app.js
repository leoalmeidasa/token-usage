const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const C = { claude: css('--claude'), copilot: css('--copilot'), codex: css('--codex'), gemini: css('--gemini'),
            antigravity: css('--antigravity'), input: css('--input'), output: css('--output'),
            cache_read: css('--cread'), cache_write: css('--cwrite'), text: css('--text'), muted: css('--muted'), line: css('--line') };
const LABEL = { claude: 'Claude Code', copilot: 'Copilot CLI', codex: 'Codex CLI', gemini: 'Gemini CLI',
                antigravity: 'Antigravity', input: 'Input', output: 'Output',
                cache_read: 'Cache lido', cache_write: 'Cache gravado', total: 'Total' };
const KEYS = ['input', 'output', 'cache_read', 'cache_write'];
// ferramentas que gravam tokens em disco; do Antigravity, só o CLI (agy) grava
const TOKEN_SOURCES = ['claude', 'copilot', 'codex', 'gemini', 'antigravity'];
const DATA_PATHS = { claude: '~/.claude/projects', copilot: '~/.copilot/session-store.db',
                     codex: '~/.codex/sessions', gemini: '~/.gemini/tmp/*/chats',
                     antigravity: '~/.gemini/antigravity-cli/conversations' };
// nomes de projeto, modelos e mensagens de erro vêm de disco/rede: nunca entram crus no HTML
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const state = { range: 30, source: 'all', metric: 'total', data: null, sort: {} };
// o app Antigravity aparece em sources só pela cota; filtra pelas fontes que têm linhas
const tokenSources = () => TOKEN_SOURCES.filter(s => state.data.sources.includes(s) && state.data.rows.some(r => r.source === s));
let daily, mix;

Chart.defaults.color = C.muted;
Chart.defaults.borderColor = C.line;
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

const fmt = n => {
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + ' B';
  if (a >= 1e6) return (n / 1e6).toFixed(1) + ' M';
  if (a >= 1e3) return (n / 1e3).toFixed(1) + ' k';
  return Math.round(n).toLocaleString('pt-BR');
};
const full = n => Math.round(n).toLocaleString('pt-BR');
const tot = r => KEYS.reduce((s, k) => s + r[k], 0);
const isoDay = d => d.toLocaleDateString('sv-SE');

function filtered() {
  let rows = state.data.rows;
  if (state.source !== 'all') rows = rows.filter(r => r.source === state.source);
  if (state.range > 0) {
    const d = new Date(); d.setDate(d.getDate() - (state.range - 1));
    const from = isoDay(d);
    rows = rows.filter(r => r.day >= from);
  }
  return rows;
}

function group(rows, keyFn) {
  const m = new Map();
  for (const r of rows) {
    const k = keyFn(r);
    let g = m.get(k);
    if (!g) m.set(k, g = { key: k, input: 0, output: 0, cache_read: 0, cache_write: 0, requests: 0, premium: 0, sessions: new Set(), sources: new Set() });
    for (const f of [...KEYS, 'requests', 'premium']) g[f] += r[f];
    r.sessions.forEach(s => g.sessions.add(s));
    g.sources.add(r.source);
  }
  return [...m.values()].map(g => ({ ...g, total: tot(g), sessions: g.sessions.size }));
}

function kpis(rows) {
  const g = group(rows, () => 'all')[0] || { input: 0, output: 0, cache_read: 0, cache_write: 0, requests: 0, premium: 0, sessions: 0, total: 0 };
  // premium requests do mês corrente (independe do filtro de período)
  const month = isoDay(new Date()).slice(0, 7);
  const prem = state.data.rows.filter(r => r.source === 'copilot' && r.day.startsWith(month)).reduce((s, r) => s + r.premium, 0);
  const hit = g.cache_read / Math.max(g.input + g.cache_read + g.cache_write, 1);
  const tiles = [
    ['Total processado', fmt(g.total), full(g.total) + ' tokens'],
    ['Input novo', fmt(g.input), full(g.input)],
    ['Output', fmt(g.output), full(g.output)],
    ['Cache lido', fmt(g.cache_read), (hit * 100).toFixed(1) + '% do input veio do cache'],
    ['Cache gravado', fmt(g.cache_write), full(g.cache_write)],
    ['Requisições', full(g.requests), g.sessions + ' sessões'],
  ];
  if (state.data.sources.includes('copilot'))
    tiles.push(['Premium Copilot (mês)', prem.toLocaleString('pt-BR', { maximumFractionDigits: 1 }), 'só pelo Copilot CLI; a cota inclui IDE e web']);
  document.getElementById('kpis').innerHTML = tiles.map(([l, v, s]) =>
    `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
}

function dayList(rows) {
  let from;
  if (state.range > 0) { from = new Date(); from.setDate(from.getDate() - (state.range - 1)); }
  else if (rows.length) from = new Date(rows[0].day + 'T12:00');
  else return [];
  const out = [], d = new Date(from), end = isoDay(new Date());
  while (isoDay(d) <= end) { out.push(isoDay(d)); d.setDate(d.getDate() + 1); }
  return out;
}

function drawDaily(rows) {
  const days = dayList(rows);
  const val = r => state.metric === 'total' ? tot(r) : r[state.metric];
  const sources = state.source === 'all' ? tokenSources() : [state.source];
  const datasets = sources.map(s => {
    const by = new Map();
    rows.filter(r => r.source === s).forEach(r => by.set(r.day, (by.get(r.day) || 0) + val(r)));
    return { label: LABEL[s], data: days.map(d => by.get(d) || 0), backgroundColor: C[s], borderRadius: 3, maxBarThickness: 28 };
  });
  const labels = days.map(d => d.slice(8, 10) + '/' + d.slice(5, 7));
  if (!daily) {
    daily = new Chart(document.getElementById('daily'), {
      type: 'bar', data: { labels, datasets },
      options: { maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, ticks: { callback: fmt } } },
        plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${full(c.raw)}` } },
                   legend: { labels: { boxWidth: 10 } } } } });
  } else { daily.data = { labels, datasets }; daily.update(); }
}

function drawMix(rows) {
  const g = group(rows, () => 'all')[0];
  const data = KEYS.map(k => g ? g[k] : 0);
  const cfg = { labels: KEYS.map(k => LABEL[k]), datasets: [{ data, backgroundColor: KEYS.map(k => C[k]), borderColor: css('--panel'), borderWidth: 2 }] };
  if (!mix) {
    mix = new Chart(document.getElementById('mix'), { type: 'doughnut', data: cfg,
      options: { maintainAspectRatio: false, animation: false, cutout: '60%',
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 10 } },
                   tooltip: { callbacks: { label: c => {
                     const s = c.dataset.data.reduce((a, b) => a + b, 0) || 1;
                     return `${c.label}: ${full(c.raw)} (${(c.raw / s * 100).toFixed(1)}%)`; } } } } } });
  } else { mix.data = cfg; mix.update(); }
}

function table(id, rows, cols, defaultSort) {
  const st = state.sort[id] || (state.sort[id] = { k: defaultSort, dir: -1 });
  rows.sort((a, b) => (typeof a[st.k] === 'string' ? a[st.k].localeCompare(b[st.k]) : a[st.k] - b[st.k]) * st.dir);
  const el = document.getElementById(id);
  el.innerHTML = '<thead><tr>' + cols.map(c => `<th data-k="${c.k}">${c.l}${st.k === c.k ? (st.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('') +
    '</tr></thead><tbody>' + rows.map(r => '<tr>' + cols.map(c => `<td title="${c.num ? full(r[c.k]) : ''}">${c.f ? c.f(r) : c.num ? fmt(r[c.k]) : esc(r[c.k])}</td>`).join('') + '</tr>').join('') +
    (rows.length ? '' : `<tr><td colspan="${cols.length}" style="color:var(--muted)">Sem dados no período</td></tr>`) + '</tbody>';
  el.querySelectorAll('th').forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    state.sort[id] = { k, dir: st.k === k ? -st.dir : -1 };
    render();
  });
}

const dots = r => [...r.sources].map(s => `<span class="tag" style="background:${C[s]}" title="${LABEL[s]}"></span>`).join('');
const numCols = [
  { k: 'total', l: 'Total', num: 1 }, { k: 'input', l: 'Input', num: 1 }, { k: 'output', l: 'Output', num: 1 },
  { k: 'cache_read', l: 'Cache lido', num: 1 }, { k: 'cache_write', l: 'Cache grav.', num: 1 }, { k: 'requests', l: 'Req.', num: 1 },
];

function render() {
  const rows = filtered();
  kpis(rows);
  drawDaily(rows);
  drawMix(rows);
  table('models', group(rows, r => r.model), [{ k: 'key', l: 'Modelo', f: r => dots(r) + esc(r.key) }, ...numCols], 'total');
  table('sources', group(rows, r => r.source).map(g => ({ ...g, key: LABEL[g.key] })),
    [{ k: 'key', l: 'Ferramenta' }, { k: 'total', l: 'Total', num: 1 }, { k: 'output', l: 'Output', num: 1 },
     { k: 'sessions', l: 'Sessões', num: 1 },
     ...(state.data.sources.includes('copilot') ? [{ k: 'premium', l: 'Premium', f: r => r.premium ? r.premium.toFixed(1) : '—' }] : [])], 'total');
  table('projects', group(rows, r => r.project), [{ k: 'key', l: 'Projeto', f: r => dots(r) + esc(r.key) }, ...numCols, { k: 'sessions', l: 'Sessões', num: 1 }], 'total');
}

async function load() {
  const foot = document.getElementById('foot');
  foot.textContent = 'Carregando…';
  try {
    const res = await fetch('/api/usage');
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.status);
    state.data = j;
    renderSourceFilter();
    render();
    const paths = tokenSources().map(s => DATA_PATHS[s]);
    foot.textContent = `Atualizado ${new Date(j.generated).toLocaleString('pt-BR')} · ` +
      (paths.length ? `fontes: ${paths.join(', ')}` : 'nenhuma ferramenta com histórico de tokens encontrada');
  } catch (e) {
    foot.innerHTML = `<span class="err">Erro ao carregar: ${esc(e.message)}</span>`;
  }
}

// botões só para as ferramentas encontradas; com uma só, o filtro não faz sentido
function renderSourceFilter() {
  const el = document.getElementById('source');
  const srcs = tokenSources();
  if (!srcs.includes(state.source)) state.source = 'all';
  const key = srcs.join();
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.hidden = srcs.length < 2;
  el.innerHTML = ['all', ...srcs].map(s =>
    `<button data-v="${s}" class="${s === state.source ? 'on' : ''}">${s === 'all' ? 'Todos' : LABEL[s]}</button>`).join('');
}

for (const id of ['range', 'source', 'metric']) {
  document.getElementById(id).addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    e.currentTarget.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    state[id] = id === 'range' ? +b.dataset.v : b.dataset.v;
    if (state.data) render();
  });
}
// ---- cotas: porcentagem restante ao vivo ----
let limitsData = null;
const ago = s => s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
function until(iso) {
  if (!iso) return '—';
  const s = (new Date(iso) - Date.now()) / 1000;
  if (s <= 0) return 'janela já renovou';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  const when = new Date(iso).toLocaleString('pt-BR', { weekday: d ? 'short' : undefined, hour: '2-digit', minute: '2-digit', day: d > 6 ? '2-digit' : undefined, month: d > 6 ? '2-digit' : undefined });
  return `renova em ${d ? d + ' d ' : ''}${h} h ${String(m).padStart(2, '0')} min (${when})`;
}
// previsão do servidor: só aparece quando há ritmo de consumo suficiente na última hora
function forecast(lim) {
  if (lim.exhausts_at) {
    const at = new Date(lim.exhausts_at);
    if (at > Date.now()) {
      const hm = at.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
      return `<div class="meta err"><span>nesse ritmo acaba às ${hm} (em ${ago((at - Date.now()) / 1000)}), antes de renovar</span></div>`;
    }
  }
  if (lim.rate_pct_h) return `<div class="meta"><span>ritmo ${lim.rate_pct_h.toLocaleString('pt-BR')}%/h · não acaba antes de renovar</span></div>`;
  return '';
}
const level = left => left > 50 ? css('--cwrite') : left > 20 ? '#eed49f' : '#ee99a0';

function renderLimits() {
  if (!limitsData) return;
  const cards = [];
  for (const [src, L] of Object.entries(limitsData)) {
    const age = L.fetched_at ? (Date.now() / 1000 - L.fetched_at) : null;
    const status = L.live ? `<span class="live" style="background:${css('--cwrite')}"></span>ao vivo · há ${ago(age)}`
      : `<span class="live" style="background:#eed49f"></span>cache local${age != null ? ' · há ' + ago(age) : ''}`;
    if (!L.limits.length) {
      cards.push(`<div class="lim"><div class="src"><span class="tag" style="background:${C[src]}"></span>${LABEL[src]}</div>
        <div class="err">Sem dados de cota${L.error ? ': ' + esc(L.error) : ''}</div></div>`);
      continue;
    }
    L.limits.forEach((lim, i) => {
      // ao vivo, janela que acabou de renovar tem uso 0; no cache, o uso atual é desconhecido
      const expired = lim.resets_at && new Date(lim.resets_at) < Date.now();
      // o aviso de login/erro vai só no primeiro cartão da ferramenta
      const warn = !L.live && L.error && i === 0 ? `<div class="meta err">${esc(L.error)}</div>` : '';
      if (lim.stale || (expired && !L.live)) {
        cards.push(`<div class="lim" title="${L.error ? 'API: ' + esc(L.error) : ''}">
          <div class="src"><span class="tag" style="background:${C[src]}"></span>${LABEL[src]}</div>
          <div class="top"><span class="name">${esc(lim.label)}</span><span class="pct">—<small> sem dado atual</small></span></div>
          <div class="bar"></div>
          <div class="meta"><span>a janela renovou depois da última leitura</span></div>
          <div class="meta"><span>${status}</span></div>${warn}</div>`);
        return;
      }
      const used = expired ? 0 : Math.min(Math.max(+lim.used_pct || 0, 0), 100);
      const left = 100 - used;
      const count = lim.total ? `${Math.max(lim.total - lim.used, 0).toLocaleString('pt-BR')} de ${lim.total.toLocaleString('pt-BR')} restantes${lim.used > lim.total ? ` · ${lim.used - lim.total} acima` : ''}` : `${used.toFixed(0)}% usado`;
      cards.push(`<div class="lim" title="${L.error ? 'API: ' + esc(L.error) : ''}">
        <div class="src"><span class="tag" style="background:${C[src]}"></span>${LABEL[src]}</div>
        <div class="top"><span class="name">${esc(lim.label)}</span><span class="pct">${left.toFixed(left < 10 && left > 0 ? 1 : 0)}%<small> restante</small></span></div>
        <div class="bar"><i style="width:${left}%;background:${level(left)}"></i></div>
        <div class="meta"><span>${count}</span><span>${until(lim.resets_at)}</span></div>${expired ? '' : forecast(lim)}
        <div class="meta"><span>${status}</span></div>${warn}</div>`);
    });
  }
  document.getElementById('limits').innerHTML = cards.join('');
  document.getElementById('limits-title').hidden = !cards.length;
}

async function loadLimits(force) {
  try {
    const res = await fetch('/api/limits' + (force ? '?force=1' : ''));
    limitsData = await res.json();
    renderLimits();
  } catch (e) {
    document.getElementById('limits').innerHTML = `<div class="lim err">Erro ao ler cotas: ${esc(e.message)}</div>`;
  }
}

document.getElementById('reload').onclick = () => { load(); loadLimits(true); };
setInterval(load, 60000);
setInterval(loadLimits, 30000);
setInterval(renderLimits, 1000);
load();
loadLimits();
