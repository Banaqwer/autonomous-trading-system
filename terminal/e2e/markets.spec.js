/* Markets slice spec (S1): MON, CHRT, TNT, HELP against the real gateway.
   Everything is compared with what the page RECEIVED (websocket frames, /api/quotes, /api/candles answers) or
   with what the gateway answers the test directly — never "within x %". Failure paths (stale / none / lagging
   quotes, a stale or failing /api/candles, a stopped gateway) are provoked by altering answers IN TRANSIT in a
   second page; the page under test never contains test data of its own. */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

const NUM = /^-?\d{1,3}(,\d{3})*(\.\d+)?$/;
function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
const nf = (v, d) => new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const settle = page => page.evaluate(() => new Promise(res => OT.requestFrame(() => setTimeout(res, 0))));     // after the module's frame()

/* every /api/candles answer a page received, by "ID|tf" */
function captureCandles(page) {
  const store = {};
  page.on('response', async r => {
    if (!/\/api\/candles\?/.test(r.url()) || r.status() !== 200) return;
    try { const j = await r.json(); if (j && j.id) { store[j.id + '|' + j.tf] = j; (store.n = store.n || {})[j.id + '|' + j.tf] = ((store.n || {})[j.id + '|' + j.tf] || 0) + 1; } } catch (e) { /* page went away */ }
  });
  return store;
}

/* MON table as displayed, next to what the page holds, read right after the module painted. */
const readMon = page => page.evaluate(() => new Promise(res => OT.requestFrame(() => setTimeout(() => {
  const isNum = v => typeof v === 'number' && isFinite(v), fmt = OT.fmt;
  const received = id => {
    const s = new Set();
    window.__otWsRecv.forEach(f => { if (f.id === id && isNum(f.last)) s.add(fmt.px(id, f.last)); });
    window.__otRestQuotes.forEach(a => { const v = a.quotes[id]; if (isNum(v)) s.add(fmt.px(id, v)); });
    return s;
  };
  const rows = [], bad = [];
  document.querySelectorAll('.mod[data-mod="MON"] .mon-table tbody tr').forEach(tr => {
    const id = tr.dataset.key, c = col => tr.querySelector('td[data-col="' + col + '"]'), q = OT.quotes[id];
    const has = !!q && isNum(q.last) && q.status !== 'none';
    const cv = c('spark').querySelector('canvas'); let ink = -1;
    try { const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data; ink = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) ink++; } catch (e) { ink = -1; }
    const rng = c('rng').firstChild, badge = c('src').querySelector('.badge');
    const shown = { last: c('last').textContent, chg: c('chg').textContent, pct: c('pct').textContent, bid: c('bid').textContent, ask: c('ask').textContent,
      lo: rng.querySelector('.rng-lo').textContent, hi: rng.querySelector('.rng-hi').textContent, none: rng.classList.contains('none'), mark: parseFloat(rng.querySelector('.rng-mark').style.left),
      badge: badge.textContent, fresh: badge.dataset.fresh, age: c('age').textContent, ink, lastCls: c('last').className, sel: tr.classList.contains('sel') };
    const px = v => (has && isNum(v) ? fmt.px(id, v) : '—');
    const range = has && isNum(q.high) && isNum(q.low) && q.high > q.low;
    const want = { last: px(q && q.last), chg: has && isNum(q.chg) ? fmt.signed(q.chg, fmt.decimals(id)) : '—', pct: has && isNum(q.chg_pct) ? fmt.pct(q.chg_pct) : '—', bid: px(q && q.bid), ask: px(q && q.ask),
      lo: range ? fmt.px(id, q.low) : '', hi: range ? fmt.px(id, q.high) : '', none: !range, badge: OT.ui.quoteBadge(q).textContent, age: has && isNum(q.ts) ? fmt.age(q.ts) : '—' };
    const crypto = OT.universe.instruments[id].cls === 'crypto';
    ['last', 'chg', 'pct', 'bid', 'ask', 'lo', 'hi', 'none', 'badge'].forEach(k => {
      if (shown[k] === want[k]) return;
      if (crypto && k === 'last' && received(id).has(shown.last)) return;         // a tick arrived between the paint and this read
      if (crypto && (k === 'chg' || k === 'pct' || k === 'bid' || k === 'ask')) return;   // follow the same tick
      bad.push(id + '.' + k + ' shows ' + JSON.stringify(shown[k]) + ' but the page holds ' + JSON.stringify(want[k]));
    });
    if (shown.last !== '—' && !received(id).has(shown.last)) bad.push(id + ' shows last ' + shown.last + ', which no quote frame and no /api/quotes answer contained');
    if (range && !crypto && Math.abs(shown.mark - Math.max(0, Math.min(1, (q.last - q.low) / (q.high - q.low))) * 100) > 0.02) bad.push(id + ' range marker at ' + shown.mark + '% for last ' + q.last + ' in ' + q.low + '–' + q.high);
    const spark = has && Array.isArray(q.spark) && q.spark.length >= 2;
    if (spark && !(ink > 20)) bad.push(id + ' has ' + q.spark.length + ' spark points but an empty sparkline');
    if (!spark && ink !== 0) bad.push(id + ' has no spark points but its sparkline has ink');
    rows.push(Object.assign({ id, src: q && q.source, status: q && q.status, session: q && q.session, cls: OT.universe.instruments[id].cls, pctVal: has && isNum(q.chg_pct) ? q.chg_pct : null, wantAge: want.age }, shown));
  });
  res({ rows, bad, sel: OT.state.sel, mod: OT.state.mod });
}, 0))));

