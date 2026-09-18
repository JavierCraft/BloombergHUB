/* Shared news / prediction market monitor. All external text is rendered as text.

   Headlines run as a *stream*, not as a reload. `BH.load` clears its host and
   redraws, which is right for a table and wrong for a feed: every tick threw
   away the list you were reading and reset its scroll. `liveStream` below keeps
   the list, remembers what it has already shown, and prepends only what is new,
   marked so you can see it arrive. Scroll position and reading place survive. */
(() => {
  const {el, load, api} = BH;
  const safeLink = (url) => /^https?:\/\//i.test(url || '') ? url : '#';
  const flowCache = new Map();
  const evidenceCache = new Map();

  // How many headlines a stream keeps before dropping the oldest. Without a cap
  // a tab left open overnight grows without bound.
  const STREAM_CAP = 150;
  // How long a newly arrived headline stays highlighted.
  const FRESH_MS = 6000;

  const num = (value) => {
    if (value == null || value === '') return null;
    const out = Number(value);
    return Number.isFinite(out) ? out : null;
  };
  const money = (value) => {
    const n = num(value);
    return n === null ? '—' : '$' + n.toLocaleString('id-ID', {maximumFractionDigits: 0});
  };
  const points = (value) => {
    const n = num(value);
    return n === null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(2)} poin`;
  };

  function timeAgo(value) {
    if (!value) return '';
    const t = new Date(value).getTime();
    if (!Number.isFinite(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return 'baru saja';
    if (s < 3600) return `${Math.round(s / 60)} menit lalu`;
    if (s < 86400) return `${Math.round(s / 3600)} jam lalu`;
    return `${Math.round(s / 86400)} hari lalu`;
  }

  function renderFlow(data, meta, host) {
    host.append(el('p', {text: data.note}), el('p', {class: 'sub', text: `${data.sample_size} transaksi · ${data.from_utc || '—'} s.d. ${data.to_utc || '—'} · diambil ${data.fetched_at}`}));
    const table = el('div'); host.append(table);
    BH.renderTable(table, data.outcomes, ['outcome', 'buy_share_pct', 'buy_usd', 'sell_usd', 'net_taker_usd', 'wallets'],
      {headers: {outcome: 'Outcome', buy_share_pct: 'Porsi nilai BUY (%)', buy_usd: 'BUY ($)', sell_usd: 'SELL ($)', net_taker_usd: 'Net taker ($)', wallets: 'Wallet sampel'}});
    host.append(el('p', {text: data.dominant_buy_flow ? `Arus BUY terbesar dalam sampel menuju ${data.dominant_buy_flow}. Ini mendukung pembacaan tekanan beli pada periode sampel, tetapi belum membuktikan alasan pelaku atau arah harga berikutnya.` : 'Tidak ada dominasi nilai BUY yang dapat ditentukan.'}));
    const detail = el('details', {}, el('summary', {text: 'Transaksi dalam sampel'}));
    const trades = el('div'); detail.append(trades); host.append(detail);
    BH.renderTable(trades, data.trades, ['time_utc', 'outcome', 'side', 'price', 'shares', 'notional_usd', 'transaction']);
  }

  // ------------------------------------------------------------- headlines

  function headlineItem(n) {
    return el('a', {class: 'item', href: safeLink(n.url), target: '_blank', rel: 'noopener'},
      el('div', {class: 'judul', text: n.title}),
      el('div', {class: 'meta'},
        el('span', {class: 'sumber', text: n.source || ''}),
        el('span', {text: n.time ? timeAgo(n.time) : 'waktu tidak tersedia'}),
        n.category ? el('span', {text: n.category}) : null),
      n.summary ? el('div', {class: 'ringkas', text: n.summary}) : null);
  }

  /* One-shot render, still used for the per-market "check the news" panels,
     which are opened deliberately and should show a fixed snapshot. */
  function headlines(data, meta, host) {
    host.append(el('p', {class: 'sub', text: `Diambil ${data.fetched_at || '—'} · ${data.count || 0} berita`}));
    if (data.failed_sources?.length) host.append(el('p', {class: 'stale', text: `Sumber gagal: ${data.failed_sources.map(x => x.source).join(', ')}`}));
    const list = el('div', {class: 'berita'});
    for (const n of data.news || []) list.append(headlineItem(n));
    host.append(list);
    if (!data.news?.length) host.append(el('p', {text: 'Tidak ada berita yang cocok dari sumber yang menjawab.'}));
  }

  /* The streaming renderer. `buildPath()` is called fresh on every tick so the
     caller can change the keyword without tearing the stream down. */
  function liveStream(host, buildPath, options = {}) {
    const state = {seen: new Map(), path: null, timer: null, stopped: false, request: 0};
    const head = el('div', {class: 'live-head'});
    const dot = el('span', {class: 'live-dot'});
    const label = el('span', {class: 'live-label', text: 'menghubungkan…'});
    const counter = el('span', {class: 'live-count'});
    head.append(dot, label, counter);
    const warn = el('div', {hidden: true});
    const list = el('div', {class: 'berita'});
    const empty = el('p', {class: 'sub', text: 'Menunggu kabar pertama…'});
    BH.clear(host).append(head, warn, empty, list);

    const keyOf = (n) => n.url || `${n.title}|${n.source}`;

    function reset() {
      state.seen.clear();
      BH.clear(list);
      empty.hidden = false;
      empty.textContent = 'Memuat…';
    }

    function trim() {
      while (list.children.length > STREAM_CAP) {
        const last = list.lastElementChild;
        for (const [k, node] of state.seen) if (node === last) state.seen.delete(k);
        last.remove();
      }
    }

    async function tick() {
      if (state.stopped) return;
      const path = buildPath();
      const request = ++state.request;
      if (path !== state.path) { state.path = path; reset(); }

      const payload = await api(path);
      if (state.stopped || request !== state.request || path !== buildPath()) return;

      if (!payload.ok) {
        dot.className = 'live-dot bad';
        label.textContent = 'pembaruan gagal — isi sebelumnya dipertahankan';
        warn.hidden = false;
        BH.clear(warn).append(el('div', {class: 'stale', text: payload.error?.message || 'Sumber tidak menjawab.'}));
        return;
      }

      const data = payload.data || {};
      warn.hidden = !(data.failed_sources?.length);
      if (data.failed_sources?.length) {
        BH.clear(warn).append(el('div', {class: 'stale',
          text: `Sumber gagal: ${data.failed_sources.map((x) => x.source).join(', ')}`}));
      }

      /* Prepend oldest-first so the newest headline ends up on top. */
      const fresh = [...new Map((data.news || []).map(n => [keyOf(n), n])).values()]
        .filter((n) => !state.seen.has(keyOf(n)));
      for (const n of fresh.slice().reverse()) {
        const node = headlineItem(n);
        node.classList.add('baru');
        list.prepend(node);
        state.seen.set(keyOf(n), node);
        setTimeout(() => node.classList.remove('baru'), FRESH_MS);
      }
      trim();

      empty.hidden = state.seen.size > 0;
      if (!state.seen.size) empty.textContent = 'Tidak ada berita yang cocok dari sumber yang menjawab.';

      const stale = payload.meta?.cached && payload.meta.cache_age_s > 30;
      dot.className = stale || data.failed_sources?.length ? 'live-dot bad' : 'live-dot';
      label.textContent = `${stale ? 'cache lama' : 'auto update'} · diperiksa ${new Date().toLocaleTimeString()} · data ${data.fetched_at || 'waktu tidak tersedia'}`;
      counter.textContent = fresh.length
        ? `${fresh.length} baru · ${state.seen.size} di layar`
        : `${state.seen.size} di layar`;

      const stamp = host.closest('.card')?.querySelector('[data-meta]');
      if (stamp) stamp.textContent = `${data.count || 0} dari sumber · ${state.seen.size} terkumpul`;
    }

    function every(ms) {
      clearInterval(state.timer);
      state.timer = null;
      if (!ms) { label.textContent = 'pembaruan otomatis dimatikan'; dot.className = 'live-dot bad'; return; }
      state.timer = setInterval(() => { if (!document.hidden) tick(); }, ms);
    }

    every(options.every ?? 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && state.timer) tick(); });
    tick();
    return {tick, every, stop() { state.stopped = true; clearInterval(state.timer); }};
  }

  // --------------------------------------------------------------- markets

  const STANCE_TONE = {periksa: 'good', 'hati-hati': 'warning', tunggu: 'serious', hindari: 'critical'};

  function briefBox(brief) {
    if (!brief || !brief.known) return null;
    const box = el('div', {class: 'sector-brief'});
    box.append(el('h4', {text: `Ringkasan kategori · ${brief.markets} pasar`}));
    const grid = el('div', {class: 'brief-grid'});
    const cell = (label, market, extra) => {
      if (!market) return null;
      return el('div', {class: 'brief-cell'},
        el('span', {class: 'label', text: label}),
        el('span', {class: 'q', text: market.question || '—'}),
        el('span', {class: 'v', text: [market.outcome ? `${market.outcome} ${market.pct}` : market.pct, extra].filter(Boolean).join(' · ')}));
    };
    grid.append(
      brief.best_ev ? cell('EV model tertinggi', {...brief.best_ev, pct: brief.best_ev.pct},
        `EV ${brief.best_ev.ev_pct > 0 ? '+' : ''}${Number(brief.best_ev.ev_pct).toFixed(1)}%${brief.best_ev.beats_market ? '' : ' · model belum terbukti'}`) : null,
      cell('Volume 24 jam terbesar', brief.busiest, `volume ${money(brief.busiest?.volume24h)}`),
      cell('Gerak 24 jam terbesar', brief.biggest_mover, points(brief.biggest_mover?.change_24h_pp)),
      cell('Paling berimbang', brief.most_contested, 'paling dekat 50/50'),
      cell('Paling mapan', brief.most_settled, 'harga tertinggi'),
      brief.resolving_soonest ? cell('Paling cepat selesai', brief.resolving_soonest,
        `${Number(brief.resolving_soonest.days).toFixed(1)} hari lagi`) : null);
    box.append(grid);
    box.append(el('p', {class: 'sub',
      text: `Total volume 24 jam ${money(brief.total_volume24h)}`
        + (brief.concentration_pct != null ? ` · ${brief.concentration_pct}% di satu pasar teratas` : '')}));
    if (brief.avg_entry_cost_pct != null || brief.modelled_markets != null) {
      box.append(el('p', {class: 'sub', text:
        `EV di harga konsensus rata-rata ${Number(brief.avg_entry_cost_pct ?? 0).toFixed(2)}% (biaya masuk) · `
        + `${brief.modelled_markets ?? 0} pasar dinilai model · ${brief.positive_ev_markets ?? 0} dengan EV model positif. `
        + 'Model hanya menilai pasar dengan tenggat ≤ 14 hari.'}));
    }
    box.append(el('p', {class: 'sub', text: brief.note}));
    return box;
  }

  function analysisBlock(a, key = '') {
    const wrap = el('div', {class: 'analysis'});
    wrap.append(el('p', {class: 'reading', text: a.reading}));

    const traits = a.character || {};
    const chips = el('div', {class: 'trait-chips'});
    const chip = (text, tone) => el('span', {class: `chip ${tone || ''}`}, el('span', {class: 'led'}), text);
    if (traits.stability) chips.append(chip(`sifat: ${traits.stability}`, traits.fresh_move ? 'serious' : ''));
    if (traits.depth) chips.append(chip(`buku: ${traits.depth}`, traits.thin ? 'warning' : 'good'));
    if (traits.spread_pp != null) chips.append(chip(`spread ${traits.spread_pp.toFixed(2)} poin`, traits.wide_spread ? 'warning' : ''));
    wrap.append(chips);

    const moves = el('dl', {class: 'facts'});
    const row = (k, v) => { if (v != null) moves.append(el('dt', {text: k}), el('dd', {text: v})); };
    row('Gerak 1 jam', traits.change_1h_pp != null ? points(traits.change_1h_pp) : null);
    row('Gerak 24 jam', traits.change_24h_pp != null ? points(traits.change_24h_pp) : null);
    row('Gerak 1 pekan', traits.change_1w_pp != null ? points(traits.change_1w_pp) : null);
    row('Likuiditas', traits.liquidity != null ? money(traits.liquidity) : null);
    row('Volume 24 jam', traits.volume24h != null ? money(traits.volume24h) : null);
    if (moves.children.length) wrap.append(moves);
    wrap.append(el('p', {class: 'sub', text: traits.stability_why}), el('p', {class: 'sub', text: traits.depth_why}));

    const econ = a.economics || {};
    if (econ.known) {
      wrap.append(el('h4', {text: 'Hitungan masuk'}));
      const facts = el('dl', {class: 'facts'});
      facts.append(el('dt', {text: 'Harga masuk'}), el('dd', {text: econ.entry_pct}));
      facts.append(el('dt', {text: 'Untung jika benar'}), el('dd', {text: `+${econ.profit_pct_if_right}%`}));
      facts.append(el('dt', {text: 'Rugi jika salah'}), el('dd', {text: `−${econ.loss_pct_if_wrong}%`}));
      if (econ.days_to_resolution != null) facts.append(el('dt', {text: 'Sisa waktu'}), el('dd', {text: `${econ.days_to_resolution} hari`}));
      if (econ.annualised_pct_if_right != null) facts.append(el('dt', {text: 'Setara per tahun jika benar'}), el('dd', {text: `${econ.annualised_pct_if_right}%`}));
      facts.append(el('dt', {text: 'Biaya bolak-balik'}), el('dd', {text: `${econ.round_trip_cost_pp} poin`}));
      wrap.append(facts, el('p', {class: 'sub', text: econ.note}));
    }

    if (a.ev && BH.ml) {
      wrap.append(el('h4', {text: 'Expected value (EV)'}));
      const evBox = BH.ml.evTable(a.ev, key);
      if (evBox) wrap.append(evBox);
    }

    const plan = a.strategy || {};
    if (plan.headline) {
      wrap.append(el('h4', {text: 'Strategi'}));
      wrap.append(el('div', {class: 'stance'},
        chip(plan.stance, STANCE_TONE[plan.stance] || ''),
        el('span', {text: plan.headline})));
      const ul = el('ul', {class: 'plan'});
      for (const point of plan.points || []) ul.append(el('li', {text: point}));
      wrap.append(ul);
      if (plan.breakeven) wrap.append(el('p', {class: 'breakeven', text: plan.breakeven}));
      if (plan.next_step) wrap.append(el('p', {text: plan.next_step}));
      wrap.append(el('a', {href: '/edge', text: 'Hitung edge dan ukuran posisi →'}));
    }

    wrap.append(el('p', {class: 'sub', text: a.why_note}));
    return wrap;
  }

  function markets(data, meta, host) {
    if (data.reachable && data.fallback) {
      host.append(el('div', {class: 'callout info'}, el('span', {class: 'bar'}),
        el('div', {class: 'body'}, el('b', {text: 'Sumber cadangan aktif. '}), data.reason)));
    } else {
      host.append(el('p', {class: data.reachable ? 'sub' : 'stale', text: data.reachable ? data.reason : (data.error || data.reason)}));
    }
    if (!data.reachable) return;

    const brief = briefBox(data.brief);
    if (brief) host.append(brief);

    if (!data.markets?.length) host.append(el('p', {text: 'Belum ada pasar aktif yang cocok.'}));

    for (const m of data.markets || []) {
      const box = el('details', {class: 'explain market-analysis', 'data-persist': m.id || m.question});
      const prices = m.outcomes || [];
      const sorted = [...prices].sort((a, b) => b.price - a.price);
      const leader = sorted[0];
      const tied = sorted.length > 1 && leader.price === sorted[1].price;
      const odds = leader ? `${tied ? 'Seimbang' : leader.outcome} ${(leader.price * 100).toFixed(1)}%` : 'Harga tidak tersedia';
      const summary = el('summary', {}, `${m.question} · ${odds}`);
      if (BH.ml && m.source) summary.append(' ', BH.ml.sourcePill(m.source));
      box.append(summary);

      const inner = el('div', {class: 'inner'});
      for (const o of prices) inner.append(el('div', {class: 'outcome-row'},
        el('span', {text: `${o.outcome}: ${(o.price * 100).toFixed(1)}% · $${o.price.toFixed(3)}`}),
        el('progress', {max: '1', value: String(o.price), 'aria-label': o.outcome})));

      inner.append(el('p', {text: `Volume 24j ${money(m.volume24h)} · total ${money(m.volume)} · likuiditas ${money(m.liquidity)} · spread ${m.spread == null ? '—' : (Number(m.spread) * 100).toFixed(2) + ' pp'}`}));
      inner.append(el('p', {class: 'sub', text: `Pembaruan sumber ${m.updated_at || 'tidak tersedia'} · berakhir ${m.end_date || 'tidak tersedia'}`}));

      if (m.analysis) inner.append(analysisBlock(m.analysis, m.id || m.question));

      if (/^0x[a-fA-F0-9]{64}$/.test(m.id || '')) {
        const flow = el('div');
        const flowButton = el('button', {type: 'button', class: 'quiet sm', text: 'Lihat arus taruhan & dominasi BUY'});
        const refreshFlow = () => load(flow, `/api/markets/polymarket/flow/${m.id}`, (d, fm, target) => {flowCache.set(m.id, d); renderFlow(d, fm, target);});
        flowButton.addEventListener('click', refreshFlow);
        inner.append(flowButton, flow);
        if (flowCache.has(m.id)) {renderFlow(flowCache.get(m.id), {}, flow); flow.dataset.loadedPath = `/api/markets/polymarket/flow/${m.id}`; refreshFlow();}
      }

      if (BH.ml) {
        const token = (m.clob_token_ids || [])[0];
        const model = m.analysis?.ev?.model;
        const fair = model?.available ? model.prob_yes : null;
        if ((m.source || 'polymarket') === 'polymarket' && token) {
          inner.append(el('h4', {text: 'IEP / IEV'}), BH.ml.ievBlock({source: 'polymarket', token, fair}));
        } else if ((m.source === 'limitless' || m.source === 'manifold') && m.id) {
          inner.append(el('h4', {text: 'IEP / IEV'}), BH.ml.ievBlock({source: m.source, id: m.id, fair}));
        }
        inner.append(el('h4', {text: 'Berita untuk pasar ini'}), BH.ml.newsEvidence(m.question),
          BH.ml.llmBlock({question: m.question, rules: m.rules || '',
            price: (m.outcomes || [])[0] ? `${((m.outcomes[0].price) * 100).toFixed(1)}%` : '',
            deadline: m.end_date || ''}));
      } else {
        const newsButton = el('button', {type: 'button', class: 'quiet sm', text: 'Periksa berita tentang pasar ini'});
        const evidence = el('div');
        const evidencePath = `/api/news?q=${encodeURIComponent(m.question)}&limit=10`;
        const refreshEvidence = () => load(evidence, evidencePath, (d, em, target) => {evidenceCache.set(m.question, d); headlines(d, em, target);});
        newsButton.addEventListener('click', refreshEvidence);
        inner.append(newsButton, evidence);
        if (evidenceCache.has(m.question)) {headlines(evidenceCache.get(m.question), {}, evidence); evidence.dataset.loadedPath = evidencePath; refreshEvidence();}
      }

      inner.append(el('h4', {text: 'Aturan penyelesaian'}), el('p', {text: m.rules || 'Belum tersedia; buka pasar untuk memeriksa aturan.'}),
        el('p', {class: 'sub', text: `Sumber resolusi: ${m.resolution_source || 'tidak tersedia'}`}),
        el('a', {href: safeLink(m.url), target: '_blank', rel: 'noopener',
          text: (BH.ml?.SOURCES?.[m.source || 'polymarket'] || {}).link || 'Buka pasar →'}));
      box.append(inner); host.append(box);
    }
  }

  // -------------------------------------------------------------- controls

  const presets = ['AI', 'Trump', 'Elon Musk', 'Bitcoin', 'Election', 'Outbreak', 'War', 'Inflation'];

  function controls(input, run, sector = '') {
    const group = input.closest('.controls');
    if (!group) return;
    const quick = el('select', {'aria-label': 'Topik pilihan'});
    quick.append(el('option', {value: '', text: 'Topik pilihan'}));
    for (const q of presets) quick.append(el('option', {value: q, text: q}));
    const trend = el('select', {'aria-label': 'Leaderboard topik dalam judul berita'});
    const preview = el('div', {class: 'trend-preview berita', hidden: true});
    group.after(preview);
    let ranked = new Map();
    const showTrend = () => {
      BH.clear(preview);
      const row = ranked.get(trend.value);
      preview.hidden = !row;
      if (!row) return;
      preview.append(el('p', {class: 'sub', text: `${trend.value} · ${row.count} judul dalam 24 jam · ${row.sources.size} feed. Contoh berita sumber:`}));
      for (const n of row.news.slice(0, 3)) preview.append(headlineItem(n));
    };
    trend.addEventListener('change', showTrend);
    trend.append(el('option', {value: '', text: 'Memuat leaderboard…'}));
    for (const [label, select] of [['Leaderboard berita', trend], ['Topik', quick]]) {
      const field = el('div', {class: 'field monitor-select'}, el('label', {text: label}), select);
      input.closest('.field').after(field);
      select.addEventListener('change', () => {if (select.value) {input.value = select.value; run();}});
    }
    const refresh = async () => {
      const p = await api(`/api/news?limit=60${sector ? '&sector=' + encodeURIComponent(sector) : ''}`);
      if (!p.ok) {trend.options[0].textContent = 'Leaderboard tidak tersedia'; return;}
      const chosen = trend.value;
      const stop = new Set('about after again against also amid been before being could first from have into just last more most news over says said some than that their them then there these they this those through today under what when where which while will with would your years year world live latest breaking updates'.split(' '));
      const ranks = new Map();
      const cutoff = Date.now() - 86400000;
      const recent = (p.data.news || []).filter((n) => {
        const t = new Date(n.time).getTime();
        return t >= cutoff && t <= Date.now();
      });
      for (const n of recent) {
        const words = new Set((n.title.toLowerCase().match(/\b(?:ai|[a-z]{4,})\b/g) || []).filter((w) => !stop.has(w)));
        for (const word of words) {
          if (!ranks.has(word)) ranks.set(word, {count: 0, sources: new Set(), news: []});
          const row = ranks.get(word); row.count++; row.sources.add(n.source); row.news.push(n);
        }
      }
      const leaders = [...ranks].filter(([, r]) => r.count >= 2).sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0])).slice(0, 15);
      trend.replaceChildren(el('option', {value: '', text: `24 jam · sampel ${recent.length} judul · bukan volume pencarian`}));
      leaders.forEach(([word, r], i) => trend.append(el('option', {value: word, text: `#${i + 1} ${word} · ${r.count} judul · ${r.sources.size} feed`})));
      if (!leaders.length) trend.append(el('option', {value: '', text: 'Belum ada topik berulang di sampel'}));
      ranked = ranks;
      trend.value = leaders.some(([word]) => word === chosen) ? chosen : '';
      showTrend();
    };
    refresh(); setInterval(() => {if (!document.hidden) refresh();}, 120000);
  }

  BH.monitor = {headlines, markets, controls, liveStream, analysisBlock, briefBox};

  // ------------------------------------------------------- sector monitors

  // Near-deadline markets of the sector, rated by the model. Loaded on its own,
  // so a slow market panel never hides the machine-learning reading.
  document.addEventListener('DOMContentLoaded', () => {
    const card = document.querySelector('[data-sector-ml]');
    if (!card || !BH.ml) return;
    const sector = card.dataset.sectorMl;
    const radarHost = card.querySelector('[data-ml-radar]');
    const detail = card.querySelector('[data-ml-detail]');
    const summary = card.querySelector('[data-ml-summary]');
    const path = `/api/deadlines?sector=${encodeURIComponent(sector)}&days=14&limit=40`;
    const render = (data, meta, target) => {
      const model = data.model || {};
      const trust = !model.trained ? 'model belum dilatih (latih di Paper Test)'
        : model.beats_market ? `model lebih tepat dari pasar di data uji (Brier ${model.brier_model} lawan ${model.brier_market})`
          : `model belum terbukti mengalahkan pasar (Brier ${model.brier_model} lawan ${model.brier_market})`;
      const down = (data.failed || []).map((f) => f.source);
      summary.textContent = `${BH.ml.radarSummary(data)} · tenggat ≤ 14 hari · ${trust}`
        + (down.length ? ` · tidak menjawab: ${down.join(', ')}` : '') + '. Klik baris untuk EV, IEP/IEV, berita, dan paper trade.';
      BH.ml.radar(target, data, {
        name: `ml-${sector}`, limit: 60, quietFailures: true, note: false, onlyModelled: true,
        detailBody: card.querySelector('[data-ml-detail-body]'),
        emptyText: 'Belum ada pasar sektor ini yang berakhir dalam 14 hari dari sumber yang menjawab.',
        onPick: (m) => {
          detail.hidden = false;
          card.querySelector('[data-ml-detail-title]').textContent = `${m.question} · ${BH.ml.sourceLabel(m.source)} · ${m.time_left} lagi`;
          requestAnimationFrame(() => detail.scrollIntoView({behavior: 'smooth', block: 'start'}));
        },
      });
    };
    const refresh = () => load(radarHost, path, render, {skeletonRows: 6});
    card.querySelector('[data-ml-detail-close]').addEventListener('click', () => { detail.hidden = true; });
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, 180000);
  });

  document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-sector-monitor]');
    if (!root) return;
    const sector = root.dataset.sectorMonitor;
    const input = root.querySelector('input');

    const term = () => input.value.trim();
    // The sector's own feeds, not headlines that happen to contain the word
    // "politics". A typed keyword searches within those feeds plus Google News.
    const newsPath = () => {
      const chosen = term();
      return !chosen || chosen === sector
        ? `/api/news?sector=${encodeURIComponent(sector)}&limit=30`
        : `/api/news?sector=${encodeURIComponent(sector)}&q=${encodeURIComponent(chosen)}&limit=30`;
    };

    const stream = liveStream(root.querySelector('[data-headlines]'), newsPath, {every: 15000});

    const loadMarkets = () => {
      const chosen = term();
      const query = !chosen || chosen === sector
        ? `sector=${encodeURIComponent(sector)}`
        : `q=${encodeURIComponent(chosen)}`;
      load(root.querySelector('[data-poly]'), `/api/markets/polymarket?${query}`, markets);
    };

    const search = () => { stream.tick(); loadMarkets(); };
    controls(input, search, sector);
    root.querySelector('button').addEventListener('click', search);
    input.addEventListener('keydown', (e) => {if (e.key === 'Enter') search();});
    root.addEventListener('monitor-query', (e) => {input.value = e.detail; search();});
    loadMarkets();
    setInterval(() => {if (!document.hidden) loadMarkets();}, 30000);
    document.addEventListener('visibilitychange', () => {if (!document.hidden) loadMarkets();});
  });
})();
