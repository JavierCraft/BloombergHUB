/* Bloomberg Hub — EV, IEV, model, news-evidence and LLM blocks.

   Shared by the sector monitors, News & Markets, Deadlines and Paper Test.
   Same rule as hub.js: every value from the API goes in through textContent.

   Colour rule kept from app.css: status colours mean status. Direction is data,
   so "naik" and "turun" use the two poles of the data palette, not good/bad. */
(() => {
  'use strict';
  const {el, api, load, clear, toast} = BH;

  const num = (v) => {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  /** 0.623 → "62.3%" */
  const pct = (v, digits = 1) => { const n = num(v); return n === null ? '—' : `${(n * 100).toFixed(digits)}%`; };
  /** 8.4 → "+8.4%" (value already in percent) */
  const signed = (v, digits = 1, unit = '%') => {
    const n = num(v);
    return n === null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(digits)}${unit}`;
  };
  const cents = (v) => { const n = num(v); return n === null ? '—' : `${(n * 100).toFixed(1)}¢`; };
  const money = (v) => { const n = num(v); return n === null ? '—' : `$${n.toLocaleString('id-ID', {maximumFractionDigits: 2})}`; };
  const shares = (v) => { const n = num(v); return n === null ? '—' : n.toLocaleString('id-ID', {maximumFractionDigits: 0}); };
  const tone = (v) => (num(v) ?? 0) > 0 ? 'pos' : (num(v) ?? 0) < 0 ? 'neg' : '';

  const WIB = new Intl.DateTimeFormat('id-ID', {
    timeZone: 'Asia/Jakarta', weekday: 'short', day: 'numeric', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  function wib(iso) {
    const d = new Date(iso);
    return Number.isFinite(d.getTime()) ? `${WIB.format(d)} WIB` : '—';
  }

  function countdown(iso) {
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return '—';
    let s = Math.round((t - Date.now()) / 1000);
    if (s <= 0) return 'tenggat lewat';
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    if (d) return `${d} hari ${h} jam`;
    if (h) return `${h} jam ${m} menit`;
    return `${m} menit`;
  }
  // One timer for every countdown on the page, not one per element.
  setInterval(() => {
    document.querySelectorAll('[data-countdown]').forEach((n) => { n.textContent = countdown(n.dataset.countdown); });
  }, 30000);

  function timeAgo(value) {
    const t = typeof value === 'number' ? value : new Date(value).getTime();
    if (!Number.isFinite(t) || t <= 0) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return 'baru saja';
    if (s < 3600) return `${Math.round(s / 60)} menit lalu`;
    if (s < 86400) return `${Math.round(s / 3600)} jam lalu`;
    return `${Math.round(s / 86400)} hari lalu`;
  }

  const safeLink = (url) => (/^https?:\/\//i.test(url || '') ? url : '#');

  /* Results opened inside a market survive the panel's own refresh. The sector
     monitor redraws its markets every 30 seconds; without this, an IEV reading
     or a paid Claude answer would vanish half a minute after it arrived. */
  const memo = new Map();
  const MEMO_CAP = 200;
  function remember(key, data) {
    memo.delete(key);
    memo.set(key, data);
    if (memo.size > MEMO_CAP) memo.delete(memo.keys().next().value);
  }
  const chip = (text, cls = '', title = '') => el('span', {class: `chip ${cls}`, title: title || null},
    el('span', {class: 'led'}), text);

  // ------------------------------------------------------------- model chip
  function directionChip(model) {
    if (!model || !model.available) {
      return el('span', {class: 'dir none', title: model?.reason || 'Model tidak tersedia', text: '—'});
    }
    const up = model.direction === 'naik';
    const down = model.direction === 'turun';
    return el('span', {
      class: `dir ${up ? 'up' : down ? 'down' : 'flat'}`,
      title: `Model ${pct(model.prob_yes)} lawan harga ${pct(model.market_prob_yes)} (${signed(model.edge_pp, 1, ' poin')})`,
      text: `${up ? '▲ naik' : down ? '▼ turun' : '■ datar'} ${pct(model.prob_yes)}`,
    });
  }

  function trustChip(model) {
    if (!model || !model.available) return null;
    return model.beats_market
      ? chip('lebih tepat dari pasar (data uji)', 'good')
      : chip('belum terbukti mengalahkan pasar', 'warning',
        'Pada data uji, model tidak lebih tepat dari harga pasar secara meyakinkan.');
  }

  // --------------------------------------------------------------- EV block
  function evTable(ev, key = '') {
    const wrap = el('div', {class: 'ev-block'});
    if (!ev) return null;
    if (!ev.known) {
      wrap.append(el('p', {class: 'sub', text: ev.reason || 'EV tidak bisa dihitung.'}));
      return wrap;
    }
    const model = ev.model || {};
    const table = el('table', {class: 'ev-table'});
    const head = el('tr');
    for (const h of ['Outcome', 'Harga', 'Masuk (ask)', 'Break-even', 'EV konsensus', 'Peluang model', 'EV model']) {
      head.append(el('th', {text: h}));
    }
    table.append(el('thead', {}, head));
    const body = el('tbody');
    for (const r of ev.rows) {
      const best = ev.best && ev.best.outcome === r.outcome;
      body.append(el('tr', {class: best ? 'best' : null},
        el('td', {class: 'strong', text: r.outcome}),
        el('td', {class: 'num', text: cents(r.price)}),
        el('td', {class: 'num', title: r.entry_source, text: cents(r.ask)}),
        el('td', {class: 'num', text: r.breakeven_pct == null ? '—' : `${r.breakeven_pct.toFixed(1)}%`}),
        el('td', {class: `num ${tone(r.ev_consensus_pct)}`, text: signed(r.ev_consensus_pct)}),
        el('td', {class: 'num', text: pct(r.model_prob)}),
        el('td', {class: `num ${tone(r.ev_model_pct)}`, text: signed(r.ev_model_pct)})));
    }
    table.append(body);
    wrap.append(el('div', {class: 'tablewrap ev-wrap'}, table));

    const chips = el('div', {class: 'trait-chips'});
    if (model.available) {
      chips.append(directionChip(model));
      const trust = trustChip(model);
      if (trust) chips.append(trust);
      if (model.bucket) chips.append(chip(`sisa waktu ${model.bucket}`));
    } else {
      chips.append(chip('model: tidak dipakai', '', model.reason || ''));
    }
    wrap.append(chips);
    wrap.append(el('p', {class: 'ev-verdict', text: ev.verdict}));
    if (!model.available && model.reason) wrap.append(el('p', {class: 'sub', text: model.reason}));
    if (model.available && model.drivers && model.drivers.length) {
      wrap.append(el('p', {class: 'sub', text: 'Koreksi terbesar model terhadap harga pasar: '
        + model.drivers.map((d) => `${d.label} (${signed(d.contribution, 2, '')} log-odds)`).join(' · ')}));
    }
    if (model.available && model.brier_model != null) {
      wrap.append(el('p', {class: 'sub', text: `Rekam jejak di data uji: Brier model ${model.brier_model} lawan pasar ${model.brier_market} (makin kecil makin tepat) · ${model.model_id}`}));
    }

    if (ev.rows.length === 2 && ev.rows[0].ask && ev.rows[1].ask) {
      const out = el('span', {class: 'sub', text: `EV dihitung langsung di browser dari perkiraan Anda untuk "${ev.rows[0].outcome}".`});
      const input = el('input', {type: 'number', min: '0', max: '100', step: '0.5', inputmode: 'decimal',
        placeholder: 'mis. 62', 'aria-label': `Perkiraan Anda (%) untuk ${ev.rows[0].outcome}`});
      const compute = () => {
        if (key) remember(`own:${key}`, input.value);
        const q = Number(input.value) / 100;
        if (input.value === '' || !(q >= 0 && q <= 1)) {
          out.textContent = 'Isi angka 0–100.';
          return;
        }
        out.textContent = ev.rows.map((r, i) => {
          const prob = i === 0 ? q : 1 - q;
          return `${r.outcome}: EV ${signed((prob / r.ask - 1) * 100)} per $1`;
        }).join(' · ');
      };
      input.addEventListener('input', compute);
      if (key && memo.get(`own:${key}`)) {
        input.value = memo.get(`own:${key}`);
        compute();
      }
      wrap.append(el('div', {class: 'ev-own'}, el('label', {text: 'Perkiraan Anda (%)'}), input, out));
    }
    wrap.append(el('p', {class: 'sub', text: ev.note}));
    return wrap;
  }

  // -------------------------------------------------------------- IEP / IEV
  function renderIev(data, host) {
    const a = data.auction || {};
    const grid = el('div', {class: 'iev-grid'});
    const cell = (label, value, foot) => el('div', {class: 'iev-cell'},
      el('span', {class: 'label', text: label}),
      el('span', {class: 'value', text: value}),
      foot ? el('span', {class: 'foot', text: foot}) : null);
    grid.append(
      cell('IEP', a.iep == null ? '—' : cents(a.iep), a.crossed ? 'harga ekuilibrium' : 'buku tidak bersilangan'),
      cell('IEV', `${shares(a.iev)} lembar`, a.iev_notional ? money(a.iev_notional) : 'tidak ada volume yang tertukar'),
      cell('Surplus', a.surplus == null ? '—' : `${shares(a.surplus)} lembar`, a.surplus_side ? `sisi ${a.surplus_side}` : ''),
      cell('Best bid / ask', `${cents(a.best_bid)} / ${cents(a.best_ask)}`, a.mid == null ? '' : `tengah ${cents(a.mid)}`),
      cell('Kedalaman', `${shares(a.bid_depth?.shares)} / ${shares(a.ask_depth?.shares)}`, 'lembar bid / ask'));
    host.append(grid, el('p', {text: a.reason || ''}), el('p', {class: 'sub', text: `Metode: ${a.method || '—'}`}));

    const fv = data.fair_value;
    if (fv && fv.known) {
      host.append(el('h4', {text: `Volume menuju nilai wajar model (${pct(fv.fair)})`}));
      const facts = el('dl', {class: 'facts'});
      const row = (k, v) => facts.append(el('dt', {text: k}), el('dd', {text: v}));
      const side = (s) => s.shares
        ? `${shares(s.shares)} lembar · biaya ${money(s.cost)} · VWAP ${cents(s.vwap)} · untung harapan ${money(s.expected_profit)} (${signed(s.ev_pct)})`
        : 'tidak ada pesanan di sisi ini yang lebih murah dari nilai wajar';
      row('Beli YES di bawah nilai wajar', side(fv.buy_yes || {}));
      row('Beli NO di bawah nilai wajar', side(fv.buy_no || {}));
      host.append(facts, el('p', {class: 'sub', text: fv.note}));
    }
    const notes = [data.note, data.source ? `Sumber buku: ${data.source}` : null, data.book_time ? `waktu buku ${data.book_time}` : null].filter(Boolean);
    if (notes.length) host.append(el('p', {class: 'sub', text: notes.join(' · ')}));
  }

  function ievBlock(params) {
    const qs = new URLSearchParams({source: params.source});
    if (params.token) qs.set('token', params.token);
    if (params.id) qs.set('id', params.id);
    if (params.fair != null) qs.set('fair', params.fair);
    const path = `/api/markets/iev?${qs}`;
    const target = el('div', {class: 'iev-block'});
    const button = el('button', {type: 'button', class: 'quiet sm', text: 'Hitung IEP / IEV dari buku pesanan'});
    button.addEventListener('click', () => load(target, path,
      (data, meta, host) => { remember(path, data || {}); renderIev(data || {}, host); }, {skeletonRows: 3}));
    if (memo.has(path)) {
      renderIev(memo.get(path), target);
      target.dataset.loadedPath = path;
    }
    return el('div', {}, button, target);
  }

  // --------------------------------------------------------- news evidence
  function renderEvidence(d, meta, host) {
    if (!d || Array.isArray(d)) { host.append(el('p', {class: 'sub', text: 'Tidak ada data berita.'})); return; }
    const facts = el('dl', {class: 'facts'});
    const row = (k, v) => facts.append(el('dt', {text: k}), el('dd', {text: v}));
    row('Kata kunci pencarian', d.query || '—');
    row('Judul relevan', `${d.n_24h ?? 0} dalam 24 jam · ${d.n_72h ?? 0} dalam 72 jam · ${d.sources_72h ?? 0} sumber`);
    row('Nada kabar (leksikon)', `${d.tone_label || '—'}${d.tone_72h == null ? '' : ` (${signed(d.tone_72h, 2, '')})`}`);
    row('Percepatan liputan', d.acceleration == null ? '—' : signed(d.acceleration, 1, ' judul/hari'));
    host.append(facts);
    if (d.search_failed) host.append(el('p', {class: 'stale', text: `Pencarian Google News gagal: ${d.search_failed}. Dipakai korpus lokal saja.`}));
    const list = el('div', {class: 'berita'});
    for (const n of d.evidence || []) {
      list.append(el('a', {class: 'item', href: safeLink(n.url), target: '_blank', rel: 'noopener'},
        el('div', {class: 'judul', text: n.title}),
        el('div', {class: 'meta'},
          el('span', {class: 'sumber', text: n.source || ''}),
          el('span', {text: n.time ? timeAgo(n.time) : 'waktu tidak tersedia'}),
          el('span', {text: `relevansi ${num(n.relevance)?.toFixed(2) ?? '—'}`}),
          el('span', {class: tone(n.tone), text: `nada ${signed(n.tone, 2, '')}`}))));
    }
    if (!(d.evidence || []).length) list.append(el('p', {class: 'sub', text: 'Belum ada judul yang cukup relevan.'}));
    host.append(list, el('p', {class: 'sub', text: d.note || ''}));
  }

  function newsEvidence(question) {
    const path = `/api/news/ml/market?q=${encodeURIComponent(question)}`;
    const target = el('div');
    const button = el('button', {type: 'button', class: 'quiet sm', text: 'Analisis berita (ML): relevansi & nada'});
    button.addEventListener('click', () => load(target, path,
      (data, meta, host) => { remember(path, data); renderEvidence(data, meta, host); }, {skeletonRows: 4}));
    if (memo.has(path)) {
      renderEvidence(memo.get(path), {}, target);
      target.dataset.loadedPath = path;
    }
    return el('div', {}, button, target);
  }

  // -------------------------------------------------------------------- LLM
  function renderLlm(d, host) {
    if (!d.ok) {
      host.append(el('div', {class: 'callout warn'}, el('span', {class: 'bar'}),
        el('div', {class: 'body', text: `${d.reason || 'Analisis gagal.'}${d.category ? ` (kategori: ${d.category})` : ''}`})));
      return;
    }
    const stanceTone = d.stance === 'YES' ? 'up' : d.stance === 'NO' ? 'down' : 'flat';
    host.append(el('div', {class: 'stance'},
      el('span', {class: `dir ${stanceTone}`, text: d.stance === 'UNCLEAR' ? 'belum jelas' : `condong ${d.stance}`}),
      el('span', {text: `peluang YES menurut Claude ${pct(d.probability_yes)} · keyakinan ${d.confidence}`})));
    host.append(el('p', {class: 'reading', text: d.summary}));
    const headlines = d.headlines || [];
    if ((d.key_points || []).length) {
      const ul = el('ul', {class: 'plan'});
      for (const p of d.key_points) {
        const li = el('li', {text: p.point + ' '});
        for (const n of p.headlines || []) {
          const h = headlines[n - 1];
          if (h) li.append(el('a', {href: safeLink(h.url), target: '_blank', rel: 'noopener', title: h.title, text: `[${n}]`}), ' ');
        }
        ul.append(li);
      }
      host.append(el('h4', {text: 'Poin utama'}), ul);
    }
    const list = (title, items) => {
      if (!(items || []).length) return;
      const ul = el('ul', {class: 'plan'});
      items.forEach((x) => ul.append(el('li', {text: x})));
      host.append(el('h4', {text: title}), ul);
    };
    list('Yang bisa mengubah pembacaan ini', d.what_would_change_it);
    list('Risiko aturan penyelesaian', d.resolution_risks);
    if (d.deadline_note) host.append(el('p', {class: 'breakeven', text: d.deadline_note}));
    const m = d.meta || {};
    host.append(el('p', {class: 'sub', text: [
      `model ${m.served_by || m.requested_model || '—'}${m.fallback_used ? ' (fallback)' : ''}`,
      m.input_tokens != null ? `${m.input_tokens} token masuk / ${m.output_tokens} keluar` : null,
      m.cost_usd != null ? `biaya ±$${m.cost_usd}` : null,
      d.cached ? `dari simpanan (${d.cache_age_s}s)` : null,
    ].filter(Boolean).join(' · ')}));
    host.append(el('p', {class: 'sub', text: d.note || ''}));
  }

  function llmBlock(market) {
    const key = `llm:${market.question}`;
    const target = el('div', {class: 'llm-block'});
    const button = el('button', {type: 'button', class: 'quiet sm', text: 'Minta pendapat Claude (LLM · berbayar)'});
    button.addEventListener('click', async () => {
      button.disabled = true;
      clear(target).append(BH.skeleton(3));
      const payload = await api('/api/news/llm', {method: 'POST', body: market});
      button.disabled = false;
      clear(target);
      if (!payload.ok) { target.append(BH.errorState(payload.error || {code: 'INTERNAL'})); return; }
      remember(key, payload.data || {});
      renderLlm(payload.data || {}, target);
    });
    if (memo.has(key)) renderLlm(memo.get(key), target);
    return el('div', {}, button, target);
  }

  // ------------------------------------------------------------ paper trade
  function paperButtons(snap, onDone) {
    const wrap = el('div', {class: 'paper-actions'});
    const labels = [snap.outcomes?.[0]?.outcome || 'YES', snap.outcomes?.[1]?.outcome || 'NO'];
    ['YES', 'NO'].forEach((side, i) => {
      const button = el('button', {type: 'button', class: 'quiet sm', text: `Paper trade: ${labels[i]}`});
      button.addEventListener('click', async () => {
        button.disabled = true;
        const payload = await api('/api/paper/trade', {method: 'POST',
          body: {source: snap.source, market_id: snap.id, side}});
        button.disabled = false;
        if (payload.ok) {
          toast(`Posisi paper dibuka: ${labels[i]} @ ${cents(payload.data.entry_price)} · uang virtual`);
          if (onDone) onDone(payload.data);
        } else {
          toast(payload.error?.message || 'Gagal membuka posisi paper.');
        }
      });
      wrap.append(button);
    });
    wrap.append(el('span', {class: 'sub', text: 'Uang virtual — tidak ada order sungguhan.'}));
    return wrap;
  }

  // ------------------------------------------------------- deadline radar
  /* One table and one detail view for every list of near-deadline markets:
     the Deadlines page, each sector page's ML card, and the Overview. */
  const SOURCES = {
    polymarket: {label: 'Polymarket', unit: '$', link: 'Buka pasar Polymarket →'},
    limitless: {label: 'Limitless · USDC', unit: '$', link: 'Buka pasar Limitless →'},
    manifold: {label: 'Manifold · uang main', unit: 'M', link: 'Buka pasar Manifold →'},
  };
  const sourceLabel = (source) => (SOURCES[source] || {label: source || '—'}).label;
  const sourcePill = (source) => el('span', {class: `pill src src-${SOURCES[source] ? source : 'other'}`, text: sourceLabel(source)});

  function sizeText(m) {
    const unit = (SOURCES[m.source] || {}).unit || '';
    if (m.liquidity != null) return `${unit}${BH.compact(m.liquidity)} likuid`;
    if (m.volume != null) return `${unit}${BH.compact(m.volume)} volume`;
    return '—';
  }

  function marketCell(m) {
    const tags = el('span', {class: 'tags'}, sourcePill(m.source), el('span', {class: 'pill', text: m.bucket}));
    if (m.event_title && m.event_title !== m.question) tags.append(el('span', {class: 'pill', text: m.event_title}));
    return el('div', {class: 'dl-market'}, el('span', {class: 'q', text: m.question}), tags);
  }

  function marketDetail(host, m) {
    clear(host);
    const facts = el('dl', {class: 'facts'});
    const row = (k, v) => { if (v !== null && v !== undefined && v !== '') facts.append(el('dt', {text: k}), el('dd', {text: v})); };
    row('Sumber', sourceLabel(m.source));
    row('Deadline', `${BH.stampUTC(m.deadline, true)} UTC · ${wib(m.deadline)}`);
    row('Sisa waktu', `${m.time_left} (${m.bucket})`);
    row('Diposting', m.created ? `${BH.stampUTC(m.created, true)} UTC` : null);
    row('Harga YES', `${pct(m.p_yes)}${m.best_bid != null ? ` · bid ${cents(m.best_bid)} / ask ${cents(m.best_ask)}` : ''}`);
    row('Gerak 24 jam / 7 hari', `${m.chg_1d == null ? '—' : signed(m.chg_1d * 100, 1, ' poin')} / ${m.chg_7d == null ? '—' : signed(m.chg_7d * 100, 1, ' poin')}`);
    row('Likuiditas / volume', sizeText(m));
    row('Volume 24 jam', m.volume24h == null ? null : BH.compact(m.volume24h));
    row('Trader', m.bettors == null ? null : String(m.bettors));
    host.append(facts, el('p', {}, el('a', {href: safeLink(m.url), target: '_blank', rel: 'noopener',
      text: (SOURCES[m.source] || {}).link || 'Buka pasar →'})));

    const heading = (text) => el('h3', {class: 'detail-h', text});
    host.append(heading('Model & EV'));
    const evBox = evTable(m.ev, `${m.source}:${m.id}`);
    if (evBox) host.append(evBox);
    if (m.model && m.model.available && (m.model.caveats || []).length) {
      host.append(el('p', {class: 'sub', text: `Catatan model: ${m.model.caveats.join(' ')}`}));
    }

    host.append(heading('IEP / IEV'));
    const fair = m.model && m.model.available ? m.model.prob_yes : null;
    if (m.source === 'polymarket' && m.token_id) host.append(ievBlock({source: 'polymarket', token: m.token_id, fair}));
    else if (m.source === 'manifold' || m.source === 'limitless') host.append(ievBlock({source: m.source, id: m.id, fair}));
    else host.append(el('p', {class: 'sub', text: 'Buku pesanan tidak tersedia untuk pasar ini.'}));

    host.append(heading('Berita'));
    host.append(newsEvidence(m.question));
    host.append(llmBlock({question: m.question, rules: m.rules || '', price: pct(m.p_yes),
      deadline: m.deadline, time_left: m.time_left}));

    host.append(heading('Paper test'));
    host.append(paperButtons(m));

    if (m.rules) {
      host.append(el('details', {class: 'explain'}, el('summary', {text: 'Aturan penyelesaian'}),
        el('div', {class: 'inner'}, el('p', {text: m.rules}))));
    }
  }

  /** Render a `/api/deadlines` payload as a sortable table; a row click fills `options.detailBody`. */
  function radar(host, data, options = {}) {
    if (!data || Array.isArray(data)) data = {markets: []};
    for (const f of data.failed || []) {
      if (options.quietFailures && (data.markets || []).length) continue;
      host.append(el('div', {class: 'stale'}, el('span', {text: `${f.source}: ${f.reason}`})));
    }
    const byKey = new Map();
    // Overview and sector cards list only markets the model can rate; the
    // Deadlines page lists everything and says so per row.
    const all = data.markets || [];
    const shown = options.onlyModelled ? all.filter((m) => m.model && m.model.available) : all;
    if (options.onlyModelled && shown.length < all.length) {
      host.append(el('p', {class: 'sub radar-note', text: `${all.length - shown.length} pasar tidak ditampilkan karena di luar jangkauan model `
        + '(tinggal kurang dari ±5 jam, misalnya pasar kripto 5 menit/per jam). Semuanya ada di halaman Deadlines.'}));
    }
    const records = shown.map((m) => {
      byKey.set(`${m.source}:${m.id}`, m);
      const best = m.ev && m.ev.best;
      return {
        _m: m, deadline_ms: m.deadline_ms, market: m.question, yes_price: m.p_yes,
        model_prob: m.model && m.model.available ? m.model.prob_yes : null,
        best_ev: best ? best.ev_model_pct : null,
        size: m.liquidity ?? m.volume ?? null, posted: m.created_ms || null,
      };
    });
    const columns = options.columns || ['deadline_ms', 'market', 'yes_price', 'model_prob', 'best_ev', 'size'];
    const table = el('div', {class: 'radar-table'});
    host.append(table);
    BH.renderTable(table, records, columns, {
      name: options.name || 'deadlines', sort: 'deadline_ms', dir: 'asc', limit: options.limit || 200,
      headers: {deadline_ms: 'Deadline', market: 'Market', yes_price: 'YES price', model_prob: 'ML P(YES) · direction',
        best_ev: 'Best EV (model)', size: 'Liquidity / volume', posted: 'Posted'},
      rowAttrs: (r) => ({'data-market': `${r._m.source}:${r._m.id}`}),
      cells: {
        deadline_ms: (r) => el('div', {class: 'dl-deadline'},
          el('span', {class: 'left', 'data-countdown': r._m.deadline, text: countdown(r._m.deadline)}),
          el('span', {class: 'when', text: wib(r._m.deadline)})),
        market: (r) => marketCell(r._m),
        yes_price: (r) => el('div', {},
          el('div', {text: pct(r._m.p_yes)}),
          el('div', {class: 'when', text: r._m.best_bid != null ? `bid ${cents(r._m.best_bid)} · ask ${cents(r._m.best_ask)}` : 'tanpa buku'})),
        model_prob: (r) => directionChip(r._m.model),
        best_ev: (r) => {
          const best = r._m.ev && r._m.ev.best;
          return best ? el('span', {class: 'pos', text: `${best.outcome} ${signed(best.ev_model_pct)}`})
            : el('span', {class: 'muted', text: '—'});
        },
        size: (r) => el('span', {text: sizeText(r._m)}),
        posted: (r) => el('span', {text: r._m.created ? BH.stampUTC(r._m.created, true) : '—'}),
      },
      emptyText: options.emptyText || 'Tidak ada pasar dengan tenggat dalam rentang ini dari sumber yang menjawab.',
    });
    if (data.note && options.note !== false) host.append(el('p', {class: 'sub radar-note', text: data.note}));

    if (options.detailBody) {
      table.addEventListener('click', (event) => {
        const tr = event.target.closest('tr[data-market]');
        if (!tr || event.target.closest('a,button,input')) return;
        const m = byKey.get(tr.dataset.market);
        if (!m) return;
        table.querySelectorAll('tr[data-market]').forEach((x) => x.classList.toggle('on', x === tr));
        if (options.onPick) options.onPick(m);
        marketDetail(options.detailBody, m);
      });
    }
    return byKey;
  }

  /** One-line summary of a radar payload: markets per source, modelled, positive EV. */
  function radarSummary(data) {
    const by = Object.entries(data.by_source || {}).map(([k, v]) => `${sourceLabel(k)} ${v}`).join(' · ');
    return `${data.count ?? 0} pasar${by ? ` (${by})` : ''} · ${data.with_model ?? 0} dinilai model · ${data.positive_ev ?? 0} EV model positif`;
  }

  BH.ml = {
    num, pct, signed, cents, money, shares, tone, wib, countdown, timeAgo, chip,
    directionChip, trustChip, evTable, renderIev, ievBlock, renderEvidence, newsEvidence,
    renderLlm, llmBlock, paperButtons, SOURCES, sourceLabel, sourcePill, marketDetail, radar, radarSummary,
  };
})();
