/* Macro spec (S6): SPLC, NEWS, ECO against the real gateway.
   What is on screen is compared with what the page received (its own /api answers, captured in transit)
   and with a second fetch by this test; interactions use real mouse and keyboard input; the failure paths
   (stale payload altered in transit, upstream error, a feed that is down, a rate-limited calendar host,
   hostile headline text, a gateway that stops and comes back) are provoked by altering answers in
   transit — never by the page. Upstream-vs-gateway comparisons are done by hand (see the slice report). */
'use strict';
const http = require('http');

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res({ status: r.statusCode, body: JSON.parse(b) }); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
/* the page's number format, written out again here on purpose */
function num(v, d) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  let s = v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  if (/^-0(\.0*)?$/.test(s)) s = s.slice(1);
  return s;
}
const signed = (v, d) => { const s = num(v, d); return v > 0 && !/^0(\.0*)?$/.test(s) ? '+' + s : s; };
const p2 = n => (n < 10 ? '0' : '') + n;
const hm = t => { const d = new Date(t * 1000); return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + 'Z'; };
const ymd = t => { const d = new Date(t * 1000); return d.getUTCFullYear() + '-' + p2(d.getUTCMonth() + 1) + '-' + p2(d.getUTCDate()); };
const MOD = m => '.mod[data-mod="' + m + '"]';
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* remember every JSON answer of a route as the page received it */
function tap(page, re) {
  const got = [];
  page.on('response', async r => { if (re.test(r.url()) && r.status() === 200) { try { got.push(await r.json()); } catch (e) { /* aborted */ } } });
  return got;
}
/* alter (or replace) the answers of one route in transit */
async function alter(p, glob, fn) {
  await p.page.route(glob, async route => {
    let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
    let body; try { body = await res.json(); } catch (e) { return route.fulfill({ response: res }); }
    const out = await fn(body, res);
    if (out && out.__status) return route.fulfill({ status: out.__status, contentType: 'application/json', headers: out.__headers || {}, body: JSON.stringify(out.body) });
    await route.fulfill({ response: res, json: out || body });
  });
}

