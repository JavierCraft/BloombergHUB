/* Bloomberg Hub — kerangka antarmuka.
 *
 * Isi berkas ini: satu jalur pengambilan data yang memahami amplop API, satu
 * komponen yang menampilkan empat keadaan dengan jujur (memuat / gagal /
 * kosong / ada data), tabel yang bisa diurutkan, dan empat bentuk grafik SVG.
 *
 * Aturan teks dari luar: setiap nilai yang datang dari API — judul artikel,
 * nama tim, nama kolom — dimasukkan lewat `textContent`, tidak pernah lewat
 * `innerHTML`. Teks pihak ketiga adalah data, bukan markup.
 */
(() => {
  'use strict';

  // -------------------------------------------------------------- pembantu
  const $ = (sel, root = document) => root.querySelector(sel);

  function el(tag, attrs = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      node.setAttribute(k, String(v));
    }
    return node;
  }

  function icon(path, color) {
    const s = svg('svg', {
      viewBox: '0 0 24 24', fill: 'none', stroke: color || 'currentColor',
      'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    });
    s.append(svg('path', { d: path }));
    return s;
  }

  const ICONS = {
    box: 'M21 8v13H3V8M1 3h22v5H1zM10 12h4',
    key: 'M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3',
    block: 'M4.93 4.93l14.14 14.14M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z',
    clock: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zM12 6v6l4 2',
    cloud: 'M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z',
    lock: 'M5 11h14v10H5zM8 11V7a4 4 0 118 0v4',
    edit: 'M11 4H4v16h16v-7M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z',
    alert: 'M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z',
    empty: 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4',
  };

  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

  function compact(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value ?? '');
    const abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (abs >= 1e4) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    if (Number.isInteger(n)) return n.toLocaleString('id-ID');
    return n.toLocaleString('id-ID', { maximumFractionDigits: 4 });
  }

  function niceTicks(min, max, count = 4) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
    const raw = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const start = Math.ceil(min / step) * step;
    const out = [];
    for (let v = start; v <= max + step * 0.001; v += step) out.push(Number(v.toFixed(10)));
    return out.length ? out : [min, max];
  }

  function toast(message) {
    let host = $('.toasts');
    if (!host) { host = el('div', { class: 'toasts' }); document.body.append(host); }
    const node = el('div', { class: 'toast', text: message });
    host.append(node);
    setTimeout(() => node.remove(), 3800);
  }

  // ------------------------------------------------------------------- api
  async function api(path, options = {}) {
    const init = { headers: { 'Content-Type': 'application/json' }, ...options };
    if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
    try {
      const response = await fetch(path, init);
      return await response.json();
    } catch (err) {
      return {
        ok: false, data: null, meta: {},
        error: {
          code: 'NETWORK',
          message: 'Server Bloomberg Hub tidak merespons.',
          hint: 'Pastikan `python app.py` masih berjalan, lalu muat ulang halaman.',
        },
      };
    }
  }

  // -------------------------------------------------------------- keadaan
  const STATES = {
    MISSING_DEP:        { title: 'Package not installed', icon: ICONS.box,   color: 'var(--warning)' },
    MISSING_CREDENTIAL: { title: 'API key required',      icon: ICONS.key,   color: 'var(--warning)' },
    NOT_CLONED:         { title: 'Data not downloaded',   icon: ICONS.cloud, color: 'var(--warning)' },
    NETWORK_BLOCKED:    { title: 'Blocked',               icon: ICONS.block, color: 'var(--critical)' },
    RATE_LIMITED:       { title: 'Rate limited',          icon: ICONS.clock, color: 'var(--serious)' },
    UPSTREAM_ERROR:     { title: 'Upstream down',         icon: ICONS.alert, color: 'var(--serious)' },
    TRADING_DISABLED:   { title: 'Trading locked',        icon: ICONS.lock,  color: 'var(--critical)' },
    BAD_REQUEST:        { title: 'Invalid input',         icon: ICONS.edit,  color: 'var(--warning)' },
    NOT_FOUND:          { title: 'Not found',             icon: ICONS.empty, color: 'var(--ink-3)' },
    NETWORK:            { title: 'Server unreachable',    icon: ICONS.cloud, color: 'var(--critical)' },
    INTERNAL:           { title: 'Something went wrong',  icon: ICONS.alert, color: 'var(--critical)' },
  };

  function skeleton(rows = 4) {
    const box = el('div', { class: 'skeleton' });
    for (let i = 0; i < rows; i += 1) box.append(el('i'));
    return box;
  }

  function errorState(error) {
    const spec = STATES[error.code] || STATES.INTERNAL;
    const box = el('div', { class: 'state' });

    box.append(el('div', { class: 'icon' }, icon(spec.icon, spec.color)));
    box.append(el('div', { class: 'msg', text: spec.title }));
    if (error.message) box.append(el('div', { class: 'hint', text: error.message }));

    const command = error.hint && /^(pip|git|python) /.test(error.hint.trim())
      ? error.hint.trim()
      : (error.detail && error.detail.command) || null;

    if (command) {
      const fix = el('div', { class: 'fix' }, el('code', { text: command }));
      fix.append(el('button', {
        class: 'quiet sm', text: 'Copy',
        onclick: () => navigator.clipboard?.writeText(command).then(
          () => toast('Command copied.'),
          () => toast('Browser blocked the copy.'),
        ),
      }));
      box.append(fix);
    } else if (error.hint) {
      box.append(el('div', { class: 'hint muted', text: error.hint }));
    }
    return box;
  }

  // ---------------------------------------------------------------- tabel
  function renderTable(host, records, columns, options = {}) {
    clear(host);
    if (!records || !records.length) {
      host.append(el('div', { class: 'empty', text: options.emptyText || 'No rows to show.' }));
      return;
    }

    const cols = (columns && columns.length ? columns : Object.keys(records[0]))
      .filter((c) => !(options.hide || []).includes(c));
    const state = { sort: options.sort || null, dir: options.dir || 'desc', filter: '' };

    const wrap = el('div', { class: 'tablewrap' });
    const table = el('table');
    const thead = el('thead');
    const tbody = el('tbody');
    table.append(thead, tbody);
    wrap.append(table);

    const bar = el('div', { class: 'tablebar' });
    const counter = el('span', { class: 'grow' });
    const search = el('input', {
      type: 'search', placeholder: 'Filter rows…',
      oninput: (e) => { state.filter = e.target.value.toLowerCase(); draw(); },
    });
    const csv = el('button', {
      class: 'quiet sm', text: 'Download CSV',
      onclick: () => exportCsv(visible(), cols, options.name || 'bloomberg-hub'),
    });
    bar.append(counter, search, csv);

    const sorted = () => {
      if (!state.sort) return records;
      const key = state.sort;
      const sign = state.dir === 'asc' ? 1 : -1;
      return [...records].sort((a, b) => {
        const x = a[key]; const y = b[key];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        const nx = Number(x); const ny = Number(y);
        if (Number.isFinite(nx) && Number.isFinite(ny)) return (nx - ny) * sign;
        return String(x).localeCompare(String(y)) * sign;
      });
    };
    const visible = () => (state.filter
      ? sorted().filter((r) => cols.some((c) => String(r[c] ?? '').toLowerCase().includes(state.filter)))
      : sorted());

    function drawHead() {
      clear(thead);
      const tr = el('tr');
      for (const col of cols) {
        const th = el('th', {
          title: 'Click to sort',
          onclick: () => {
            if (state.sort === col) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
            else { state.sort = col; state.dir = 'desc'; }
            draw();
          },
        });
        th.append(document.createTextNode(options.headers?.[col] || col));
        if (state.sort === col) th.append(el('span', { class: 'dir', text: state.dir === 'asc' ? '↑' : '↓' }));
        tr.append(th);
      }
      thead.append(tr);
    }

    function draw() {
      drawHead();
      clear(tbody);
      const rows = visible();
      const limit = options.limit || 400;
      for (const row of rows.slice(0, limit)) {
        // `rowAttrs` lets a caller stamp identity onto the <tr> — a row-level
        // click handler then survives the redraw that sorting and filtering
        // cause, instead of depending on which column a value landed in.
        const tr = el('tr', options.rowAttrs ? options.rowAttrs(row) : {});
        for (const [i, col] of cols.entries()) {
          const value = row[col];
          const numeric = typeof value === 'number';
          const td = el('td', { class: [
            numeric ? 'num' : '',
            i === 0 && !numeric ? 'strong' : '',
            (options.mono || []).includes(col) ? 'mono' : '',
          ].filter(Boolean).join(' ') });

          if (numeric && (options.signed || []).includes(col)) {
            if (value > 0) td.classList.add('pos');
            else if (value < 0) td.classList.add('neg');
          }
          if (options.cells?.[col]) {
            td.append(options.cells[col](row));
          } else if (value === null || value === undefined || value === '') {
            td.append(el('span', { class: 'muted', text: '—' }));
          } else if (typeof value === 'string' && /^https?:\/\//.test(value)) {
            td.append(el('a', { href: value, target: '_blank', rel: 'noopener', text: 'Open ↗' }));
          } else {
            td.textContent = numeric ? compact(value) : String(value);
            if (!numeric && String(value).length > 55) td.classList.add('wrap');
          }
          tr.append(td);
        }
        tbody.append(tr);
      }
      counter.textContent = rows.length > limit
        ? `Showing ${limit} of ${rows.length} rows`
        : `${rows.length} rows`;
    }

    draw();
    host.append(wrap, bar);
  }

  function exportCsv(rows, cols, name) {
    const esc = (v) => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))];
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' }));
    const link = el('a', { href: url, download: `${name}.csv` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------------------------------------------------------------- pemuat
  async function load(host, path, render, options = {}) {
    if (!host) return null;
    if (!options.fetch?.body && host.dataset.pendingPath === path) return null;
    host.dataset.pendingPath = path;
    const ticket = (host._ticket || 0) + 1;
    host._ticket = ticket;
    const retained = !options.fetch?.body && host.dataset.loadedPath === path;
    if (!retained) clear(host).append(skeleton(options.skeletonRows || 4));

    const payload = await api(path, options.fetch || {});
    if (host._ticket !== ticket) return null;
    delete host.dataset.pendingPath;
    if (!payload.ok && retained) {
      host.querySelector('.refresh-error')?.remove();
      host.prepend(el('div', {class: 'stale refresh-error', text: 'Pembaruan gagal; data sebelumnya dipertahankan. ' + (payload.error?.message || '')}));
      return payload;
    }
    const openDetails = retained ? new Set([...host.querySelectorAll('details[open]')].map(d => d.dataset.persist || d.querySelector('summary')?.textContent)) : new Set();
    clear(host);

    if (!payload.ok) {
      host.append(errorState(payload.error || { code: 'INTERNAL' }));
      return payload;
    }

    const meta = payload.meta || {};
    for (const note of meta.notes || []) {
      host.append(el('div', { class: 'stale' }, icon(ICONS.clock), el('span', { text: note })));
    }

    const target = el('div');
    host.append(target);
    render(payload.data, meta, target);
    for (const detail of host.querySelectorAll('details')) {
      if (openDetails.has(detail.dataset.persist || detail.querySelector('summary')?.textContent)) detail.open = true;
    }
    host.dataset.loadedPath = path;

    const stamp = host.closest('.card')?.querySelector('[data-meta]');
    if (stamp) {
      const bits = [];
      if (meta.rows) bits.push(`${meta.rows} rows`);
      if (meta.elapsed_ms >= 100) bits.push(`${(meta.elapsed_ms / 1000).toFixed(1)}s`);
      if (meta.cached) bits.push('cached');
      bits.push(`dicek ${new Date().toLocaleTimeString()}`);
      if (meta.cache_age_s != null) bits.push(`usia cache ${Math.round(meta.cache_age_s)}s`);
      stamp.textContent = bits.join(' · ');
    }
    return payload;
  }

  const loadTable = (host, path, options = {}) => load(host, path, (data, meta, target) => {
    renderTable(target, data, options.columns || meta.columns, options);
  }, options);

  // ---------------------------------------------------------------- grafik
  const tip = el('div', { class: 'charttip', hidden: true });
  document.addEventListener('DOMContentLoaded', () => document.body.append(tip));

  function showTip(event, nodes) {
    clear(tip);
    nodes.forEach((n) => tip.append(n));
    tip.hidden = false;
    const pad = 16;
    const box = tip.getBoundingClientRect();
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    if (x + box.width > window.innerWidth - 8) x = event.clientX - box.width - pad;
    if (y + box.height > window.innerHeight - 8) y = event.clientY - box.height - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  function mount(host, draw) {
    if (!host) return;
    const run = () => {
      const width = host.clientWidth || 600;
      if (width < 40) return;
      clear(host);
      host.append(draw(width));
    };
    run();
    if (host._ro) host._ro.disconnect();
    host._ro = new ResizeObserver(run);
    host._ro.observe(host);
  }

  /** Grafik garis satu seri, dengan garis bidik dan keterangan saat disentuh. */
  function lineChart(host, points, options = {}) {
    mount(host, (width) => {
      const height = options.height || 210;
      const pad = { t: 14, r: 18, b: 26, l: 52 };
      const iw = Math.max(20, width - pad.l - pad.r);
      const ih = Math.max(20, height - pad.t - pad.b);
      const root = svg('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });

      const ys = points.map((p) => Number(p.y)).filter(Number.isFinite);
      if (!ys.length) return root;

      let lo = Math.min(...ys);
      let hi = Math.max(...ys);
      if (options.baseline !== undefined) { lo = Math.min(lo, options.baseline); hi = Math.max(hi, options.baseline); }
      if (options.zeroBase) lo = Math.min(0, lo);
      const ticks = niceTicks(lo, hi, 4);
      lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);

      const sx = (i) => pad.l + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw);
      const sy = (v) => pad.t + ih - ((v - lo) / (hi - lo || 1)) * ih;

      for (const t of ticks) {
        root.append(svg('line', { class: 'grid-line', x1: pad.l, x2: pad.l + iw, y1: sy(t), y2: sy(t) }));
        const label = svg('text', { class: 'tick', x: pad.l - 9, y: sy(t) + 4, 'text-anchor': 'end' });
        label.textContent = options.tickFormat ? options.tickFormat(t) : compact(t);
        root.append(label);
      }

      if (options.baseline !== undefined) {
        root.append(svg('line', {
          class: 'axis-line', x1: pad.l, x2: pad.l + iw,
          y1: sy(options.baseline), y2: sy(options.baseline),
        }));
      }

      const color = options.color || 'var(--series-1)';
      const path = points.map((p, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(2)},${sy(p.y).toFixed(2)}`).join(' ');

      if (options.area !== false) {
        const base = sy(options.baseline !== undefined ? options.baseline : lo);
        root.append(svg('path', {
          d: `${path} L${sx(points.length - 1)},${base} L${sx(0)},${base} Z`,
          fill: color, opacity: 0.1, stroke: 'none',
        }));
      }
      root.append(svg('path', { class: 'series', d: path, stroke: color }));

      const last = points[points.length - 1];
      root.append(svg('circle', { class: 'end-dot', cx: sx(points.length - 1), cy: sy(last.y), r: 4, fill: color }));
      const endLabel = svg('text', { class: 'label', x: sx(points.length - 1) - 9, y: sy(last.y) - 11, 'text-anchor': 'end' });
      endLabel.textContent = options.tickFormat ? options.tickFormat(last.y) : compact(last.y);
      root.append(endLabel);

      for (const [i, anchor] of [[0, 'start'], [points.length - 1, 'end']]) {
        if (!points[i]) continue;
        const t = svg('text', { class: 'tick', x: sx(i), y: height - 7, 'text-anchor': anchor });
        t.textContent = String(points[i].x ?? '');
        root.append(t);
      }

      const cross = svg('line', { class: 'crosshair', y1: pad.t, y2: pad.t + ih, opacity: 0 });
      const marker = svg('circle', { class: 'end-dot', r: 5, fill: color, opacity: 0 });
      const hit = svg('rect', { class: 'hit', x: pad.l, y: pad.t, width: iw, height: ih });
      root.append(cross, marker, hit);

      hit.addEventListener('pointermove', (event) => {
        const box = root.getBoundingClientRect();
        const rel = ((event.clientX - box.left) - pad.l) / (iw || 1);
        const idx = Math.max(0, Math.min(points.length - 1, Math.round(rel * (points.length - 1))));
        const p = points[idx];
        cross.setAttribute('x1', sx(idx)); cross.setAttribute('x2', sx(idx)); cross.setAttribute('opacity', 1);
        marker.setAttribute('cx', sx(idx)); marker.setAttribute('cy', sy(p.y)); marker.setAttribute('opacity', 1);
        showTip(event, [
          el('div', { class: 'v', text: options.tickFormat ? options.tickFormat(p.y) : compact(p.y) }),
          el('div', { class: 'k' }, el('i', { style: `background:${color}` }),
            el('span', { text: `${options.seriesName || 'nilai'} · ${p.x ?? ''}` })),
        ]);
      });
      hit.addEventListener('pointerleave', () => {
        cross.setAttribute('opacity', 0); marker.setAttribute('opacity', 0); hideTip();
      });
      return root;
    });
  }

  /** Grafik nada: biru di atas nol, merah di bawah, netral abu di garis nol. */
  function toneChart(host, points, options = {}) {
    mount(host, (width) => {
      const height = options.height || 210;
      const pad = { t: 14, r: 18, b: 26, l: 52 };
      const iw = Math.max(20, width - pad.l - pad.r);
      const ih = Math.max(20, height - pad.t - pad.b);
      const root = svg('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });

      const ys = points.map((p) => Number(p.y)).filter(Number.isFinite);
      if (!ys.length) return root;

      const bound = Math.max(Math.abs(Math.min(...ys)), Math.abs(Math.max(...ys)), 0.5);
      const sx = (i) => pad.l + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw);
      const sy = (v) => pad.t + ih - ((v + bound) / (2 * bound)) * ih;

      for (const t of niceTicks(-bound, bound, 4)) {
        root.append(svg('line', { class: 'grid-line', x1: pad.l, x2: pad.l + iw, y1: sy(t), y2: sy(t) }));
        const label = svg('text', { class: 'tick', x: pad.l - 9, y: sy(t) + 4, 'text-anchor': 'end' });
        label.textContent = t.toFixed(1);
        root.append(label);
      }

      const zero = sy(0);
      root.append(svg('line', { class: 'axis-line', x1: pad.l, x2: pad.l + iw, y1: zero, y2: zero }));

      const id = `c${Math.random().toString(36).slice(2)}`;
      const defs = svg('defs');
      for (const [suffix, y, h] of [['p', pad.t, zero - pad.t], ['n', zero, pad.t + ih - zero]]) {
        const clip = svg('clipPath', { id: `${id}${suffix}` });
        clip.append(svg('rect', { x: pad.l, y, width: iw, height: Math.max(0, h) }));
        defs.append(clip);
      }
      root.append(defs);

      const path = points.map((p, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(2)},${sy(p.y).toFixed(2)}`).join(' ');
      const closed = `${path} L${sx(points.length - 1)},${zero} L${sx(0)},${zero} Z`;

      root.append(svg('path', { d: closed, fill: 'var(--pole-pos)', opacity: 0.1, 'clip-path': `url(#${id}p)` }));
      root.append(svg('path', { d: closed, fill: 'var(--pole-neg)', opacity: 0.1, 'clip-path': `url(#${id}n)` }));
      root.append(svg('path', { class: 'series', d: path, stroke: 'var(--pole-pos)', 'clip-path': `url(#${id}p)` }));
      root.append(svg('path', { class: 'series', d: path, stroke: 'var(--pole-neg)', 'clip-path': `url(#${id}n)` }));

      for (const [i, anchor] of [[0, 'start'], [points.length - 1, 'end']]) {
        if (!points[i]) continue;
        const t = svg('text', { class: 'tick', x: sx(i), y: height - 7, 'text-anchor': anchor });
        t.textContent = String(points[i].x ?? '');
        root.append(t);
      }

      const cross = svg('line', { class: 'crosshair', y1: pad.t, y2: pad.t + ih, opacity: 0 });
      const hit = svg('rect', { class: 'hit', x: pad.l, y: pad.t, width: iw, height: ih });
      root.append(cross, hit);

      hit.addEventListener('pointermove', (event) => {
        const box = root.getBoundingClientRect();
        const rel = ((event.clientX - box.left) - pad.l) / (iw || 1);
        const idx = Math.max(0, Math.min(points.length - 1, Math.round(rel * (points.length - 1))));
        const p = points[idx];
        cross.setAttribute('x1', sx(idx)); cross.setAttribute('x2', sx(idx)); cross.setAttribute('opacity', 1);
        showTip(event, [
          el('div', { class: 'v', text: Number(p.y).toFixed(2) }),
          el('div', { class: 'k' },
            el('i', { style: `background:${p.y >= 0 ? 'var(--pole-pos)' : 'var(--pole-neg)'}` }),
            el('span', { text: `${p.y >= 0 ? 'nada positif' : 'nada negatif'} · ${p.x ?? ''}` })),
        ]);
      });
      hit.addEventListener('pointerleave', () => { cross.setAttribute('opacity', 0); hideTip(); });
      return root;
    });
  }

  /**
   * Grafik kalibrasi: perkiraan model dibanding kenyataan, dengan garis lurus
   * sebagai acuan model sempurna. Luas titik mengikuti jumlah pengamatan,
   * supaya kelompok berisi 4 data tidak terlihat sepenting yang berisi 400.
   */
  function reliabilityChart(host, bins, options = {}) {
    const points = (bins || []).filter((b) => b.count > 0 && b.mean_predicted !== null);
    mount(host, (width) => {
      const height = options.height || 260;
      const pad = { t: 16, r: 18, b: 34, l: 46 };
      const size = Math.max(20, Math.min(width - pad.l - pad.r, height - pad.t - pad.b));
      const root = svg('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });

      const sx = (v) => pad.l + v * size;
      const sy = (v) => pad.t + size - v * size;

      for (const t of [0, 0.25, 0.5, 0.75, 1]) {
        root.append(svg('line', { class: 'grid-line', x1: pad.l, x2: pad.l + size, y1: sy(t), y2: sy(t) }));
        root.append(svg('line', { class: 'grid-line', x1: sx(t), x2: sx(t), y1: pad.t, y2: pad.t + size }));
        const yl = svg('text', { class: 'tick', x: pad.l - 8, y: sy(t) + 4, 'text-anchor': 'end' });
        yl.textContent = `${t * 100}%`;
        const xl = svg('text', { class: 'tick', x: sx(t), y: pad.t + size + 17, 'text-anchor': 'middle' });
        xl.textContent = `${t * 100}%`;
        root.append(yl, xl);
      }

      root.append(svg('line', { class: 'axis-line', x1: sx(0), y1: sy(0), x2: sx(1), y2: sy(1) }));
      const perfect = svg('text', { class: 'tick', x: sx(1) - 6, y: sy(1) + 16, 'text-anchor': 'end' });
      perfect.textContent = 'model sempurna';
      root.append(perfect);

      const maxCount = Math.max(...points.map((p) => p.count), 1);
      for (const bin of points) {
        const r = 5 + 8 * Math.sqrt(bin.count / maxCount);
        const cx = sx(bin.mean_predicted);
        const cy = sy(bin.observed);
        root.append(svg('circle', {
          class: 'end-dot', cx, cy, r,
          fill: Math.abs(bin.gap_pp) > 10 ? 'var(--series-2)' : 'var(--series-1)',
        }));
        const hit = svg('circle', { class: 'hit', cx, cy, r: Math.max(16, r + 9) });
        hit.addEventListener('pointerenter', (event) => showTip(event, [
          el('div', { class: 'v', text: `${(bin.observed * 100).toFixed(1)}% benar-benar terjadi` }),
          el('div', { class: 'k' }, el('i', { style: 'background:var(--series-1)' }),
            el('span', { text: `model memperkirakan ${(bin.mean_predicted * 100).toFixed(1)}%` })),
          el('div', { class: 'k' }, el('span', { text: `${bin.count} pengamatan · selisih ${bin.gap_pp > 0 ? '+' : ''}${bin.gap_pp} poin` })),
        ]));
        hit.addEventListener('pointerleave', hideTip);
        root.append(hit);
      }

      const title = svg('text', { class: 'tick', x: pad.l + size / 2, y: height - 4, 'text-anchor': 'middle' });
      title.textContent = 'perkiraan model';
      root.append(title);
      return root;
    });
  }

  function sparkline(host, values, options = {}) {
    mount(host, (width) => {
      const height = options.height || 32;
      const root = svg('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}` });
      const nums = (values || []).map(Number).filter(Number.isFinite);
      if (nums.length < 2) return root;
      const lo = Math.min(...nums); const hi = Math.max(...nums);
      const sx = (i) => (i / (nums.length - 1)) * (width - 4) + 2;
      const sy = (v) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 6);
      root.append(svg('path', {
        class: 'series', 'stroke-width': 1.5,
        d: nums.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' '),
        stroke: options.color || 'var(--ink-3)',
      }));
      return root;
    });
  }

  // ------------------------------------------------------------------ krom

  /* Jim Simons, dalam kata-katanya sendiri.
   *
   * Nomor 3–7 adalah lima prinsip yang berkali-kali ia sampaikan di ceramahnya;
   * sisanya dari wawancara dan profil yang terdokumentasi. Ditulis dalam bahasa
   * aslinya, bukan terjemahan — kutipan yang diterjemahkan berhenti menjadi
   * kutipan. Urutannya diacak setiap kali halaman dimuat.
   */
  const KUTIPAN = [
    "We're right 50.75 percent of the time, but we're 100 percent right 50.75 percent of the time. You can make billions that way.",
    "There's no data like more data.",
    "Do something new. Don't run with the pack.",
    "Surround yourself with the smartest and best people you can.",
    "Be guided by beauty.",
    "Don't give up.",
    "Hope for good luck.",
    "We search through historical data looking for anomalous patterns that we would not expect to occur at random.",
    "I have no opinion on any stocks. The computer has its opinions and we slavishly follow them.",
    "The advantage scientists bring into the game is less their mathematical or computational skills than their ability to think scientifically.",
    "In this business it's easy to confuse luck with brains.",
    "I did a lot of math. I made a lot of money, and I gave almost all of it away.",
    "Luck plays a meaningful role in everyone's lives.",
    "Great people, great infrastructure, open environment, and get everybody compensated roughly based on the overall performance.",
    "Past performance is the best predictor of success.",
    "The efficient market hypothesis is not exactly correct. There are anomalies in the data.",
  ];

  /* Ambil satu kutipan, hindari yang sudah tampil di tempat lain pada muatan
   * yang sama. Tanpa ini, layar pembuka dan kaki halaman kerap kembar dan
   * keacakannya jadi tidak terasa. */
  const sudahTampil = new Set();
  function acak(arr) {
    const sisa = arr.filter((q) => !sudahTampil.has(q));
    const pilihan = sisa.length ? sisa : arr;
    const q = pilihan[Math.floor(Math.random() * pilihan.length)];
    sudahTampil.add(q);
    return q;
  }

  /* Menu samping hanya selebar 232 piksel, jadi kutipan panjang akan mendorong
   * tata letaknya. Yang pendek saja yang dipakai di sana. */
  const acakPendek = (batas = 95) => acak(
    KUTIPAN.filter((q) => q.length <= batas).length ? KUTIPAN.filter((q) => q.length <= batas) : KUTIPAN,
  );

  /* Layar pembuka.
   *
   * Kunjungan pertama dalam satu sesi menampilkan logo bersama satu kutipan
   * acak. Perpindahan halaman berikutnya hanya menampilkan logo dan bilah —
   * kutipan yang muncul di setiap klik akan cepat terasa mengganggu.
   */
  function initSplash() {
    const splash = $('#splash');
    if (!splash) return;

    const pertama = !sessionStorage.getItem('bh-splash');
    const tahan = pertama ? 2000 : 320;

    if (pertama) {
      splash.dataset.mode = 'kutipan';
      const teks = $('#splash-kutipan');
      const sumber = $('#splash-sumber');
      if (teks) teks.textContent = '"' + acak(KUTIPAN) + '"';
      if (sumber) sumber.textContent = 'Jim Simons - Renaissance Technologies';
      try { sessionStorage.setItem('bh-splash', '1'); } catch (e) { /* mode privat */ }
    } else {
      splash.dataset.mode = 'memuat';
    }

    const mulai = performance.now();
    const tutup = () => {
      const sisa = Math.max(0, tahan - (performance.now() - mulai));
      setTimeout(() => splash.classList.add('pergi'), sisa);
    };
    if (document.readyState === 'complete') tutup();
    else window.addEventListener('load', tutup, { once: true });

    // Klik untuk melewati — jangan pernah menahan orang yang sudah tahu mau ke mana.
    splash.addEventListener('click', () => splash.classList.add('pergi'));

    // Tampilkan lagi saat berpindah halaman, supaya perpindahannya tidak terasa
    // seperti layar membeku.
    document.addEventListener('click', (e) => {
      const tautan = e.target.closest('a[href]');
      if (!tautan || e.metaKey || e.ctrlKey || e.shiftKey || tautan.target === '_blank') return;
      const url = new URL(tautan.href, location.href);
      if (url.origin !== location.origin || url.pathname === location.pathname) return;
      splash.dataset.mode = 'memuat';
      splash.classList.remove('pergi');
    });
    window.addEventListener('pageshow', (e) => {
      if (e.persisted) splash.classList.add('pergi');
    });
  }

  /* Kutipan di kaki menu samping — berganti setiap kali halaman dimuat.
   * Hanya satu tempat: dulu ada juga di kaki halaman, tapi dua kutipan pada
   * satu layar saling berebut perhatian. */
  function initKutipan() {
    const rail = $('#rail-teks');
    if (rail) rail.textContent = '"' + acakPendek() + '"';
  }

  /* Jam New York dan Jakarta.
   *
   * Zona waktunya dihitung browser lewat Intl, bukan diambil dari internet —
   * jadi tetap tepat walau semua sumber sedang mati, dan tidak ada
   * penyesuaian musim panas yang perlu diurus sendiri.
   */
  function initClocks() {
    const zona = [
      { jam: '#jam-ny', tgl: '#tgl-ny', tz: 'America/New_York' },
      { jam: '#jam-wib', tgl: '#tgl-wib', tz: 'Asia/Jakarta' },
    ].filter((z) => $(z.jam));
    if (!zona.length) return;

    const jamFmt = (tz) => new Intl.DateTimeFormat('id-ID', {
      timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
    const tglFmt = (tz) => new Intl.DateTimeFormat('id-ID', {
      timeZone: tz, weekday: 'short', day: 'numeric', month: 'short',
    });

    const siap = zona.map((z) => ({ ...z, f1: jamFmt(z.tz), f2: tglFmt(z.tz) }));
    const gambar = () => {
      const sekarang = new Date();
      for (const z of siap) {
        const a = $(z.jam);
        const b = $(z.tgl);
        if (a) a.textContent = z.f1.format(sekarang);
        if (b) b.textContent = z.f2.format(sekarang);
      }
    };
    gambar();
    setInterval(gambar, 1000);
  }

  /* Menu samping bisa dilipat jadi ikon saja, dan pilihannya diingat. */
  function initRail() {
    const app = $('#app');
    const tombol = $('#rail-toggle');
    if (!app || !tombol) return;
    if (localStorage.getItem('bh-rail') === 'tutup') app.classList.add('rail-tutup');
    tombol.addEventListener('click', () => {
      const tutup = app.classList.toggle('rail-tutup');
      try { localStorage.setItem('bh-rail', tutup ? 'tutup' : 'buka'); } catch (e) { /* abaikan */ }
    });
  }

  function initTheme() {
    const stored = localStorage.getItem('bh-theme');
    if (stored) document.documentElement.setAttribute('data-theme', stored);
    const button = $('#theme');
    if (!button) return;
    const gelap = () => (document.documentElement.getAttribute('data-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';
    const label = () => {
      clear(button);
      button.append(icon(gelap()
        ? 'M12 3v1m0 16v1m9-9h-1M4 12H3m15.36 6.36l-.71-.71M6.35 6.35l-.7-.7m12.72 0l-.71.7M6.34 17.66l-.7.71M16 12a4 4 0 11-8 0 4 4 0 018 0z'
        : 'M21 12.79A9 9 0 1111.21 3 7 9 0 0021 12.79z'));
      button.title = gelap() ? 'Switch to light theme' : 'Switch to dark theme';
    };
    label();
    button.addEventListener('click', () => {
      const next = gelap() ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('bh-theme', next);
      label();
    });
  }

  function initPulse() {
    const block = $('#pulse-block');
    const status = $('#pulse-status');
    if (block) {
      const tarik = async () => {
        const payload = await api('/api/polygon/block');
        block.textContent = payload.ok
          ? Number(payload.data.block).toLocaleString('id-ID')
          : 'offline';
      };
      tarik();
      setInterval(tarik, 20000);
    }
    if (status) {
      api('/api/health').then((payload) => {
        if (!payload.ok) { status.textContent = '—'; return; }
        const s = payload.data.summary;
        clear(status);
        status.append(el('span', { class: 'chip good' }, el('span', { class: 'led' }), `${s.live} live`));
        if (s.degraded) status.append(el('span', { class: 'chip warning' }, el('span', { class: 'led' }), String(s.degraded)));
        if (s.blocked) status.append(el('span', { class: 'chip critical' }, el('span', { class: 'led' }), String(s.blocked)));
      });
    }
  }

  // -------------------------------------------------------- pencarian dalam
  /* Satu kotak untuk empat hal sekaligus:
   *
   *   1. halaman  — dijawab seketika dari daftar menu
   *   2. isi halaman yang sedang dibuka — teks apa pun yang tampak di layar
   *   3. berita   — dicari langsung ke tujuh sumber sekaligus
   *   4. pasar prediksi & simpanan berkas teks
   *
   * Nomor 1 dan 2 tampil tanpa jeda. Nomor 3 dan 4 butuh jaringan, jadi
   * ditunda sebentar setelah orang berhenti mengetik dan tidak pernah menahan
   * hasil yang sudah ada.
   */
  function cariDiHalaman(term, batas = 8) {
    const wadah = $('main.content');
    if (!wadah || term.length < 2) return [];

    const hasil = [];
    const terlihat = new Set();
    const walker = document.createTreeWalker(wadah, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue || node.nodeValue.trim().length < 2) return NodeFilter.FILTER_REJECT;
        const induk = node.parentElement;
        if (!induk || induk.closest('script,style')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    let node;
    while ((node = walker.nextNode())) {
      const teks = node.nodeValue.trim();
      if (!teks.toLowerCase().includes(term)) continue;
      const induk = node.parentElement;
      if (terlihat.has(induk)) continue;
      terlihat.add(induk);

      const bagian = induk.closest('section.card, .stat, details');
      const judulBagian = bagian ? (bagian.querySelector('h2, h3, .label, summary') || {}).textContent : '';
      hasil.push({
        jenis: 'halaman-ini',
        judul: teks.slice(0, 90),
        ket: (judulBagian || '').trim().slice(0, 34),
        el: induk,
      });
      if (hasil.length >= batas) break;
    }
    return hasil;
  }

  function lompatKe(elemen) {
    elemen.scrollIntoView({ behavior: 'smooth', block: 'center' });
    elemen.classList.add('bh-sorot');
    setTimeout(() => elemen.classList.remove('bh-sorot'), 2600);
  }

  function initSearch() {
    const box = $('#cmdk');
    if (!box) return;
    const input = $('#cmdk-input', box);
    const daftar = $('#cmdk-list', box);
    const spin = $('#cmdk-spin', box);
    const info = $('#cmdk-info', box);
    const halaman = window.BH_NAV || [];

    let baris = [];       // daftar rata untuk navigasi papan ketik
    let kursor = 0;
    let tundaan = null;
    let permintaan = 0;
    let jauh = null;      // hasil dari server

    const buka = () => {
      box.hidden = false;
      input.value = '';
      jauh = null;
      gambar('');
      input.focus();
    };
    const tutup = () => { box.hidden = true; clearTimeout(tundaan); };

    function jalankan(item) {
      if (item.jenis === 'halaman-ini') { tutup(); lompatKe(item.el); return; }
      if (item.url) {
        if (/^https?:\/\//.test(item.url)) window.open(item.url, '_blank', 'noopener');
        else window.location.href = item.url;
        return;
      }
      if (item.path) window.location.href = item.path;
    }

    function grup(judul, items, render) {
      if (!items.length) return;
      daftar.append(el('div', { class: 'cmdk-grup', text: `${judul} · ${items.length}` }));
      for (const item of items) {
        const node = render(item);
        node.addEventListener('click', () => jalankan(item));
        item._node = node;
        baris.push(item);
        daftar.append(node);
      }
    }

    function gambar(term) {
      clear(daftar);
      baris = [];

      const t = term.trim().toLowerCase();

      const cocokHalaman = halaman.filter(
        (h) => !t || `${h.label} ${h.hint || ''}`.toLowerCase().includes(t),
      ).map((h) => ({ ...h, jenis: 'halaman' }));

      grup('Pages', cocokHalaman, (item) => el('div', { class: 'cmdk-item' },
        el('span', { class: 'judul', text: item.label }),
        el('span', { class: 'ket', text: item.hint || '' })));

      if (t.length >= 2) {
        grup('On this page', cariDiHalaman(t), (item) => el('div', { class: 'cmdk-item' },
          el('span', { class: 'judul', text: item.judul }),
          item.ket ? el('span', { class: 'tanda', text: item.ket }) : null));
      }

      if (jauh) {
        grup('News', (jauh.news || []).slice(0, 8), (item) => el('div', { class: 'cmdk-item' },
          el('span', { class: 'judul', text: item.title }),
          el('span', { class: 'tanda', text: item.source || '' })));

        const pasar = (jauh.markets || []).slice(0, 6)
          .map((m) => ({ ...m, url: `/news?m=${encodeURIComponent(m.id)}` }));
        grup('Markets', pasar, (item) => el('div', { class: 'cmdk-item' },
          el('span', { class: 'judul', text: item.question }),
          el('span', { class: 'ket', text: item.probability_pct || '' })));

        grup('Saved to file', (jauh.stored || []).slice(0, 5), (item) => el('div', { class: 'cmdk-item' },
          el('span', { class: 'judul', text: item.title || '' }),
          el('span', { class: 'tanda', text: item.source || 'saved' })));

        const sumber = (jauh.sources || []).slice(0, 5).map((x) => ({ ...x, url: x.page || '/sources' }));
        grup('Data sources', sumber, (item) => el('div', { class: 'cmdk-item' },
          el('span', { class: 'judul', text: item.label }),
          el('span', { class: 'ket', text: item.category || '' })));
      }

      if (!baris.length) {
        daftar.append(el('div', { class: 'cmdk-kosong', text: t
          ? 'Belum ada yang cocok. Coba kata kunci lain.'
          : 'Ketik untuk mencari halaman, isi halaman ini, berita, dan pasar prediksi.' }));
      }

      kursor = 0;
      sorot();
      if (info) {
        info.textContent = jauh
          ? `${jauh.total} external results`
          : (t.length >= 2 ? 'searching…' : '');
      }
    }

    function sorot() {
      baris.forEach((item, i) => item._node.classList.toggle('on', i === kursor));
      baris[kursor]?._node.scrollIntoView({ block: 'nearest' });
    }

    async function cariJauh(term) {
      if (term.trim().length < 2) { jauh = null; return; }
      const nomor = ++permintaan;
      if (spin) spin.hidden = false;
      const payload = await api(`/api/search?q=${encodeURIComponent(term.trim())}`);
      if (nomor !== permintaan) return;          // hasil lama, sudah tidak relevan
      if (spin) spin.hidden = true;
      jauh = payload.ok ? payload.data : null;
      gambar(input.value);
    }

    input.addEventListener('input', () => {
      gambar(input.value);
      clearTimeout(tundaan);
      tundaan = setTimeout(() => cariJauh(input.value), 400);
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { kursor = Math.min(baris.length - 1, kursor + 1); sorot(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { kursor = Math.max(0, kursor - 1); sorot(); e.preventDefault(); }
      else if (e.key === 'Enter') { if (baris[kursor]) jalankan(baris[kursor]); }
      else if (e.key === 'Escape') tutup();
    });

    box.addEventListener('click', (e) => { if (e.target === box) tutup(); });
    $('#search-open')?.addEventListener('click', buka);
    $('#cmdk-open')?.addEventListener('click', buka);

    document.addEventListener('keydown', (e) => {
      const mengetik = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); buka(); }
      else if (e.key === '/' && !mengetik && box.hidden) { e.preventDefault(); buka(); }
    });

    // Alamat dengan ?cari=… langsung membuka pencarian dan mengisinya, jadi
    // satu hasil pencarian bisa dibagikan sebagai tautan biasa.
    const awal = new URLSearchParams(location.search).get('cari');
    if (awal) {
      buka();
      input.value = awal;
      gambar(awal);
      cariJauh(awal);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initSplash();
    initKutipan();
    initTheme();
    initClocks();
    initRail();
    initPulse();
    initSearch();
  });

  /* Cap waktu UTC yang enak dibaca: "11 Sep 16:56". Sumber jadwal mengirim ISO
     penuh dengan mikrodetik dan offset, yang benar tapi tidak terbaca sekilas
     di dalam tabel. Zona tetap UTC — jadwal turnamen dan tenggat pasar memang
     diumumkan dalam UTC, jadi mengubahnya ke waktu lokal justru menyesatkan. */
  const BULAN = ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des'];
  function stampUTC(value, withYear = false) {
    if (!value) return '';
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return String(value);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const tahun = withYear ? ` ${d.getUTCFullYear()}` : '';
    return `${d.getUTCDate()} ${BULAN[d.getUTCMonth()]}${tahun} ${hh}:${mm}`;
  }

  window.BH = {
    $, el, svg, icon, ICONS, clear, api, load, loadTable, renderTable,
    errorState, skeleton, lineChart, toneChart, reliabilityChart, sparkline,
    compact, toast, exportCsv, lompatKe, KUTIPAN, stampUTC,
  };
})();
