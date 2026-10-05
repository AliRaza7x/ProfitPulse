'use strict';
/* ProfitPulse front end: no build step, no dependencies. Charts are hand-built SVG styled by CSS class,
   so the light/dark theme switches instantly. Motion is skipped entirely under prefers-reduced-motion. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = $('#view');
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.classList.contains('still');
let CUR = 'PKR';
let uid = 0;

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
  date(iso) { return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); },
};
const COUNT_FMT = { money: v => fmt.money(v), pct1: v => fmt.pct(v, 1), pct0: v => fmt.pct(v, 0), int: v => fmt.full(v) };
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
const TYPE_COLOR = { EXCESS_DISCOUNT: 'var(--rasp)', EXCESS_RETURNS: 'var(--amber)', INVENTORY_DISCREPANCY: 'var(--ink)', PROCUREMENT_OVERPAYMENT: 'var(--teal)', LOW_MARGIN_SHORTFALL: 'var(--muted)' };
// --teal = steel blue (normal), --rasp = vermilion (risk), --amber = ochre (watch); see the note at the top of app.css.

async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* keep default */ }
    throw new Error(msg);
  }
  return r.json();
}

/* ---------------------------------------------------------------- icons, theme, toasts */
const ICON = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  leakage: '<path d="M12 3s6 6.2 6 10.5A6 6 0 0 1 6 13.5C6 9.2 12 3 12 3z"/><path d="M9.5 14a2.6 2.6 0 0 0 2.5 2.4"/>',
  inventory: '<path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5M12 12v9"/>',
  branches: '<path d="M4 9l1.5-5h13L20 9"/><path d="M4 9v11h16V9"/><path d="M4 9a2.7 2.7 0 0 0 5.3 0 2.7 2.7 0 0 0 5.4 0A2.7 2.7 0 0 0 20 9"/><path d="M10 20v-5h4v5"/>',
  suppliers: '<path d="M2 6h11v10H2z"/><path d="M13 9h5l3 3v4h-8z"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
  products: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  health: '<path d="M2 12h5l3-7 4 14 3-7h5"/>',
};
const icon = id => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[id]}</svg>`;

$('#theme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('pp.theme', next); } catch (_) { /* private mode */ }
});

function toast(msg, isErr = false) {
  const t = Object.assign(document.createElement('div'), { className: `toast${isErr ? ' err' : ''}`, textContent: msg });
  $('#toasts').append(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, isErr ? 6500 : 3800);
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

/* ---------------------------------------------------------------- reveal + count-up */
const io = new IntersectionObserver(entries => entries.forEach(e => {
  if (!e.isIntersecting) return;
  e.target.classList.add('in');
  $$('.cu', e.target).forEach(countUp);
  io.unobserve(e.target);
}), { threshold: 0.06, rootMargin: '0px 0px -5% 0px' });

function armReveal(root) {
  $$('[data-reveal]', root).forEach(el => {
    if (REDUCED) el.classList.add('in'); else io.observe(el);
  });
}
function countUp(el) {
  const to = +el.dataset.to, f = COUNT_FMT[el.dataset.f] || String;
  if (REDUCED || isNaN(to)) { el.textContent = f(to); return; }
  const t0 = performance.now(), dur = 1100;
  const step = now => {
    const p = Math.min((now - t0) / dur, 1), e = 1 - Math.pow(1 - p, 3);
    el.textContent = f(to * e);
    if (p < 1) requestAnimationFrame(step); else el.textContent = f(to);
  };
  requestAnimationFrame(step);
}
const count = (v, f, cls = '') => `<span class="cu ${cls}" data-to="${v ?? ''}" data-f="${f}">${(COUNT_FMT[f] || String)(v)}</span>`;

/* ---------------------------------------------------------------- charts (class-styled) */
/** Every peer as a dot on one line; the flagged one in vermilion with a pulsing ring; shaded typical range; dashed flag line. */
function peerStrip({ points, focus, median, scale, thr, direction, metric, caption }) {
  if (!points?.length) return '';
  const W = 720, H = 112, pad = 44, cy = 58;
  const vals = points.map(p => p.value);
  let lo = Math.min(...vals, median ?? Infinity), hi = Math.max(...vals);
  const span = (hi - lo) || 1; lo -= span * 0.04; hi += span * 0.04;
  const x = v => pad + (W - 2 * pad) * ((v - lo) / (hi - lo));
  const clampX = px => Math.min(Math.max(px, 70), W - 70);
  const dense = points.length > 120;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(caption || 'Peer comparison')}">
    <line class="gl" x1="${pad}" x2="${W - pad}" y1="${cy}" y2="${cy}" stroke-width="2" stroke-linecap="round"/>`;
  if (median != null && scale) {
    const bl = Math.max(lo, median - thr * scale), bh = Math.min(hi, median + thr * scale);
    svg += `<rect class="band" x="${x(bl)}" y="${cy - 17}" width="${Math.max(x(bh) - x(bl), 2)}" height="34" rx="6" data-tip="Typical range: within ${thr} robust standard deviations of the peer median"/>`;
    const tl = direction === 'low' ? median - thr * scale : median + thr * scale;
    if (tl > lo && tl < hi) svg += `<line class="thr" x1="${x(tl)}" x2="${x(tl)}" y1="${cy - 24}" y2="${cy + 24}" stroke-width="1.6"/><text class="ax" x="${clampX(x(tl))}" y="${cy + 42}" text-anchor="middle" font-size="12">flagged beyond this line</text>`;
  }
  if (median != null) svg += `<line class="s-ink" x1="${x(median)}" x2="${x(median)}" y1="${cy - 16}" y2="${cy + 16}" stroke-width="2.4" stroke-linecap="round"/>
    <text class="ax" x="${clampX(x(median))}" y="${cy - 26}" text-anchor="middle" font-size="13">peer median ${esc(mf(metric, median))}</text>`;
  const ordered = [...points].sort((a, b) => (a.id === focus) - (b.id === focus));
  ordered.forEach((p, i) => {
    const f = p.id === focus, op = f ? 1 : dense ? 0.35 : 0.65;
    svg += `<circle class="pop ${f ? 'f-rasp' : 'f-teal'}" style="--d:${f ? 18 : Math.min(i, 40)};--o:${op}" cx="${x(p.value).toFixed(1)}" cy="${cy}" r="${f ? 9 : dense ? 2.8 : 5}" data-tip="<b>${esc(p.id)}</b> ${esc(mf(metric, p.value))}"/>`;
  });
  const fp = points.find(p => p.id === focus);
  if (fp) svg += `<circle class="ring" cx="${x(fp.value)}" cy="${cy}" r="9"/><text class="f-rasp" x="${clampX(x(fp.value))}" y="${cy - 26}" text-anchor="middle" font-size="15.5" font-weight="700">${esc(fp.id)} ${esc(mf(metric, fp.value))}</text>`;
  svg += '</svg>';
  return `<figure class="strip">${svg}<figcaption>${esc(caption || `All ${points.length} peers compared. The flagged one is in red.`)}</figcaption></figure>`;
}