module.exports = {
  name: 'markets', slice: 'markets',
  async run(ctx) {
    const { page, check } = ctx;
    const only = (process.env.OT_E2E_SECTION || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean);
    const section = async (name, fn) => {
      if (only.length && !only.some(w => name.toLowerCase().indexOf(w) >= 0)) return;
      const t0 = Date.now();
      try { await fn(); } catch (e) { ctx.fail(name + ' threw: ' + (e && e.stack || e)); }
      console.log('      [' + name + '] ' + ((Date.now() - t0) / 1000).toFixed(1) + ' s');
    };
    const uni = await getJson(ctx.base + '/api/universe');
    const allIds = [].concat(...uni.groups.map(g => g.ids));
    const dec = id => (uni.instruments[id].decimals == null ? 2 : uni.instruments[id].decimals);
    const candles = captureCandles(page);
    let dialogs = 0;
    page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });

    /* ════════════════════════ MON ════════════════════════ */
    await section('mon table', async () => {
      await ctx.select('CL'); await ctx.go('MON');
      await ctx.waitFor(() => document.querySelectorAll('.mod[data-mod="MON"] .mon-table tbody tr').length === 23, { msg: 'MON shows 23 rows' });
      await page.evaluate(() => OT.refreshQuotes());
      let m = await readMon(page);
      if (m.bad.length) { await ctx.sleep(400); m = await readMon(page); }                // one retry: a Yahoo batch can land mid-read
      check(m.rows.length === allIds.length && m.rows.map(r => r.id).join() === allIds.join(), 'MON default order differs from the universe groups: ' + m.rows.map(r => r.id).join());
      check(!m.bad.length, 'MON shows something the page does not hold: ' + m.bad.slice(0, 6).join(' | '));
      for (const r of m.rows) {
        if (r.last !== '—') {
          check(NUM.test(r.last) && (r.last.split('.')[1] || '').length === dec(r.id), r.id + ': MON last is ' + JSON.stringify(r.last) + ' (decimals ' + dec(r.id) + ')');
          check(String(r.badge).toUpperCase().indexOf(String(r.src).toUpperCase()) >= 0, r.id + ': row badge ' + JSON.stringify(r.badge) + ' does not name the source ' + r.src);
          check(/^\d+[smhd]$/.test(r.age), r.id + ': age cell is ' + JSON.stringify(r.age));
        }
        if (r.cls !== 'crypto' && r.session === 'closed') check(r.bid === '—' && r.ask === '—' || r.src === 'alpaca', r.id + ': bid/ask shown for a closed market without a two-sided source');
      }
      const kinds = [...new Set(m.rows.map(r => r.fresh))];
      check(kinds.length >= 3, 'MON rows show fewer than three freshness classes (' + kinds.join() + '): the page should tell real-time, delayed and end-of-day apart');
      console.log('      MON: 23 rows exact; freshness classes on screen: ' + kinds.join(' ') + '; bid/ask on ' + m.rows.filter(r => r.bid !== '—').map(r => r.id).join(' '));
      // the gateway asked directly: one instrument's figures in the side panel
      const g = await getJson(ctx.base + '/api/quote?id=CL');
      const sideNow = () => page.evaluate(() => { const dd = Array.from(document.querySelectorAll('.mon-side .ms-kv dd')).map(d => d.textContent); return { id: document.querySelector('.ms-id').textContent, last: document.querySelector('.ms-last').textContent, dd, ts: OT.quotes.CL.ts,
        chain: Array.from(document.querySelectorAll('.ms-chain .ms-link')).map(l => l.textContent.trim() + (l.classList.contains('on') ? '*' : '')), note: document.querySelector('.ms-note').textContent, badge: document.querySelector('.mon-side .ms-src .badge').textContent }; });
      const s = await sideNow();
      check(s.id === 'CL', 'side panel shows ' + s.id + ' while CL is selected');
      if (g.ts === s.ts) {
        check(s.last === nf(g.last, dec('CL')), 'side panel last ' + s.last + ' != gateway /api/quote ' + g.last);
        check(s.dd[0] === nf(g.prev_close, dec('CL')), 'side panel prev close ' + s.dd[0] + ' != gateway ' + g.prev_close);
        check(s.dd[2] === (g.high == null ? '—' : nf(g.high, dec('CL'))) && s.dd[3] === (g.low == null ? '—' : nf(g.low, dec('CL'))), 'side panel high/low ' + s.dd[2] + '/' + s.dd[3] + ' != gateway ' + g.high + '/' + g.low);
      } else console.log('      (side panel vs /api/quote skipped: the quote moved between the two reads)');
      check(s.dd[4] === '—' && s.dd[5] === '—' && s.dd[6] === '—', 'CL has no bid/ask source, but the side panel shows ' + s.dd.slice(4, 7).join(' / '));
      check(s.chain.length === uni.instruments.CL.quote.sources.length && s.chain.filter(x => /\*$/.test(x)).length === 1, 'side panel source chain: ' + s.chain.join(' › '));
      check(s.note === (g.note || ''), 'side panel note ' + JSON.stringify(s.note) + ' != gateway note ' + JSON.stringify(g.note));
      await ctx.assertNoBadText('.mod[data-mod="MON"]', 'MON');
      await ctx.assertHeaderBadge('MON');
    });

    await section('mon interaction', async () => {
      await ctx.select('CL'); await ctx.go('MON');
      const row = id => '.mon-table tbody tr[data-key="' + id + '"]';
      // real click selects; the row node is the same afterwards (patched, not rebuilt)
      await page.evaluate(() => { window.__tr = document.querySelector('.mon-table tbody tr[data-key="BTC"]'); window.__td = window.__tr.querySelector('td[data-col="last"]'); window.__chg = 0;
        window.__mo = new MutationObserver(() => { window.__chg++; }); window.__mo.observe(window.__td, { childList: true, characterData: true, subtree: true }); });
      await ctx.mouse.click(row('NVDA') + ' td[data-col="chg"]');
      await settle(page);
      let st = await page.evaluate(() => ({ sel: OT.state.sel, rows: Array.from(document.querySelectorAll('.mon-table tr.sel')).map(r => r.dataset.key).join(), rail: (document.querySelector('#watch .wrow.sel') || {}).dataset.id, side: document.querySelector('.ms-id').textContent, hash: location.hash }));
      check(st.sel === 'NVDA' && st.rows === 'NVDA' && st.rail === 'NVDA' && st.side === 'NVDA' && st.hash === '#MON/NVDA', 'click on the NVDA row: ' + JSON.stringify(st));
      // keyboard: down / up move in the table's order
      await page.keyboard.press('ArrowDown'); await settle(page);
      check(await page.evaluate(() => OT.state.sel) === 'TSLA', 'ArrowDown on a focused MON row selected ' + await page.evaluate(() => OT.state.sel) + ' (expected TSLA)');
      await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp'); await settle(page);
      check(await page.evaluate(() => OT.state.sel) === 'AAPL', 'ArrowUp twice selected ' + await page.evaluate(() => OT.state.sel) + ' (expected AAPL)');
      check(await page.evaluate(() => document.activeElement && document.activeElement.dataset.key) === 'AAPL', 'keyboard focus did not follow the selection in MON');
      // sort by Chg % (real click on the header): descending, empty values last; arrows then follow THAT order
      await ctx.mouse.click('.mon-table th[data-col="pct"]'); await settle(page);
      let m = await readMon(page);
      const vals = m.rows.map(r => r.pctVal), nn = vals.filter(v => v !== null);
      check(nn.every((v, i) => !i || nn[i - 1] >= v) && vals.slice(nn.length).every(v => v === null), 'MON sorted by Chg % is not descending: ' + m.rows.map(r => r.id + ' ' + r.pct).join(', '));
      check(await page.evaluate(() => document.querySelector('.mon-table th[data-col="pct"]').getAttribute('aria-sort')) === 'descending', 'Chg % header does not say it is sorted');
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      const order = m.rows.map(r => r.id), at = order.indexOf('AAPL');
      await page.keyboard.press('ArrowDown'); await settle(page);
      const got = await page.evaluate(() => OT.state.sel);
      check(got === order[Math.min(order.length - 1, at + 1)], 'ArrowDown with a sorted MON selected ' + got + ', the next row on screen is ' + order[Math.min(order.length - 1, at + 1)]);
      await ctx.shot('mon_sorted');
      await ctx.mouse.click('.mon-table th[data-col="pct"]'); await ctx.mouse.click('.mon-table th[data-col="pct"]'); await settle(page);
      m = await readMon(page);
      check(m.rows.map(r => r.id).join() === allIds.join(), 'third click on a sorted header did not restore the default order');
      // live: the BTC last cell changes in place
      await ctx.waitFor(() => window.__chg > 0, { timeout: 15000, msg: 'the BTC last cell in MON changed within 15 s (crypto stream)' });
      check(await page.evaluate(() => window.__tr.isConnected && window.__td.isConnected && window.__tr === document.querySelector('.mon-table tbody tr[data-key="BTC"]')), 'MON rows are rebuilt on update instead of patched in place');
      await page.evaluate(() => window.__mo.disconnect());
      // hover read-outs (real mouse)
      await ctx.mouse.hover(row('CL') + ' .rng-track');
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /Day range of CL/.test(t.textContent); }, { msg: 'tooltip on the CL day-range bar' });
      await ctx.mouse.hover(row('GC') + ' td[data-col="src"] .badge');
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && t.textContent.indexOf('source: ' + String(OT.quotes.GC.source).toUpperCase()) >= 0; }, { msg: 'tooltip on a row badge names the source' });
      // mini chart: real bars, hover read-out, timeframe switch
      await ctx.select('GC');
      await ctx.waitFor(() => /bars/.test(document.querySelector('.ms-clegend').textContent) && !document.querySelector('.ms-cbox .ot-state'), { timeout: 15000, msg: 'mini chart loaded for GC' });
      const mini = await page.evaluate(() => { const c = document.querySelector('.ms-cbox canvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
        return { ink: n, legend: document.querySelector('.ms-clegend').textContent, tf: document.querySelector('.ms-tabs .ot-tab[aria-selected="true"]').dataset.id, badge: document.querySelector('.ms-chead .badge').textContent }; });
      const pay = candles['GC|' + mini.tf];
      check(mini.ink > 500, 'mini chart canvas is empty (' + mini.ink + ' px)');
      check(!!pay && pay.candles.length >= 2, 'no /api/candles answer was received for the mini chart (GC ' + mini.tf + ')');
      if (pay) {
        const a = pay.candles[0], b = pay.candles[pay.candles.length - 1];
        check(mini.legend.indexOf(pay.candles.length + ' ' + mini.tf + ' bars') === 0 && mini.legend.indexOf(nf(a.c, dec('GC')) + ' › ' + nf(b.c, dec('GC'))) > 0, 'mini chart legend ' + JSON.stringify(mini.legend) + ' does not match the received bars (' + pay.candles.length + ', ' + a.c + ' › ' + b.c + ')');
        check(mini.badge.toUpperCase().indexOf(pay.source.toUpperCase()) >= 0, 'mini chart badge ' + mini.badge + ' does not name ' + pay.source);
      }
      const box = await page.locator('.ms-cbox canvas').boundingBox();
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5, { steps: 5 });
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /O [\d,.]+ +H [\d,.]+ +L [\d,.]+ +C [\d,.]+/.test(t.textContent); }, { msg: 'mini chart hover read-out (O H L C)' });
      if (pay) {
        const tip = await page.evaluate(() => document.querySelector('.ot-tip').textContent);
        const mm = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)Z .*C ([\d,.]+)/.exec(tip);
        const t = mm ? Date.UTC(+mm[1], +mm[2] - 1, +mm[3], +mm[4], +mm[5]) / 1000 : null, bar = pay.candles.find(x => x.t === t);
        check(!!bar && nf(bar.c, dec('GC')) === mm[6], 'mini chart read-out ' + JSON.stringify(tip) + ' is not a received bar');
      }
      await page.mouse.move(700, 400);
      await ctx.mouse.click('.ms-tabs .ot-tab[data-id="1d"]');
      await ctx.waitFor(() => /1d bars/.test(document.querySelector('.ms-clegend').textContent), { timeout: 15000, msg: 'mini chart switched to 1d bars' });
      check(!!candles['GC|1d'], 'the 1d tab did not fetch /api/candles tf=1d');
      await ctx.select('US2Y');
      await ctx.waitFor(() => /1d values/.test(document.querySelector('.ms-clegend').textContent), { timeout: 15000, msg: 'mini chart of an end-of-day yield (1d values)' });
      check(await page.evaluate(() => Array.from(document.querySelectorAll('.ms-tabs .ot-tab')).map(b => b.dataset.id).join()) === uni.instruments.US2Y.candles.tfs.filter(t => ['15m', '1h', '1d', '1w'].indexOf(t) >= 0).join(), 'mini chart offers timeframes US2Y does not have');
      check(await page.evaluate(() => document.querySelector('.ms-open [data-open="BOOK"]').disabled && !document.querySelector('.ms-open [data-open="CHRT"]').disabled), 'side panel module buttons do not follow the instrument’s capabilities');
      // double-click opens the chart of that row
      await page.dblclick(row('EURUSD') + ' td[data-col="last"]');
      await settle(page);
      st = await page.evaluate(() => ({ mod: OT.state.mod, sel: OT.state.sel }));
      check(st.mod === 'CHRT' && st.sel === 'EURUSD', 'double-click on the EURUSD row → ' + JSON.stringify(st));
      await ctx.go('MON'); await ctx.select('CL');
      // fast switching: 60 selections in a row, the page ends on the last one with one module visible
      for (let i = 0; i < 60; i++) await page.evaluate(id => OT.select(id), allIds[(i * 5) % allIds.length]);
      await ctx.select('BTC'); await settle(page);
      st = await page.evaluate(() => ({ side: document.querySelector('.ms-id').textContent, sel: Array.from(document.querySelectorAll('.mon-table tr.sel')).map(r => r.dataset.key).join(), vis: Array.from(document.querySelectorAll('#main .mod')).filter(s => !s.hidden).length }));
      check(st.side === 'BTC' && st.sel === 'BTC' && st.vis === 1, 'after fast switching: ' + JSON.stringify(st));
      await ctx.waitFor(() => /bars/.test(document.querySelector('.ms-clegend').textContent), { timeout: 15000, msg: 'mini chart of BTC after fast switching' });
      await ctx.assertNoBadText('.mod[data-mod="MON"]', 'MON');
      await ctx.shot('mon');
    });

    await section('mon stale none lag', async () => {
      const staleId = 'EURUSD', noneId = 'US2Y', none2 = 'HG';
      const p2 = await ctx.open({ tag: 'mon-stale', ws: true });
      const alter = q => {
        if (!q || typeof q !== 'object') return q;
        if (q.id === staleId) { q.status = 'stale'; q.note = 'e2e: yahoo has not answered (changed in transit by the test)'; }
        if (q.id === noneId || q.id === none2) Object.assign(q, { last: null, prev_close: null, chg: null, chg_pct: null, bid: null, ask: null, open: null, high: null, low: null, volume: null, ts: null, source: null, status: 'none', note: 'e2e: no source answered (changed in transit by the test)', spark: [] });
        return q;
      };
      await p2.page.route('**/api/quotes', async route => {
        let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
        const body = await res.json(); alter(body.quotes[staleId]); alter(body.quotes[noneId]); alter(body.quotes[none2]);
        await route.fulfill({ response: res, json: body });
      });
      p2.ws.rewrite(m => (m && m.type === 'quote' && (m.id === staleId || m.id === noneId || m.id === none2) ? alter(m) : undefined));
      await p2.goto('MON/' + staleId);
      const pg = p2.page;
      await p2.waitFor(([a, b]) => OT.quotes[a] && OT.quotes[a].status === 'stale' && OT.quotes[b] && OT.quotes[b].status === 'none', { arg: [staleId, noneId], timeout: 45000, msg: 'the altered quotes reached the page' });
      let m = await readMon(pg);
      const a = m.rows.find(r => r.id === staleId), b = m.rows.find(r => r.id === noneId);
      check(!m.bad.length, 'MON with a stale and a none row: ' + m.bad.slice(0, 4).join(' | '));
      check(a.fresh === 'STALE' && /mon-stale/.test(a.lastCls) && NUM.test(a.last), staleId + ' stale row: ' + JSON.stringify(a));
      check(b.last === '—' && b.chg === '—' && b.pct === '—' && b.fresh === 'NO' && /NO SOURCE/.test(b.badge) && b.none && b.ink === 0 && b.age === '—', noneId + ' none row: ' + JSON.stringify(b));
      const note = await pg.evaluate(() => document.querySelector('.mod[data-mod="MON"] .mod-note').textContent);
      check(/1 STALE/.test(note) && /2 without a source/.test(note), 'MON header counts: ' + note);
      await pg.evaluate(() => OT.select('US2Y')); await settle(pg);
      const sd = await pg.evaluate(() => ({ last: document.querySelector('.ms-last').textContent, note: document.querySelector('.ms-note').textContent, badge: document.querySelector('.mon-side .ms-src .badge').textContent }));
      check(sd.last === '—' && /NO SOURCE/.test(sd.note) && /no source answered/.test(sd.note) && /NO SOURCE/.test(sd.badge), 'side panel for an instrument without a source: ' + JSON.stringify(sd));
      await p2.assertNoBadText('.mod[data-mod="MON"]', 'MON stale/none'); await p2.assertContrast(4.5, '.mod[data-mod="MON"]', 'MON stale/none');
      await p2.shotNow('mon_stale_none_1920x1080');
      // TNT refuses a stale price
      await pg.evaluate(() => { OT.select('EURUSD'); OT.go('TNT'); }); await settle(pg);
      let t = await pg.evaluate(() => ({ dis: document.querySelector('.tk-submit').disabled, text: document.querySelector('.tk-preview').textContent }));
      check(t.dis && /REFUSED/.test(t.text) && /STALE/.test(t.text), 'TNT with a stale quote: ' + JSON.stringify(t));
      // … and an instrument without any price
      await pg.evaluate(() => OT.select('HG')); await settle(pg);
      t = await pg.evaluate(() => ({ dis: document.querySelector('.tk-submit').disabled, text: document.querySelector('.tk-preview').textContent, last: document.querySelector('.tk-quote').textContent, badge: document.querySelector('.tk-src .badge').textContent }));
      check(t.dis && /REFUSED/.test(t.text) && /no price for HG/.test(t.text) && /no source answered/.test(t.text) && /NO SOURCE/.test(t.badge) && !/\d/.test(t.last), 'TNT without a price: ' + JSON.stringify(t));
      await pg.click('.tnt-ticket .ot-tab[data-id="limit"]'); await pg.fill('input[aria-label="Limit price"]', '5'); await settle(pg);
      t = await pg.evaluate(() => ({ dis: document.querySelector('.tk-submit').disabled, text: document.querySelector('.tk-preview').textContent }));
      check(t.dis && /REFUSED/.test(t.text), 'TNT accepts a limit order for an instrument without any price: ' + JSON.stringify(t));
      await pg.click('.tnt-ticket .ot-tab[data-id="market"]');
      // LAG: the websocket goes silent — streamed real-time rows must say so, and TNT must refuse them
      await pg.evaluate(() => { OT.select('BTC'); OT.go('MON'); });
      p2.ws.mute(true);
      await p2.waitFor(() => { const b = document.querySelector('.mon-table tr[data-key="BTC"] td[data-col="src"] .badge'); return b && b.dataset.fresh === 'LAG'; }, { timeout: 30000, msg: 'BTC row shows LAG when the stream is silent' });
      m = await readMon(pg);
      const btc = m.rows.find(r => r.id === 'BTC');
      check(/mon-lag/.test(btc.lastCls), 'lagging BTC row is not marked: ' + JSON.stringify(btc));
      await pg.evaluate(() => OT.go('TNT')); await settle(pg);
      t = await pg.evaluate(() => ({ dis: document.querySelector('.tk-submit').disabled, text: document.querySelector('.tk-preview').textContent }));
      check(t.dis && /LAG/.test(t.text), 'TNT with a lagging quote: ' + JSON.stringify(t));
      p2.ws.mute(false);
      await p2.waitFor(() => !document.querySelector('.tk-submit').disabled, { timeout: 30000, msg: 'TNT accepts BTC orders again when the stream is back' });
      p2.errors().forEach(e => ctx.fail(e));
      await p2.close();
    });

    await section('no candles capability', async () => {
      /* Every instrument of the real universe has bars. Here /api/universe is altered in transit so that RB has none:
         CHRT must show the core's NO LIVE SOURCE state with the gateway's reason and the other instruments as picks;
         MON must not draw a mini chart or offer the chart; HELP must say so. */
      const why = 'e2e: no free bar source for this contract (changed in transit by the test)';
      const p = await ctx.open({ tag: 'nocandles' });
      await p.page.route('**/api/universe', async route => {
        let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
        const body = await res.json(); body.instruments.RB.caps.candles = false; body.instruments.RB.why = Object.assign({}, body.instruments.RB.why, { candles: why });
        await route.fulfill({ response: res, json: body });
      });
      let asked = 0;
      p.page.on('request', r => { if (/\/api\/candles\?id=RB/.test(r.url())) asked++; });
      await p.goto('CHRT/RB');
      const ns = await p.page.evaluate(() => { const st = document.querySelector('.mod[data-mod="CHRT"] .mod-cover:not([hidden]) .ot-state'); return st ? { kind: st.dataset.kind, text: st.textContent, picks: Array.from(st.querySelectorAll('[data-pick]')).map(b => b.dataset.pick) } : null; });
      check(ns && ns.kind === 'nosource' && ns.text.indexOf(why) >= 0 && ns.picks.length === allIds.length - 1 && ns.picks.indexOf('RB') < 0, 'CHRT for an instrument without bars: ' + JSON.stringify(ns));
      await p.shotNow('chrt_nosource_1920x1080');
      await p.mouse.click('.mod[data-mod="CHRT"] [data-pick="GC"]');
      await p.waitFor(() => OT.state.sel === 'GC' && OTM.chart.state().payloadId === 'GC' && OTM.chart.state().n > 1, { timeout: 20000, msg: 'a pick in the NO SOURCE state opens that instrument’s chart' });
      await p.page.evaluate(() => { OT.select('RB'); OT.go('MON'); }); await settle(p.page); await p.sleep(600);
      const m = await p.page.evaluate(() => ({ st: (document.querySelector('.ms-cbox .ot-state') || { dataset: {} }).dataset.kind, text: (document.querySelector('.ms-cbox .ot-state') || {}).textContent, chartBtn: document.querySelector('.ms-open [data-open="CHRT"]').disabled, tabs: document.querySelectorAll('.ms-tabs .ot-tab').length }));
      check(m.st === 'nosource' && m.text.indexOf(why) >= 0 && m.chartBtn && m.tabs === 0, 'MON side panel for an instrument without bars: ' + JSON.stringify(m));
      await p.page.evaluate(() => OT.go('HELP')); await settle(p.page);
      const hb = await p.page.evaluate(() => document.querySelector('.help-insts tbody tr[data-key="RB"] td[data-col="bars"]').textContent + ' || ' + document.querySelector('.help-why').textContent);
      check(hb.indexOf('none — ' + why) === 0 && hb.split('||')[1].indexOf(why) >= 0, 'HELP for an instrument without bars: ' + hb.slice(0, 160));
      check(asked === 0, '/api/candles was requested ' + asked + ' time(s) for an instrument without the candles capability');
      p.errors().forEach(e => ctx.fail(e));
      await p.close();
    });

    /* ════════════════════════ CHRT ════════════════════════ */
    const chartState = pg => pg.evaluate(() => OTM.chart.state());
    const waitChart = (c, key, timeout) => c.waitFor(k => { const s = OTM.chart.state(); return s.key === k && s.n > 1 && s.payloadId === k.split('|')[0] && !document.querySelector('.mod[data-mod="CHRT"] .mod-body > .ot-state:not(.strip)'); }, { arg: key, timeout: timeout || 20000, msg: 'chart loaded ' + key });
    const legend = pg => pg.evaluate(() => { const g = k => { const e = document.querySelector('.chrt-legend .cl-item .val'); return e; }; const L = document.querySelector('.chrt-legend');
      const items = {}; L.querySelectorAll('.cl-item').forEach(it => { if (it.getBoundingClientRect().width > 0) items[it.querySelector('.cl-k').textContent] = it.querySelector('.val').textContent; });
      return { hidden: L.hidden, tf: L.querySelector('.cl-tf').textContent, time: L.querySelector('.cl-time').textContent, items, novol: L.querySelector('.cl-note').getBoundingClientRect().width > 0 }; });
    const setTf = async tf => { await ctx.mouse.click('.mod[data-mod="CHRT"] .chrt-tfs .ot-tab[data-id="' + tf + '"]'); };

    await section('chrt data', async () => {
      await ctx.select('AAPL'); await ctx.go('CHRT');
      check(await page.evaluate(() => Array.from(document.querySelectorAll('.chrt-tfs .ot-tab')).map(b => b.dataset.id).join()) === uni.instruments.AAPL.candles.tfs.join(), 'CHRT timeframe buttons differ from AAPL candles.tfs');
      await setTf('1d'); await waitChart(ctx, 'AAPL|1d');
      let s = await chartState(page), L = await legend(page);
      const pay = candles['AAPL|1d'];
      check(!!pay, 'no /api/candles answer captured for AAPL 1d');
      if (pay) {
        const b = pay.candles[pay.candles.length - 1], d = dec('AAPL');
        check(s.n === pay.candles.length && s.last.t === b.t && s.last.c === b.c && s.last.o === b.o && s.last.h === b.h && s.last.l === b.l, 'chart bars differ from the /api/candles answer: ' + JSON.stringify(s.last) + ' vs ' + JSON.stringify(b));
        check(L.items.O === nf(b.o, d) && L.items.H === nf(b.h, d) && L.items.L === nf(b.l, d) && L.items.C === nf(b.c, d), 'legend OHLC ' + JSON.stringify(L.items) + ' != last received bar ' + JSON.stringify(b));
        check(L.tf === '1d' && L.time.indexOf(new Date(b.t * 1000).toISOString().slice(0, 10)) === 0 && /latest bar/.test(L.time), 'legend bar time ' + JSON.stringify(L.time) + ' for t=' + b.t);
        check(b.t % 86400 === 0, 'daily bar is not dated at 00:00 UTC: t=' + b.t);
        check(!!L.items.V === !!pay.volume, 'volume read-out ' + L.items.V + ' although payload.volume=' + pay.volume);
        const hd = await ctx.assertHeaderBadge('CHRT');
        check(hd.badge && hd.badge.toUpperCase().indexOf(pay.source.toUpperCase()) >= 0, 'CHRT header badge ' + hd.badge + ' does not name ' + pay.source);
      }
      check(s.ascending && s.maxT <= Date.now() / 1000 + 60, 'chart holds unsorted or future-dated bars (max t ' + s.maxT + ')');
      check(await page.evaluate(() => { const c = Array.from(document.querySelectorAll('.chrt-chart canvas')); return c.length >= 2 && c.some(x => { try { const d = x.getContext('2d').getImageData(0, 0, x.width, x.height).data; let n = 0; for (let i = 3; i < d.length; i += 16) if (d[i]) n++; return n > 500; } catch (e) { return false; } }); }), 'chart canvases are empty');
      // crosshair read-out = a received bar
      const box = await page.locator('.chrt-chart').boundingBox();
      await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.4, { steps: 6 });
      await ctx.waitFor(() => !/latest bar/.test(document.querySelector('.chrt-legend .cl-time').textContent), { msg: 'crosshair legend follows the mouse' });
      L = await legend(page);
      if (pay) {
        const t = Date.parse(L.time.trim().slice(0, 10) + 'T00:00:00Z') / 1000, bar = pay.candles.find(x => x.t === t), d = dec('AAPL');
        check(!!bar && L.items.O === nf(bar.o, d) && L.items.H === nf(bar.h, d) && L.items.L === nf(bar.l, d) && L.items.C === nf(bar.c, d), 'crosshair read-out ' + JSON.stringify(L) + ' is not the received bar ' + JSON.stringify(bar));
        if (bar && pay.volume) check(/^[\d,.]+[KMBT]?$/.test(L.items.V), 'crosshair volume read-out: ' + L.items.V);
      }
      await page.mouse.move(box.x + box.width + 300, box.y - 60);
      // MA overlays: labelled, toggled, the choice survives an instrument switch
      check(await page.evaluate(() => { const i = document.querySelector('.chrt-legend .cl-ma20'); return i.getBoundingClientRect().width > 0 && /MA20/.test(i.textContent) && /DERIVED/.test(i.getAttribute('data-tip')); }), 'MA20 is not labelled (with DERIVED in its tooltip) in the legend');
      await ctx.mouse.click('.mod[data-mod="CHRT"] [data-ma="ma50"]');
      check(await page.evaluate(() => document.querySelector('.chrt-legend .cl-ma50').getBoundingClientRect().width > 0 && document.querySelector('[data-ma="ma50"]').getAttribute('aria-pressed') === 'true'), 'MA50 toggle did not show the overlay label');
      if (pay && pay.candles.length >= 50) {
        const want = pay.candles.slice(-50).reduce((a, x) => a + x.c, 0) / 50;
        check((await legend(page)).items.MA50 === nf(want, dec('AAPL')), 'MA50 in the legend ' + (await legend(page)).items.MA50 + ' != mean of the last 50 received closes ' + want);
      }
      // an end-of-day yield: line, only its own timeframes, no volume
      await ctx.select('US2Y'); await waitChart(ctx, 'US2Y|1d');
      s = await chartState(page); L = await legend(page);
      check(await page.evaluate(() => Array.from(document.querySelectorAll('.chrt-tfs .ot-tab')).map(b => b.dataset.id).join()) === uni.instruments.US2Y.candles.tfs.join(), 'CHRT offers timeframes US2Y does not have');
      check(s.line && !s.hasVol && L.items.value && !L.items.O && !L.items.V && await page.evaluate(() => document.querySelector('.chrt-vollabel').hidden), 'US2Y should be a line without volume: ' + JSON.stringify({ line: s.line, vol: s.hasVol, items: L.items }));
      check(await page.evaluate(() => document.querySelector('[data-ma="ma50"]').getAttribute('aria-pressed')) === 'true', 'the MA50 choice was lost on an instrument switch');
      await ctx.mouse.click('.mod[data-mod="CHRT"] [data-ma="ma50"]');
      const q2 = await getJson(ctx.base + '/api/candles?id=US2Y&tf=1d&limit=500');
      check(s.last.c === q2.candles[q2.candles.length - 1].c && s.n === q2.candles.length, 'US2Y chart differs from the gateway asked directly');
      await ctx.shot('chrt_line');
      // FX: candles, no volume pane
      await ctx.select('EURUSD'); await waitChart(ctx, 'EURUSD|1d');
      s = await chartState(page); L = await legend(page);
      check(!s.line && !s.hasVol && L.novol && !L.items.V, 'EURUSD (no volume at the source) shows a volume pane or read-out');
    });

    await section('chrt view', async () => {
      await ctx.select('BTC'); await ctx.go('CHRT'); await setTf('5m'); await waitChart(ctx, 'BTC|5m');
      const rel = s => ({ w: s.range.to - s.range.from, to: s.range.to - s.n, from: s.range.from - s.n });
      const s0 = await chartState(page);
      await ctx.mouse.wheel('.chrt-chart', -240, 6); await ctx.sleep(300);
      const s1 = await chartState(page);
      check(rel(s1).w < rel(s0).w * 0.8, 'wheel over the chart did not zoom in: ' + rel(s0).w.toFixed(1) + ' → ' + rel(s1).w.toFixed(1) + ' bars');
      await ctx.mouse.drag('.chrt-chart', 380, 0, 12); await ctx.sleep(300);
      const s2 = await chartState(page);
      check(s2.range.from < s1.range.from - 5, 'dragging the chart did not scroll back in time: from ' + s1.range.from.toFixed(1) + ' → ' + s2.range.from.toFixed(1));
      // a data refresh keeps the view (5m bars: the gateway cache is 20 s)
      await ctx.waitFor(t => OTM.chart.state().loadedAt > t, { arg: s2.loadedAt, timeout: 40000, msg: 'CHRT refetched BTC 5m at its cache time' });
      const s3 = await chartState(page);
      check(Math.abs(rel(s3).w - rel(s2).w) < 0.02 && Math.abs(s3.range.from - s2.range.from) < 1.02, 'a data refresh moved the view: ' + JSON.stringify(s2.range) + ' → ' + JSON.stringify(s3.range));
      check(s3.barSpacing === s2.barSpacing, 'a data refresh changed the zoom (bar spacing ' + s2.barSpacing + ' → ' + s3.barSpacing + ')');
      check((candles.n || {})['BTC|5m'] >= 2, 'no second /api/candles request seen for BTC 5m');
      // resize keeps the zoom
      await ctx.setViewport(1500, 900); await ctx.sleep(300);
      const s4 = await chartState(page);
      check(s4.barSpacing === s3.barSpacing && Math.abs(s4.range.to - s3.range.to) < 1.02, 'resize reset the chart view: spacing ' + s3.barSpacing + ' → ' + s4.barSpacing + ', right edge ' + s3.range.to.toFixed(1) + ' → ' + s4.range.to.toFixed(1));
      await ctx.setViewport(1920, 1080); await ctx.sleep(300);
      const s5 = await chartState(page);
      // instrument switch and back: this instrument + timeframe gets its view back; so does a module switch
      await ctx.select('ETH'); await waitChart(ctx, 'ETH|5m');
      const e0 = await chartState(page);
      check(e0.payloadId === 'ETH' && e0.last.c < 20000, 'after switching to ETH the chart still holds ' + e0.payloadId + ' bars (last ' + e0.last.c + ')');
      await ctx.select('BTC'); await waitChart(ctx, 'BTC|5m'); await ctx.sleep(300);
      const s6 = await chartState(page);
      check(Math.abs(rel(s6).w - rel(s5).w) < 0.6 && Math.abs(rel(s6).from - rel(s5).from) < 1.6, 'the BTC 5m view was not restored after ETH: ' + JSON.stringify(rel(s5)) + ' → ' + JSON.stringify(rel(s6)));
      await ctx.go('MON'); await ctx.go('CHRT'); await waitChart(ctx, 'BTC|5m'); await ctx.sleep(300);
      const s7 = await chartState(page);
      check(Math.abs(rel(s7).w - rel(s6).w) < 0.6 && Math.abs(rel(s7).from - rel(s6).from) < 1.6, 'the view was not restored after leaving CHRT and coming back: ' + JSON.stringify(rel(s6)) + ' → ' + JSON.stringify(rel(s7)));
      await ctx.shot('chrt_zoomed');
      await ctx.mouse.click('.mod[data-mod="CHRT"] [data-latest]'); await ctx.sleep(200);
      check((await chartState(page)).range.to > s7.n - 1, '"Latest" did not bring the newest bars into view');
      // the last bar follows the live quote (same source, crypto): the legend close becomes a received price
      await page.evaluate(() => { window.__seen = new Set(); OT.on('quote', (id, q) => { if (id === 'BTC') window.__seen.add(OT.fmt.px('BTC', q.last)); }); window.__seen.add(OT.fmt.px('BTC', OT.quotes.BTC.last)); });
      await ctx.sleep(2500);
      const live = await page.evaluate(() => ({ c: Array.from(document.querySelectorAll('.chrt-legend .cl-item')).filter(i => i.querySelector('.cl-k').textContent === 'C')[0].querySelector('.val').textContent, seen: Array.from(window.__seen), text: document.querySelector('.chrt-live').textContent, last: OTM.chart.state().last }));
      check(live.seen.indexOf(live.c) >= 0, 'the live last bar shows close ' + live.c + ', which is not a BTC price the page received (' + live.seen.slice(-4).join(' ') + ')');
      check(/LIVE/.test(live.text) && /Binance/.test(live.text), 'CHRT does not say how the last bar is kept live: ' + live.text);
      check(live.last.h >= live.last.c && live.last.l <= live.last.c, 'the live last bar has close outside its high/low: ' + JSON.stringify(live.last));
      // 1m: subscribed to the kline stream
      await setTf('1m'); await waitChart(ctx, 'BTC|1m');
      await ctx.waitFor(() => window.__otWsSent.some(f => f.d.indexOf('kline:BTC') >= 0), { msg: 'CHRT subscribed to kline:BTC for the 1m chart' });
      const n0 = (await chartState(page));
      check(n0.maxT <= Date.now() / 1000 + 60 && n0.ascending, 'BTC 1m holds future-dated or unsorted bars');
      // fast switching across instruments and timeframes: ends on the right series
      for (let i = 0; i < 24; i++) await page.evaluate(([id, tf]) => { OT.select(id); const b = document.querySelector('.chrt-tfs .ot-tab[data-id="' + tf + '"]'); if (b) b.click(); }, [['CL', 'BTC', 'SPX', 'EURUSD', 'AAPL', 'US10Y'][i % 6], ['1m', '1h', '1d', '15m'][i % 4]]);
      await ctx.select('NG'); await setTf('1h'); await waitChart(ctx, 'NG|1h');
      const f = await chartState(page), q = await getJson(ctx.base + '/api/candles?id=NG&tf=1h&limit=500');
      check(f.payloadId === 'NG' && f.payloadTf === '1h' && f.n >= q.candles.length - 1 && Math.abs(f.first.t - q.candles[0].t) <= 3600, 'after fast switching the chart is not NG 1h: ' + JSON.stringify({ id: f.payloadId, tf: f.payloadTf, n: f.n }));
      const prev = q.candles.find(b => b.t === f.prev.t);
      check(!!prev && prev.c === f.prev.c && prev.o === f.prev.o && prev.h === f.prev.h && prev.l === f.prev.l, 'a closed NG 1h bar on the chart differs from the gateway asked directly: ' + JSON.stringify(f.prev) + ' vs ' + JSON.stringify(prev));
      check(await page.evaluate(() => document.querySelector('.mod[data-mod="CHRT"] .mod-inst-id').textContent) === 'NG', 'CHRT header instrument after fast switching');
      await ctx.assertNoBadText('.mod[data-mod="CHRT"]', 'CHRT');
      await ctx.assertCanvasText(12, 'CHRT canvases');
      await ctx.shot('chrt');
    });

    await section('chrt failure paths', async () => {
      const p3 = await ctx.open({ tag: 'chrt-fail', ws: true });
      const pg = p3.page;
      let mode = 'ok';
      await pg.route('**/api/candles?*', async route => {
        const u = route.request().url();
        if (mode === 'fail' && /id=NG/.test(u)) return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'e2e: yahoo answered HTTP 503 (injected by the test)', source: 'yahoo' } }) });
        if (mode === 'stale' && /id=CL/.test(u)) {
          let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
          const body = await res.json(); body.stale = true; body.error = { code: 'upstream_error', message: 'e2e: HTTP 503 from yahoo (changed in transit by the test)', source: 'yahoo' };
          return route.fulfill({ response: res, json: body });
        }
        return route.continue();
      });
      p3.allow(/502|api\/candles/);
      // upstream error with nothing to show: an error state with a retry, then recovery
      mode = 'fail';
      await p3.goto('CHRT/NG');
      await p3.waitFor(() => { const s = document.querySelector('.mod[data-mod="CHRT"] .mod-body > .ot-state[data-kind="error"]'); return s && /could not be loaded/.test(s.textContent) && /HTTP 503/.test(s.textContent) && /Retry/.test(s.textContent); }, { timeout: 20000, msg: 'CHRT shows an error state with the gateway’s message and a retry when /api/candles fails' });
      check((await chartState(pg)).n === 0, 'bars on the chart although /api/candles failed');
      await p3.shotNow('chrt_error_1920x1080');
      await p3.assertContrast(4.5, '.mod[data-mod="CHRT"]', 'CHRT error state');
      mode = 'ok';
      await pg.click('.mod[data-mod="CHRT"] .ot-state .btn');
      await waitChart(p3, 'NG|' + (await pg.evaluate(() => document.querySelector('.chrt-tfs .ot-tab[aria-selected="true"]').dataset.id)), 25000);
      // a stale answer (the gateway served an old one because the refresh failed): STALE in the header, bars stay
      mode = 'stale';
      await pg.evaluate(() => OT.select('CL'));
      await p3.waitFor(() => { const b = document.querySelector('.mod[data-mod="CHRT"] .mod-head .badge'); return b && b.dataset.fresh === 'STALE' && /HTTP 503/.test(b.getAttribute('data-tip')) && OTM.chart.state().payloadId === 'CL' && OTM.chart.state().n > 1; }, { timeout: 20000, msg: 'CHRT header says STALE (with the reason) for a stale /api/candles answer' });
      check(!/LIVE/.test(await pg.evaluate(() => document.querySelector('.chrt-live').textContent)), 'CHRT claims a live last bar on a stale answer');
      await p3.shotNow('chrt_stale_1920x1080');
      mode = 'ok';
      // a refresh that fails while bars are on screen: bars stay, header turns STALE; next good answer clears it
      await pg.evaluate(() => { OT.select('BTC'); document.querySelector('.chrt-tfs .ot-tab[data-id="1m"]').click(); });
      await waitChart(p3, 'BTC|1m');
      // websocket kline: a frame for the current minute updates the last bar; an older one is ignored; a newer one appends
      const s0 = await chartState(pg);
      const mk = (t, c) => ({ type: 'kline', id: 'BTC', tf: '1m', t, o: s0.last.o, h: Math.max(s0.last.h, c), l: Math.min(s0.last.l, c), c, v: 1.5 });
      const c1 = Math.round((s0.last.c + 12.34) * 100) / 100;
      p3.ws.rewrite(m => (m && m.type === 'kline' ? null : undefined));           // hold the real klines back while the test's own frames are checked
      await p3.sleep(300);
      const s0b = await chartState(pg);
      p3.ws.inject(mk(s0b.last.t, c1));
      await p3.waitFor(c => OTM.chart.state().last.c === c, { arg: c1, msg: 'a kline frame for the current bar updates it' });
      p3.ws.inject(mk(s0b.last.t - 600, 1));
      p3.ws.inject(mk(s0b.last.t + 60, c1 + 1));
      await p3.waitFor(([n, c]) => { const s = OTM.chart.state(); return s.n === n + 1 && s.last.c === c; }, { arg: [s0b.n, c1 + 1], msg: 'a kline frame for the next minute appends one bar' });
      const s1 = await chartState(pg);
      check(s1.ascending && s1.prev.c === c1, 'kline handling left the bars unsorted or lost the previous bar: ' + JSON.stringify([s1.prev, s1.last]));
      p3.ws.rewrite(null);
      p3.errors().forEach(e => ctx.fail(e));
      await p3.close();
    });

    /* ════════════════════════ TNT ════════════════════════ */
    const acct = pg => pg.evaluate(() => OTM.paper.account());
    const ticket = pg => pg.evaluate(() => new Promise(res => OT.requestFrame(() => setTimeout(res, 0)))).then(() => pg.evaluate(() => ({ dis: document.querySelector('.tk-submit').disabled, preview: document.querySelector('.tk-preview').textContent, label: document.querySelector('.tk-submit').textContent, msg: document.querySelector('.tk-msg').textContent,
      form: !document.querySelector('.tk-formbox').hidden, blocked: document.querySelector('.tk-blocked').hidden ? null : document.querySelector('.tk-blocked').textContent, qty: document.querySelector('input[aria-label="Quantity"]').value })));
    const setQty = async (pg, v) => { await pg.fill('input[aria-label="Quantity"]', String(v)); };

    await section('tnt', async () => {
      const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'js', 'mod', 'tnt.js'), 'utf8');
      check(!/\bfetch\s*\(|XMLHttpRequest|sendBeacon|new\s+WebSocket|OT\.api\s*\(|EventSource/.test(src), 'tnt.js contains a network call: the paper blotter must not be able to reach anything');
      await page.evaluate(() => localStorage.removeItem('ot.paper.v1'));
      await ctx.goto('TNT/BTC');
      const reqs = [];
      page.on('request', r => reqs.push(r.method() + ' ' + r.url()));
      await ctx.waitFor(() => !document.querySelector('.tk-submit').disabled, { msg: 'TNT ticket ready for BTC' });
      let a = await acct(page);
      check(a.fills.length === 0 && a.orders.length === 0 && Object.keys(a.positions).length === 0, 'paper account is not empty after clearing the storage');
      check(await page.evaluate(() => /PAPER/.test(document.querySelector('.mod[data-mod="TNT"] .mod-title').textContent) && /PAPER/.test(document.querySelector('#tabs .tab[data-mod="TNT"]').title) && document.querySelectorAll('.mod[data-mod="TNT"] .paper-chip').length >= 4 && /NO REAL ORDERS/.test(document.querySelector('.mod[data-mod="TNT"] .mod-controls').textContent)), 'TNT is not clearly labelled PAPER');
      await page.evaluate(() => { window.__q = []; const rec = id => { const q = OT.quotes[id]; window.__q.push({ id, bid: q.bid, ask: q.ask, last: q.last }); }; rec('BTC'); OT.on('quote', id => { if (id === 'BTC') rec(id); }); });
      // market BUY at the real ask (typed quantity, real click)
      await setQty(page, '0.5');
      let t = await ticket(page);
      check(/Fills now at the real ask/.test(t.preview) && /Binance, RT/.test(t.preview) && /BUY 0\.5 BTC at MARKET — PAPER/.test(t.label), 'ticket preview before a BTC market buy: ' + JSON.stringify(t));
      await ctx.mouse.click('.tk-submit'); await settle(page);
      a = await acct(page);
      let f = a.fills[0];
      const quotes = await page.evaluate(() => window.__q);
      check(a.fills.length === 1 && f.id === 'BTC' && f.side === 'buy' && f.qty === 0.5 && f.basis === 'ask' && f.src === 'binance' && f.fresh === 'RT', 'BTC market buy fill: ' + JSON.stringify(f));
      check(quotes.some(x => x.ask === f.px), 'the BTC buy filled at ' + f.px + ', which is not an ask the page received (' + quotes.slice(-3).map(x => x.ask).join(' ') + ')');
      check(a.positions.BTC && a.positions.BTC.qty === 0.5 && a.positions.BTC.avg === f.px, 'position after the buy: ' + JSON.stringify(a.positions.BTC));
      t = await ticket(page);
      check(/PAPER FILL/.test(t.msg) && /real ask/.test(t.msg), 'ticket message after the fill: ' + t.msg);
      // market SELL part of it at the real bid: realised P/L by the average-price method
      await ctx.mouse.click('.tk-side[data-side="sell"]'); await setQty(page, '0.2'); await ctx.mouse.click('.tk-submit'); await settle(page);
      a = await acct(page); const f2 = a.fills[0];
      const q2 = await page.evaluate(() => window.__q);
      check(f2.side === 'sell' && f2.basis === 'bid' && q2.some(x => x.bid === f2.px), 'BTC market sell did not fill at a received bid: ' + JSON.stringify(f2));
      check(Math.abs(f2.realised - (f2.px - f.px) * 0.2) < 1e-6 && Math.abs(a.positions.BTC.qty - 0.3) < 1e-9 && a.positions.BTC.avg === f.px, 'realised P/L / remaining position wrong: ' + JSON.stringify([f2.realised, a.positions.BTC]));
      const prow = await page.evaluate(() => new Promise(res => OT.requestFrame(() => setTimeout(() => { const tr = document.querySelector('.tnt-pos tbody tr[data-key="BTC"]'), c = k => tr.querySelector('td[data-col="' + k + '"]').textContent; res({ qty: c('qty'), avg: c('avg'), mark: c('mark'), unreal: c('unreal'), real: c('real'), last: OT.quotes.BTC.last, badge: tr.querySelector('.badge').dataset.fresh }); }, 0))));
      const num = s => Number(String(s).replace(/[^0-9.+-]/g, ''));
      check(prow.qty === '+0.3 BTC' && prow.avg === nf(f.px, 2) && prow.badge === 'RT', 'BTC position row: ' + JSON.stringify(prow));
      check(Math.abs(num(prow.unreal) - (num(prow.mark) - f.px) * 0.3) < 0.011, 'unrealised P/L ' + prow.unreal + ' is not 0.3 × (mark ' + prow.mark + ' − avg ' + f.px + ')');
      check(num(prow.real) === Math.round(f2.realised * 100) / 100 || Math.abs(num(prow.real) - f2.realised) < 0.006, 'realised P/L shown ' + prow.real + ' != ' + f2.realised);
      // Enter in the quantity field places the order
      await ctx.mouse.click('.tk-side[data-side="buy"]'); await page.focus('input[aria-label="Quantity"]'); await setQty(page, '0.1'); await page.keyboard.press('Enter'); await settle(page);
      a = await acct(page);
      check(a.fills.length === 3 && a.fills[0].qty === 0.1 && a.fills[0].side === 'buy', 'Enter in the quantity field did not place the order: ' + a.fills.length + ' fills');
      // bad quantities are refused before anything happens
      for (const bad of ['-5', '0', 'abc', '']) {
        await setQty(page, bad); t = await ticket(page);
        check(t.dis && /greater than 0/.test(t.preview), 'quantity ' + JSON.stringify(bad) + ' is not refused: ' + JSON.stringify(t));
        await page.focus('input[aria-label="Quantity"]'); await page.keyboard.press('Enter');
      }
      check((await acct(page)).orders.length === a.orders.length, 'an order was created from an invalid quantity');
      // a future without bid/ask: fills at LAST and says so; the contract-size helper fills the quantity
      await ctx.select('CL'); await settle(page);
      const cl = await page.evaluate(() => OT.quotes.CL);
      if (cl.status === 'ok' && cl.session === 'open') {
        await ctx.mouse.click('.tk-lot'); t = await ticket(page);
        check(t.qty === '1000' && /real last/.test(t.preview) && /no bid\/ask/.test(t.preview), 'CL ticket after the contract helper: ' + JSON.stringify(t));
        await ctx.mouse.click('.tk-submit'); await settle(page);
        a = await acct(page); const fc = a.fills[0];
        check(fc.id === 'CL' && fc.basis === 'last' && fc.qty === 1000 && fc.src === 'yahoo' && fc.px === cl.last, 'CL market buy should fill at the last price ' + cl.last + ': ' + JSON.stringify(fc));
        const cell = await page.evaluate(() => { const tr = document.querySelector('.tnt-fill tbody tr'); return { at: tr.querySelector('td[data-col="basis"]').textContent, q: tr.querySelector('td[data-col="quote"]').textContent, px: tr.querySelector('td[data-col="px"]').textContent }; });
        check(cell.at === 'LAST' && /YAHOO D10/.test(cell.q) && cell.px === nf(cl.last, 2), 'CL fill row: ' + JSON.stringify(cell));
        check(/no bid\/ask/.test((await ticket(page)).msg), 'the fill message does not say the order filled at last');
      } else console.log('      (CL fill-at-last skipped: CL session ' + cl.session + ', status ' + cl.status + ')');
      // yields and indices: not tradable, said plainly, with the tradable instruments as picks
      for (const [id, word] of [['US10Y', 'yield'], ['SPX', 'index']]) {
        await ctx.select(id); await settle(page); t = await ticket(page);
        check(!t.form && t.blocked && t.blocked.indexOf(id + ' cannot be paper-traded') >= 0 && t.blocked.indexOf(word) >= 0, id + ' in TNT: ' + JSON.stringify(t.blocked));
        const picks = await page.evaluate(() => Array.from(document.querySelectorAll('.tk-blocked [data-pick]')).map(b => b.dataset.pick));
        check(picks.length === allIds.filter(x => ['rate', 'index'].indexOf(uni.instruments[x].cls) < 0).length && picks.every(x => ['rate', 'index'].indexOf(uni.instruments[x].cls) < 0), id + ': picks are ' + picks.join());
      }
      await ctx.shot('tnt_refused');
      await ctx.mouse.click('.tk-blocked [data-pick="GC"]'); await settle(page);
      { const sel = await page.evaluate(() => OT.state.sel), tk = await ticket(page); check(sel === 'GC' && tk.form, 'clicking a pick did not bring the ticket back: sel ' + sel + ' ' + JSON.stringify(tk)); }
      // an equity: by session
      await ctx.select('AAPL'); await settle(page);
      const aq = await page.evaluate(() => OT.quotes.AAPL);
      const before = (await acct(page)).orders.length;
      await ctx.mouse.click('.tnt-ticket .ot-tab[data-id="market"]'); await setQty(page, '10'); t = await ticket(page);
      if (aq.session === 'closed') {
        check(t.dis && /REFUSED/.test(t.preview) && /closed/.test(t.preview), 'AAPL market order while the US session is closed: ' + JSON.stringify(t));
        await ctx.mouse.click('.tnt-ticket .ot-tab[data-id="limit"]'); await settle(page);
        t = await ticket(page);
        const lim = await page.evaluate(() => document.querySelector('input[aria-label="Limit price"]').value);
        check(!t.dis && /will rest/.test(t.preview) && Number(lim) === aq.last, 'AAPL limit order while closed: ' + JSON.stringify(t) + ' limit field ' + lim);
        await ctx.mouse.click('.tk-submit'); await settle(page);
        a = await acct(page);
        check(a.orders.length === before + 1 && a.orders[0].status === 'working' && a.orders[0].type === 'limit' && a.fills[0].id !== 'AAPL', 'AAPL limit order while closed should rest: ' + JSON.stringify(a.orders[0]));
        check(/WORKING · market closed/.test(await page.evaluate(() => document.querySelector('.tnt-ord tbody tr td[data-col="status"]').textContent)), 'resting order row does not say the market is closed');
        console.log('      TNT/AAPL: US session CLOSED — market refused, limit rests');
      } else {
        const two = typeof aq.bid === 'number' && typeof aq.ask === 'number';
        check(!t.dis && (two ? /real ask/.test(t.preview) : /real last/.test(t.preview)), 'AAPL market order in an open session: ' + JSON.stringify(t));
        await ctx.mouse.click('.tk-submit'); await settle(page);
        a = await acct(page);
        check(a.fills[0].id === 'AAPL' && a.fills[0].basis === (two ? 'ask' : 'last') && a.fills[0].src === aq.source, 'AAPL market buy in an open session: ' + JSON.stringify(a.fills[0]));
        console.log('      TNT/AAPL: US session OPEN — market buy filled at ' + a.fills[0].px + ' (' + a.fills[0].basis + ', ' + a.fills[0].src + ' ' + a.fills[0].fresh + '; quote bid/ask ' + aq.bid + ' / ' + aq.ask + ')');
        await ctx.mouse.click('.tnt-ticket .ot-tab[data-id="limit"]'); await settle(page);
        await page.fill('input[aria-label="Limit price"]', String(Math.round(aq.last * 0.8 * 100) / 100)); await ctx.mouse.click('.tk-submit'); await settle(page);
        a = await acct(page);
        check(a.orders[0].status === 'working' && a.orders[0].id === 'AAPL', 'a far-away AAPL buy limit should rest: ' + JSON.stringify(a.orders[0]));
      }
      // cancel the resting order with its button
      const n = a.orders[0].n;
      await ctx.mouse.click('.tnt-ord [data-cancel="' + n + '"]'); await settle(page);
      a = await acct(page);
      check(a.orders.find(o => o.n === n).status === 'cancelled', 'Cancel did not cancel order #' + n);
      await ctx.mouse.click('.tnt-ticket .ot-tab[data-id="market"]');
      // the list of real prices: click selects
      await ctx.mouse.click('.tnt-mkt tbody tr[data-key="ETH"]'); await settle(page);
      check(await page.evaluate(() => OT.state.sel) === 'ETH', 'clicking ETH in the price list did not select it');
      const mk = await page.evaluate(() => Array.from(document.querySelectorAll('.tnt-mkt tbody tr')).map(tr => tr.dataset.key + ':' + tr.querySelector('td[data-col="how"]').textContent));
      check(mk.length === 16 && mk.filter(x => /bid\/ask$/.test(x)).length >= 3, 'price list rows: ' + mk.join(' '));
      await ctx.assertNoBadText('.mod[data-mod="TNT"]', 'TNT'); await ctx.assertHeaderBadge('TNT');
      await ctx.shot('tnt');
      // persistence across a reload
      const saved = await acct(page);
      await ctx.goto('TNT/BTC');
      a = await acct(page);
      check(JSON.stringify(a.positions) === JSON.stringify(saved.positions) && a.fills.length === saved.fills.length && a.orders.length === saved.orders.length && a.seq === saved.seq, 'the paper account did not survive a reload');
      check(await page.evaluate(() => document.querySelectorAll('.tnt-fill tbody tr').length) === Math.min(100, saved.fills.length) && await page.evaluate(() => document.querySelectorAll('.tnt-pos tbody tr').length) === Object.keys(saved.positions).filter(k => saved.positions[k].qty).length, 'tables after reload do not show the stored account');
      // reset with a confirmation built into the page
      await ctx.mouse.click('[data-reset]'); await settle(page);
      let rs = await page.evaluate(() => ({ ask: !document.querySelector('.tnt-reset-ask').hidden, text: document.querySelector('.tnt-reset-text').textContent }));
      check(rs.ask && /Delete \d+ position\(s\), \d+ order\(s\) and \d+ fill\(s\)\?/.test(rs.text), 'reset confirmation: ' + JSON.stringify(rs));
      await ctx.mouse.click('[data-reset-no]'); await settle(page);
      check((await acct(page)).fills.length === saved.fills.length && await page.evaluate(() => document.querySelector('.tnt-reset-ask').hidden), '"Keep" did not keep the account');
      await ctx.mouse.click('[data-reset]'); await settle(page); await ctx.mouse.click('[data-reset-yes]'); await settle(page);
      a = await acct(page);
      check(a.fills.length === 0 && a.orders.length === 0 && Object.keys(a.positions).length === 0 && JSON.parse(await page.evaluate(() => localStorage.getItem('ot.paper.v1'))).fills.length === 0, 'reset did not empty the account and its storage');
      check(dialogs === 0, 'a native dialog (window.confirm / alert) was opened ' + dialogs + ' time(s)');
      check(await page.evaluate(() => /NO OPEN PAPER POSITIONS/.test(document.querySelector('.tnt-pos').textContent) && /NO PAPER FILLS/.test(document.querySelector('.tnt-fill').textContent)), 'empty blotter does not say it is empty');
      // nothing left the page except its own GETs to the gateway
      const foreign = reqs.filter(r => r.indexOf('GET ' + ctx.base + '/') !== 0);
      check(!foreign.length, 'requests other than GETs to the gateway were made while paper trading: ' + foreign.slice(0, 4).join(' | '));
      check(!reqs.some(r => /order|trade|broker|position/i.test(r)), 'a request that looks like an order was made: ' + reqs.filter(r => /order|trade|broker|position/i.test(r)).slice(0, 3).join(' | '));
    });

    await section('tnt limit', async () => {
      const p4 = await ctx.open({ tag: 'tnt-limit', ws: true });
      const pg = p4.page;
      await p4.goto('TNT/ETH');
      await p4.waitFor(() => !document.querySelector('.tk-submit').disabled, { msg: 'TNT ticket ready for ETH' });
      const last = await pg.evaluate(() => OT.quotes.ETH.last), lim = Math.round(last * 0.9 * 100) / 100;
      await pg.click('.tnt-ticket .ot-tab[data-id="limit"]');
      await pg.fill('input[aria-label="Limit price"]', String(lim)); await pg.fill('input[aria-label="Quantity"]', '2');
      let t = await ticket(pg);
      check(/Rests until the ask is at or below/.test(t.preview), 'far-away buy limit preview: ' + t.preview);
      await pg.click('.tk-submit'); await settle(pg);
      let a = await acct(pg);
      check(a.orders.length === 1 && a.orders[0].status === 'working' && a.fills.length === 0, 'a buy limit 10 % below the market should rest: ' + JSON.stringify(a.orders[0]));
      await p4.sleep(1500);
      check((await acct(pg)).fills.length === 0, 'the resting limit filled although no quote crossed it');
      // a quote that crosses (sent to this page by the test through its websocket): fills at the limit price
      const cross = Math.round((lim - 1) * 100) / 100;
      p4.ws.inject({ type: 'quote', id: 'ETH', last: cross, bid: cross - 0.01, ask: cross, ts: Date.now() / 1000 + 1, source: 'binance' });
      await p4.waitFor(() => OTM.paper.account().fills.length === 1, { msg: 'the resting buy limit fills when a quote crosses it' });
      await settle(pg);
      a = await acct(pg); const f = a.fills[0];
      check(f.basis === 'limit' && f.px === lim && f.cross === cross && f.crossBasis === 'ask' && a.orders[0].status === 'filled' && a.positions.ETH.qty === 2 && a.positions.ETH.avg === lim, 'limit fill: ' + JSON.stringify(f));
      const row = await pg.evaluate(() => { const tr = document.querySelector('.tnt-fill tbody tr'); return tr.querySelector('td[data-col="basis"]').textContent + ' | ' + tr.querySelector('td[data-col="quote"]').textContent + ' | ' + document.querySelector('.tnt-ord tbody tr td[data-col="status"]').textContent; });
      check(/^LIMIT \| BINANCE RT .* ask [\d,.]+ \| FILLED /.test(row), 'limit fill rows: ' + row);
      await p4.shotNow('tnt_limit_fill_1920x1080');
      // a marketable limit fills at once at the real ask, not at the limit
      await p4.sleep(1200);
      const now = await pg.evaluate(() => OT.quotes.ETH);
      await pg.fill('input[aria-label="Limit price"]', String(Math.round(now.ask * 1.05 * 100) / 100));
      t = await ticket(pg);
      check(/Marketable: fills now at the real ask/.test(t.preview), 'marketable limit preview: ' + t.preview);
      await pg.click('.tk-submit'); await settle(pg);
      a = await acct(pg);
      check(a.fills.length === 2 && a.fills[0].basis === 'ask' && a.fills[0].px < now.ask * 1.02, 'a marketable buy limit should fill at the ask: ' + JSON.stringify(a.fills[0]));
      p4.errors().forEach(e => ctx.fail(e));
      await p4.close();
    });

    /* ════════════════════════ HELP ════════════════════════ */
    await section('help', async () => {
      await ctx.select('CL'); await ctx.go('MON');
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.keyboard.press('?'); await settle(page);
      check(await page.evaluate(() => OT.state.mod) === 'HELP', '"?" did not open HELP');
      await ctx.waitFor(() => document.querySelectorAll('.help-status tbody tr').length > 3 && !document.querySelector('.help-status .ot-state'), { timeout: 15000, msg: 'HELP loaded the gateway status' });
      const st = await getJson(ctx.base + '/api/status');
      const h = await page.evaluate(() => {
        const rowsOf = sel => Array.from(document.querySelectorAll(sel + ' tbody tr'));
        const cell = (tr, c) => { const td = tr.querySelector('td[data-col="' + c + '"]'); return td ? td.textContent : null; };
        return {
          inst: rowsOf('.help-insts').map(tr => ({ id: tr.dataset.key, chain: cell(tr, 'chain'), now: cell(tr, 'now'), want: OT.ui.quoteBadge(OT.quotes[tr.dataset.key]).textContent, bars: cell(tr, 'bars'), book: cell(tr, 'book'), opt: cell(tr, 'options'), paper: cell(tr, 'paper'),
            bookTip: tr.querySelector('td[data-col="book"] span').getAttribute('data-tip') })),
          mods: rowsOf('.help-mods').map(tr => ({ id: tr.dataset.key, key: Array.from(tr.querySelectorAll('td[data-col="key"] kbd')).map(k => k.textContent).join('+'), shows: cell(tr, 'shows'), forText: cell(tr, 'forText'), title: tr.querySelector('.mon-name').textContent })),
          modIds: OT.modules(), tabKeys: OT.modules().map(id => (/press ((?:Shift\+)?\d)/.exec(document.querySelector('#tabs .tab[data-mod="' + id + '"]').title) || [])[1]),
          feeds: rowsOf('.help-status .hs > div:nth-of-type(2)').map(tr => tr.dataset.key + '=' + cell(tr, 'state')), sources: rowsOf('.help-status .hs > div:nth-of-type(3)').map(tr => tr.dataset.key),
          badges: Array.from(document.querySelectorAll('.help-badges .hb-row')).map(r => ({ label: r.querySelector('.badge .b-f').textContent, now: r.querySelector('.hb-now').textContent })),
          cmds: Array.from(document.querySelectorAll('.help-keys .hk-cmd')).map(c => c.textContent), text: document.querySelector('.mod[data-mod="HELP"] .mod-body').textContent,
          why: document.querySelector('.help-why').textContent, paper: document.querySelector('.help-paper').textContent, keys: Array.from(document.querySelectorAll('.help-keys kbd')).map(k => k.textContent),
        };
      });
      // instruments: chain and delay exactly as /api/universe says
      check(h.inst.map(r => r.id).join() === allIds.join(), 'HELP instruments table: ' + h.inst.map(r => r.id).join());
      const NAME = { yahoo: 'Yahoo Finance', lse: 'LSE data', tiingo: 'Tiingo', alpaca: 'Alpaca', binance: 'Binance', cboe: 'CBOE', fred: 'FRED', treasury: 'Treasury.gov', bundesbank: 'Bundesbank' };
      for (const r of h.inst) {
        const u = uni.instruments[r.id], want = u.quote.sources.map(s => (NAME[s] || s) + ' ' + (u.quote.delays[s] || '')).join('  ›  ');
        check(r.chain === want, r.id + ': HELP chain ' + JSON.stringify(r.chain) + ' != universe ' + JSON.stringify(want));
        check(r.now === r.want, r.id + ': HELP "right now" badge ' + r.now + ' != the quote’s badge ' + r.want);
        check(u.candles.tfs.every(tf => r.bars.indexOf(tf) >= 0), r.id + ': HELP bars column ' + r.bars);
        check((r.book === 'yes') === !!u.caps.book && (r.opt === 'yes') === !!u.caps.options, r.id + ': HELP capabilities book=' + r.book + ' options=' + r.opt);
        if (!u.caps.book) check(r.bookTip === u.why.book, r.id + ': the reason for “no book” is not the gateway’s: ' + r.bookTip);
        check((r.paper === 'yes') === (['rate', 'index'].indexOf(u.cls) < 0), r.id + ': HELP paper column ' + r.paper);
      }
      for (const id of allIds) for (const cap of Object.keys(uni.instruments[id].why || {})) check(h.why.indexOf(uni.instruments[id].why[cap]) >= 0, 'HELP “not available” list lacks the gateway’s reason for ' + id + ' ' + cap);
      // modules: what is registered, with the keys the tabs really have
      check(h.mods.map(m => m.id).join() === h.modIds.join(), 'HELP modules ' + h.mods.map(m => m.id).join() + ' != registered ' + h.modIds.join());
      h.mods.forEach((m, i) => { check(m.key === (h.tabKeys[i] || ''), m.id + ': HELP key ' + m.key + ' != tab shortcut ' + h.tabKeys[i]); check(m.shows.length > 30, m.id + ': no description'); });
      check(/BTC ETH SOL/.test((h.mods.find(m => m.id === 'BOOK') || {}).forText || '') || /BEING REBUILT/.test((h.mods.find(m => m.id === 'BOOK') || {}).shows || ''), 'HELP does not say which instruments BOOK is available for');
      // gateway status: every source and feed the gateway reports
      check(h.sources.join() === Object.keys(st.sources).sort().join(), 'HELP sources ' + h.sources.join() + ' != /api/status ' + Object.keys(st.sources).sort().join());
      check(h.feeds.map(x => x.split('=')[0]).join() === Object.keys(st.feeds).sort().join(), 'HELP feeds ' + h.feeds.join() + ' != /api/status ' + Object.keys(st.feeds).sort().join());
      Object.keys(st.feeds).forEach(n => check(h.feeds.indexOf(n + '=' + String(st.feeds[n].state).toUpperCase()) >= 0, 'HELP feed state for ' + n + ': ' + h.feeds.join() + ' vs gateway ' + st.feeds[n].state));
      check(Object.keys(st.keys).filter(k => st.keys[k]).every(k => h.text.indexOf(k) >= 0), 'HELP does not list the configured key names');
      // badges: every class explained, and every instrument is in exactly one class right now
      const labels = h.badges.map(b => b.label);
      ['RT', 'D1', 'D10', 'D15', 'EOD', 'CLOSED', 'STALE', 'LAG', 'NO SOURCE', 'DERIVED'].forEach(l => check(labels.some(x => x.indexOf(l) === 0), 'HELP badge legend lacks ' + l));
      const nowIds = [].concat(...h.badges.filter(b => /^now: /.test(b.now) && b.now !== 'now: none').map(b => b.now.replace('now: ', '').split(' ')));
      check(nowIds.slice().sort().join() === allIds.slice().sort().join(), 'HELP “now” lists do not cover every instrument exactly once: ' + nowIds.join(' '));
      // commands listed are commands that work
      check(h.cmds.length >= 8, 'HELP lists only ' + h.cmds.length + ' command examples');
      for (const c of h.cmds) { const r = await page.evaluate(x => OT.parseCommand(x), c); check(r && !r.error && !r.empty, 'HELP lists the command ' + JSON.stringify(c) + ', which the command line rejects'); }
      check(/PAPER ONLY/.test(h.paper) && /market is closed/.test(h.paper) && /STALE/.test(h.paper) && /Yields/.test(h.paper), 'HELP paper rules incomplete');
      await ctx.assertNoBadText('.mod[data-mod="HELP"]', 'HELP'); await ctx.assertHeaderBadge('HELP');
      await ctx.shot('help');
      // the keys HELP documents do what it says (real keyboard)
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      const press = async (k, want, what) => { await page.keyboard.press(k); await settle(page); const s = await page.evaluate(() => OT.state.mod + '/' + OT.state.sel); check(s === want, 'key ' + what + ' → ' + s + ', HELP says ' + want); };
      await press('Digit1', 'MON/CL', '1'); await press('Digit2', 'CHRT/CL', '2'); await press('BracketRight', h.modIds[2] + '/CL', ']'); await press('BracketLeft', 'CHRT/CL', '[');
      const iHelp = h.modIds.indexOf('HELP');
      if (iHelp >= 10) await press('Shift+Digit' + ((iHelp - 9) % 10), 'HELP/CL', 'Shift+' + (iHelp - 9));
      await page.keyboard.press('Slash');
      check(await page.evaluate(() => document.activeElement && document.activeElement.id) === 'cmd', '"/" did not focus the command line');
      await page.keyboard.type('gold chrt'); await page.keyboard.press('Enter'); await settle(page);
      check(await page.evaluate(() => OT.state.mod + '/' + OT.state.sel) === 'CHRT/GC', 'command "gold chrt" → ' + await page.evaluate(() => OT.state.mod + '/' + OT.state.sel));
      // clicking a module row opens it; an instrument row selects it
      await ctx.go('HELP');
      await page.evaluate(() => document.querySelector('.help-mods tbody tr[data-key="TNT"]').scrollIntoView({ block: 'center' }));
      await ctx.mouse.click('.help-mods tbody tr[data-key="TNT"] td[data-col="id"]'); await settle(page);
      check(await page.evaluate(() => OT.state.mod) === 'TNT', 'clicking the TNT row in HELP did not open TNT');
      await ctx.go('HELP');
      await page.evaluate(() => document.querySelector('.help-insts tbody tr[data-key="NVDA"]').scrollIntoView({ block: 'center' }));
      await ctx.mouse.click('.help-insts tbody tr[data-key="NVDA"] td[data-col="chain"]'); await settle(page);
      check(await page.evaluate(() => OT.state.sel) === 'NVDA', 'clicking the NVDA row in HELP did not select it');
      await ctx.shotNow('help_instruments_1920x1080');
    });

    /* ════════════════════════ gateway stopped ════════════════════════ */
    await section('gateway offline', async () => {
      const target = new URL(ctx.base);
      const proxy = ctx.tcpProxy(+target.port, target.hostname);
      await proxy.start();
      const p5 = await ctx.open({ tag: 'offline', base: proxy.base });
      p5.allow(/ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_SOCKET|WebSocket connection|Failed to load resource|net::|\/api\/|\/ws/);
      const pg = p5.page;
      await p5.goto('CHRT/BTC');
      await p5.waitFor(() => OT.state.gateway === 'online' && OTM.chart.state().n > 1, { timeout: 20000, msg: 'chart loaded through the proxy' });
      const n0 = (await chartState(pg)).n;
      await proxy.stop();
      const off = await p5.waitFor(() => OT.state.gateway === 'offline', { timeout: 20000, msg: 'gateway offline detected' });
      if (off) {
        await p5.sleep(1500);
        const c = await pg.evaluate(() => ({ n: OTM.chart.state().n, filter: getComputedStyle(document.querySelector('.chrt-chart canvas')).filter, live: document.querySelector('.chrt-live').textContent, banner: !document.getElementById('offline').hidden }));
        check(c.n >= n0 && c.filter !== 'none' && c.banner, 'CHRT while the gateway is offline should keep its bars, greyed: ' + JSON.stringify(c));
        check(!/^LIVE/.test(c.live), 'CHRT still says LIVE while the gateway is offline: ' + c.live);
        await p5.shotNow('chrt_offline_1920x1080');
        await pg.evaluate(() => OT.go('TNT')); await settle(pg);
        const t = await ticket(pg);
        check(t.dis && /REFUSED/.test(t.preview) && /offline/.test(t.preview), 'TNT while the gateway is offline: ' + JSON.stringify(t));
        await pg.evaluate(() => OT.go('MON')); await settle(pg);
        const m = await pg.evaluate(() => ({ color: getComputedStyle(document.querySelector('.mon-table td[data-col="last"]')).color, side: getComputedStyle(document.querySelector('.ms-last')).color, t3: (() => { const d = document.createElement('i'); d.style.color = 'var(--text-3)'; document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; })() }));
        check(m.color === m.t3 && m.side === m.t3, 'MON values are not greyed while the gateway is offline: ' + JSON.stringify(m));
        await p5.assertContrast(4.5, '.mod[data-mod="MON"]', 'MON offline'); await p5.assertNoBadText(null, 'offline');
        await p5.shotNow('mon_offline_1920x1080');
      }
      await proxy.start();
      const back = await p5.waitFor(() => OT.state.gateway === 'online' && OT.state.stream === 'live', { timeout: 40000, msg: 'page back online after the gateway restarted' });
      if (back) {
        await pg.evaluate(() => OT.go('TNT'));
        await p5.waitFor(() => !document.querySelector('.tk-submit').disabled, { timeout: 15000, msg: 'TNT accepts orders again after the gateway is back' });
        const t0 = (await chartState(pg)).loadedAt;
        await pg.evaluate(() => OT.go('CHRT'));
        await p5.waitFor(t => OTM.chart.state().loadedAt > t && /LIVE|refetched/.test(document.querySelector('.chrt-live').textContent), { arg: t0, timeout: 30000, msg: 'CHRT refetched its bars after the gateway came back' });
      }
      p5.errors().forEach(e => ctx.fail(e));
      await p5.close();
      await proxy.stop();
    });

    await section('final', async () => {
      await ctx.setViewport(1920, 1080);
      await ctx.select('CL'); await ctx.go('TNT'); await ctx.go('HELP'); await ctx.go('CHRT'); await ctx.go('MON');
      await ctx.assertNoBadText(null, 'final'); await ctx.assertContrast(4.5, null, 'final'); await ctx.assertCanvasText(12, 'final');
      check(dialogs === 0, 'a native dialog was opened during the run');
    });
  },
};
