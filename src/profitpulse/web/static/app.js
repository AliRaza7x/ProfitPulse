'use strict';
/* ProfitPulse front end: no build step, no dependencies. Charts are hand-built SVG. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = $('#view');
let CUR = 'PKR';

/* ---------------------------------------------------------------- formatting */
const fmt = {
  money(n, cur = CUR) {
    if (n == null || isNaN(n)) return 'n/a';
    const a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e9) return `${s}${cur} ${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${s}${cur} ${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${s}${cur} ${(a / 1e3).toFixed(0)}K`;
    return `${s}${cur} ${a.toFixed(0)}`;
  },
  full: n => n == null ? 'n/a' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 }),
  pct: (x, d = 1) => x == null ? 'n/a' : `${(x * 100).toFixed(d)}%`,
  spct: (x, d = 1) => x == null ? 'n/a' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`,
  num: (x, d = 1) => x == null ? 'n/a' : Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }),
  axis(v) { const a = Math.abs(v); return a >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : v.toFixed(0); },
  month(ym) { const [y, m] = ym.split('-'); return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m - 1]} ${y.slice(2)}`; },
  date(iso) { const d = new Date(iso); return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); },
};
const METRIC = {
  discount_rate: { label: 'Discount rate', f: v => fmt.pct(v) },
  return_rate_units: { label: 'Return rate', f: v => fmt.pct(v) },
  adjustment_avg_qty: { label: 'Average stock adjustment', f: v => `${v.toFixed(1)} units` },
  damage_rate: { label: 'Damage rate', f: v => fmt.pct(v) },
  gross_margin: { label: 'Gross margin', f: v => fmt.pct(v) },
  ppv_pct: { label: 'Cost vs standard', f: v => fmt.spct(v) },
};
const mf = (metric, v) => (METRIC[metric]?.f ?? (x => fmt.num(x, 2)))(v);
const TYPE = {
  EXCESS_DISCOUNT: 'Excess discounting', EXCESS_RETURNS: 'Excess returns', INVENTORY_DISCREPANCY: 'Inventory discrepancy',
  PROCUREMENT_OVERPAYMENT: 'Procurement overpayment', LOW_MARGIN_SHORTFALL: 'Low-margin pricing opportunity',
};
const TYPE_COLOR = { EXCESS_DISCOUNT: '#CF2E5E', EXCESS_RETURNS: '#D99A0B', INVENTORY_DISCREPANCY: '#16232C', PROCUREMENT_OVERPAYMENT: '#0F7B6C', LOW_MARGIN_SHORTFALL: '#9DB0A8' };
const INK = '#16232C', TEAL = '#0F7B6C', RASP = '#CF2E5E', AMBER = '#D99A0B', GRID = '#C9D2CD', MUTED = '#56666E';

async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* keep default */ }
    throw new Error(msg);
  }
  return r.json();
}

/* ---------------------------------------------------------------- tooltip */
const tip = $('#tip');
document.addEventListener('pointermove', e => {
  const t = e.target.closest?.('[data-tip]');
  if (!t) { tip.classList.remove('on'); return; }
  tip.innerHTML = t.dataset.tip;
  tip.classList.add('on');
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > innerHeight - 8) y = e.clientY - h - pad;
  tip.style.left = `${x}px`; tip.style.top = `${y}px`;
});
document.addEventListener('pointerleave', () => tip.classList.remove('on'));