/** Line chart. series: [{name,color:'teal'|'rasp'|..., values, dash, width, area}] */
function lineChart({ labels, series, height = 270, width = 860, yFmt = fmt.axis, zero = true, tipFmt, bare = false }) {
  const pl = bare ? 6 : 60, pr = bare ? 8 : 16, pt = bare ? 10 : 14, pb = bare ? 8 : 32, n = labels.length, id = `g${++uid}`;
  const all = series.flatMap(s => s.values.filter(v => v != null));
  if (!all.length) return '';
  let lo = zero ? Math.min(0, ...all) : Math.min(...all), hi = Math.max(...all);
  const padY = (hi - lo) * 0.1 || 1; hi += padY; if (!zero) lo -= padY;
  if (bare) lo = Math.min(...all) - (hi - Math.min(...all)) * 1.6;   // keep month-to-month noise from looking dramatic
  const x = i => pl + (width - pl - pr) * (n === 1 ? 0.5 : i / (n - 1));
  const y = v => pt + (height - pt - pb) * (1 - (v - lo) / (hi - lo));
  let g = '';
  if (!bare) {
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      g += `<line class="gl" x1="${pl}" x2="${width - pr}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${pl - 8}" y="${y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`;
    }
    const step = Math.ceil(n / 8);
    labels.forEach((l, i) => { if (i % step === 0) g += `<text class="ax" x="${x(i)}" y="${height - 9}" text-anchor="middle">${esc(fmt.month(l))}</text>`; });
  }
  series.forEach((s, si) => {
    const pts = s.values.map((v, i) => v == null ? null : [x(i), y(v)]);
    const d = pts.map((p, i) => p ? `${i && pts[i - 1] ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}` : '').join('');
    if (s.area || (bare && si === 0)) {
      const first = pts.find(Boolean), last = [...pts].reverse().find(Boolean);
      g += `<defs><linearGradient id="${id}${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--${s.color});stop-opacity:.30"/><stop offset="1" style="stop-color:var(--${s.color});stop-opacity:0"/></linearGradient></defs>
        <path class="area" d="${d}L${last[0]},${height - pb}L${first[0]},${height - pb}Z" fill="url(#${id}${si})"/>`;
    }
    g += s.dash   // a dashed reference line fades in (the draw-in trick would overwrite its dash pattern)
      ? `<path class="area s-${s.color}" d="${d}" fill="none" stroke-width="${s.width || 2}" stroke-linejoin="round" stroke-linecap="round" stroke-dasharray="6 5"/>`
      : `<path class="draw s-${s.color}" style="--d:${si}" pathLength="1" d="${d}" fill="none" stroke-width="${s.width || (bare ? 2.6 : 2.2)}" stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  const colW = (width - pl - pr) / Math.max(n - 1, 1);
  labels.forEach((l, i) => {
    const rows = series.filter(s => s.values[i] != null).map(s => `${esc(s.name)} <b>${esc((tipFmt || yFmt)(s.values[i]))}</b>`).join('<br>');
    g += `<g class="pt"><rect class="hit" x="${x(i) - colW / 2}" y="${pt}" width="${colW}" height="${height - pt - pb}" data-tip="${esc(fmt.month(l))}<br>${rows.replace(/"/g, '&quot;')}"/><line class="xh" x1="${x(i)}" x2="${x(i)}" y1="${pt}" y2="${height - pb}"/>` +
      series.map(s => s.values[i] == null ? '' : `<circle class="f-${s.color}" cx="${x(i)}" cy="${y(s.values[i])}" r="5" stroke="var(--ground)" stroke-width="2"/>`).join('') + '</g>';
  });
  const last = series[0].values.length - 1;
  if (bare && series[0].values[last] != null) g += `<circle class="f-${series[0].color}" cx="${x(last)}" cy="${y(series[0].values[last])}" r="5.5"/><circle class="ring" style="stroke:var(--${series[0].color})" cx="${x(last)}" cy="${y(series[0].values[last])}" r="5.5"/>`;
  const legend = !bare && series.length > 1 ? `<div class="legend">${series.map(s => `<span><i style="background:var(--${s.color})"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  return `<div class="chart">${legend}<svg viewBox="0 0 ${width} ${height}" role="img">${g}</svg></div>`;
}

