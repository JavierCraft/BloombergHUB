/* Bloomberg Hub — public data panels.

   36 public APIs (src/datahub/catalog.py) shown as tabs on the page they help:
   prices and FX on Overview, official US numbers on Politics, model releases on
   Tech, charts on Culture, schedules on Sports, player counts on Esports.
   Same rule as hub.js: every value from the API goes in through textContent. */
(() => {
  'use strict';
  const {el, api, load, clear} = BH;

  const HEADERS = {
    asset: 'Asset', usd: 'USD', idr: 'IDR', change_24h_pct: '24h %', market_cap_usd: 'Market cap (USD)',
    rank: '#', coin: 'Coin', symbol: 'Symbol', market_cap_rank: 'Cap rank', price_usd: 'Price (USD)',
    date: 'Date', value: 'Value', reading: 'Reading', chain: 'Chain', tvl_usd: 'TVL (USD)', token: 'Token',
    pair: 'Pair', rate: 'Rate', source: 'Source', updated: 'Updated', metal: 'Metal',
    total_debt_trillion_usd: 'Total debt ($T)', held_by_public_trillion: 'Held by public ($T)',
    intragovernmental_trillion: 'Intragovernmental ($T)', type: 'Type', security: 'Security',
    avg_rate_pct: 'Avg rate %', period: 'Period', cpi_u: 'CPI-U', yoy_pct: 'YoY %', preliminary: 'Preliminary',
    indicator: 'Indicator', year: 'Year', unit: 'Unit', published: 'Published', title: 'Title',
    document: 'Document', url: 'Link', article: 'Article', views: 'Views', about: 'About',
    provider: 'Provider', shares_today: 'Shares today', accounts_today: 'Accounts today', tag: 'Tag',
    uses_today: 'Uses today', released: 'Released', model: 'Model', id: 'ID', context_tokens: 'Context',
    input_usd_per_mtok: 'Input $/MTok', output_usd_per_mtok: 'Output $/MTok', upvotes: 'Upvotes',
    repo: 'Repository', name: 'Name', package: 'Package', version: 'Version', score: 'Score',
    comments: 'Comments', tags: 'Tags', reactions: 'Reactions', artist: 'Artist / publisher', genre: 'Genre',
    time_utc: 'Time (UTC)', match: 'Match', round: 'Round', venue: 'Venue', kickoff_utc: 'Kickoff (UTC)',
    home: 'Home', away: 'Away', finished: 'Finished', matchday: 'Matchday', season: 'Season', race: 'Race',
    circuit: 'Circuit', session: 'Session', start_utc: 'Start (UTC)', state: 'State', status: 'Status',
    game: 'Game', players_now: 'Players now', appid: 'App ID', magnitude: 'Mag', place: 'Place',
    depth_km: 'Depth (km)', tsunami: 'Tsunami', alert: 'Alert', time_wib: 'Time (WIB)', depth: 'Depth',
    region: 'Region', tsunami_potential: 'Tsunami potential', felt: 'Felt', country: 'Country',
    from: 'From', to: 'To', category: 'Category', latest: 'Latest', city: 'City', max_temp_c: 'Max °C',
    max_temp_f: 'Max °F', rain_mm: 'Rain (mm)', net_utc: 'Launch (UTC)', pad: 'Pad',
  };
  const SIGNED = ['change_24h_pct', 'yoy_pct'];
  const HIDE = ['appid', 'id', 'document'];

  const fmt = (n) => (Number.isInteger(n)
    ? n.toLocaleString('en-US')
    : n.toLocaleString('en-US', {maximumFractionDigits: Math.abs(n) < 1 ? 6 : 4}));

  function renderSource(data, meta, host) {
    if (!data || Array.isArray(data)) { host.append(el('div', {class: 'empty', text: 'Tidak ada data.'})); return; }
    host.append(el('p', {class: 'dh-why', text: data.good_for}));
    if (data.caution) host.append(el('p', {class: 'sub', text: `Catatan: ${data.caution}`}));

    const rows = data.rows || [];
    // meta.columns keeps the parser's column order; the JSON object itself arrives key-sorted.
    const order = meta && meta.columns && meta.columns.length ? meta.columns : (rows.length ? Object.keys(rows[0]) : []);
    const columns = order.filter((c) => !HIDE.includes(c));
    const cells = {};
    for (const col of columns) {
      if (rows.some((r) => typeof r[col] === 'number') && col !== 'rank') {
        cells[col] = (r) => (typeof r[col] === 'number' ? el('span', {text: fmt(r[col])}) : el('span', {class: 'muted', text: r[col] == null ? '—' : String(r[col])}));
      }
    }
    const table = el('div');
    host.append(table);
    BH.renderTable(table, rows, columns, {
      name: data.code, headers: HEADERS, cells, signed: SIGNED, limit: 120,
      emptyText: 'Sumber menjawab, tetapi belum ada baris untuk ditampilkan.',
    });
    const foot = [
      `${data.name}`,
      meta && meta.cached ? `dari simpanan (${Math.round(meta.cache_age_s || 0)} detik)` : 'baru diambil',
      `disimpan ${Math.round((data.ttl || 0) / 60)} menit`,
    ];
    host.append(el('p', {class: 'sub dh-foot'}, `${foot.join(' · ')} · `,
      el('a', {href: /^https?:/.test(data.docs || data.urls?.[0] || '') ? (data.docs || data.urls[0]) : '#',
        target: '_blank', rel: 'noopener', text: 'sumber ↗'})));
  }

  /** Tabs for every public data source of a page ("overview", "politics", "tech", …). */
  async function panel(host, page, options = {}) {
    if (!host) return;
    clear(host).append(BH.skeleton(2));
    const payload = await api(`/api/datahub/catalog?page=${encodeURIComponent(page)}`);
    clear(host);
    if (!payload.ok) { host.append(BH.errorState(payload.error)); return; }
    const sources = payload.data.sources || [];
    if (!sources.length) { host.append(el('div', {class: 'empty', text: 'Belum ada sumber data untuk halaman ini.'})); return; }

    const tabs = el('div', {class: 'tabs dh-tabs', role: 'tablist', 'aria-label': 'Sumber data'});
    const body = el('div', {class: 'dh-body', role: 'tabpanel'});
    host.append(tabs, body);
    const buttons = [];
    const select = (src, button) => {
      buttons.forEach((b) => { b.classList.toggle('on', b === button); b.setAttribute('aria-selected', String(b === button)); });
      load(body, `/api/datahub/${encodeURIComponent(src.code)}`, renderSource, {skeletonRows: 5});
    };
    sources.forEach((src, i) => {
      const button = el('button', {type: 'button', role: 'tab', title: `${src.category_label} · ${src.good_for}`,
        text: src.name});
      button.addEventListener('click', () => select(src, button));
      buttons.push(button);
      tabs.append(button);
      if (i === (options.initial || 0)) select(src, button);
    });
    host.append(el('p', {class: 'sub', text: `${sources.length} sumber data publik untuk halaman ini · semuanya dicoba dari koneksi ini pada ${payload.data.probed_at} · daftar lengkap di halaman Sources.`}));
  }

  /** A single source rendered straight into `host` (Overview tiles use this). */
  function one(host, code, render) {
    return load(host, `/api/datahub/${encodeURIComponent(code)}`, render || renderSource, {skeletonRows: 3});
  }

  BH.datahub = {panel, one, renderSource, HEADERS};

  // Any `<section data-datahub="page">` with a `[data-datahub-body]` inside mounts itself.
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-datahub]').forEach((section) => {
      panel(section.querySelector('[data-datahub-body]') || section, section.dataset.datahub);
    });
  });
})();