/* ---------------------------------------------------------------- charts */
/** Every peer as a dot on one line; the flagged entity in raspberry; the peer median as a tick. */
function peerStrip({ points, focus, median, metric, caption }) {
  if (!points?.length) return '';
  const W = 680, H = 84, pad = 30, cy = 40;
  const vals = points.map(p => p.value);
  let lo = Math.min(...vals, median ?? Infinity), hi = Math.max(...vals);
  const span = (hi - lo) || 1; lo -= span * 0.03; hi += span * 0.03;
  const x = v => pad + (W - 2 * pad) * ((v - lo) / (hi - lo));
  const dense = points.length > 120;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(caption || 'Peer comparison')}">
    <line x1="${pad}" x2="${W - pad}" y1="${cy}" y2="${cy}" stroke="${GRID}" stroke-width="1.5"/>`;
  if (median != null) svg += `<line x1="${x(median)}" x2="${x(median)}" y1="${cy - 15}" y2="${cy + 15}" stroke="${INK}" stroke-width="2"/>
    <text x="${x(median)}" y="${cy + 33}" text-anchor="middle" font-size="14" fill="${MUTED}">peer median ${esc(mf(metric, median))}</text>`;
  const ordered = [...points].sort((a, b) => (a.id === focus) - (b.id === focus));
  for (const p of ordered) {
    const f = p.id === focus;
    svg += `<circle cx="${x(p.value).toFixed(1)}" cy="${cy}" r="${f ? 8 : dense ? 2.8 : 4.6}" fill="${f ? RASP : TEAL}" fill-opacity="${f ? 1 : dense ? 0.35 : 0.6}"
      data-tip="<b>${esc(p.id)}</b> ${esc(mf(metric, p.value))}"/>`;
  }
  const fp = points.find(p => p.id === focus);
  if (fp) svg += `<text x="${Math.min(Math.max(x(fp.value), 60), W - 60)}" y="${cy - 17}" text-anchor="middle" font-size="15.5" font-weight="700" fill="${RASP}">${esc(fp.id)} ${esc(mf(metric, fp.value))}</text>`;
  svg += '</svg>';
  return `<figure class="strip">${svg}<figcaption>${esc(caption || `All ${points.length} peers compared. The flagged one is in red.`)}</figcaption></figure>`;
}

function lineChart({ labels, series, height = 260, width = 840, yFmt = fmt.axis, zero = true, tipFmt }) {
  const pl = 60, pr = 16, pt = 14, pb = 32, n = labels.length;
  const all = series.flatMap(s => s.values.filter(v => v != null));
  if (!all.length) return '';
  let lo = zero ? Math.min(0, ...all) : Math.min(...all), hi = Math.max(...all);
  const padY = (hi - lo) * 0.08 || 1; hi += padY; if (!zero) lo -= padY;
  const x = i => pl + (width - pl - pr) * (n === 1 ? 0.5 : i / (n - 1));
  const y = v => pt + (height - pt - pb) * (1 - (v - lo) / (hi - lo));
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4;
    g += `<line x1="${pl}" x2="${width - pr}" y1="${y(v)}" y2="${y(v)}" stroke="${GRID}" stroke-width="1"/><text x="${pl - 8}" y="${y(v) + 4}" text-anchor="end" font-size="12.5" fill="${MUTED}">${yFmt(v)}</text>`;
  }
  const step = Math.ceil(n / 8);
  labels.forEach((l, i) => { if (i % step === 0) g += `<text x="${x(i)}" y="${height - 9}" text-anchor="middle" font-size="12.5" fill="${MUTED}">${esc(fmt.month(l))}</text>`; });
  for (const s of series) {
    const d = s.values.map((v, i) => v == null ? '' : `${i && s.values[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2.6}" stroke-linejoin="round" stroke-linecap="round" ${s.dash ? `stroke-dasharray="${s.dash}"` : ''}/>`;
  }
  const colW = (width - pl - pr) / Math.max(n - 1, 1);
  labels.forEach((l, i) => {
    const rows = series.filter(s => s.values[i] != null).map(s => `${esc(s.name)} <b>${esc((tipFmt || yFmt)(s.values[i]))}</b>`).join('<br>');
    g += `<g class="pt"><rect class="hit" x="${x(i) - colW / 2}" y="${pt}" width="${colW}" height="${height - pt - pb}" data-tip="${esc(fmt.month(l))}<br>${rows.replace(/"/g, '&quot;')}"/>` +
      series.map(s => s.values[i] == null ? '' : `<circle cx="${x(i)}" cy="${y(s.values[i])}" r="4.5" fill="${s.color}" stroke="#fff" stroke-width="1.5"/>`).join('') + '</g>';
  });
  const legend = series.length > 1 ? `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  return `<div class="chart">${legend}<svg viewBox="0 0 ${width} ${height}" role="img">${g}</svg></div>`;
}

function hbars(items, { width = 820, rowH = 34, labelW = 150, valFmt = fmt.axis } = {}) {
  if (!items.length) return '';
  const top = Math.max(...items.map(i => i.value), 1e-9), H = rowH * items.length + 6;
  let g = '';
  items.forEach((it, i) => {
    const yy = 3 + i * rowH, w = Math.max((width - labelW - 110) * (it.value / top), 2);
    g += `<g ${it.tip ? `data-tip="${esc(it.tip)}"` : ''}><text x="${labelW - 12}" y="${yy + rowH / 2 + 5}" text-anchor="end" font-size="14.5" fill="${INK}" font-weight="${it.bold ? 700 : 500}">${esc(it.label)}</text>
      <rect x="${labelW}" y="${yy + 6}" width="${w}" height="${rowH - 14}" rx="2" fill="${it.color || TEAL}"/>
      <text x="${labelW + w + 8}" y="${yy + rowH / 2 + 5}" font-size="13.5" fill="${MUTED}">${esc(valFmt(it.value))}</text></g>`;
  });
  return `<div class="chart"><svg viewBox="0 0 ${width} ${H}" role="img">${g}</svg></div>`;
}

function scatter({ points, xFmt, yFmt, xLabel, yLabel, width = 840, height = 380 }) {
  const pl = 64, pr = 24, pt = 20, pb = 54;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const xr = (Math.max(...xs) - Math.min(...xs)) || 1, yr = (Math.max(...ys) - Math.min(...ys)) || 1;
  const x0 = Math.min(...xs) - xr * 0.1, x1 = Math.max(...xs) + xr * 0.1, y0 = Math.min(...ys) - yr * 0.12, y1 = Math.max(...ys) + yr * 0.12;
  const X = v => pl + (width - pl - pr) * ((v - x0) / (x1 - x0)), Y = v => pt + (height - pt - pb) * (1 - (v - y0) / (y1 - y0));
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const vy = y0 + (y1 - y0) * i / 4, vx = x0 + (x1 - x0) * i / 4;
    g += `<line x1="${pl}" x2="${width - pr}" y1="${Y(vy)}" y2="${Y(vy)}" stroke="${GRID}"/><text x="${pl - 8}" y="${Y(vy) + 4}" text-anchor="end" font-size="12.5" fill="${MUTED}">${yFmt(vy)}</text>
      <text x="${X(vx)}" y="${height - pb + 20}" text-anchor="middle" font-size="12.5" fill="${MUTED}">${xFmt(vx)}</text>`;
  }
  g += `<text x="${(pl + width - pr) / 2}" y="${height - 8}" text-anchor="middle" font-size="13.5" fill="${INK}" font-weight="600">${esc(xLabel)}</text>
    <text transform="translate(16 ${(pt + height - pb) / 2}) rotate(-90)" text-anchor="middle" font-size="13.5" fill="${INK}" font-weight="600">${esc(yLabel)}</text>`;
  for (const p of points) {
    g += `<g data-tip="${esc(p.tip)}"><circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.r}" fill="${p.color}" fill-opacity="${p.hot ? 0.9 : 0.55}" stroke="#fff" stroke-width="1.5"/>
      ${p.hot ? `<text x="${X(p.x) + p.r + 5}" y="${Y(p.y) + 4.5}" font-size="13.5" font-weight="700" fill="${RASP}">${esc(p.label)}</text>` : ''}</g>`;
  }
  return `<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img">${g}</svg></div>`;
}

function tape(segments, cur = CUR) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  return `<div class="tape" role="img" aria-label="Share of exposure by type">${segments.map(s => `<i style="flex:${s.value};background:${s.color}" data-tip="<b>${esc(s.label)}</b><br>${esc(fmt.money(s.value, cur))} (${(100 * s.value / total).toFixed(0)}%)"></i>`).join('')}</div>
    <div class="tape-key">${segments.map(s => `<span><i style="background:${s.color}"></i>${esc(s.label)} <b>${esc(fmt.money(s.value, cur))}</b></span>`).join('')}</div>`;
}

function mixColor(t, a, b) { const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)); const [r1, g1, b1] = p(a), [r2, g2, b2] = p(b); return `rgb(${Math.round(r1 + (r2 - r1) * t)},${Math.round(g1 + (g2 - g1) * t)},${Math.round(b1 + (b2 - b1) * t)})`; }