/** Hero bars: the last 12 months, the latest month lit, the rest in soft slate. */
function heroBars(rows) {
  if (!rows.length) return '';
  const W = 640, H = 220, gap = 10, lb = 30, n = rows.length, bw = (W - gap * (n - 1)) / n;
  const vals = rows.map(r => r.net_revenue), hi = Math.max(...vals), lo = Math.min(...vals) * 0.55;
  const y = v => (H - lb) * (1 - (v - lo) / (hi - lo || 1)) + 4;
  let g = '';
  rows.forEach((r, i) => {
    const x = i * (bw + gap), top = y(r.net_revenue), last = i === n - 1;
    g += `<g data-tip="${esc(fmt.month(r.year_month))}<br>Net sales <b>${esc(fmt.money(r.net_revenue))}</b>"><rect class="hb ${last ? 'on' : ''} grow-y" style="--d:${i}" x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${(H - lb - top).toFixed(1)}" rx="7"/>
      <text class="hbl" x="${(x + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">${esc(fmt.month(r.year_month).split(' ')[0])}</text></g>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Net sales, last 12 months">${g}</svg>`;
}

function spark(values, color = 'teal') {
  const v = values.filter(x => x != null);
  if (v.length < 2) return '';
  const W = 160, H = 34, lo = Math.min(...v), hi = Math.max(...v), r = (hi - lo) || 1;
  const d = values.map((val, i) => `${i ? 'L' : 'M'}${(i / (values.length - 1) * W).toFixed(1)},${(H - 4 - (val - lo) / r * (H - 8)).toFixed(1)}`).join('');
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="s-${color}" d="${d}" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function hbars(items, { width = 820, rowH = 36, labelW = 150, valFmt = fmt.axis } = {}) {
  if (!items.length) return '';
  const top = Math.max(...items.map(i => i.value), 1e-9), H = rowH * items.length + 6;
  let g = '';
  items.forEach((it, i) => {
    const yy = 3 + i * rowH, w = Math.max((width - labelW - 110) * (it.value / top), 3);
    const bh = Math.min(rowH - 18, 12);   // slim bars on a faint full-width track
    g += `<g ${it.tip ? `data-tip="${esc(it.tip)}"` : ''}><text class="tl" x="${labelW - 14}" y="${yy + rowH / 2 + 4.5}" text-anchor="end" font-size="13" font-weight="${it.bold ? 600 : 400}">${esc(it.label)}</text>
      <rect class="track" x="${labelW}" y="${yy + (rowH - bh) / 2}" width="${width - labelW - 110}" height="${bh}" rx="${bh / 2}"/>
      <rect class="grow f-${it.color || 'teal'}" style="--d:${i}" x="${labelW}" y="${yy + (rowH - bh) / 2}" width="${w}" height="${bh}" rx="${bh / 2}"/>
      <text class="ax" x="${width - 98}" y="${yy + rowH / 2 + 4.5}" font-size="12.5">${esc(valFmt(it.value))}</text></g>`;
  });
  return `<div class="chart"><svg viewBox="0 0 ${width} ${H}" role="img">${g}</svg></div>`;
}

function scatter({ points, xFmt, yFmt, xLabel, yLabel, width = 860, height = 400 }) {
  const pl = 66, pr = 28, pt = 22, pb = 56;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const xr = (Math.max(...xs) - Math.min(...xs)) || 1, yr = (Math.max(...ys) - Math.min(...ys)) || 1;
  const x0 = Math.min(...xs) - xr * 0.1, x1 = Math.max(...xs) + xr * 0.1, y0 = Math.min(...ys) - yr * 0.12, y1 = Math.max(...ys) + yr * 0.12;
  const X = v => pl + (width - pl - pr) * ((v - x0) / (x1 - x0)), Y = v => pt + (height - pt - pb) * (1 - (v - y0) / (y1 - y0));
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const vy = y0 + (y1 - y0) * i / 4, vx = x0 + (x1 - x0) * i / 4;
    g += `<line class="gl" x1="${pl}" x2="${width - pr}" y1="${Y(vy)}" y2="${Y(vy)}"/><text class="ax" x="${pl - 8}" y="${Y(vy) + 4}" text-anchor="end">${yFmt(vy)}</text>
      <text class="ax" x="${X(vx)}" y="${height - pb + 20}" text-anchor="middle">${xFmt(vx)}</text>`;
  }
  g += `<text class="tl" x="${(pl + width - pr) / 2}" y="${height - 8}" text-anchor="middle" font-size="13.5" font-weight="600">${esc(xLabel)}</text>
    <text class="tl" transform="translate(16 ${(pt + height - pb) / 2}) rotate(-90)" text-anchor="middle" font-size="13.5" font-weight="600">${esc(yLabel)}</text>`;
  points.forEach((p, i) => {
    g += `<g data-tip="${esc(p.tip)}"><circle class="pop f-${p.hot ? 'rasp' : 'teal'}" style="--d:${i};--o:${p.hot ? 0.92 : 0.6}" cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.r}" stroke="var(--ground)" stroke-width="2"/>
      ${p.hot ? `<text class="f-rasp" x="${X(p.x) + p.r + 6}" y="${Y(p.y) + 5}" font-size="13.5" font-weight="700">${esc(p.label)}</text>` : ''}</g>`;
  });
  return `<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img">${g}</svg></div>`;
}

function tape(segments, cur = CUR) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  return `<div class="tape" role="img" aria-label="Share of exposure by type">${segments.map((s, i) => `<i class="seg-in" style="--d:${i};flex:${s.value};background:${s.color}" data-tip="<b>${esc(s.label)}</b><br>${esc(fmt.money(s.value, cur))} (${(100 * s.value / total).toFixed(0)}%)"></i>`).join('')}</div>
    <div class="tape-key">${segments.map(s => `<span><i style="background:${s.color}"></i>${esc(s.label)} <b>${esc(fmt.money(s.value, cur))}</b></span>`).join('')}</div>`;
}
const heatColor = t => t < 0.5
  ? `color-mix(in srgb, var(--rasp) ${Math.round((0.5 - t) * 2 * 46)}%, var(--ground-2))`
  : `color-mix(in srgb, var(--teal) ${Math.round((t - 0.5) * 2 * 40)}%, var(--ground-2))`;

/* ---------------------------------------------------------------- tables (stack into cards on phones) */
const TABLES = {};
let tableSeq = 0;
/** cols: [{key,label,left?,html:(row)=>string,sort:(row)=>value,why?,sortable?}] */
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
  const prim = Math.max(t.cols.findIndex(c => c.primary), 0);      // the column that becomes the card heading on phones
  const body = rows.map(({ r, i }) => `<tr class="${t.pick ? 'pick' : ''} ${t.selected != null && t.selected === r[t.pick?.key || 'id'] ? 'sel' : ''}" data-t="${id}" data-i="${i}" ${t.pick ? 'tabindex="0"' : ''}>${t.cols.map((c, ci) => `<td class="${c.left ? 'l' : ''} ${c.why ? 'why' : ''} ${ci === prim ? 'primary' : ''}" data-label="${esc(c.label)}">${c.html ? c.html(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('');
  return `<table class="stack"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
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

const sentence = t => { t = String(t ?? ''); return t.charAt(0).toUpperCase() + t.slice(1); };
const chip = (text, cls) => `<span class="chip ${esc(cls || text)}">${esc(sentence(text))}</span>`;
const bar = (frac, cls = '') => `<span class="inl-bar ${cls}"><i style="width:${Math.max(0, Math.min(1, frac)) * 100}%"></i></span>`;
const delta = v => v == null ? 'n/a' : `<span class="${v >= 0 ? 'pos' : 'neg'}">${fmt.spct(v)}</span>`;

/* ---------------------------------------------------------------- shared blocks */
function trimLabel(a) {
  const t = a.explanation || '', p = `${a.entity_label}: `;
  const rest = t.startsWith(p) ? t.slice(p.length) : t;
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}
const stripOf = (s, a, caption) => peerStrip({ points: s.points, focus: a.entity_id, median: s.median ?? a.baseline_value, scale: s.scale, thr: s.threshold_z, direction: s.direction, metric: a.metric, caption });
function caseFile(a, { basis, i = 0 } = {}) {
  const sevLabel = { critical: 'Critical', high: 'High', medium: 'Medium' }[a.severity] || a.severity;
  const typeLabel = TYPE[a.leakage_type] || METRIC[a.metric]?.label || a.metric;
  const cap = s => `All ${s.points.length} ${s.entity === "branch" ? "branches" : s.entity + "s"} compared on ${METRIC[a.metric]?.label?.toLowerCase() || a.metric}. Shaded: typical range. Dashed line: where flagging starts.`;
  let strip = '';
  if (a.strip) strip = stripOf(a.strip, a, cap(a.strip));
  else if (a.detection_method === 'PEER') strip = `<div data-strip="${esc(a.check_id)}|${esc(a.entity_id)}|${esc(a.metric)}|${a.baseline_value}"></div>`;
  const vs = a.observed_value != null && a.metric
    ? `<div class="versus"><span class="obs">${esc(mf(a.metric, a.observed_value))}</span><span class="vs">${a.detection_method === 'PEER' ? 'against a peer median of' : 'recently, against'} <b>${esc(mf(a.metric, a.baseline_value))}</b></span></div>` : '';
  return `<article class="case spot sev-${esc(a.severity)}" data-reveal style="--d:${i}">
    <div><h3>${esc(a.entity_label)}<small>${esc(typeLabel)}</small></h3>${vs}
      <p class="why">${esc(trimLabel(a))}</p>${strip}${basis ? `<p class="basis">${esc(basis)}</p>` : ''}</div>
    <div class="case-fig">${a.estimated_exposure ? `<span class="amt">${esc(fmt.money(a.estimated_exposure))}</span><span class="per">about ${esc(fmt.money(a.annualised_exposure))} a year</span>` : `<span class="per">Change in behaviour, not sized</span>`}
      <span class="sev">${esc(sevLabel)} severity</span></div></article>`;
}
async function hydrateStrips(root) {
  for (const el of $$('[data-strip]', root)) {
    const [check, entity, metric, median] = el.dataset.strip.split('|');
    try {
      const s = await api(`/api/peer-strip?check=${encodeURIComponent(check)}&entity=${encodeURIComponent(entity)}`);
      const a = { entity_id: entity, metric, baseline_value: +median };
      el.outerHTML = stripOf(s, a, `All ${s.points.length} ${s.entity === "branch" ? "branches" : s.entity + "s"} compared on ${METRIC[metric]?.label?.toLowerCase() || metric}. Shaded: typical range. Dashed line: where flagging starts.`);
    } catch (_) { el.remove(); }
  }
}
const kpi = (k, valueHtml, d, { bad = false, sp = '' } = {}) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v ${bad ? 'bad' : ''}">${valueHtml}</div><div class="d">${d || ''}</div>${sp}</div>`;
const stripRow = items => `<div class="strip-row" data-reveal>${items.join('')}</div>`;
const pageHead = (title, sub) => `<section data-reveal><h1 class="title">${esc(title)}</h1><p class="sub">${esc(sub)}</p></section>`;
const section = (title, note, body, id = '') => `<section data-reveal ${id ? `id="${id}"` : ''}><h2 class="sec">${esc(title)}</h2>${note ? `<p class="sec-note">${note}</p>` : ''}${body}</section>`;
const emphasise = t => esc(t).replace(/(PKR [\d.,]+[KMB]?)/g, '<em>$1</em>');

/* ---------------------------------------------------------------- views */
const VIEWS = {
  async overview() {
    const [d, br, su, inv, pr] = await Promise.all([api('/api/overview'), api('/api/branches'), api('/api/suppliers'), api('/api/inventory'), api('/api/products')]);
    CUR = d.currency;
    const k = d.kpis, v = key => k[key]?.value_numeric, monthly = d.monthly, labels = monthly.map(m => m.year_month);
    const last = monthly[monthly.length - 1], prior = monthly[monthly.length - 13];
    const yoy = prior ? last.net_revenue / prior.net_revenue - 1 : null;
    const detected = d.leakage_by_type.filter(t => t.leakage_type !== 'LOW_MARGIN_SHORTFALL');
    const aboveAll = su.scorecards.filter(s => s.ppv_amount > 1), above = aboveAll.slice(0, 4), atStd = su.scorecards.length - aboveAll.length;

    const hero = `<section class="hero" data-reveal>
      <div><h1 class="lead">${emphasise(d.headline.lead)}</h1><p class="support">${esc(d.headline.support)}</p>
        <div class="hero-cta"><a class="pill solid" href="#findings">See the findings <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a><button class="pill" type="button" id="hero-dl">Download the report</button></div></div>
      <div class="pulse"><div class="ttl"><div><b>Net sales</b><small>Last 12 months</small></div><div class="now">${esc(fmt.money(last.net_revenue))}<small>${yoy != null ? `${esc(fmt.spct(yoy))} on a year ago` : 'last month'}</small></div></div>
        ${heroBars(monthly.slice(-12))}
        <div class="cap"><span>Data ${esc(fmt.month(labels[0]))} to ${esc(fmt.month(labels[labels.length - 1]))}</span><span>12-month total <b>${esc(fmt.money(monthly.slice(-12).reduce((a, m) => a + m.net_revenue, 0)))}</b></span></div></div></section>`;

    const strip = stripRow([
      kpi('Net sales', count(v('net_revenue'), 'money'), `${esc(fmt.money(v('net_revenue_12m')))} in the last 12 months`, { sp: spark(monthly.map(m => m.net_revenue), 'teal') }),
      kpi('Gross profit', count(v('gross_profit'), 'money'), `margin ${esc(fmt.pct(v('gross_margin')))}`, { sp: spark(monthly.map(m => m.gross_profit), 'ink') }),
      kpi('Discounts given', count(v('discount_rate'), 'pct1'), `${esc(fmt.money(v('discount_amount')))} off list prices`, { sp: spark(monthly.map(m => m.discount_rate), 'rasp') }),
      kpi('Units returned', count(v('return_rate_units'), 'pct1'), `${esc(fmt.money(v('return_value')))} refunded`, { sp: spark(monthly.map(m => m.return_rate_units), 'amber') }),
      kpi('Leakage per year', count(v('leakage_annualised'), 'money'), `${esc(fmt.money(v('leakage_total')))} over the period`, { bad: true }),
    ]);

    const findings = section('Case files', 'The largest findings, each compared with every peer. These are statistical flags with a size and an explanation; someone still has to find the cause.',
      `${d.findings.map((a, i) => caseFile(a, { i })).join('') || '<p class="muted">Nothing stands out from peers right now.</p>'}<p><a class="more" href="#/leakage">See every leakage finding</a></p>`, 'findings');

    const sc = br.scorecards;
    const tiles = section('How the branches compare', 'Each branch scored against the others on margin, discounting, returns, stock loss and growth. Select one for the full breakdown.',
      `<div class="tiles">${sc.map((r, i) => `<a class="tile spot ${esc(r.performance_band)}" href="#/branches" data-branch="${esc(r.branch_id)}" style="--d:${i}" data-reveal>
        <div class="id">${esc(r.branch_id)}</div><div class="city">${esc(r.city)}</div><div class="sc">${fmt.num(r.performance_score, 0)}</div>
        <div class="bandname">${esc(sentence(r.performance_band))}${r.operational_risk !== 'low' ? `, ${esc(r.operational_risk)} risk` : ''}</div><div class="meter"><i style="width:${r.performance_score}%"></i></div></a>`).join('')}</div>`);

    const mix = section('Where the exposure sits', 'Detected leakage by type. Findings can overlap, so read this as exposure, not as cash that can all be recovered.',
      detected.length ? tape(detected.map(t => ({ label: TYPE[t.leakage_type] || t.leakage_type, value: t.exposure_amount, color: TYPE_COLOR[t.leakage_type] || 'var(--muted)' }))) : '<p class="muted">No detected leakage.</p>');

    const chart = `<section data-reveal><h2 class="sec">Sales and profit by month</h2>
      <div class="controls"><div class="seg" role="group" aria-label="Measure" id="m-seg">${[['sales', 'Sales and profit'], ['margin', 'Margin'], ['discount', 'Discount rate'], ['returns', 'Return rate']].map(([id, l], i) => `<button type="button" data-m="${id}" aria-pressed="${i === 0}">${l}</button>`).join('')}</div></div>
      <div id="m-chart"></div></section>`;

    const watch = section('Worth a look', 'Three short lists to start the week with.', `<div class="lists">
      <div><h3>Paid above standard cost</h3><p class="note">${above.length ? `${fmt.full(atStd)} of ${fmt.full(su.scorecards.length)} suppliers were paid exactly standard.` : 'All suppliers at standard cost.'}</p>
        ${above.map(s => `<a class="wl" href="#/suppliers"><span class="n">${esc(s.supplier_id)}</span><span class="t">${esc(fmt.spct(s.ppv_pct))} on ${esc(fmt.money(s.purchase_standard_spend))} of purchases</span><span class="x bad">${esc(fmt.money(s.ppv_amount))}</span></a>`).join('')}<a class="more" href="#/suppliers">Supplier intelligence</a></div>
      <div><h3>Closest to a stockout</h3><p class="note">Movement-based proxy, not a stock count.</p>
        ${inv.risk_top.slice(0, 4).map(r => `<a class="wl" href="#/inventory"><span class="n">${esc(r.product_id)}</span><span class="t">${esc(r.category)}, ${fmt.num(r.velocity_per_day, 1)} a day, ${fmt.pct(r.replenishment_ratio, 0)} replenished</span><span class="x bad">${fmt.num(r.stockout_risk_score, 0)}</span></a>`).join('')}<a class="more" href="#/inventory">Inventory intelligence</a></div>
      <div><h3>Products needing attention</h3><p class="note">${fmt.full(pr.counts.problematic)} of ${fmt.full(pr.counts.total)} products are flagged.</p>
        ${pr.problematic.slice(0, 4).map(p => `<a class="wl" href="#/products"><span class="n">${esc(p.product_id)}</span><span class="t">${esc(p.category)}, margin ${fmt.pct(p.gross_margin, 0)}, returns ${fmt.pct(p.return_rate_units, 0)}</span><span class="x">${esc(fmt.money(p.net_revenue))}</span></a>`).join('')}<a class="more" href="#/products">Product intelligence</a></div></div>`);

    return {
      html: hero + strip + findings + tiles + mix + chart + watch,
      after() {
        const draw = m => {
          const S = {
            sales: [{ name: 'Net sales', color: 'teal', area: true, values: monthly.map(x => x.net_revenue) }, { name: 'Gross profit', color: 'ink', values: monthly.map(x => x.gross_profit) }],
            margin: [{ name: 'Gross margin', color: 'teal', area: true, values: monthly.map(x => x.gross_margin) }],
            discount: [{ name: 'Discount rate', color: 'rasp', area: true, values: monthly.map(x => x.discount_rate) }],
            returns: [{ name: 'Return rate', color: 'amber', area: true, values: monthly.map(x => x.return_rate_units) }],
          }[m];
          const host = $('#m-chart');
          host.innerHTML = lineChart({ labels, series: S, yFmt: m === 'sales' ? fmt.axis : x => fmt.pct(x), zero: m === 'sales' || m === 'margin', tipFmt: m === 'sales' ? x => fmt.money(x) : x => fmt.pct(x, 2) });
          host.closest('[data-reveal]').classList.add('in');
        };
        draw('sales');
        $('#m-seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#m-seg button').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.m); });
        $('#hero-dl').addEventListener('click', () => download('pdf'));
        $$('.tile', view).forEach(t => t.addEventListener('click', () => { try { sessionStorage.setItem('pp.branch', t.dataset.branch); } catch (_) { /* ignore */ } }));
      },
    };
  },

  async leakage() {
    const d = await api('/api/leakage');
    CUR = d.currency;
    const detected = d.by_type.filter(t => t.leakage_type !== 'LOW_MARGIN_SHORTFALL');
    const opp = d.items.filter(i => i.leakage_type === 'LOW_MARGIN_SHORTFALL');
    const found = d.items.filter(i => i.leakage_type !== 'LOW_MARGIN_SHORTFALL');
    const totalAnn = detected.reduce((a, t) => a + t.annualised_exposure, 0);
    const html = pageHead('Revenue leakage', 'Money that leaves the business through discounts, returns, inventory discrepancies and supplier prices. Every figure is the gap between one entity and its peers, applied to that entity\'s own volume.') +
      `<section data-reveal>${detected.length ? tape(detected.map(t => ({ label: TYPE[t.leakage_type] || t.leakage_type, value: t.exposure_amount, color: TYPE_COLOR[t.leakage_type] || 'var(--muted)' }))) : ''}
        ${table([
          { key: 't', label: 'Type', left: true, html: r => esc(TYPE[r.leakage_type] || r.leakage_type), sort: r => r.exposure_amount },
          { key: 'findings', label: 'Findings', html: r => fmt.full(r.findings) },
          { key: 'exposure_amount', label: 'Exposure', html: r => fmt.money(r.exposure_amount) },
          { key: 'annualised_exposure', label: 'Per year', html: r => fmt.money(r.annualised_exposure) },
          { key: 'share', label: 'Share of detected', html: r => r.leakage_type === 'LOW_MARGIN_SHORTFALL' ? '<span class="muted">opportunity, counted apart</span>' : `${bar(r.annualised_exposure / (totalAnn || 1), 'rasp')}${fmt.pct(r.annualised_exposure / (totalAnn || 1), 0)}`, sort: r => r.annualised_exposure },
        ], d.by_type, { sortKey: 'exposure_amount' })}</section>` +
      section('Detected leakage', 'Raised only where the entity is far from its peers (robust z-score) and the gap is big enough to matter. Inventory-discrepancy exposure is an upper bound: stock adjustments carry no direction in the source data.',
        found.map((i, n) => caseFile({ ...i, estimated_exposure: i.exposure_amount, explanation: i.explanation || i.basis, severity: i.severity || 'medium' }, { basis: i.basis, i: n })).join('') || '<p class="muted">No detected leakage.</p>') +
      section('Pricing opportunity in low-margin products', `${fmt.full(opp.length)} products sit in the bottom margin decile of their category. Lifting them to the category median would be worth ${esc(fmt.money(opp.reduce((a, i) => a + i.exposure_amount, 0)))} over the period. Every category has a bottom decile by definition, so this is shown as an opportunity, never added to detected leakage.`,
        `${table([
          { key: 'entity_label', label: 'Product', left: true, html: r => `<span class="id">${esc(r.entity_label)}</span>` },
          { key: 'exposure_amount', label: 'Shortfall vs category median', html: r => fmt.money(r.exposure_amount) },
          { key: 'annualised_exposure', label: 'Per year', html: r => fmt.money(r.annualised_exposure) },
        ], opp.slice(0, 15), { sortKey: 'exposure_amount' })}<p class="muted">Showing the 15 largest of ${fmt.full(opp.length)}. All of them are in the Excel report.</p>`);
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
      return `<td style="background:${heatColor(t)}" data-tip="<b>${esc(b)}, ${esc(c)}</b><br>Replenished ${fmt.pct(r.replenishment_ratio, 0)} of units sold<br>${fmt.full(r.units_sold)} sold, ${fmt.full(r.units_purchased)} purchased<br>Damaged or adjusted: ${fmt.pct(r.shrink_rate, 1)} of units sold">${fmt.pct(r.replenishment_ratio, 0)}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table>`;
    const coverage = mv.reduce((a, m) => a + m.units_purchased, 0) / mv.reduce((a, m) => a + m.units_sold, 0);
    const html = pageHead('Inventory intelligence', 'Where stock is moving, where it is not being replaced, and which products deserve a reorder review.') +
      `<section data-reveal><div class="margin-note"><h3>Read this first</h3><p>The data records stock movements but not stock on hand, so none of this is a count of units on the shelf. The scores below rank products by sales speed, how much of what was sold has been bought back, and how long since the last purchase. Use them to decide where to look, then check the shelf.</p></div>
        ${stripRow([
          kpi('High stockout-risk proxy', count(v('stockout_high'), 'int'), 'fast sellers that are barely replenished', { bad: true }),
          kpi('Reorder review', count(v('reorder_flags'), 'int'), 'fast sellers, no recent purchase'),
          kpi('Slow-moving', count(v('slow_moving'), 'int'), 'bottom fifth of their category'),
          kpi('Dead-stock candidates', count(v('dead_stock'), 'int'), 'no sale in 60 days'),
        ])}</section>` +
      section('Units purchased and sold', `Across the whole business, purchases cover only about ${fmt.pct(coverage, 0)} of the units sold. Either stock is held elsewhere, or purchases are missing from the data. Worth asking.`,
        lineChart({ labels: mv.map(m => m.year_month), series: [{ name: 'Units sold', color: 'ink', values: mv.map(m => m.units_sold) }, { name: 'Units purchased', color: 'teal', area: true, values: mv.map(m => m.units_purchased) }], yFmt: x => fmt.axis(x), tipFmt: x => fmt.full(x) })) +
      section('Products to look at first', 'Highest stockout-risk proxy. Click a column to re-sort.', table([
        { key: 'product_id', label: 'Product', left: true, html: r => `<span class="id">${esc(r.product_id)}</span>`, sort: r => r.product_id },
        { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
        { key: 'velocity_per_day', label: 'Units a day', html: r => fmt.num(r.velocity_per_day, 2) },
        { key: 'replenishment_ratio', label: 'Replenished', html: r => fmt.pct(r.replenishment_ratio, 0) },
        { key: 'days_since_last_purchase', label: 'Days since purchase', html: r => r.days_since_last_purchase ?? 'n/a' },
        { key: 'stockout_risk_score', label: 'Risk score', html: r => `${bar(r.stockout_risk_score / 100, r.stockout_risk_level === 'high' ? 'rasp' : 'amber')}${fmt.num(r.stockout_risk_score, 0)}` },
        { key: 'stockout_risk_level', label: 'Level', html: r => chip(r.stockout_risk_level) },
        { key: 'explanation', label: 'Why', why: true, html: r => esc(r.explanation), sortable: false },
      ], d.risk_top, { sortKey: 'stockout_risk_score' })) +
      section('Replenishment by branch and category', 'Units purchased as a share of units sold in the last 180 days. Red cells are being replaced least. Hover for the numbers.', `<div class="tablewrap">${heat}</div>`) +
      section('Slowest-moving products', '', table([
        { key: 'product_id', label: 'Product', left: true, html: r => `<span class="id">${esc(r.product_id)}</span>` },
        { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
        { key: 'velocity_per_day', label: 'Units a day', html: r => fmt.num(r.velocity_per_day, 2) },
        { key: 'units_sold_recent', label: 'Units sold, last 90 days', html: r => fmt.full(r.units_sold_recent) },
        { key: 'days_since_last_sale', label: 'Days since last sale', html: r => r.days_since_last_sale ?? 'never' },
      ], d.slow, { sortKey: 'velocity_per_day', desc: false }));
    return { html };
  },

  async branches() {
    const d = await api('/api/branches');
    CUR = d.currency;
    const sc = d.scorecards, byB = {};
    d.monthly.forEach(r => (byB[r.branch_id] ||= []).push(r));
    const months = [...new Set(d.monthly.map(r => r.year_month))].sort();
    const rank = { high: 0, medium: 1, low: 2 };
    let saved = null; try { saved = sessionStorage.getItem('pp.branch'); sessionStorage.removeItem('pp.branch'); } catch (_) { /* ignore */ }
    const start = sc.find(r => r.branch_id === saved) || [...sc].sort((a, b) => (rank[a.operational_risk] - rank[b.operational_risk]) || a.performance_rank - b.performance_rank)[0];
    const maxRev = Math.max(...sc.map(r => r.net_revenue));
    const points = sc.map(r => ({ x: r.gross_margin, y: r.discount_rate, r: 7 + 7 * Math.sqrt(r.net_revenue / maxRev), label: r.branch_id, hot: r.operational_risk !== 'low',
      tip: `<b>${r.branch_id} ${r.city}</b><br>Margin ${fmt.pct(r.gross_margin)}<br>Discount rate ${fmt.pct(r.discount_rate)}<br>Net sales ${fmt.money(r.net_revenue)}<br>Score ${fmt.num(r.performance_score, 0)}` }));
    const comp = d.weights.components;
    const html = pageHead('Branch performance', 'How each branch compares on margin, discounting, returns, stock losses and growth, with a score you can take apart.') +
      section('Margin against discounting', 'Branches that discount heavily and earn thin margins sit apart from the healthy cluster. Dot size is net sales; red marks a branch with a high or medium risk finding.', scatter({ points, xFmt: x => fmt.pct(x, 0), yFmt: y => fmt.pct(y, 0), xLabel: 'Gross margin', yLabel: 'Discount rate' })) +
      section('Scorecard', 'The score is 100 times the weighted average of five measures, each compared with the other branches (typical = 0.5, far worse = 0, far better = 1). A big gap costs far more than a small one. Select a branch to see its score taken apart.',
        table([
          { key: 'performance_rank', label: 'Rank', html: r => r.performance_rank, sort: r => -r.performance_rank },
          { key: 'branch_id', label: 'Branch', left: true, primary: true, html: r => `<span class="id">${esc(r.branch_id)}</span> ${esc(r.city)}` },
          { key: 'net_revenue', label: 'Net sales', html: r => fmt.money(r.net_revenue) },
          { key: 'gross_margin', label: 'Margin', html: r => fmt.pct(r.gross_margin) },
          { key: 'discount_rate', label: 'Discount', html: r => fmt.pct(r.discount_rate) },
          { key: 'return_rate_units', label: 'Returns', html: r => fmt.pct(r.return_rate_units) },
          { key: 'shrink_rate', label: 'Stock loss', html: r => fmt.pct(r.shrink_rate) },
          { key: 'growth_rate', label: 'Growth', html: r => delta(r.growth_rate) },
          { key: 'performance_score', label: 'Score', html: r => `${bar(r.performance_score / 100, r.performance_band === 'weak' ? 'rasp' : r.performance_band === 'watch' ? 'amber' : '')}${fmt.num(r.performance_score, 0)}` },
          { key: 'performance_band', label: 'Band', html: r => chip(r.performance_band) },
          { key: 'operational_risk', label: 'Risk', html: r => chip(r.operational_risk) },
        ], sc, { pick: { key: 'branch_id', fn: r => showBranch(r.branch_id) }, selected: start.branch_id, sortKey: 'performance_rank', desc: true })) +
      '<section id="branch-detail" data-reveal></section>';
    const showBranch = id => {
      const r = sc.find(x => x.branch_id === id), anoms = d.anomalies.filter(a => a.entity_id === id);
      const parts = [['gross_margin', 'score_gross_margin'], ['discount_rate', 'score_discount_rate'], ['return_rate_units', 'score_return_rate'], ['shrink_rate', 'score_shrink_rate'], ['growth_rate', 'score_growth_rate']];
      const names = { gross_margin: 'Gross margin', discount_rate: 'Discount rate', return_rate_units: 'Return rate', shrink_rate: 'Stock loss', growth_rate: 'Growth' };
      const items = parts.map(([m, s]) => ({ label: names[m], value: Math.max(r[s] * comp[m].weight * 100, 0.4), color: r[s] < 0.34 ? 'rasp' : r[s] < 0.67 ? 'amber' : 'teal', tip: `<b>${names[m]}</b><br>Measure score ${fmt.num(r[s], 2)} x weight ${fmt.pct(comp[m].weight, 0)} = ${fmt.num(r[s] * comp[m].weight * 100, 1)} of ${fmt.num(comp[m].weight * 100, 0)} points` }));
      const host = $('#branch-detail');
      host.classList.remove('in');
      host.innerHTML = `<h2 class="sec">${esc(r.branch_id)} (${esc(r.city)}), score ${fmt.num(r.performance_score, 0)} of 100</h2>
        <p class="sec-note">Points earned on each measure. Worth: ${parts.map(([m]) => `${names[m].toLowerCase()} ${fmt.pct(comp[m].weight, 0)}`).join(', ')}. Short bars are where this branch loses points.</p>
        ${hbars(items, { valFmt: x => `${x.toFixed(1)} pts`, labelW: 120, rowH: 34, width: 720 })}
        <div class="controls"><div class="seg" id="bm-seg" role="group" aria-label="Measure">${[['discount_rate', 'Discount rate'], ['gross_margin', 'Margin'], ['return_rate_units', 'Return rate']].map(([k, l], i) => `<button type="button" data-k="${k}" aria-pressed="${i === 0}">${l}</button>`).join('')}</div></div>
        <div id="bm-chart"></div>${anoms.map((a, i) => caseFile(a, { i })).join('')}`;
      armReveal(host); setTimeout(() => host.classList.add('in'), 30);
      const draw = key => {
        const series = byB[id] || [], med = months.map(mo => { const vals = d.monthly.filter(x => x.year_month === mo && x.branch_id !== id).map(x => x[key]).filter(x => x != null).sort((a, b) => a - b); return vals.length ? vals[Math.floor(vals.length / 2)] : null; });
        $('#bm-chart').innerHTML = lineChart({ labels: months, series: [{ name: id, color: 'rasp', area: true, values: months.map(mo => series.find(x => x.year_month === mo)?.[key] ?? null) }, { name: 'Median of other branches', color: 'teal', values: med, dash: true, width: 2 }], yFmt: x => fmt.pct(x, 1), tipFmt: x => fmt.pct(x, 1), zero: false });
      };
      draw('discount_rate');
      $('#bm-seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('#bm-seg button').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.k); });
      hydrateStrips(host);
    };
    return { html, after: () => showBranch(start.branch_id) };
  },

  async suppliers() {
    const d = await api('/api/suppliers');
    CUR = d.currency;
    const sc = d.scorecards, flagged = new Set(d.anomalies.map(a => a.entity_id));
    const above = sc.filter(x => x.ppv_amount > 1), top = [...above].sort((a, b) => b.ppv_amount - a.ppv_amount).slice(0, 12), atStd = sc.length - above.length;
    const byS = {}; d.monthly.forEach(r => (byS[r.supplier_id] ||= []).push(r));
    const months = [...new Set(d.monthly.map(r => r.year_month))].sort();
    const html = pageHead('Supplier intelligence', 'What you pay each supplier compared with the standard cost of the goods, how steady their orders are, and how their products perform after sale.') +
      section('Paid above standard cost', `Standard cost is the median cost recorded on a product's non-purchase events. A supplier far above its peers is not necessarily at fault (a price rise may have been agreed) but it is where to ask first. ${fmt.full(atStd)} of ${fmt.full(sc.length)} suppliers were paid exactly the standard cost.`,
        hbars(top.map(s => ({ label: s.supplier_id, value: Math.max(s.ppv_amount, 0), color: flagged.has(s.supplier_id) ? 'rasp' : 'teal', bold: flagged.has(s.supplier_id), tip: `<b>${s.supplier_id}</b><br>${fmt.pct(s.ppv_pct)} above standard on ${fmt.money(s.purchase_standard_spend)} of purchases` })), { valFmt: x => fmt.money(x), labelW: 100, rowH: 36 }) +
        d.anomalies.map((a, i) => caseFile(a, { i })).join('')) +
      (Object.keys(byS).length ? section('Monthly cost against standard', 'Purchase price against standard cost, by month, for suppliers with a finding. A flat line well above zero means the premium is built into every order.',
        lineChart({ labels: months, series: Object.entries(byS).map(([id, rows]) => ({ name: id, color: 'rasp', area: true, values: months.map(mo => rows.find(r => r.year_month === mo)?.ppv_pct ?? null) })).concat([{ name: 'Zero (at standard cost)', color: 'teal', values: months.map(() => 0), dash: true, width: 1.8 }]), yFmt: x => fmt.pct(x, 0), tipFmt: x => fmt.spct(x, 1), zero: true })) : '') +
      section('All suppliers', '', '<div class="controls"><input type="search" id="sup-q" placeholder="Filter by supplier ID" aria-label="Filter suppliers"></div><div id="sup-table"></div>');
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
    const html = pageHead('Product intelligence', `${fmt.full(c.total)} products, sorted into the groups that matter: where the margin is, where it is not, and where customers push back.`) +
      `<section data-reveal><div class="controls"><div class="seg" id="seg" role="group" aria-label="Product group">${segs.map(([k, l], i) => `<button type="button" data-k="${k}" aria-pressed="${i === 0}">${l}<span class="n">${fmt.full(c[k])}</span></button>`).join('')}</div></div>
        <div id="seg-table"></div><p class="muted" id="seg-note"></p></section>` +
      section('By category', 'Margin and return rate for each category.', table([
        { key: 'category', label: 'Category', left: true, html: r => esc(r.category) },
        { key: 'products', label: 'Products', html: r => fmt.full(r.products) },
        { key: 'net_revenue', label: 'Net sales', html: r => fmt.money(r.net_revenue) },
        { key: 'gross_margin', label: 'Margin', html: r => `${bar(r.gross_margin / 0.4)}${fmt.pct(r.gross_margin)}` },
        { key: 'return_rate', label: 'Return rate', html: r => fmt.pct(r.return_rate) },
      ], d.category, { sortKey: 'net_revenue' }));
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
    const html = pageHead('Pipeline health', 'Every event is accounted for: read, accepted or quarantined, then stored. If any of these numbers stop adding up, the quality gates fail.') +
      `<section data-reveal><div class="flow">
        <div><div class="k">Published to Kafka</div><div class="v">${count(f.published, 'int')}</div></div>
        <div><div class="k">Read from Kafka</div><div class="v">${count(f.landed, 'int')}</div></div>
        <div><div class="k">Passed validation</div><div class="v">${count(f.accepted, 'int')}</div></div>
        <div><div class="k">Quarantined</div><div class="v ${f.rejected ? 'bad' : ''}">${count(f.rejected, 'int')}</div></div>
        <div><div class="k">Stored in warehouse</div><div class="v">${count(f.stored, 'int')}</div></div></div>
        <p class="sec-note">${ok} of ${d.checks.length} reconciliation, integrity and freshness checks pass. Total processing time across Spark stages: ${fmt.num(d.processing_seconds, 0)} seconds.</p></section>` +
      section('Stages, latest run', '', table([
        { key: 'stage', label: 'Stage', left: true, html: r => esc(stageName[r.stage] || r.stage), sort: r => r.stage },
        { key: 'status', label: 'Status', html: r => chip(r.status.toLowerCase(), r.status === 'SUCCESS' ? 'pass' : 'fail') },
        { key: 'duration_seconds', label: 'Duration', html: r => r.duration_seconds == null ? 'n/a' : `${bar(r.duration_seconds / maxDur)}${fmt.num(r.duration_seconds, 1)} s` },
        { key: 'rows_in', label: 'Rows in', html: r => fmt.full(r.rows_in) },
        { key: 'rows_out', label: 'Rows out', html: r => fmt.full(r.rows_out) },
        { key: 'rows_rejected', label: 'Rejected', html: r => fmt.full(r.rows_rejected) },
      ], d.runs, { sortKey: null })) +
      section('Quality gates', '', `<ul class="checks">${d.checks.filter(c => c.severity !== 'INFO').map(c => `<li><span>${chip(c.status.toLowerCase())}</span><span><span class="nm">${esc(c.check_name.replaceAll('_', ' ').toLowerCase().replace(/^./, m => m.toUpperCase()))}</span><span class="ms">${esc(c.message || '')}</span></span></li>`).join('')}</ul>`) +
      section('Quarantine', 'Nothing is dropped silently. Rejected messages are stored verbatim with the rule they broke; warnings were accepted and recorded.', d.rules.length ? table([
        { key: 'rule_code', label: 'Rule', left: true, html: r => `<span class="id">${esc(r.rule_code)}</span>` },
        { key: 'disposition', label: 'What happened', html: r => chip(r.disposition === 'REJECTED' ? 'rejected' : 'accepted with warning', r.disposition === 'REJECTED' ? 'fail' : 'warn') },
        { key: 'failures', label: 'Messages', html: r => fmt.full(r.failures) },
      ], d.rules, { sortKey: 'failures' }) : '<p class="muted">Nothing has been rejected or flagged.</p>');
    return { html };
  },
};

const PAGES = [
  { id: 'overview', name: 'Executive overview', rail: 'Overview', short: 'Overview' }, { id: 'leakage', name: 'Revenue leakage', short: 'Leakage' },
  { id: 'inventory', name: 'Inventory intelligence', rail: 'Inventory', short: 'Stock' }, { id: 'branches', name: 'Branch performance', rail: 'Branches', short: 'Branches' },
  { id: 'suppliers', name: 'Supplier intelligence', rail: 'Suppliers', short: 'Suppliers' }, { id: 'products', name: 'Product intelligence', rail: 'Products', short: 'Products' },
  { id: 'health', name: 'Pipeline health', short: 'Health' },
];
let counts = {};

function renderNav(active) {
  const first = !$('#nav .ind');
  $('#nav').innerHTML = '<span class="ind" aria-hidden="true"></span>' + PAGES.map(p => {
    const n = counts[p.id];
    const badge = p.id === 'overview' ? '' : p.id === 'health' ? (n ? `<span class="count hot">${n}</span>` : '<span class="count ok">ok</span>') : n != null ? `<span class="count ${n && p.id !== 'inventory' && p.id !== 'products' ? 'hot' : ''}">${fmt.full(n)}</span>` : '';
    return `<a href="#/${p.id}" title="${esc(p.name)}" ${p.id === active ? 'aria-current="page"' : ''}>${icon(p.id)}<span class="label">${esc(p.rail || p.name)}</span>${badge}</a>`;
  }).join('');
  $('#tabbar').innerHTML = PAGES.map(p => `<a href="#/${p.id}" ${p.id === active ? 'aria-current="page"' : ''} aria-label="${esc(p.name)}">${icon(p.id)}<span>${esc(p.short)}</span>${(p.id === 'leakage' || p.id === 'branches' || p.id === 'suppliers') && counts[p.id] ? '<i class="dot"></i>' : ''}</a>`).join('');
  placeNav(first);
  $('#crumb').textContent = PAGES.find(p => p.id === active)?.name || '';
  document.title = `${PAGES.find(p => p.id === active)?.name || 'Overview'} | ProfitPulse`;
}

let navPrev = null;
/** Slide the rail's highlight from the previous page's item to the current one. */
function placeNav(instant) {
  const ind = $('#nav .ind'), cur = $('#nav a[aria-current="page"]');
  if (!ind || !cur || !cur.offsetHeight) return;
  const go = (el, anim) => { ind.style.transition = anim ? '' : 'none'; ind.style.transform = `translateY(${el.offsetTop}px)`; ind.style.height = `${el.offsetHeight}px`; ind.classList.add('on'); };
  if (instant || navPrev == null || REDUCED) go(cur, false);
  else { ind.style.transition = 'none'; ind.style.transform = `translateY(${navPrev}px)`; ind.classList.add('on'); void ind.offsetWidth; requestAnimationFrame(() => go(cur, true)); }
  navPrev = cur.offsetTop;
}

/** Segmented controls get a sliding thumb under the pressed button. */
function placeSeg(seg, instant) {
  const on = $('button[aria-pressed="true"]', seg), ind = $('.seg-ind', seg);
  if (!on || !ind) return;
  ind.style.transition = instant || REDUCED ? 'none' : '';
  ind.style.width = `${on.offsetWidth}px`;
  ind.style.transform = `translate(${on.offsetLeft}px, ${on.offsetTop - 3}px)`;
}
function armSegs(root = document) {
  $$('.seg', root).forEach(seg => {
    if (seg.classList.contains('has-ind')) return;
    seg.classList.add('has-ind');
    seg.prepend(Object.assign(document.createElement('span'), { className: 'seg-ind' }));
    requestAnimationFrame(() => placeSeg(seg, true));
    seg.addEventListener('click', () => requestAnimationFrame(() => placeSeg(seg)));
  });
}
new MutationObserver(() => armSegs(view)).observe(view, { childList: true, subtree: true });
addEventListener('resize', () => { $$('.seg.has-ind').forEach(sg => placeSeg(sg, true)); placeNav(true); }, { passive: true });

/** Pointer-led highlight on tiles and case files. */
document.addEventListener('pointermove', e => {
  const el = e.target.closest?.('.spot');
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty('--mx', `${e.clientX - r.left}px`);
  el.style.setProperty('--my', `${e.clientY - r.top}px`);
}, { passive: true });

/** Thin brass progress line while a page loads. */
const progress = {
  el: $('#progress'),
  start() { if (!this.el) return; this.el.className = ''; void this.el.offsetWidth; this.el.className = 'run'; },
  done() { if (this.el) this.el.className = 'done'; },
};

const skeleton = () => `<section><div class="skel" style="height:64px;width:min(620px,90%);margin-bottom:16px"></div><div class="skel" style="height:64px;width:min(440px,70%);margin-bottom:26px"></div><div class="skel" style="height:22px;width:min(520px,80%)"></div></section>
  <section style="margin-top:56px"><div class="skel" style="height:110px"></div></section><section style="margin-top:56px"><div class="skel" style="height:240px"></div></section>`;

let routeToken = 0;
async function route() {
  const id = (location.hash.replace(/^#\//, '').split('?')[0] || 'overview');
  const page = VIEWS[id] ? id : 'overview';
  const token = ++routeToken;
  renderNav(page);
  view.setAttribute('aria-busy', 'true');
  progress.start();
  view.innerHTML = skeleton();
  let res;
  try { res = await VIEWS[page](); } catch (e) { res = { html: `<div class="empty"><h1>Nothing to show yet</h1><p class="err">${esc(e.message)}</p></div>` }; }
  if (token !== routeToken) return;                       // a newer navigation superseded this one
  const apply = () => {
    view.innerHTML = res.html;
    armReveal(view);
    if (res.after) res.after();
    view.removeAttribute('aria-busy');
    progress.done();
    armSegs(view);
    window.scrollTo({ top: 0, behavior: 'instant' });
  };
  if (document.startViewTransition && !REDUCED) {
    // A transition can be skipped when navigations overlap; the update callback still runs, so ignore the rejections.
    const t = document.startViewTransition(apply);
    t.ready.catch(() => {}); t.finished.catch(() => {}); t.updateCallbackDone.catch(() => {});
  } else apply();
}

/* ---------------------------------------------------------------- downloads + top bar */
async function download(kind) {
  const name = kind === 'pdf' ? 'PDF' : 'Excel';
  const btns = $$(`[data-name="${name}"]`);
  if (btns[0]?.getAttribute('aria-busy') === 'true') return;
  const labels = btns.map(b => b.innerHTML);
  const label = b => b.querySelector('span') || b;
  btns.forEach(b => { b.setAttribute('aria-busy', 'true'); label(b).textContent = `Preparing ${name}...`; });
  try {
    const r = await fetch(`/api/reports/${kind}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `The server returned ${r.status}`);
    const blob = await r.blob();
    const file = /filename="?([^";]+)/.exec(r.headers.get('Content-Disposition') || '')?.[1] || `ProfitPulse_report.${kind === 'pdf' ? 'pdf' : 'xlsx'}`;
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: file });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast(`${file} downloaded`);
  } catch (err) { toast(`Could not create the ${name}: ${err.message}`, true); }
  finally { btns.forEach((b, i) => { b.removeAttribute('aria-busy'); b.innerHTML = labels[i]; }); }
}
function wireChrome() {
  for (const [id, kind] of [['dl-pdf', 'pdf'], ['dl-xlsx', 'xlsx'], ['dl-pdf-2', 'pdf'], ['dl-xlsx-2', 'xlsx']]) {
    $(`#${id}`).addEventListener('click', e => { e.preventDefault(); $('#report-menu').hidden = true; $('#report-btn').setAttribute('aria-expanded', 'false'); download(kind); });
  }
  const menu = $('#report-menu'), btn = $('#report-btn');
  btn.addEventListener('click', e => { e.stopPropagation(); menu.hidden = !menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden)); });
  document.addEventListener('click', e => { if (!menu.contains(e.target)) { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') menu.hidden = true; });
  const bar = $('#topbar');
  addEventListener('scroll', () => bar.classList.toggle('scrolled', scrollY > 8), { passive: true });
}

async function boot() {
  wireChrome();
  try {
    const s = await api('/api/status');
    CUR = s.currency;
    if (!s.ready) {
      renderNav('overview'); $('#live-text').textContent = 'No data yet';
      view.innerHTML = `<div class="empty"><h1>No analytics yet</h1><p class="sub">The pipeline has not produced results. Trigger the <b>ingestion_pipeline</b> DAG in Airflow, or run <code>scripts/run_stages.sh</code>, then reload this page.</p></div>`;
      return;
    }
    const ov = await api('/api/overview');
    counts = ov.counts.by_page;
    const asOf = fmt.date(ov.kpis.as_of_date.value_text);
    $('#asof').textContent = `Data to ${asOf}. Amounts in ${CUR}.`;
    $('#live-text').textContent = `Data to ${asOf}`;
    api('/api/health').then(h => { const ok = h.checks.filter(c => c.status === 'PASS').length; $('#foot').innerHTML = `<span>ProfitPulse</span><span>Amounts in ${esc(CUR)}</span><span>${ok} of ${h.checks.length} pipeline checks passing</span><span>${fmt.full(h.flow.stored)} events stored</span>`; }).catch(() => {});
  } catch (e) { /* the view will show the message */ }
  addEventListener('hashchange', route);
  route();
}
boot();