module.exports = {
  name: 'macro', slice: 'macro',
  async run(ctx) {
    const { page, check } = ctx;
    const section = async (name, fn) => { console.log('    · ' + name); try { await fn(); } catch (e) { ctx.fail(name + ' threw: ' + (e && e.stack || e)); } };
    const supplyGot = tap(page, /\/api\/supply\/oil/), newsGot = tap(page, /\/api\/news/), calGot = tap(page, /\/api\/calendar/);
    let supplyReqs = 0, newsReqs = 0, calReqs = 0;
    page.on('request', r => { if (/\/api\/supply\/oil/.test(r.url())) supplyReqs++; if (/\/api\/news/.test(r.url())) newsReqs++; if (/\/api\/calendar/.test(r.url())) calReqs++; });

    /* ════════════════════════════ SPLC ════════════════════════════ */
    const dec = u => (u === '%' ? 1 : 0);
    const chgText = n => (typeof n.chg !== 'number' ? 'no prior week' : signed(n.chg, dec(n.unit)) + (n.unit === '%' ? ' pt' : ' (' + signed(n.chg_pct, 2) + '%)'));
    const chgClass = n => (typeof n.chg !== 'number' || n.chg === 0 || !n.tight ? 'flat' : (n.chg > 0) === (n.tight === 'up') ? 'up' : 'down');
    const cardsShown = pg => pg.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="SPLC"] .splc-node')).map(el => ({
      key: el.dataset.key, label: el.querySelector('.sn-label').textContent, unit: el.querySelector('.sn-unit').textContent, value: el.querySelector('.sn-val').textContent,
      chg: el.querySelector('.sn-chg').textContent, cls: el.querySelector('.sn-chg').className.replace('sn-chg ', ''), sel: el.classList.contains('sel'),
      w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height, spark: el.querySelector('canvas').clientWidth })));
    const splcReady = (c, extra) => c.waitFor(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'); return s && !s.querySelector('.mod-body > .ot-state') && /\d/.test((s.querySelector('.splc-node[data-key="stocks"] .sn-val') || {}).textContent || '') && s.querySelectorAll('.splc-partners tbody tr').length > 3; },
      Object.assign({ timeout: 45000, msg: 'SPLC shows the EIA nodes and the partner table' }, extra || {}));

    await section('SPLC: every node shows the EIA figure it received', async () => {
      await ctx.select('CL'); await ctx.go('SPLC');
      await splcReady(ctx);
      const d = supplyGot[supplyGot.length - 1];
      check(!!d && d.source === 'eia' && d.freshness === 'WEEKLY' && d.nodes.length === 18, 'the page received /api/supply/oil with 18 nodes');
      const again = (await getJson(ctx.base + '/api/supply/oil')).body;
      check(again.week === d.week && JSON.stringify(again.nodes.map(n => n.value)) === JSON.stringify(d.nodes.map(n => n.value)), 'a second fetch of /api/supply/oil gives the same week and values');
      const shown = await cardsShown(page);
      check(shown.length === 18, 'SPLC shows ' + shown.length + ' node cards (18 expected)');
      d.nodes.forEach(n => {
        const c = shown.find(x => x.key === n.key);
        if (!check(!!c, 'no card for node ' + n.key)) return;
        check(c.value === num(n.value, dec(n.unit)), n.key + ': shows ' + c.value + ', received ' + n.value);
        check(c.chg === chgText(n), n.key + ': change shows "' + c.chg + '", expected "' + chgText(n) + '"');
        check(c.cls === chgClass(n), n.key + ': change colour class ' + c.cls + ', expected ' + chgClass(n) + ' (chg ' + n.chg + ', tightens when ' + n.tight + ')');
        check(c.label === n.label, n.key + ': label ' + c.label);
        check(c.w > 90 && c.h > 30 && c.spark > 40, n.key + ': card ' + Math.round(c.w) + 'x' + Math.round(c.h) + ', sparkline ' + c.spark + ' px wide');
        check(/^[+\-]|^0|^no prior/.test(c.chg), n.key + ': the change carries its sign: ' + c.chg);
      });
      const head = await page.evaluate(() => { const hd = document.querySelector('.mod[data-mod="SPLC"] .mod-head'); return { badge: hd.querySelector('.badge').textContent, fresh: hd.querySelector('.badge').dataset.fresh, note: hd.querySelector('.mod-note').textContent, week: document.querySelector('.splc-week').textContent }; });
      check(head.fresh === 'WEEKLY' && head.badge.indexOf(d.week.slice(5)) >= 0 && /EIA/.test(head.badge), 'header badge says WEEKLY with the week-ending date: ' + head.badge);
      check(head.note.indexOf('week ending ' + d.week) >= 0 && /not live/.test(head.note), 'header note names the week and says it is not live: ' + head.note);
      check(!/LIVE|RT\b/.test(head.badge), 'weekly data is never labelled live: ' + head.badge);
      await ctx.assertHeaderBadge('SPLC');
      console.log('      week ending ' + d.week + ' · stocks ' + shown.find(x => x.key === 'stocks').value + ' kbbl · production ' + shown.find(x => x.key === 'production').value + ' kb/d');
      /* partners */
      const p = d.partners;
      if (check(!!p.rows && p.rows.length > 5, 'the page received import partners: ' + JSON.stringify(p.error || {}))) {
        const rows = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="SPLC"] .splc-partners tbody tr')).map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.textContent)));
        const meta = await page.evaluate(() => ({ title: document.querySelector('.splc-partners .sd-title').textContent, badge: document.querySelector('.splc-partners .badge').textContent, total: document.querySelector('.sp-total').textContent }));
        check(rows.length === p.rows.length, 'partner table has ' + rows.length + ' rows, received ' + p.rows.length);
        p.rows.slice(0, 8).forEach((r, i) => {
          const s = rows[i] || [];
          check(s[0] === String(i + 1) && s[1] === r.name && s[2] === num(r.value_usd / 1e9, 2) && s[4] === num(r.share, 2) + '%', 'partner row ' + (i + 1) + ' shows ' + s.slice(0, 5).join(' | ') + ', received ' + r.name + ' ' + r.value_usd + ' ' + r.share);
        });
        check(meta.title.indexOf(String(p.year)) >= 0 && /ANNUAL/.test(meta.badge) && meta.badge.indexOf(String(p.year)) >= 0 && /UN-COMTRADE/.test(meta.badge), 'partners are labelled ANNUAL with their year and source: ' + meta.title + ' / ' + meta.badge);
        check(meta.total.indexOf(num(p.total.value_usd / 1e9, 2)) >= 0, 'World total shown: ' + meta.total);
      }
    });

    await section('SPLC: click-through detail, hover read-out, range, keyboard', async () => {
      const d = supplyGot[supplyGot.length - 1];
      await ctx.mouse.click(MOD('SPLC') + ' .splc-node[data-key="cushing"]');
      const n = d.nodes.find(x => x.key === 'cushing');
      const det = await page.evaluate(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'); return { title: s.querySelector('.splc-detail .sd-title').textContent, series: s.querySelector('.sd-series').textContent, value: s.querySelector('.sd-value').textContent,
        chg: s.querySelector('.sd-chg').textContent, week: s.querySelector('.sd-week').textContent, sel: s.querySelector('.splc-node.sel').dataset.key, prev: s.querySelector('.sd-kv dd').textContent }; });
      check(det.sel === 'cushing' && det.title === n.label && det.series === 'EIA ' + n.series && det.value === num(n.value, 0), 'clicking the Cushing card opens its detail: ' + JSON.stringify(det));
      check(det.week.indexOf(n.week) >= 0 && det.prev.indexOf(num(n.prev, 0)) >= 0 && det.chg.indexOf(signed(n.chg, 0)) === 0, 'detail shows week, prior week and change: ' + det.week + ' / ' + det.prev + ' / ' + det.chg);
      /* hover: the read-out is a real week of the history */
      const box = await page.locator(MOD('SPLC') + ' .sd-chart canvas').boundingBox();
      const seen = [];
      for (const fx of [0.2, 0.5, 0.8]) {
        await page.mouse.move(box.x + 10 + (box.width - 88) * fx, box.y + box.height * 0.5, { steps: 5 });
        await sleep(120);
        const tip = await page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.textContent : null; });
        const m = tip && /Week ending (\d{4}-\d\d-\d\d)[\s\S]*?: ([\d,.\-]+) kbbl/.exec(tip);
        if (!check(!!m, 'chart hover shows a read-out at ' + fx + ': ' + tip)) continue;
        const pt = n.history.find(h => ymd(h[0]) === m[1]);
        check(!!pt && num(pt[1], 0) === m[2], 'hover read-out ' + m[1] + ' = ' + m[2] + ' is a point of the series (' + (pt ? pt[1] : 'no such week') + ')');
        seen.push(m[1]);
      }
      check(new Set(seen).size === 3 && seen[0] < seen[1] && seen[1] < seen[2], 'the read-out follows the mouse left to right: ' + seen.join(' '));
      /* range 2Y: the left edge is an older week than at 1Y */
      await page.mouse.move(box.x + 12, box.y + box.height * 0.5, { steps: 3 }); await sleep(120);
      const left1y = await page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? (/(\d{4}-\d\d-\d\d)/.exec(t.textContent) || [])[1] : null; });
      await ctx.mouse.click(MOD('SPLC') + ' .sd-range .ot-tab[data-id="104"]');
      await page.mouse.move(box.x + 12, box.y + box.height * 0.5, { steps: 3 }); await sleep(250);
      const left2y = await page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? (/(\d{4}-\d\d-\d\d)/.exec(t.textContent) || [])[1] : null; });
      check(left1y && left2y && left2y < left1y, 'the 2Y range reaches further back (' + left2y + ') than 1Y (' + left1y + ')');
      await page.mouse.move(box.x - 40, box.y - 40);
      /* keyboard: arrows walk the nodes */
      await page.locator(MOD('SPLC') + ' .splc-node[data-key="production"]').focus();
      await page.keyboard.press('ArrowDown');
      const k1 = await page.evaluate(() => document.querySelector('.mod[data-mod="SPLC"] .splc-node.sel').dataset.key);
      check(k1 === 'imports', 'ArrowDown on a focused node selects the next one: ' + k1);
      await page.keyboard.press('Enter');
      /* canvases drew something */
      const ink = await page.evaluate(() => { const cv = document.querySelector('.mod[data-mod="SPLC"] .sd-chart canvas'), g = cv.getContext('2d'); const px = g.getImageData(0, 0, cv.width, cv.height).data; let n = 0; for (let i = 3; i < px.length; i += 4) if (px[i]) n++; return n / (px.length / 4); });
      check(ink > 0.02, 'the history chart is drawn (' + (ink * 100).toFixed(1) + ' % of its pixels are painted)');
      await ctx.assertCanvasText(12);
    });

    await section('SPLC: selection and range survive instrument switches; no refetch inside the cache time', async () => {
      await ctx.mouse.click(MOD('SPLC') + ' .splc-node[data-key="exports"]');
      const before = supplyReqs;
      for (const id of ['BTC', 'EURUSD', 'AAPL', 'CL', 'NG']) { await page.evaluate(i => OT.select(i), id); await sleep(60); }
      await ctx.go('MON'); await sleep(300); await ctx.go('SPLC'); await sleep(400);
      const st = await page.evaluate(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'); return { sel: s.querySelector('.splc-node.sel').dataset.key, range: s.querySelector('.sd-range .ot-tab[aria-selected="true"]').dataset.id, title: s.querySelector('.splc-detail .sd-title').textContent, state: !!s.querySelector('.mod-body > .ot-state') }; });
      check(st.sel === 'exports' && st.range === '104' && /export/i.test(st.title) && !st.state, 'the selected node and the range survive five instrument switches and a module switch: ' + JSON.stringify(st));
      check(supplyReqs === before, 'no /api/supply/oil request inside the cache time (was ' + before + ', now ' + supplyReqs + ')');
      await ctx.select('CL');
      await ctx.assertNoBadText(MOD('SPLC')); await ctx.assertMinFontPx(12, MOD('SPLC')); await ctx.assertContrast(4.5, MOD('SPLC'));
      await ctx.assertNoOverflow('SPLC');
      await ctx.shot('macro_splc');
    });

    await section('SPLC: stale payload, partner failure, upstream error and recovery', async () => {
      const p = await ctx.open({ tag: 'splc-fail' });
      let mode = 'stale';
      await alter(p, '**/api/supply/oil', body => {
        if (mode === 'stale') { Object.assign(body, { stale: true, as_of: body.as_of - 1800, error: { code: 'upstream_error', message: 'HTTP 503 Service Unavailable', source: 'eia' } }); body.partners = { source: 'un-comtrade', error: { code: 'upstream_error', message: 'HTTP 500 injected by the test', source: 'comtrade' } }; return body; }
        if (mode === 'down') return { __status: 502, body: { error: { code: 'upstream_error', message: 'injected by the test: EIA HTTP 503', source: 'eia', retry_after: 40 } }, __headers: { 'Retry-After': '40' } };
        return body;
      });
      p.allow(/502|supply\/oil/);
      await p.goto('SPLC/CL');
      await p.waitFor(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'); return s && /\d/.test((s.querySelector('.splc-node[data-key="stocks"] .sn-val') || {}).textContent || ''); }, { timeout: 45000, msg: 'SPLC rendered the altered answer' });
      const r = await p.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'), b = s.querySelector('.mod-head .badge'); return { fresh: b.dataset.fresh, tip: b.getAttribute('data-tip'), asof: s.querySelector('.mod-asof').textContent,
        pstate: (s.querySelector('.splc-partners .ot-state .st-title') || {}).textContent, pdetail: (s.querySelector('.splc-partners .ot-state .st-detail') || {}).textContent, prow: s.querySelectorAll('.splc-partners tbody tr').length, nodes: s.querySelectorAll('.splc-node').length }; });
      check(r.fresh === 'STALE' && /HTTP 503/.test(r.tip), 'a stale answer is shown as STALE with the gateway’s reason: ' + r.fresh + ' / ' + r.tip);
      check(/[34]\dm ago/.test(r.asof), 'the age of a stale answer is the age of the old data (30 min older than the gateway’s copy, itself up to 15 min old): ' + r.asof);
      check(/not available/.test(r.pstate || '') && /HTTP 500 injected/.test(r.pdetail || '') && r.prow === 0 && r.nodes === 18, 'a Comtrade failure shows an error in the partner panel only: ' + JSON.stringify(r));
      await p.shotNow('macro_splc_stale_1920x1080');
      await p.close();
      /* nothing to show at all: the error state, then recovery */
      const q = await ctx.open({ tag: 'splc-down' });
      mode = 'down';
      await alter(q, '**/api/supply/oil', body => (mode === 'down' ? { __status: 502, body: { error: { code: 'upstream_error', message: 'injected by the test: EIA HTTP 503', source: 'eia', retry_after: 40 } }, __headers: { 'Retry-After': '40' } } : body));
      q.allow(/502|supply\/oil/);
      await q.goto('SPLC/CL');
      await q.waitFor(() => document.querySelector('.mod[data-mod="SPLC"] .mod-body > .ot-state.k-error'), { timeout: 20000, msg: 'SPLC shows an error state when EIA fails and nothing is cached' });
      const e = await q.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="SPLC"] .ot-state'); return { text: s.textContent, retry: (s.querySelector('.st-retry') || {}).textContent }; });
      check(/EIA: injected by the test: EIA HTTP 503/.test(e.text) && /Retrying in \d+s/.test(e.retry || ''), 'the error state names the source, the reason and a retry countdown: ' + JSON.stringify(e));
      await q.shotNow('macro_splc_error_1920x1080');
      mode = 'ok';
      await q.page.locator(MOD('SPLC') + ' .ot-state .btn').click();
      await splcReady(q, { msg: 'SPLC recovers after "Retry now" once the source answers again' });
      await q.close();
    });

    /* ════════════════════════════ NEWS ════════════════════════════ */
    const newsRows = pg => pg.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="NEWS"] .nw-row')).filter(el => !el.hidden).map(el => { const a = el.querySelector('.nw-title');
      return { id: el.dataset.id, time: el.querySelector('.nw-time').textContent, age: el.querySelector('.nw-age').textContent, src: el.querySelector('.nw-src').textContent, title: a.textContent, tag: a.tagName,
        href: a.getAttribute('href'), target: a.getAttribute('target'), rel: a.getAttribute('rel'), syms: Array.from(el.querySelectorAll('.nw-sym:not(.more)')).map(s => s.textContent), sel: el.classList.contains('sel'), isNew: el.classList.contains('new') }; }));
    const newsReady = (c, extra) => c.waitFor(() => { const s = document.querySelector('.mod[data-mod="NEWS"]'); return s && !s.querySelector('.mod-body > .ot-state') && s.querySelectorAll('.nw-row').length > 10; }, Object.assign({ timeout: 45000, msg: 'NEWS shows headlines' }, extra || {}));
    const whenText = (it, now) => (it.date_only ? ymd(it.t).slice(5) : (ymd(now) === ymd(it.t) ? '' : ymd(it.t).slice(5) + ' ') + hm(it.t));

    await section('NEWS: rows are the headlines received, newest first, as links that open in a new tab', async () => {
      await ctx.go('NEWS');
      await newsReady(ctx);
      const d = newsGot[newsGot.length - 1];
      check(!!d && d.items.length > 20 && d.providers.length >= 4, 'the page received /api/news: ' + (d ? d.items.length + ' items, ' + d.providers.length + ' providers' : 'nothing'));
      const rows = await newsRows(page), now = Date.now() / 1000;
      check(rows.length === d.items.length, 'NEWS shows ' + rows.length + ' rows, received ' + d.items.length + ' items');
      let bad = 0;
      d.items.slice(0, 60).forEach((it, i) => {
        const r = rows[i] || {};
        const ok = r.id === it.id && r.title === it.title && r.src === it.source && r.time === whenText(it, now) && r.syms.join(',') === it.symbols.slice(0, 4).join(',') &&
          (it.url ? (r.tag === 'A' && r.href === it.url && r.target === '_blank' && /noopener/.test(r.rel || '')) : r.tag === 'SPAN');
        if (!ok && bad++ < 3) ctx.fail('NEWS row ' + i + ' shows ' + JSON.stringify(r) + ', received ' + JSON.stringify({ id: it.id, t: it.t, source: it.source, title: it.title, url: it.url, symbols: it.symbols }));
      });
      check(bad === 0, bad + ' of the first 60 rows differ from what the page received');
      const ts = d.items.map(i => i.t);
      check(ts.every((t, i) => !i || ts[i - 1] >= t), 'items are newest first');
      check(rows.every(r => r.age === 'filed' || /^\d+[smhd]$/.test(r.age)), 'every row shows an age: ' + rows.slice(0, 5).map(r => r.age).join(' '));
      const links = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="NEWS"] a')).map(a => [a.getAttribute('href'), a.target, a.rel]));
      check(links.length > 10 && links.every(l => /^https?:\/\//.test(l[0]) && l[1] === '_blank' && /noopener/.test(l[2]) && /noreferrer/.test(l[2])), 'every link is http(s), target=_blank, rel="noopener noreferrer"');
      /* status line: one chip per provider, with what the gateway said */
      const chips = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="NEWS"] .nw-prov:not(.rss-sum)')).map(c => ({ label: c.querySelector('span').textContent, n: c.querySelector('b').textContent, cls: c.className })));
      check(chips.length === d.providers.length, 'status line has ' + chips.length + ' chips for ' + d.providers.length + ' providers');
      d.providers.forEach(pv => {
        const c = chips.find(x => x.label === pv.label);
        const want = pv.state === 'ok' || pv.state === 'stale' ? String(pv.n) : pv.state === 'error' ? 'DOWN' : pv.state === 'off' ? 'OFF' : '…';
        check(!!c && c.n === want && c.cls.indexOf('s-' + pv.state) >= 0, 'provider chip ' + pv.label + ' shows ' + (c ? c.n + ' ' + c.cls : 'nothing') + ', gateway says ' + pv.state + ' n=' + pv.n);
      });
      console.log('      ' + d.items.length + ' headlines · providers: ' + d.providers.map(x => x.name + '=' + x.state + (x.ok ? ':' + x.n : '')).join(' '));
      await ctx.assertHeaderBadge('NEWS');
      const again = (await getJson(ctx.base + '/api/news?limit=500')).body;
      const ids = new Set(again.items.map(i => i.id));
      check(d.items.slice(0, 40).filter(i => ids.has(i.id)).length >= 30, 'a second fetch of /api/news returns the same headlines');
    });

    await section('NEWS: topic, instrument and text filters; keyboard; reading pane; opening the original', async () => {
      const d = newsGot[newsGot.length - 1];
      await ctx.mouse.click(MOD('NEWS') + ' .nw-topics .ot-tab[data-id="energy"]');
      let rows = await newsRows(page);
      const energy = d.items.filter(i => i.topics.indexOf('energy') >= 0);
      check(rows.length === energy.length && rows.every((r, i) => r.id === energy[i].id), 'topic Energy shows the ' + energy.length + ' energy headlines (' + rows.length + ' shown)');
      const count = await page.evaluate(() => document.querySelector('.mod[data-mod="NEWS"] .nw-count').textContent);
      check(count === energy.length + ' of ' + d.items.length + ' headlines', 'the count says what is shown: ' + count);
      await ctx.mouse.click(MOD('NEWS') + ' .nw-topics .ot-tab[data-id=""]');
      /* selected instrument */
      const pick = ['NVDA', 'CL', 'BTC', 'SPX'].find(id => d.items.filter(i => i.ids.indexOf(id) >= 0).length >= 2) || 'CL';
      await ctx.select(pick);
      await ctx.mouse.click(MOD('NEWS') + ' .nw-bar .btn');
      rows = await newsRows(page);
      const mine = d.items.filter(i => i.ids.indexOf(pick) >= 0);
      check(rows.length === mine.length && rows.every((r, i) => r.id === mine[i].id), '"Only ' + pick + '" shows the ' + mine.length + ' headlines about it (' + rows.length + ' shown)');
      const other = ['CL', 'BTC', 'NVDA', 'EURUSD'].find(id => id !== pick);
      await ctx.select(other);
      await sleep(150);
      rows = await newsRows(page);
      const theirs = d.items.filter(i => i.ids.indexOf(other) >= 0);
      const btn = await page.evaluate(() => { const b = document.querySelector('.mod[data-mod="NEWS"] .nw-bar .btn'); return b.textContent + '|' + b.getAttribute('aria-pressed'); });
      check(btn === 'Only ' + other + '|true', 'the instrument filter follows the selection and stays on: ' + btn);
      const empty = await page.evaluate(() => (document.querySelector('.mod[data-mod="NEWS"] .nw-list .ot-state .st-title') || {}).textContent || null);
      check(theirs.length ? rows.length === theirs.length : (rows.length === 0 && /No headline matches/.test(empty || '')), 'switching to ' + other + ' shows its ' + theirs.length + ' headlines (' + rows.length + ' shown' + (empty ? ', state: ' + empty : '') + ')');
      await ctx.mouse.click(MOD('NEWS') + ' .nw-bar .btn');
      /* text filter */
      const word = (d.items.find(i => /\b[A-Za-z]{6,}\b/.test(i.title)).title.match(/\b[A-Za-z]{6,}\b/) || ['market'])[0];
      await page.locator(MOD('NEWS') + ' .nw-search').fill(word);
      rows = await newsRows(page);
      const lw = word.toLowerCase(), hits = d.items.filter(i => i.title.toLowerCase().indexOf(lw) >= 0 || i.source.toLowerCase().indexOf(lw) >= 0 || i.symbols.some(s => s.toLowerCase() === lw));
      check(rows.length === hits.length && rows.length > 0, 'text filter "' + word + '" shows ' + rows.length + ' rows, ' + hits.length + ' items contain it');
      await page.locator(MOD('NEWS') + ' .nw-search').fill('');
      /* keyboard */
      await page.locator(MOD('NEWS') + ' .nw-list').focus();
      await page.keyboard.press('Home');
      for (const k of ['ArrowDown', 'ArrowDown', 'j', 'k', 'ArrowDown']) await page.keyboard.press(k);
      rows = await newsRows(page);
      const selIdx = rows.findIndex(r => r.sel);
      check(selIdx === 3, 'Home, then Down Down J K Down selects the fourth row (selected index ' + selIdx + ')');
      const it = d.items[3];
      const pv = await page.evaluate(() => { const s = document.querySelector('.mod[data-mod="NEWS"] .nw-preview'); return { title: s.querySelector('.pv-title').textContent, meta: s.querySelector('.pv-meta').textContent, when: s.querySelector('.pv-when').textContent, summary: s.querySelector('.pv-summary').textContent,
        link: s.querySelector('.pv-link').hidden ? null : s.querySelector('.pv-link').getAttribute('href'), rel: s.querySelectorAll('.pv-rel-row').length, mod: OT.state.mod, sel: OT.state.sel }; });
      check(pv.title === it.title && pv.meta.indexOf(it.source) === 0 && pv.link === it.url && (it.summary ? pv.summary === it.summary : /no summary/.test(pv.summary)), 'the reading pane shows the selected headline: ' + JSON.stringify(pv).slice(0, 300));
      check(it.date_only || pv.when.indexOf(ymd(it.t) + ' ' + hm(it.t).slice(0, 5)) === 0, 'the reading pane gives the UTC time: ' + pv.when);
      check(pv.mod === 'NEWS' && pv.sel === other, 'arrow keys inside the list do not move the instrument selection (' + pv.sel + ')');
      await page.keyboard.press('End');
      rows = await newsRows(page);
      check(rows[rows.length - 1].sel, 'End selects the last row');
      const vis = await page.evaluate(() => { const l = document.querySelector('.mod[data-mod="NEWS"] .nw-list'), r = l.querySelector('.nw-row.sel').getBoundingClientRect(), b = l.getBoundingClientRect(); return r.top >= b.top && r.bottom <= b.bottom + 1; });
      check(vis, 'the selected row is scrolled into view');
      await page.keyboard.press('Home');
      /* Enter opens the original in a new tab */
      const withUrl = d.items.findIndex(i => i.url);
      for (let i = 0; i < withUrl; i++) await page.keyboard.press('ArrowDown');
      /* the publisher's site is not loaded by the test: the request is recorded and aborted */
      const asked = [], external = url => url.hostname !== '127.0.0.1' && url.hostname !== 'localhost';
      await page.context().route(external, route => { asked.push(route.request().url()); route.abort(); });
      const popupP = page.context().waitForEvent('page', { timeout: 8000 }).catch(() => null);
      await page.keyboard.press('Enter');
      const popup = await popupP;
      if (check(!!popup, 'Enter opens the original in a new tab')) {
        for (let i = 0; i < 40 && !asked.length; i++) await sleep(100);
        check(asked[0] === d.items[withUrl].url, 'the new tab asks for the headline’s own URL: ' + asked[0] + ' (item: ' + d.items[withUrl].url + ')');
        const opener = await popup.evaluate(() => window.opener === null).catch(() => true);
        check(opener, 'the new tab has no window.opener');
        await popup.close();
      }
      await page.context().unroute(external);
      /* a ticker chip of a terminal instrument selects it */
      const chip = await page.locator(MOD('NEWS') + ' .nw-row:not([hidden]) button.nw-sym.link').first();
      if (await chip.count()) {
        const sym = await chip.textContent();
        await chip.click();
        const now = await page.evaluate(() => OT.state.sel);
        check(now === sym, 'clicking the ticker chip ' + sym + ' selects that instrument (' + now + ')');
      }
      await ctx.select('CL');
      await page.locator(MOD('NEWS') + ' .nw-list').focus(); await page.keyboard.press('Home');
      await ctx.assertNoBadText(MOD('NEWS')); await ctx.assertMinFontPx(12, MOD('NEWS')); await ctx.assertContrast(4.5, MOD('NEWS'));
      await ctx.assertNoOverflow('NEWS');
      await ctx.shot('macro_news');
    });

    await section('NEWS: new headlines do not move what is being read; hostile text stays text; a feed that is down is named', async () => {
      const p = await ctx.open({ tag: 'news-new' });
      let phase = 0, full = null;
      await alter(p, '**/api/news*', body => {
        full = full || JSON.parse(JSON.stringify(body));
        const out = JSON.parse(JSON.stringify(full));
        out.ttl = 15;                                             // the page obeys the gateway's cache time: 15 s here
        const now = Date.now() / 1000;
        if (phase === 0) out.items = out.items.slice(6);          // the six newest arrive later
        else {
          out.items.unshift({ id: 'hostile0000000001', t: now - 5, provider: 'rss', source: '<b>evil</b> feed', title: '<img src=x onerror="window.__pwned=1">Oil <script>window.__pwned=2</script> jumps',
            url: 'javascript:window.__pwned=3', symbols: ['CL<i>'], ids: [], topics: ['energy'], summary: '<a href="https://evil.example" onclick="window.__pwned=4">click</a>' });
          const prov = out.providers.find(x => x.kind === 'rss');
          Object.assign(prov, { ok: false, state: 'error', n: 0, error: 'HTTP 503 Service Unavailable (injected by the test)' });
        }
        phase++;
        return out;
      });
      await p.goto('NEWS/CL');
      await newsReady(p);
      const pg = p.page;
      await pg.evaluate(() => { const l = document.querySelector('.mod[data-mod="NEWS"] .nw-list'); l.scrollTop = 620; });
      await sleep(200);
      const before = await pg.evaluate(() => { const l = document.querySelector('.mod[data-mod="NEWS"] .nw-list'), b = l.getBoundingClientRect();
        const row = Array.from(l.querySelectorAll('.nw-row')).find(r => r.getBoundingClientRect().top >= b.top + 40); return { id: row.dataset.id, top: row.getBoundingClientRect().top, scroll: l.scrollTop, n: l.querySelectorAll('.nw-row').length, pill: document.querySelector('.nw-pill').hidden }; });
      check(before.pill === true, 'no "new" pill before anything new arrived');
      await p.waitFor(n => document.querySelectorAll('.mod[data-mod="NEWS"] .nw-row').length > n, { arg: before.n, timeout: 40000, msg: 'the second answer (7 more headlines) arrived after the 15 s cache time' });
      const after = await pg.evaluate(id => { const l = document.querySelector('.mod[data-mod="NEWS"] .nw-list'), row = l.querySelector('.nw-row[data-id="' + id + '"]'), pill = document.querySelector('.nw-pill');
        return { top: row.getBoundingClientRect().top, scroll: l.scrollTop, n: l.querySelectorAll('.nw-row').length, pill: pill.hidden ? null : pill.textContent, fresh: l.querySelectorAll('.nw-row.new').length }; }, before.id);
      check(after.n === before.n + 7, 'seven headlines were added (' + before.n + ' → ' + after.n + ')');
      check(Math.abs(after.top - before.top) <= 1 && after.scroll > before.scroll, 'the row being read did not move (top ' + before.top.toFixed(1) + ' → ' + after.top.toFixed(1) + ', scrollTop ' + before.scroll + ' → ' + after.scroll + ')');
      check(after.pill === '7 new ↑' && after.fresh === 7, 'a pill announces the new headlines: ' + after.pill + ' (' + after.fresh + ' rows marked new)');
      /* the hostile item is plain text everywhere */
      const h = await pg.evaluate(() => { const row = document.querySelector('.mod[data-mod="NEWS"] .nw-row[data-id="hostile0000000001"]'), t = row.querySelector('.nw-title');
        return { tag: t.tagName, text: t.textContent, kids: row.querySelectorAll('img,script,b,i,a').length, src: row.querySelector('.nw-src').textContent, sym: row.querySelector('.nw-sym').textContent, pwned: window.__pwned || null }; });
      check(h.tag === 'SPAN' && h.kids === 0 && h.text.indexOf('<img src=x') === 0 && h.src === '<b>evil</b> feed' && h.sym === 'CL<i>' && h.pwned === null, 'hostile markup in a headline is shown as text, a javascript: link is not a link: ' + JSON.stringify(h));
      await p.shotNow('macro_news_new_pill_1920x1080');
      await p.mouse.click('.nw-pill');
      await p.waitFor(() => document.querySelector('.mod[data-mod="NEWS"] .nw-list').scrollTop < 8 && document.querySelector('.nw-pill').hidden, { timeout: 5000, msg: 'the pill scrolls to the newest and disappears' });
      await pg.locator(MOD('NEWS') + ' .nw-row[data-id="hostile0000000001"]').click();
      const pv = await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="NEWS"] .nw-preview'); return { title: s.querySelector('.pv-title').textContent, sum: s.querySelector('.pv-summary').textContent, kids: s.querySelector('.pv-summary').children.length + s.querySelector('.pv-title').children.length, link: s.querySelector('.pv-link').hidden, nolink: s.querySelector('.note[hidden]') === null, pwned: window.__pwned || null }; });
      check(pv.kids === 0 && pv.link === true && /onclick/.test(pv.sum) && pv.pwned === null, 'the reading pane also treats it as text and offers no link: ' + JSON.stringify(pv));
      /* the feed that went down is named */
      const down = await pg.evaluate(() => { const c = document.querySelector('.mod[data-mod="NEWS"] .nw-prov.s-error'); return { chip: c ? c.textContent : null, note: document.querySelector('.mod[data-mod="NEWS"] .mod-note').textContent }; });
      check(/DOWN$/.test(down.chip || '') && /1 feed down/.test(down.note), 'a feed that is down is named in the status line and in the header: ' + JSON.stringify(down));
      await p.mouse.hover('.nw-prov.s-error');
      await sleep(250);
      const tip = await pg.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.textContent : null; });
      check(/HTTP 503 Service Unavailable \(injected by the test\)/.test(tip || ''), 'its chip says why on hover: ' + tip);
      await p.close();
    });

    await section('NEWS: upstream error and an answer with nothing fresh', async () => {
      const q = await ctx.open({ tag: 'news-fail' });
      let mode = 'down';
      await alter(q, '**/api/news*', body => {
        if (mode === 'down') return { __status: 404, body: { error: { code: 'no_source', message: 'no news provider answered: Alpaca / Benzinga: HTTP 500 (injected by the test)' } } };
        body.providers.forEach(x => { if (x.ok) Object.assign(x, { state: 'stale', error: 'HTTP 503 (injected by the test)' }); });
        return Object.assign(body, { stale: true, as_of: body.as_of - 1200, error: { code: 'upstream_error', message: 'no news provider could be refreshed (injected by the test)' } });
      });
      q.allow(/404|api\/news/);
      await q.goto('NEWS/CL');
      await q.waitFor(() => { const s = document.querySelector('.mod[data-mod="NEWS"] .mod-body > .ot-state'); return s && s.dataset.kind !== 'loading'; }, { timeout: 20000, msg: 'NEWS shows a state when no provider answered' });
      const e = await q.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="NEWS"] .mod-body > .ot-state'); return { kind: s.dataset.kind, text: s.textContent }; });
      check(e.kind === 'nosource' && /no news provider answered/.test(e.text), 'with no provider the page says so instead of showing an empty list: ' + JSON.stringify(e));
      mode = 'stale';
      await q.page.locator(MOD('NEWS') + ' .mod-body > .ot-state .btn').click().catch(() => {});
      await newsReady(q, { timeout: 90000, msg: 'NEWS shows the headlines once the route answers again' });
      const s = await q.page.evaluate(() => { const b = document.querySelector('.mod[data-mod="NEWS"] .mod-head .badge'); return { fresh: b.dataset.fresh, tip: b.getAttribute('data-tip'), chips: document.querySelectorAll('.mod[data-mod="NEWS"] .nw-prov.s-stale').length }; });
      check(s.fresh === 'STALE' && /no news provider could be refreshed/.test(s.tip) && s.chips > 3, 'when nothing could be refreshed the header says STALE with the reason: ' + JSON.stringify(s));
      await q.close();
    });

    /* ════════════════════════════ ECO ════════════════════════════ */
    const IMPACT = { high: 'HIGH', medium: 'MED', low: 'LOW', holiday: 'HOLIDAY', none: '' };
    const ecoRows = pg => pg.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="ECO"] tbody tr')).map(tr => {
      if (tr.classList.contains('ec-day')) return { day: tr.dataset.day, today: tr.classList.contains('today'), hidden: tr.hidden, text: tr.textContent };
      if (tr.classList.contains('ec-now')) return { now: true, hidden: tr.hidden, text: tr.textContent };
      const q = s => tr.querySelector(s).textContent;
      return { id: tr.dataset.id, hidden: tr.hidden, past: tr.classList.contains('past'), utc: q('.ec-utc'), local: q('.ec-local'), ctry: q('.ec-ctry'), imp: q('.ec-tag'), title: q('.ec-title'), actual: q('.ec-val'), src: q('.ec-srcs'), fc: q('.ec-fc'), prev: q('.ec-prev') };
    }));
    const ecoReady = (c, extra) => c.waitFor(() => { const s = document.querySelector('.mod[data-mod="ECO"]'); return s && !s.querySelector('.mod-body > .ot-state') && s.querySelectorAll('tbody tr.ec-row').length > 5; }, Object.assign({ timeout: 45000, msg: 'ECO shows the calendar' }, extra || {}));

    await section('ECO: every row is an event received; grouped by UTC day; past / next separated; actuals carry their source', async () => {
      await ctx.go('ECO');
      await ecoReady(ctx);
      const d = calGot[calGot.length - 1];
      check(!!d && d.source === 'forexfactory' && d.events.length > 10, 'the page received /api/calendar: ' + (d ? d.events.length + ' events' : 'nothing'));
      const all = await ecoRows(page), rows = all.filter(r => r.id), now = Date.now() / 1000;
      check(rows.length === d.events.length, 'ECO shows ' + rows.length + ' rows, received ' + d.events.length + ' events');
      let bad = 0;
      d.events.forEach((ev, i) => {
        const r = rows[i] || {}, numeric = ev.numeric !== false && ev.impact !== 'holiday';
        const ok = r.id === ev.id && r.utc === hm(ev.t) && r.ctry === (ev.country === 'ALL' ? 'GLB' : ev.country) && r.imp === IMPACT[ev.impact] && r.title === ev.title &&
          r.fc === (ev.forecast || (numeric ? '—' : '')) && r.prev === (ev.previous || (numeric ? '—' : '')) && r.actual === (ev.actual || (numeric ? '—' : '')) &&
          (ev.actual ? r.src.indexOf(ev.actual_source.split(' ')[0]) === 0 : r.src === '');
        if (!ok && bad++ < 3) ctx.fail('ECO row ' + i + ' shows ' + JSON.stringify(r) + ', received ' + JSON.stringify(ev));
      });
      check(bad === 0, bad + ' rows differ from what the page received');
      /* never an actual for something that has not happened, never an actual without a source */
      check(d.events.every(ev => ev.t <= now + 60 || ev.actual === null), 'no future event has an actual');
      check(d.events.every(ev => !ev.actual || (ev.actual_source && ev.actual_period)), 'every actual names its source and period');
      const filled = d.events.filter(ev => ev.actual);
      console.log('      ' + d.events.length + ' events · actuals: ' + (filled.map(ev => ev.title + ' ' + ev.actual + ' [' + ev.actual_source + ' ' + ev.actual_period + ']').join(' · ') || 'none yet this week'));
      /* days */
      const days = all.filter(r => r.day), want = Array.from(new Set(d.events.map(ev => ymd(ev.t))));
      check(days.map(x => x.day).join(' ') === want.join(' '), 'one group per UTC day: ' + days.map(x => x.day).join(' '));
      const today = ymd(now);
      check(days.filter(x => x.today).map(x => x.day).join() === (want.indexOf(today) >= 0 ? today : ''), 'today (' + today + ') is the highlighted day: ' + days.filter(x => x.today).map(x => x.day).join());
      if (want.indexOf(today) >= 0) check(/TODAY/.test(days.find(x => x.day === today).text), 'today’s header says TODAY');
      /* the order in the DOM is chronological and each event sits under its own day */
      let cur = null, okOrder = true;
      all.forEach(r => { if (r.day) cur = r.day; else if (r.id) { const ev = d.events.find(e => e.id === r.id); if (ymd(ev.t) !== cur) okOrder = false; } });
      check(okOrder, 'every event sits under the header of its UTC day');
      /* NOW line */
      const ni = all.findIndex(r => r.now), nowRow = all[ni];
      const beforeIds = all.slice(0, ni).filter(r => r.id).map(r => r.id), afterIds = all.slice(ni + 1).filter(r => r.id).map(r => r.id);
      const tOf = id => d.events.find(e => e.id === id).t, t2 = Date.now() / 1000;
      check(ni >= 0 && !nowRow.hidden && beforeIds.every(id => tOf(id) <= t2 + 1) && afterIds.every(id => tOf(id) > now - 2), 'the NOW line separates past (' + beforeIds.length + ') from next (' + afterIds.length + ')');
      check(rows.every(r => r.past === (tOf(r.id) <= t2 + 1) || Math.abs(tOf(r.id) - t2) < 3), 'past rows are marked as past');
      check(/^NOW \d\d:\d\d:\d\dZ/.test(nowRow.text), 'the NOW line shows the UTC time: ' + nowRow.text);
      /* local time column = the browser's zone */
      const loc = await page.evaluate(ts => ts.map(t => { const x = new Date(t * 1000), p = n => (n < 10 ? '0' : '') + n; return p(x.getHours()) + ':' + p(x.getMinutes()) + (x.getDate() !== x.getUTCDate() ? '*' : ''); }), d.events.slice(0, 20).map(e => e.t));
      check(rows.slice(0, 20).every((r, i) => r.local === loc[i]), 'the Local column is the browser’s local time');
      await ctx.assertHeaderBadge('ECO');
      const again = (await getJson(ctx.base + '/api/calendar')).body;
      check(again.events.length === d.events.length && again.events.every((e, i) => e.id === d.events[i].id && e.forecast === d.events[i].forecast && e.previous === d.events[i].previous), 'a second fetch of /api/calendar gives the same events');
    });

    await section('ECO: countdown runs; filters; jump; click-through', async () => {
      const d = calGot[calGot.length - 1];
      const next = () => { const t = Date.now() / 1000; return d.events.find(e => e.t > t && (e.impact === 'high' || e.impact === 'medium')); };
      const read = () => page.evaluate(() => { const s = document.querySelector('.mod[data-mod="ECO"]'); return { clock: s.querySelector('.ec-clock').textContent, title: s.querySelector('.ec-next-title').textContent, when: s.querySelector('.ec-next-when').textContent, now: s.querySelector('.ec-now').textContent }; });
      const toSec = c => { const m = /^(?:(\d+)d )?(\d\d):(\d\d):(\d\d)$/.exec(c); return m ? (+m[1] || 0) * 86400 + +m[2] * 3600 + +m[3] * 60 + +m[4] : NaN; };
      const ev = next();
      const a = await read();
      if (ev) {
        const left = ev.t - Date.now() / 1000;
        check(Math.abs(toSec(a.clock) - left) <= 3, 'the countdown (' + a.clock + ') is the time to the next medium/high event (' + Math.round(left) + ' s: ' + ev.title + ')');
        check(a.title.indexOf(ev.title) === 0 && a.when.indexOf(hm(ev.t)) >= 0, 'the countdown names the event and its time: ' + a.title + ' / ' + a.when);
        await sleep(2100);
        const b = await read();
        check(toSec(b.clock) < toSec(a.clock) && b.now !== a.now, 'the countdown and the NOW line keep counting (' + a.clock + ' → ' + b.clock + ')');
      } else check(/No further/.test(a.title), 'with no further medium/high event the box says so: ' + a.title);
      /* filters with real clicks */
      const us = d.events.filter(e => e.country === 'US');
      await ctx.mouse.click(MOD('ECO') + ' .ec-chip[data-country="US"]');
      let rows = (await ecoRows(page)).filter(r => r.id && !r.hidden);
      check(rows.length === us.length && rows.every(r => r.ctry === 'US'), 'country US shows its ' + us.length + ' events (' + rows.length + ' shown)');
      await ctx.mouse.click(MOD('ECO') + ' .ot-tab[data-id="high"]');
      rows = (await ecoRows(page)).filter(r => r.id && !r.hidden);
      const usHigh = us.filter(e => e.impact === 'high');
      const state = await page.evaluate(() => (document.querySelector('.mod[data-mod="ECO"] .ec-tablebox .ot-state .st-title') || {}).textContent || null);
      check(rows.length === usHigh.length && rows.every(r => r.imp === 'HIGH') && (usHigh.length > 0 || /No event matches/.test(state || '')), 'US + High only shows ' + usHigh.length + ' events (' + rows.length + ' shown' + (state ? ', state: ' + state : '') + ')');
      const cnt = await page.evaluate(() => document.querySelector('.mod[data-mod="ECO"] .ec-count').textContent);
      check(cnt === usHigh.length + ' of ' + d.events.length + ' events', 'the count says what is shown: ' + cnt);
      const dayHidden = (await ecoRows(page)).filter(r => r.day && !r.hidden).length;
      check(dayHidden === new Set(usHigh.map(e => ymd(e.t))).size, 'days without a matching event are hidden (' + dayHidden + ' day headers shown)');
      /* filters survive an instrument switch and a module switch */
      await page.evaluate(() => OT.select('BTC')); await ctx.go('NEWS'); await ctx.go('ECO'); await ctx.select('CL');
      rows = (await ecoRows(page)).filter(r => r.id && !r.hidden);
      check(rows.length === usHigh.length, 'the filters survive an instrument switch and a module switch (' + rows.length + ' rows)');
      await ctx.mouse.click(MOD('ECO') + ' .ot-tab[data-id="all"]');
      await ctx.mouse.click(MOD('ECO') + ' .ec-chip[data-country=""]');
      rows = (await ecoRows(page)).filter(r => r.id && !r.hidden);
      check(rows.length === d.events.length, 'All restores every event (' + rows.length + ')');
      /* jump to now / click the countdown box */
      await page.evaluate(() => { document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').scrollTop = 0; });
      await ctx.mouse.click(MOD('ECO') + ' .ec-bar .btn:not(.ec-chip)');
      await ctx.waitFor(() => { const b = document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').getBoundingClientRect(), r = document.querySelector('.mod[data-mod="ECO"] .ec-now').getBoundingClientRect(); return r.top > b.top + 20 && r.bottom < b.bottom; }, { timeout: 5000, msg: '"Jump to now" brings the NOW line into view' });
      if (ev) {
        await page.evaluate(() => { document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').scrollTop = 0; });
        await ctx.mouse.click(MOD('ECO') + ' .ec-next');
        await ctx.waitFor(id => { const b = document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').getBoundingClientRect(), tr = document.querySelector('.mod[data-mod="ECO"] tr[data-id="' + id + '"]'); if (!tr) return false; const r = tr.getBoundingClientRect(); return r.top > b.top && r.bottom < b.bottom + 1; },
          { arg: next().id, timeout: 5000, msg: 'clicking the countdown box shows that event in the table' });
      }
      /* wheel scroll works and does not jump back on the next second */
      await sleep(900);                                           // let the smooth scroll of the click settle
      const s0 = await page.evaluate(() => { const b = document.querySelector('.mod[data-mod="ECO"] .ec-tablebox'); b.scrollTop = 0; return b.scrollTop; });
      await ctx.mouse.wheel(MOD('ECO') + ' .ec-tablebox', 240, 3);
      await sleep(700);
      const s1 = await page.evaluate(() => document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').scrollTop);
      await sleep(1300);
      const s2 = await page.evaluate(() => document.querySelector('.mod[data-mod="ECO"] .ec-tablebox').scrollTop);
      check(s1 > s0 + 300 && Math.abs(s2 - s1) < 2, 'the wheel scrolls the table and the position holds across ticks (' + s0 + ' → ' + s1 + ' → ' + s2 + ')');
      await ctx.mouse.click(MOD('ECO') + ' .ec-bar .btn:not(.ec-chip)');
      await sleep(500);
      await ctx.assertNoBadText(MOD('ECO')); await ctx.assertMinFontPx(12, MOD('ECO')); await ctx.assertContrast(4.5, MOD('ECO'));
      await ctx.assertNoOverflow('ECO');
      await ctx.shot('macro_eco');
    });

    await section('ECO: an event about to happen, an actual with its source, stale and rate-limited answers', async () => {
      const p = await ctx.open({ tag: 'eco-alter' });
      let mode = 'inject';
      await alter(p, '**/api/calendar', body => {
        if (mode === 'limited') return { __status: 429, __headers: { 'Retry-After': '1800' }, body: { error: { code: 'rate_limited', message: 'the calendar host rate-limits (HTTP 429); it allows only a few requests a day per address', source: 'forexfactory', retry_after: 1800 } } };
        const now = Date.now() / 1000;
        body.events = body.events.filter(e => !(e.t > now - 130 && e.t < now + 400));
        body.events.push({ id: 'test-soon', t: Math.floor(now) + 95, currency: 'USD', country: 'US', title: 'Injected Test Release', impact: 'high', forecast: '1.0%', previous: '0.9%', actual: null, actual_source: null, actual_period: null, actual_derived: false, actual_note: 'not released yet', numeric: true });
        body.events.push({ id: 'test-past', t: Math.floor(now) - 120, currency: 'USD', country: 'US', title: 'Injected Past Release', impact: 'medium', forecast: '0.3%', previous: '0.6%', actual: '0.4%', actual_source: 'BLS TESTSERIES', actual_period: '2026-10', actual_derived: false, actual_note: 'the publisher’s own figure', numeric: true });
        body.events.push({ id: 'test-derived', t: Math.floor(now) - 60, currency: 'USD', country: 'US', title: 'Injected Derived Release', impact: 'low', forecast: '-0.6M', previous: '-0.9M', actual: '-3.5M', actual_source: 'EIA PET.WCESTUS1.W', actual_period: '2026-11-06', actual_derived: true, actual_note: 'computed from two observations of the official series', numeric: true });
        body.events.sort((a, b) => a.t - b.t);
        if (mode === 'stale') Object.assign(body, { stale: true, as_of: body.as_of - 7200, error: { code: 'rate_limited', message: 'the calendar host rate-limits (HTTP 429)', source: 'forexfactory' } });
        return body;
      });
      p.allow(/429|api\/calendar/);
      await p.goto('ECO/CL');
      await ecoReady(p);
      const pg = p.page;
      const r = await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="ECO"]'), q = (id, c) => s.querySelector('tr[data-id="' + id + '"] ' + c).textContent, now = s.querySelector('.ec-now');
        return { clock: s.querySelector('.ec-clock').textContent, title: s.querySelector('.ec-next-title').textContent, nextAfterNow: now.nextElementSibling && now.nextElementSibling.dataset.id, prevBeforeNow: now.previousElementSibling && now.previousElementSibling.dataset.id,
          nowText: now.textContent, pastVal: q('test-past', '.ec-val'), pastSrc: q('test-past', '.ec-srcs'), derSrc: q('test-derived', '.ec-srcs'), derVal: q('test-derived', '.ec-val'), soonVal: q('test-soon', '.ec-val'), soonPast: s.querySelector('tr[data-id="test-soon"]').classList.contains('past') }; });
      const m = /^00:0([01]):(\d\d)$/.exec(r.clock);
      check(!!m && +m[1] * 60 + +m[2] <= 95 && +m[1] * 60 + +m[2] >= 80 && r.title.indexOf('Injected Test Release') === 0, 'the countdown targets the event 95 s away: ' + r.clock + ' ' + r.title);
      check(r.nextAfterNow === 'test-soon' && r.prevBeforeNow === 'test-derived' && /next: Injected Test Release \(US\) in (\d+s|2 min)/.test(r.nowText), 'the NOW line sits between the last past event and the next one: ' + JSON.stringify([r.prevBeforeNow, r.nextAfterNow, r.nowText]));
      check(r.pastVal === '0.4%' && /^BLS · \+0\.1% vs fcst$/.test(r.pastSrc), 'an actual shows its source and the difference to the forecast: ' + r.pastVal + ' ' + r.pastSrc);
      check(r.derVal === '-3.5M' && /^EIA · DERIVED · -2\.9M vs fcst$/.test(r.derSrc), 'a computed actual is marked DERIVED: ' + r.derVal + ' ' + r.derSrc);
      check(r.soonVal === '—' && r.soonPast === false, 'an event that has not happened shows "—": ' + r.soonVal);
      await p.mouse.hover('tr[data-id="test-past"] .ec-actual');
      await sleep(250);
      const tip = await pg.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.textContent : null; });
      check(/BLS TESTSERIES, period 2026-10/.test(tip || '') && /publisher/.test(tip || ''), 'hovering the actual names the series and the period: ' + tip);
      await p.shotNow('macro_eco_next_event_1920x1080');
      /* the event passes: the NOW line moves below it and the row turns "past" — in place, without a reload */
      const node = await pg.evaluate(() => { const tr = document.querySelector('.mod[data-mod="ECO"] tr[data-id="test-soon"]'); tr.__same = true; return true; });
      await p.waitFor(() => { const s = document.querySelector('.mod[data-mod="ECO"]'), tr = s.querySelector('tr[data-id="test-soon"]'); return tr.classList.contains('past') && s.querySelector('.ec-now').previousElementSibling === tr && tr.__same === true; },
        { timeout: 120000, msg: 'when the event time passes, its row becomes past and the NOW line moves below it (same DOM node)' });
      const after = await pg.evaluate(() => document.querySelector('.mod[data-mod="ECO"] .ec-next-title').textContent);
      check(after.indexOf('Injected Test Release') < 0, 'the countdown moved on to the following event: ' + after);
      await p.close();
      /* stale answer */
      const q = await ctx.open({ tag: 'eco-stale' });
      mode = 'stale';
      await alter(q, '**/api/calendar', body => Object.assign(body, { stale: true, as_of: body.as_of - 7200, error: { code: 'rate_limited', message: 'the calendar host rate-limits (HTTP 429)', source: 'forexfactory' } }));
      await q.goto('ECO/CL');
      await ecoReady(q);
      const st = await q.page.evaluate(() => { const b = document.querySelector('.mod[data-mod="ECO"] .mod-head .badge'); return { fresh: b.dataset.fresh, tip: b.getAttribute('data-tip'), asof: document.querySelector('.mod[data-mod="ECO"] .mod-asof').textContent }; });
      check(st.fresh === 'STALE' && /rate-limits/.test(st.tip) && /\dh ago/.test(st.asof), 'a calendar served from the last good copy is STALE with the reason and its real age: ' + JSON.stringify(st));
      await q.close();
      /* nothing to show: the rate limit is explained */
      const z = await ctx.open({ tag: 'eco-429' });
      await z.page.route('**/api/calendar', route => route.fulfill({ status: 429, contentType: 'application/json', headers: { 'Retry-After': '1800' },
        body: JSON.stringify({ error: { code: 'rate_limited', message: 'the calendar host rate-limits (HTTP 429); it allows only a few requests a day per address', source: 'forexfactory', retry_after: 1800 } }) }));
      z.allow(/429|api\/calendar/);
      await z.goto('ECO/CL');
      await z.waitFor(() => document.querySelector('.mod[data-mod="ECO"] .mod-body > .ot-state.k-error'), { timeout: 20000, msg: 'ECO shows an error state when the calendar host refuses' });
      const e = await z.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="ECO"] .mod-body > .ot-state'); return s.textContent; });
      check(/FOREXFACTORY: the calendar host rate-limits/.test(e) && /Retrying in \d+s/.test(e) && /waits before asking again/.test(e), 'the rate limit is explained, with a retry countdown: ' + e);
      let calls = 0;
      z.page.on('request', rq => { if (/api\/calendar/.test(rq.url())) calls++; });
      await sleep(4000);
      check(calls === 0, 'the page does not hammer a rate-limited route (' + calls + ' requests in 4 s)');
      await z.shotNow('macro_eco_rate_limited_1920x1080');
      await z.close();
    });

    /* ════════════════════════════ all three: gateway stops and comes back; narrow screen ════════════════════════════ */
    await section('gateway stopped and restarted: the three pages keep their data, say offline, and recover', async () => {
      const target = new URL(ctx.base);
      const proxy = ctx.tcpProxy(+target.port, target.hostname);
      await proxy.start();
      const p = await ctx.open({ tag: 'offline', base: proxy.base });
      p.allow(/ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_SOCKET|WebSocket connection|Failed to load resource|net::|\/api\/|\/ws/);
      await alter(p, '**/api/calendar', body => Object.assign(body, { ttl: 15 }));
      await p.goto('SPLC/CL'); await splcReady(p);
      await p.go('NEWS'); await newsReady(p);
      await p.go('ECO'); await ecoReady(p);
      const pg = p.page;
      const before = await pg.evaluate(() => document.querySelectorAll('.mod[data-mod="ECO"] tr.ec-row').length);
      await proxy.stop();
      await p.waitFor(() => OT.state.gateway === 'offline', { timeout: 25000, msg: 'the page notices the gateway is gone' });
      await sleep(16000);                                   // a poll comes due while the gateway is away
      const off = await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="ECO"]'); return { rows: s.querySelectorAll('tr.ec-row').length, state: !!s.querySelector('.mod-body > .ot-state'), banner: !document.getElementById('offline').hidden, note: s.querySelector('.mod-note').textContent,
        clock: s.querySelector('.ec-clock').textContent }; });
      check(off.rows === before && !off.state && off.banner, 'ECO keeps the calendar on screen while the gateway is offline (banner shown): ' + JSON.stringify(off));
      check(/refresh failed/.test(off.note), 'the header says the refresh failed and what is shown: ' + off.note);
      await p.go('SPLC');
      const sp = await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="SPLC"]'); return { v: s.querySelector('.splc-node[data-key="stocks"] .sn-val').textContent, color: getComputedStyle(s.querySelector('.splc-node[data-key="stocks"] .sn-val')).color, t3: getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim() }; });
      check(/\d/.test(sp.v), 'SPLC keeps its figures while offline: ' + sp.v);
      await p.shotNow('macro_offline_1920x1080');
      await proxy.start();
      await p.waitFor(() => OT.state.gateway === 'online', { timeout: 30000, msg: 'the page is online again after the gateway came back' });
      await p.go('ECO');
      await p.waitFor(() => !/refresh failed/.test(document.querySelector('.mod[data-mod="ECO"] .mod-note').textContent), { timeout: 90000, msg: 'ECO refreshes by itself after the gateway is back' });
      await p.close();
      await proxy.stop();
    });

    await section('1280x720: nothing is cut off, panels scroll inside', async () => {
      const p = await ctx.open({ tag: '1280', viewport: { width: 1280, height: 720 } });
      await p.goto('SPLC/CL'); await splcReady(p);
      for (const m of ['SPLC', 'NEWS', 'ECO']) {
        await p.go(m);
        await (m === 'SPLC' ? splcReady(p) : m === 'NEWS' ? newsReady(p) : ecoReady(p));
        await p.assertNoOverflow(m + ' at 1280x720');
        const r = await p.page.evaluate(mm => { const b = document.querySelector('.mod[data-mod="' + mm + '"] .mod-body'), out = [];
          if (b.scrollWidth > b.clientWidth + 1) out.push('module body scrolls sideways (' + b.scrollWidth + ' > ' + b.clientWidth + ')');
          b.querySelectorAll('.splc-node, .nw-prov, .ec-next, .ec-chip, .ot-tab, .btn').forEach(el => { if (el.offsetParent && el.scrollWidth > el.clientWidth + 2) out.push((el.className + ' "' + el.textContent.slice(0, 30) + '" is cut off').trim()); });
          return out; }, m);
        check(!r.length, m + ' at 1280x720: ' + r.slice(0, 4).join('; '));
        await p.assertMinFontPx(12, MOD(m));
        await p.shotNow('macro_' + m.toLowerCase() + '_1280x720');
      }
      await p.close();
    });

    await section('1536x864 (1920x1080 at 125 % scaling): SPLC stacked and legible, a click reveals the history; NEWS folds tickers; HELP lists the modules', async () => {
      const p = await ctx.open({ tag: '1536', viewport: { width: 1536, height: 864 } });
      await p.goto('SPLC/CL'); await splcReady(p);
      await p.assertNoOverflow('SPLC at 1536x864');
      const r = await p.page.evaluate(() => { const b = document.querySelector('.mod[data-mod="SPLC"] .mod-body'), out = [];
        b.querySelectorAll('.splc-node').forEach(el => {
          if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) out.push('node "' + el.textContent.slice(0, 24) + '" overflows its card');
          const l = el.querySelector('.sn-label'); if (l && l.scrollWidth > l.clientWidth + 1) out.push('label "' + l.textContent + '" is cut off'); });
        const f = b.querySelector('.splc-flow').getBoundingClientRect(), d = b.querySelector('.splc-detail').getBoundingClientRect();
        if (d.top < f.bottom - 1) out.push('detail is beside the flow at this width (' + Math.round(f.width) + ' px for four columns)');
        const t = b.querySelector('.splc-partners .ot-table'), tb = t && t.parentElement; if (tb && tb.scrollWidth > tb.clientWidth + 1) out.push('partner table is cut off sideways');
        return out; });
      check(!r.length, 'SPLC at 1536x864: ' + r.slice(0, 4).join('; '));
      const vis = () => { const d = document.querySelector('.mod[data-mod="SPLC"] .splc-detail').getBoundingClientRect(); return d.top >= 0 && d.bottom <= window.innerHeight + 1; };
      check(!(await p.page.evaluate(vis)), 'SPLC at 1536x864: the detail starts below the fold (precondition of the reveal test)');
      await p.page.locator(MOD('SPLC') + ' .splc-node[data-key="cushing"]').click();
      await p.waitFor(vis, { timeout: 4000, msg: 'SPLC at 1536x864: a mouse click on a node scrolls its history chart into view' });
      check(await p.page.evaluate(() => /Cushing/.test(document.querySelector('.mod[data-mod="SPLC"] .sd-title').textContent)), 'SPLC at 1536x864: the detail shows the clicked node');
      await p.shotNow('macro_splc_1536x864_detail');
      await p.go('NEWS'); await newsReady(p);
      const fold = await p.page.evaluate(() => { const bad = []; let folded = 0;
        document.querySelectorAll('.mod[data-mod="NEWS"] .nw-row').forEach(row => {
          const chips = Array.from(row.querySelectorAll('.nw-sym:not(.more)')), shown = chips.filter(c => getComputedStyle(c).display !== 'none'), more = row.querySelector('.nw-sym.more');
          const total = (more && more.getAttribute('data-tip') ? more.getAttribute('data-tip').split(' ').length : chips.length);
          const said = more && getComputedStyle(more).display !== 'none' ? parseInt(more.innerText.replace('+', ''), 10) : 0;
          if (shown.length + said !== total) bad.push(row.querySelector('.nw-title').textContent.slice(0, 30) + ': ' + shown.length + ' shown + ' + said + ' ≠ ' + total);
          if (chips.some(c => c.classList.contains('link') && getComputedStyle(c).display === 'none')) bad.push('an instrument ticker is hidden');
          if (shown.length < chips.length) folded++; });
        return { bad, folded }; });
      check(!fold.bad.length, 'NEWS at 1536x864: shown tickers + "+n" = all tickers of the headline: ' + fold.bad.slice(0, 3).join('; '));
      await p.assertNoOverflow('NEWS at 1536x864');
      await p.shotNow('macro_news_1536x864');
      await p.go('ECO'); await ecoReady(p);
      await p.assertNoOverflow('ECO at 1536x864');
      await p.shotNow('macro_eco_1536x864');
      await p.go('HELP');
      await p.waitFor(() => /UN Comtrade, annual/.test(document.querySelector('.mod[data-mod="HELP"]').textContent), { timeout: 8000, msg: 'HELP shows the SPLC description' });
      const help = await p.page.evaluate(() => { const t = document.querySelector('.mod[data-mod="HELP"]').textContent;
        return [/UN Comtrade, annual/, /keyless RSS feeds/, /ForexFactory weekly export/, /not declared/].map(re => re.test(t)); });
      check(help[0] && help[1] && help[2], 'HELP shows what SPLC, NEWS and ECO declared (help.shows): ' + help.slice(0, 3).join(' '));
      await p.close();
    });

    await section('an answer that arrives after the user left the module is shown on return (no stuck loading, no exception)', async () => {
      // the shell spec found this: a fast tab switch left ECO with data but no rows ("Cannot read properties of undefined")
      const p = await ctx.open({ tag: 'late' });
      for (const glob of ['**/api/supply/oil*', '**/api/news*', '**/api/calendar*'])
        await p.page.route(glob, async route => { await new Promise(r => setTimeout(r, 2500)); try { await route.continue(); } catch (e) { /* page closed */ } });
      await p.goto('MON/CL');
      for (const m of ['SPLC', 'NEWS', 'ECO']) { await p.go(m); await p.page.waitForTimeout(300); }      // each request is in flight when the user moves on
      await p.go('MON');
      await p.page.waitForTimeout(6000);                                                                  // all three answer while hidden
      const asked = [];
      p.page.on('request', r => { if (/\/api\/(supply\/oil|news|calendar)/.test(r.url())) asked.push(r.url()); });
      await p.go('ECO'); await ecoReady(p, { timeout: 3000, msg: 'ECO shows the calendar that arrived while it was hidden' });
      check(await p.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="ECO"]'); return !!s.querySelector('tr.ec-now') || s.querySelectorAll('tbody tr').length > 10; }), 'ECO after a late answer: rows and the NOW line are there');
      await p.go('NEWS'); await newsReady(p, { timeout: 3000, msg: 'NEWS shows the headlines that arrived while it was hidden' });
      await p.go('SPLC'); await splcReady(p, { timeout: 3000, msg: 'SPLC shows the figures that arrived while it was hidden' });
      check(asked.length === 0, 'the late answers are used, not asked for again: ' + asked.length + ' extra request(s)');
      const errs = p.errors ? p.errors() : [];
      check(errs.length === 0, 'no console error after late answers: ' + errs.slice(0, 3).join(' | '));
      await p.close();
    });

    check(supplyReqs <= 2, 'SPLC asked /api/supply/oil ' + supplyReqs + ' time(s) during the whole spec (cache time 15 min)');
    check(calReqs <= 3 && newsReqs <= 3, 'NEWS asked ' + newsReqs + ' and ECO ' + calReqs + ' time(s) on the main page (cache time 5 min)');
    const errs = ctx.errors();
    check(errs.length === 0, 'console / network errors on the main page: ' + errs.slice(0, 5).join(' | '));
  },
};