/* ---------------------------------------------------------------- tables */
const TABLES = {};
let tableSeq = 0;
/** cols: [{key,label,left?,html:(row)=>string,sort:(row)=>value,why?}] */
function table(cols, rows, { pick, selected, sortKey, desc = true, id } = {}) {
  id = id || `t${++tableSeq}`;
  TABLES[id] = { cols, rows, sortKey, desc, pick, selected };
  return `<div class="tablewrap" id="tw-${id}">${tableBody(id)}</div>`;
}
function tableBody(id) {
  const t = TABLES[id];
  let rows = t.rows.map((r, i) => ({ r, i }));
  const col = t.cols.find(c => c.key === t.sortKey);
  if (col) {
    const sv = col.sort || (r => r[col.key]);
    rows.sort((a, b) => { const x = sv(a.r), y = sv(b.r); if (x == null) return 1; if (y == null) return -1; return (x < y ? -1 : x > y ? 1 : 0) * (t.desc ? -1 : 1); });
  }
  const head = t.cols.map(c => `<th class="${c.left ? 'l' : ''} ${c.why ? 'why' : ''} ${c.sort || c.sortable !== false ? 'sortable' : ''}" scope="col" data-t="${id}" data-k="${c.key}" ${t.sortKey === c.key ? `aria-sort="${t.desc ? 'descending' : 'ascending'}"` : ''}>${esc(c.label)}</th>`).join('');
  const body = rows.map(({ r, i }) => `<tr class="${t.pick ? 'pick' : ''} ${t.selected != null && t.selected === r[t.pick?.key || 'id'] ? 'sel' : ''}" data-t="${id}" data-i="${i}" ${t.pick ? 'tabindex="0"' : ''}>${t.cols.map(c => `<td class="${c.left ? 'l' : ''} ${c.why ? 'why' : ''}">${c.html ? c.html(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
document.addEventListener('click', e => {
  const th = e.target.closest('th.sortable[data-t]');
  if (th) {
    const t = TABLES[th.dataset.t];
    t.desc = t.sortKey === th.dataset.k ? !t.desc : true; t.sortKey = th.dataset.k;
    $(`#tw-${th.dataset.t}`).innerHTML = tableBody(th.dataset.t);
    return;
  }
  const tr = e.target.closest('tr.pick');
  if (tr) { const t = TABLES[tr.dataset.t]; const row = t.rows[+tr.dataset.i]; t.selected = row[t.pick.key]; t.pick.fn(row); $(`#tw-${tr.dataset.t}`).innerHTML = tableBody(tr.dataset.t); }
});
document.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('tr.pick')) { e.preventDefault(); e.target.click(); } });

const chip = (text, cls) => `<span class="chip ${esc(cls || text)}">${esc(text)}</span>`;
const bar = (frac, cls = '') => `<span class="inl-bar ${cls}"><i style="width:${Math.max(0, Math.min(1, frac)) * 100}%"></i></span>`;
const delta = v => v == null ? 'n/a' : `<span class="${v >= 0 ? 'pos' : 'neg'}">${fmt.spct(v)}</span>`;

/* ---------------------------------------------------------------- shared blocks */
function trimLabel(a) {
  const t = a.explanation || '', p = `${a.entity_label}: `;
  const rest = t.startsWith(p) ? t.slice(p.length) : t;
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}
function caseFile(a, { basis } = {}) {
  const sevLabel = { critical: 'Critical', high: 'High', medium: 'Medium' }[a.severity] || a.severity;
  const typeLabel = TYPE[a.leakage_type] || METRIC[a.metric]?.label || a.metric;
  let strip = '';
  if (a.strip) strip = peerStrip({ points: a.strip.points, focus: a.entity_id, median: a.baseline_value, metric: a.metric, caption: `All ${a.strip.points.length} ${a.entity_type}s compared on ${METRIC[a.metric]?.label?.toLowerCase() || a.metric}. The tick is the peer median.` });
  else if (a.detection_method === 'PEER') strip = `<div data-strip="${esc(a.check_id)}|${esc(a.entity_id)}|${esc(a.metric)}|${a.baseline_value}"></div>`;
  return `<article class="case sev-${esc(a.severity)}">
    <div><h3>${esc(a.entity_label)}<small>${esc(typeLabel)}</small></h3>
      <p class="why">${esc(trimLabel(a))}</p>${strip}${basis ? `<p class="basis">${esc(basis)}</p>` : ''}</div>
    <div class="case-fig">${a.estimated_exposure ? `<span class="amt">${esc(fmt.money(a.estimated_exposure))}</span><span class="per">about ${esc(fmt.money(a.annualised_exposure))} a year</span>` : `<span class="per">Change in behaviour, not sized</span>`}
      <span class="sev">${esc(sevLabel)} severity</span></div></article>`;
}
async function hydrateStrips(root) {
  for (const el of $$('[data-strip]', root)) {
    const [check, entity, metric, median] = el.dataset.strip.split('|');
    try {
      const s = await api(`/api/peer-strip?check=${encodeURIComponent(check)}&entity=${encodeURIComponent(entity)}`);
      el.outerHTML = peerStrip({ points: s.points, focus: entity, median: +median, metric, caption: `All ${s.points.length} ${s.entity}s compared on ${METRIC[metric]?.label?.toLowerCase() || metric}. The tick is the peer median.` });
    } catch (_) { el.remove(); }
  }
}
const stripRow = items => `<div class="strip-row">${items.map(i => `<div><div class="k">${esc(i.k)}</div><div class="v ${i.bad ? 'bad' : ''}">${i.v}</div><div class="d">${i.d || ''}</div></div>`).join('')}</div>`;
const pageHead = (title, sub) => `<h1 class="title">${esc(title)}</h1><p class="sub">${esc(sub)}</p>`;
const kv = (k, key) => k[key]?.value_numeric;

/* ---------------------------------------------------------------- views */
const VIEWS = {
  async overview() {
    const d = await api('/api/overview');
    CUR = d.currency;
    const k = d.kpis, v = key => k[key]?.value_numeric;
    const monthly = d.monthly, labels = monthly.map(m => m.year_month);
    const detected = d.leakage_by_type.filter(t => t.leakage_type !== 'LOW_MARGIN_SHORTFALL');
    const html = `
      <section class="hero"><h1 class="lead">${esc(d.headline.lead)}</h1><p class="support">${esc(d.headline.support)}</p></section>
      <section>${stripRow([
        { k: 'Net sales', v: fmt.money(v('net_revenue')), d: `${fmt.money(v('net_revenue_12m'))} in the last 12 months` },
        { k: 'Gross profit', v: fmt.money(v('gross_profit')), d: `margin ${fmt.pct(v('gross_margin'))}` },
        { k: 'Discounts given', v: fmt.pct(v('discount_rate')), d: `${fmt.money(v('discount_amount'))} off list prices` },
        { k: 'Units returned', v: fmt.pct(v('return_rate_units')), d: `${fmt.money(v('return_value'))} refunded` },
        { k: 'Leakage per year', v: fmt.money(v('leakage_annualised')), d: `${fmt.money(v('leakage_total'))} over the period`, bad: true },
      ])}</section>
      <section><h2 class="sec">Case files</h2>
        <p class="sec-note">The largest findings, each compared with every peer. These are statistical flags with a size and an explanation; someone still has to find the cause.</p>
        ${d.findings.map(a => caseFile(a)).join('') || '<p class="muted">Nothing stands out from peers right now.</p>'}
        <p><a class="linkbtn" href="#/leakage">See every leakage finding</a></p></section>
      <section><h2 class="sec">Where the exposure sits</h2>
        <p class="sec-note">Detected leakage by type. Findings can overlap, so read this as exposure, not as cash that can all be recovered.</p>
        ${detected.length ? tape(detected.map(t => ({ label: TYPE[t.leakage_type] || t.leakage_type, value: t.exposure_amount, color: TYPE_COLOR[t.leakage_type] || MUTED }))) : '<p class="muted">No detected leakage.</p>'}</section>
      <section><h2 class="sec">Sales and profit by month</h2>
        <div class="controls"><div class="seg" role="group" aria-label="Measure" id="m-seg">
          ${[['sales', 'Sales and profit'], ['margin', 'Margin'], ['discount', 'Discount rate'], ['returns', 'Return rate']].map(([id, l], i) => `<button type="button" data-m="${id}" aria-pressed="${i === 0}">${l}</button>`).join('')}</div></div>
        <div id="m-chart"></div></section>`;
    return {
      html,
      after() {
        const draw = m => {
          const S = {
            sales: [{ name: 'Net sales', color: TEAL, values: monthly.map(x => x.net_revenue) }, { name: 'Gross profit', color: INK, values: monthly.map(x => x.gross_profit) }],
            margin: [{ name: 'Gross margin', color: TEAL, values: monthly.map(x => x.gross_margin) }],
            discount: [{ name: 'Discount rate', color: RASP, values: monthly.map(x => x.discount_rate) }],
            returns: [{ name: 'Return rate', color: AMBER, values: monthly.map(x => x.return_rate_units) }],
          }[m];
          $('#m-chart').innerHTML = lineChart({ labels, series: S, yFmt: m === 'sales' ? fmt.axis : x => fmt.pct(x), zero: m === 'sales' || m === 'margin' ? true : false, tipFmt: m === 'sales' ? v => fmt.money(v) : x => fmt.pct(x, 2) });
        };
        draw('sales');
        $('#m-seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#m-seg button').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.m); });
      },
    };
  },

  async leakage() {
    const d = await api('/api/leakage');
    CUR = d.currency;
    const detected = d.by_type.filter(t => t.kind === 'detected' || (t.leakage_type !== 'LOW_MARGIN_SHORTFALL'));
    const opp = d.items.filter(i => i.leakage_type === 'LOW_MARGIN_SHORTFALL');
    const found = d.items.filter(i => i.leakage_type !== 'LOW_MARGIN_SHORTFALL');
    const totalAnn = detected.reduce((a, t) => a + t.annualised_exposure, 0);
    const html = `${pageHead('Revenue leakage', 'Money that leaves the business through discounts, returns, inventory discrepancies and supplier prices. Every figure is the gap between one entity and its peers, applied to that entity\'s own volume.')}
      <section>${detected.length ? tape(detected.map(t => ({ label: TYPE[t.leakage_type] || t.leakage_type, value: t.exposure_amount, color: TYPE_COLOR[t.leakage_type] || MUTED }))) : ''}
        ${table([
          { key: 't', label: 'Type', left: true, html: r => esc(TYPE[r.leakage_type] || r.leakage_type), sort: r => r.exposure_amount },
          { key: 'findings', label: 'Findings', html: r => fmt.full(r.findings) },
          { key: 'exposure_amount', label: 'Exposure', html: r => fmt.money(r.exposure_amount) },
          { key: 'annualised_exposure', label: 'Per year', html: r => fmt.money(r.annualised_exposure) },
          { key: 'share', label: 'Share of detected', html: r => r.leakage_type === 'LOW_MARGIN_SHORTFALL' ? '<span class="muted">opportunity, counted apart</span>' : `${bar(r.annualised_exposure / (totalAnn || 1), 'rasp')}${fmt.pct(r.annualised_exposure / (totalAnn || 1), 0)}`, sort: r => r.annualised_exposure },
        ], d.by_type, { sortKey: 'exposure_amount' })}</section>
      <section><h2 class="sec">Detected leakage</h2>
        <p class="sec-note">Raised only where the entity is far from its peers (robust z-score) and the gap is big enough to matter. Inventory-discrepancy exposure is an upper bound: stock adjustments carry no direction in the source data.</p>
        ${found.map(i => caseFile({ ...i, observed_value: i.observed_value, metric: i.metric || '', estimated_exposure: i.exposure_amount, annualised_exposure: i.annualised_exposure, explanation: i.explanation || i.basis, severity: i.severity || 'medium', baseline_value: i.baseline_value }, { basis: i.basis })).join('') || '<p class="muted">No detected leakage.</p>'}</section>
      <section><h2 class="sec">Pricing opportunity in low-margin products</h2>
        <p class="sec-note">${fmt.full(opp.length)} products sit in the bottom margin decile of their category. Lifting them to the category median would be worth ${esc(fmt.money(opp.reduce((a, i) => a + i.exposure_amount, 0)))} over the period. Every category has a bottom decile by definition, so this is shown as an opportunity, never added to detected leakage.</p>
        ${table([
          { key: 'entity_label', label: 'Product', left: true, html: r => `<span class="id">${esc(r.entity_label)}</span>` },
          { key: 'exposure_amount', label: 'Shortfall vs category median', html: r => fmt.money(r.exposure_amount) },
          { key: 'annualised_exposure', label: 'Per year', html: r => fmt.money(r.annualised_exposure) },
        ], opp.slice(0, 15), { sortKey: 'exposure_amount' })}
        <p class="muted">Showing the 15 largest of ${fmt.full(opp.length)}. All of them are in the Excel report.</p></section>`;
    return { html, after: () => hydrateStrips(view) };
  },

  async inventory() {
    const d = await api('/api/inventory');
    CUR = d.currency;
    const k = d.kpis, v = key => k[key]?.value_numeric, mv = d.movement;
    const ratio = d.branch_category.map(r => r.replenishment_ratio).filter(x => x != null).sort((a, b) => a - b);
    const lo = ratio[Math.floor(ratio.length * 0.05)] ?? 0, hi = ratio[Math.floor(ratio.length * 0.95)] ?? 1;
    const branches = [...new Set(d.branch_category.map(r => r.branch_id))].sort(), cats = [...new Set(d.branch_category.map(r => r.category))].sort();
    const cell = {}; d.branch_category.forEach(r => { cell[`${r.branch_id}|${r.category}`] = r; });
    const heat = `<table class="heat"><thead><tr><th></th>${cats.map(c => `<th scope="col">${esc(c)}</th>`).join('')}</tr></thead><tbody>${branches.map(b => `<tr><th class="l" scope="row">${esc(b)}</th>${cats.map(c => {
      const r = cell[`${b}|${c}`]; if (!r || r.replenishment_ratio == null) return '<td></td>';
      const t = Math.max(0, Math.min(1, (r.replenishment_ratio - lo) / ((hi - lo) || 1)));
      return `<td style="background:${mixColor(t, '#E9879F', '#7CC7B8')};color:#16232C" data-tip="<b>${esc(b)}, ${esc(c)}</b><br>Replenished ${fmt.pct(r.replenishment_ratio, 0)} of units sold<br>${fmt.full(r.units_sold)} sold, ${fmt.full(r.units_purchased)} purchased<br>Damaged or adjusted: ${fmt.pct(r.shrink_rate, 1)} of units sold">${fmt.pct(r.replenishment_ratio, 0)}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table>`;
    const html = `${pageHead('Inventory intelligence', 'Where stock is moving, where it is not being replaced, and which products deserve a reorder review.')}
      <div class="margin-note"><h3>Read this first</h3><p>The data records stock movements but not stock on hand, so none of this is a count of units on the shelf. The scores below rank products by sales speed, how much of what was sold has been bought back, and how long since the last purchase. Use them to decide where to look, then check the shelf.</p></div>
      <section>${stripRow([
        { k: 'High stockout-risk proxy', v: fmt.full(v('stockout_high')), d: 'fast sellers that are barely replenished', bad: true },
        { k: 'Reorder review', v: fmt.full(v('reorder_flags')), d: 'fast sellers, no recent purchase' },
        { k: 'Slow-moving', v: fmt.full(v('slow_moving')), d: 'bottom fifth of their category' },
        { k: 'Dead-stock candidates', v: fmt.full(v('dead_stock')), d: 'no sale in 60 days' },
      ])}</section>
      <section><h2 class="sec">Units purchased and sold</h2>
        <p class="sec-note">Across the whole business, purchases cover only about ${fmt.pct(mv.reduce((a, m) => a + m.units_purchased, 0) / mv.reduce((a, m) => a + m.units_sold, 0), 0)} of the units sold. Either stock is held elsewhere, or purchases are missing from the data. Worth asking.</p>
        ${lineChart({ labels: mv.map(m => m.year_month), series: [{ name: 'Units sold', color: INK, values: mv.map(m => m.units_sold) }, { name: 'Units purchased', color: TEAL, values: mv.map(m => m.units_purchased) }], yFmt: x => fmt.axis(x), tipFmt: x => fmt.full(x) })}</section>
      <section><h2 class="sec">Products to look at first</h2>
        <p class="sec-note">Highest stockout-risk proxy. Click a column to re-sort.</p>
        ${table([
          { key: 'product_id', label: 'Product', left: true, html: r => `<span class="id">${esc(r.product_id)}</span>`, sort: r => r.product_id },
          { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
          { key: 'velocity_per_day', label: 'Units a day', html: r => fmt.num(r.velocity_per_day, 2) },
          { key: 'replenishment_ratio', label: 'Replenished', html: r => fmt.pct(r.replenishment_ratio, 0) },
          { key: 'days_since_last_purchase', label: 'Days since purchase', html: r => r.days_since_last_purchase ?? 'n/a' },
          { key: 'stockout_risk_score', label: 'Risk score', html: r => `${bar(r.stockout_risk_score / 100, r.stockout_risk_level === 'high' ? 'rasp' : 'amber')}${fmt.num(r.stockout_risk_score, 0)}` },
          { key: 'stockout_risk_level', label: 'Level', html: r => chip(r.stockout_risk_level) },
          { key: 'explanation', label: 'Why', why: true, html: r => esc(r.explanation), sortable: false },
        ], d.risk_top, { sortKey: 'stockout_risk_score' })}</section>
      <section><h2 class="sec">Replenishment by branch and category</h2>
        <p class="sec-note">Units purchased as a share of units sold in the last 180 days. Red cells are being replaced least. Hover for the numbers.</p>
        <div class="tablewrap">${heat}</div></section>
      <section><h2 class="sec">Slowest-moving products</h2>
        ${table([
          { key: 'product_id', label: 'Product', left: true, html: r => `<span class="id">${esc(r.product_id)}</span>` },
          { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
          { key: 'velocity_per_day', label: 'Units a day', html: r => fmt.num(r.velocity_per_day, 2) },
          { key: 'units_sold_recent', label: 'Units sold, last 90 days', html: r => fmt.full(r.units_sold_recent) },
          { key: 'days_since_last_sale', label: 'Days since last sale', html: r => r.days_since_last_sale ?? 'never' },
        ], d.slow, { sortKey: 'velocity_per_day', desc: false })}</section>`;
    return { html };
  },

  async branches() {
    const d = await api('/api/branches');
    CUR = d.currency;
    const sc = d.scorecards, byB = {};
    d.monthly.forEach(r => (byB[r.branch_id] ||= []).push(r));
    const months = [...new Set(d.monthly.map(r => r.year_month))].sort();
    const hotFirst = [...sc].sort((a, b) => ({ high: 0, medium: 1, low: 2 }[a.operational_risk] - { high: 0, medium: 1, low: 2 }[b.operational_risk]) || a.performance_rank - b.performance_rank)[0];
    const maxRev = Math.max(...sc.map(r => r.net_revenue));
    const points = sc.map(r => ({ x: r.gross_margin, y: r.discount_rate, r: 6 + 7 * Math.sqrt(r.net_revenue / maxRev), label: r.branch_id, hot: r.operational_risk !== 'low', color: r.operational_risk !== 'low' ? RASP : TEAL,
      tip: `<b>${r.branch_id} ${r.city}</b><br>Margin ${fmt.pct(r.gross_margin)}<br>Discount rate ${fmt.pct(r.discount_rate)}<br>Net sales ${fmt.money(r.net_revenue)}<br>Score ${fmt.num(r.performance_score, 0)}` }));
    const comp = d.weights.components;
    const html = `${pageHead('Branch performance', 'How each branch compares on margin, discounting, returns, stock losses and growth, with a score you can take apart.')}
      <section><h2 class="sec">Margin against discounting</h2>
        <p class="sec-note">Branches that discount heavily and earn thin margins fall in the lower right of a healthy cluster. Dot size is net sales; red marks a branch with a high or medium risk finding.</p>
        ${scatter({ points, xFmt: x => fmt.pct(x, 0), yFmt: y => fmt.pct(y, 0), xLabel: 'Gross margin', yLabel: 'Discount rate' })}</section>
      <section><h2 class="sec">Scorecard</h2>
        <p class="sec-note">The score is 100 times the weighted average of five measures, each compared with the other branches (typical = 0.5, far worse = 0, far better = 1). A big gap costs far more than a small one. Click a branch to see its score taken apart.</p>
        ${table([
          { key: 'performance_rank', label: 'Rank', html: r => r.performance_rank, sort: r => -r.performance_rank },
          { key: 'branch_id', label: 'Branch', left: true, html: r => `<span class="id">${esc(r.branch_id)}</span> ${esc(r.city)}` },
          { key: 'net_revenue', label: 'Net sales', html: r => fmt.money(r.net_revenue) },
          { key: 'gross_margin', label: 'Margin', html: r => fmt.pct(r.gross_margin) },
          { key: 'discount_rate', label: 'Discount', html: r => fmt.pct(r.discount_rate) },
          { key: 'return_rate_units', label: 'Returns', html: r => fmt.pct(r.return_rate_units) },
          { key: 'shrink_rate', label: 'Stock loss', html: r => fmt.pct(r.shrink_rate) },
          { key: 'growth_rate', label: 'Growth', html: r => delta(r.growth_rate) },
          { key: 'performance_score', label: 'Score', html: r => `${bar(r.performance_score / 100, r.performance_band === 'weak' ? 'rasp' : r.performance_band === 'watch' ? 'amber' : '')}${fmt.num(r.performance_score, 0)}` },
          { key: 'performance_band', label: 'Band', html: r => chip(r.performance_band) },
          { key: 'operational_risk', label: 'Risk', html: r => chip(r.operational_risk) },
        ], sc, { pick: { key: 'branch_id', fn: r => showBranch(r.branch_id) }, selected: hotFirst.branch_id, sortKey: 'performance_rank', desc: true })}</section>
      <section id="branch-detail"></section>`;
    const showBranch = id => {
      const r = sc.find(x => x.branch_id === id), anoms = d.anomalies.filter(a => a.entity_id === id);
      const parts = [['gross_margin', 'score_gross_margin'], ['discount_rate', 'score_discount_rate'], ['return_rate_units', 'score_return_rate'], ['shrink_rate', 'score_shrink_rate'], ['growth_rate', 'score_growth_rate']];
      const names = { gross_margin: 'Gross margin', discount_rate: 'Discount rate (lower is better)', return_rate_units: 'Return rate (lower is better)', shrink_rate: 'Stock loss (lower is better)', growth_rate: 'Growth' };
      const items = parts.map(([m, s]) => ({ label: names[m].split(' (')[0], value: r[s] * comp[m].weight * 100, color: r[s] < 0.34 ? RASP : r[s] < 0.67 ? AMBER : TEAL, tip: `<b>${names[m]}</b><br>Measure score ${fmt.num(r[s], 2)} x weight ${fmt.pct(comp[m].weight, 0)} = ${fmt.num(r[s] * comp[m].weight * 100, 1)} points`, bold: false }));
      $('#branch-detail').innerHTML = `<h2 class="sec">${esc(r.branch_id)} (${esc(r.city)}), score ${fmt.num(r.performance_score, 0)} of 100</h2>
        <p class="sec-note">Points earned out of each measure's maximum: ${parts.map(([m]) => `${names[m].split(' (')[0].toLowerCase()} ${fmt.pct(comp[m].weight, 0)}`).join(', ')}. Short bars are where this branch loses points.</p>
        ${hbars(items, { valFmt: v => `${v.toFixed(1)} pts` , labelW: 140, rowH: 32, width: 700})}
        <div class="controls"><div class="seg" id="bm-seg" role="group" aria-label="Measure">${[['discount_rate', 'Discount rate'], ['gross_margin', 'Margin'], ['return_rate_units', 'Return rate']].map(([k, l], i) => `<button type="button" data-k="${k}" aria-pressed="${i === 0}">${l}</button>`).join('')}</div></div>
        <div id="bm-chart"></div>
        ${anoms.map(a => caseFile(a)).join('')}`;
      const draw = key => {
        const series = byB[id] || [], med = months.map(mo => { const vals = d.monthly.filter(x => x.year_month === mo && x.branch_id !== id).map(x => x[key]).filter(x => x != null).sort((a, b) => a - b); return vals.length ? vals[Math.floor(vals.length / 2)] : null; });
        $('#bm-chart').innerHTML = lineChart({ labels: months, series: [{ name: `${id}`, color: RASP, values: months.map(mo => series.find(x => x.year_month === mo)?.[key] ?? null) }, { name: 'Median of other branches', color: TEAL, values: med, dash: '5 4', width: 2 }], yFmt: x => fmt.pct(x, 0), tipFmt: x => fmt.pct(x, 1), zero: false });
      };
      draw('discount_rate');
      $('#bm-seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#bm-seg button').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.k); });
      hydrateStrips($('#branch-detail'));
    };
    return { html, after: () => showBranch(hotFirst.branch_id) };
  },

  async suppliers() {
    const d = await api('/api/suppliers');
    CUR = d.currency;
    const sc = d.scorecards, flagged = new Set(d.anomalies.map(a => a.entity_id));
    const above = sc.filter(x => x.ppv_amount > 1), top = [...above].sort((a, b) => b.ppv_amount - a.ppv_amount).slice(0, 12), atStd = sc.length - above.length;
    const byS = {}; d.monthly.forEach(r => (byS[r.supplier_id] ||= []).push(r));
    const months = [...new Set(d.monthly.map(r => r.year_month))].sort();
    const html = `${pageHead('Supplier intelligence', 'What you pay each supplier compared with the standard cost of the goods, how steady their deliveries are, and how their products perform after sale.')}
      <section><h2 class="sec">Paid above standard cost</h2>
        <p class="sec-note">Standard cost is the median cost recorded on a product's non-purchase events. A supplier far above its peers is not necessarily at fault (a price rise may have been agreed) but it is where to ask first. ${fmt.full(atStd)} of ${fmt.full(sc.length)} suppliers were paid exactly the standard cost.</p>
        ${hbars(top.map(s => ({ label: s.supplier_id, value: Math.max(s.ppv_amount, 0), color: flagged.has(s.supplier_id) ? RASP : TEAL, bold: flagged.has(s.supplier_id), tip: `<b>${s.supplier_id}</b><br>${fmt.pct(s.ppv_pct)} above standard on ${fmt.money(s.purchase_standard_spend)} of purchases` })), { valFmt: v => fmt.money(v), labelW: 100, rowH: 32 })}
        ${d.anomalies.map(a => caseFile(a)).join('')}</section>
      <section id="sup-flagged">${Object.keys(byS).length ? `<h2 class="sec">Monthly cost against standard</h2><p class="sec-note">Purchase price against standard cost, by month, for suppliers with a finding. A flat line well above zero means the premium is built into every order.</p>
        ${lineChart({ labels: months, series: Object.entries(byS).map(([id, rows]) => ({ name: id, color: RASP, values: months.map(mo => rows.find(r => r.year_month === mo)?.ppv_pct ?? null) })).concat([{ name: 'Zero (at standard cost)', color: TEAL, values: months.map(() => 0), dash: '5 4', width: 1.8 }]), yFmt: x => fmt.pct(x, 0), tipFmt: x => fmt.spct(x, 1), zero: true })}` : ''}</section>
      <section><h2 class="sec">All suppliers</h2>
        <div class="controls"><input type="search" id="sup-q" placeholder="Filter by supplier ID" aria-label="Filter suppliers"></div>
        <div id="sup-table"></div></section>`;
    return {
      html,
      after() {
        const cols = [
          { key: 'supplier_id', label: 'Supplier', left: true, html: r => `<span class="id">${esc(r.supplier_id)}</span>` },
          { key: 'purchase_spend', label: 'Spend', html: r => fmt.money(r.purchase_spend) },
          { key: 'ppv_amount', label: 'Above standard', html: r => fmt.money(r.ppv_amount) },
          { key: 'ppv_pct', label: 'Variance', html: r => r.ppv_pct == null ? 'n/a' : `<span class="${r.ppv_pct > 0.02 ? 'neg' : ''}">${fmt.spct(r.ppv_pct)}</span>` },
          { key: 'share_above_standard', label: 'Orders above standard', html: r => fmt.pct(r.share_above_standard, 0) },
          { key: 'purchases_above_list', label: 'Bought above our selling price', html: r => fmt.full(r.purchases_above_list) },
          { key: 'cadence_cv', label: 'Order rhythm (lower is steadier)', html: r => fmt.num(r.cadence_cv, 2) },
          { key: 'product_return_rate', label: 'Returns on its products', html: r => fmt.pct(r.product_return_rate) },
          { key: 'risk_level', label: 'Risk', html: r => chip(r.risk_level) },
        ];
        const draw = q => { TABLES.sup = undefined; $('#sup-table').innerHTML = table(cols, sc.filter(s => s.supplier_id.toLowerCase().includes(q.toLowerCase())), { id: 'sup', sortKey: 'ppv_amount' }); };
        draw('');
        $('#sup-q').addEventListener('input', e => draw(e.target.value));
        hydrateStrips(view);
      },
    };
  },

  async products() {
    const d = await api('/api/products');
    CUR = d.currency;
    const c = d.counts;
    const segs = [['problematic', 'Needs attention'], ['low_margin', 'Low margin'], ['high_margin', 'High margin'], ['high_return', 'High returns'], ['high_discount', 'Heavily discounted'], ['declining', 'Declining']];
    const cols = [
      { key: 'product_id', label: 'Product', left: true, html: r => `<span class="id">${esc(r.product_id)}</span>` },
      { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
      { key: 'net_revenue', label: 'Net sales', html: r => fmt.money(r.net_revenue) },
      { key: 'gross_margin', label: 'Margin', html: r => fmt.pct(r.gross_margin) },
      { key: 'return_rate_units', label: 'Returns', html: r => fmt.pct(r.return_rate_units) },
      { key: 'discount_rate', label: 'Discount', html: r => fmt.pct(r.discount_rate) },
      { key: 'growth_rate', label: 'Growth', html: r => delta(r.growth_rate) },
      { key: 'problem_reasons', label: 'Why flagged', why: true, html: r => esc(r.problem_reasons || ''), sortable: false },
    ];
    const html = `${pageHead('Product intelligence', `${fmt.full(c.total)} products, sorted into the groups that matter: where the margin is, where it is not, and where customers push back.`)}
      <section><div class="controls"><div class="seg" id="seg" role="group" aria-label="Product group">${segs.map(([k, l], i) => `<button type="button" data-k="${k}" aria-pressed="${i === 0}">${l}<span class="n">${fmt.full(c[k])}</span></button>`).join('')}</div></div>
        <div id="seg-table"></div>
        <p class="muted" id="seg-note"></p></section>
      <section><h2 class="sec">By category</h2>
        <p class="sec-note">Margin and return rate for each category.</p>
        ${table([
          { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
          { key: 'products', label: 'Products', html: r => fmt.full(r.products) },
          { key: 'net_revenue', label: 'Net sales', html: r => fmt.money(r.net_revenue) },
          { key: 'gross_margin', label: 'Margin', html: r => `${bar(r.gross_margin / 0.4)}${fmt.pct(r.gross_margin)}` },
          { key: 'return_rate', label: 'Return rate', html: r => fmt.pct(r.return_rate) },
        ], d.category, { sortKey: 'net_revenue' })}</section>`;
    const notes = {
      problematic: 'Flagged as statistically abnormal, or weak on two or more measures.', low_margin: 'Bottom tenth of their category by margin.',
      high_margin: 'Top tenth of their category by margin.', high_return: 'Top tenth of all products by return rate.',
      high_discount: 'Top tenth of all products by discount rate.', declining: 'Net sales fell at least 15% against the previous period.',
    };
    return {
      html,
      after() {
        const draw = k => {
          TABLES.seg = undefined;
          $('#seg-table').innerHTML = table(cols, d[k], { id: 'seg', sortKey: { problematic: 'net_revenue', low_margin: 'gross_margin', high_margin: 'gross_margin', high_return: 'return_rate_units', high_discount: 'discount_rate', declining: 'growth_rate' }[k], desc: !['low_margin', 'declining'].includes(k) });
          $('#seg-note').textContent = `${notes[k]} Showing up to ${d[k].length} of ${fmt.full(c[k])}.`;
        };
        draw('problematic');
        $('#seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#seg button').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.k); });
      },
    };
  },

  async health() {
    const d = await api('/api/health');
    const f = d.flow, ok = d.checks.filter(c => c.status === 'PASS').length;
    const maxDur = Math.max(...d.runs.map(r => r.duration_seconds || 0), 1);
    const stageName = { publish: 'Replay into Kafka', ingest: 'Kafka to raw zone', clean: 'Validate and quarantine', transform: 'Dimensions and facts', load: 'Load PostgreSQL', features: 'Spark features', analytics: 'Detect and score', publish_analytics: 'Publish analytics', dq: 'Quality gates' };
    const html = `${pageHead('Pipeline health', 'Every event is accounted for: read, accepted or quarantined, then stored. If any of these numbers stop adding up, the quality gates fail.')}
      <section><div class="flow">
        <div><div class="k">Published to Kafka</div><div class="v">${fmt.full(f.published)}</div></div>
        <div><div class="k">Read from Kafka</div><div class="v">${fmt.full(f.landed)}</div></div>
        <div><div class="k">Passed validation</div><div class="v">${fmt.full(f.accepted)}</div></div>
        <div><div class="k">Quarantined</div><div class="v ${f.rejected ? 'bad' : ''}">${fmt.full(f.rejected)}</div></div>
        <div><div class="k">Stored in warehouse</div><div class="v">${fmt.full(f.stored)}</div></div></div>
        <p class="sec-note">${ok} of ${d.checks.length} reconciliation, integrity and freshness checks pass. Total processing time across Spark stages: ${fmt.num(d.processing_seconds, 0)} seconds.</p></section>
      <section><h2 class="sec">Stages, latest run</h2>
        ${table([
          { key: 'stage', label: 'Stage', left: true, html: r => esc(stageName[r.stage] || r.stage), sort: r => r.stage },
          { key: 'status', label: 'Status', html: r => chip(r.status.toLowerCase(), r.status === 'SUCCESS' ? 'pass' : 'fail') },
          { key: 'duration_seconds', label: 'Duration', html: r => r.duration_seconds == null ? 'n/a' : `${bar(r.duration_seconds / maxDur)}${fmt.num(r.duration_seconds, 1)} s` },
          { key: 'rows_in', label: 'Rows in', html: r => fmt.full(r.rows_in) },
          { key: 'rows_out', label: 'Rows out', html: r => fmt.full(r.rows_out) },
          { key: 'rows_rejected', label: 'Rejected', html: r => fmt.full(r.rows_rejected) },
        ], d.runs, { sortKey: null })}</section>
      <section><h2 class="sec">Quality gates</h2>
        <ul class="checks">${d.checks.filter(c => c.severity !== 'INFO').map(c => `<li><span>${chip(c.status.toLowerCase())}</span><span><span class="nm">${esc(c.check_name.replaceAll('_', ' ').toLowerCase().replace(/^./, m => m.toUpperCase()))}</span><span class="ms">${esc(c.message || '')}</span></span></li>`).join('')}</ul></section>
      <section><h2 class="sec">Quarantine</h2>
        <p class="sec-note">Nothing is dropped silently. Rejected messages are stored verbatim with the rule they broke; warnings were accepted and recorded.</p>
        ${d.rules.length ? table([
          { key: 'rule_code', label: 'Rule', left: true, html: r => `<span class="id">${esc(r.rule_code)}</span>` },
          { key: 'disposition', label: 'What happened', html: r => chip(r.disposition === 'REJECTED' ? 'rejected' : 'accepted with warning', r.disposition === 'REJECTED' ? 'fail' : 'warn') },
          { key: 'failures', label: 'Messages', html: r => fmt.full(r.failures) },
        ], d.rules, { sortKey: 'failures' }) : '<p class="muted">Nothing has been rejected or flagged.</p>'}</section>`;
    return { html };
  },
};

const PAGES = [
  { id: 'overview', name: 'Executive overview' }, { id: 'leakage', name: 'Revenue leakage' }, { id: 'inventory', name: 'Inventory intelligence' },
  { id: 'branches', name: 'Branch performance' }, { id: 'suppliers', name: 'Supplier intelligence' }, { id: 'products', name: 'Product intelligence' },
  { id: 'health', name: 'Pipeline health' },
];
let counts = {};

function renderNav(active) {
  $('#nav').innerHTML = PAGES.map(p => {
    const n = counts[p.id];
    const badge = p.id === 'overview' ? '' : p.id === 'health' ? (n ? `<span class="count hot">${n}</span>` : '<span class="count ok">ok</span>') : n != null ? `<span class="count ${n && p.id !== 'inventory' && p.id !== 'products' ? 'hot' : ''}">${fmt.full(n)}</span>` : '';
    return `<a href="#/${p.id}" ${p.id === active ? 'aria-current="page"' : ''}>${esc(p.name)}${badge}</a>`;
  }).join('');
}

async function route() {
  const id = (location.hash.replace(/^#\//, '') || 'overview');
  const page = VIEWS[id] ? id : 'overview';
  renderNav(page);
  view.setAttribute('aria-busy', 'true');
  try {
    const { html, after } = await VIEWS[page]();
    view.innerHTML = html;
    if (after) await after();
  } catch (e) {
    view.innerHTML = `<div class="empty"><h1>Nothing to show yet</h1><p class="err">${esc(e.message)}</p></div>`;
  }
  view.removeAttribute('aria-busy');
  window.scrollTo({ top: 0 });
}

/* Downloads: fetch as a blob so the button can show progress and report errors. */
function wireDownloads() {
  const status = $('#dl-status');
  for (const btn of [$('#dl-pdf'), $('#dl-xlsx')]) {
    btn.addEventListener('click', async e => {
      e.preventDefault();
      if (btn.getAttribute('aria-busy') === 'true') return;
      const name = btn.dataset.name, original = btn.textContent;
      btn.setAttribute('aria-busy', 'true'); btn.textContent = `Preparing ${name}...`; status.className = 'status'; status.textContent = '';
      try {
        const r = await fetch(btn.getAttribute('href'));
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `The server returned ${r.status}`);
        const blob = await r.blob();
        const file = /filename="?([^";]+)/.exec(r.headers.get('Content-Disposition') || '')?.[1] || `ProfitPulse_report.${name === 'PDF' ? 'pdf' : 'xlsx'}`;
        const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: file });
        document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        status.textContent = `${file} downloaded.`;
      } catch (err) {
        status.className = 'status err'; status.textContent = `Could not create the ${name}: ${err.message}`;
      } finally { btn.removeAttribute('aria-busy'); btn.textContent = original; }
    });
  }
}

async function boot() {
  wireDownloads();
  try {
    const s = await api('/api/status');
    CUR = s.currency;
    if (!s.ready) {
      renderNav('overview');
      view.innerHTML = `<div class="empty"><h1>No analytics yet</h1><p class="sub">The pipeline has not produced results. Trigger the <b>ingestion_pipeline</b> DAG in Airflow, or run <code>scripts/run_stages.sh</code>, then reload this page.</p></div>`;
      return;
    }
    const ov = await api('/api/overview');
    counts = ov.counts.by_page;
    $('#asof').textContent = `Data to ${fmt.date(ov.kpis.as_of_date.value_text)}. Amounts in ${CUR}.`;
  } catch (e) { /* the view will show the message */ }
  addEventListener('hashchange', route);
  route();
}
boot();
