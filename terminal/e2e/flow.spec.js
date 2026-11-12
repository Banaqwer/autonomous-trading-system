/* Flow slice spec (S2): BOOK, HEAT, FOOT against the real gateway.
   What is on screen is compared with what the page RECEIVED (websocket `book` / `trade` frames and the answers of
   /api/book, /api/trades, /api/book/history, /api/footprint — recorded by the init script below), exactly as
   formatted; one value per route is also compared with the gateway asked directly and, where the network allows
   it, with Binance itself (curl through the proxy; the numbers are printed). Failure paths are provoked in
   second pages by altering answers / frames IN TRANSIT; the page under test never contains test data. */
'use strict';
const http = require('http');
const { execFile } = require('child_process');

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
/* Binance asked directly (through the machine's proxy). Resolves null when the network does not allow it. */
function binance(pathAndQuery) {
  return new Promise(res => {
    execFile('curl', ['-sS', '--max-time', '8', 'https://data-api.binance.vision/api/v3/' + pathAndQuery], { timeout: 10000 }, (err, out) => {
      if (err) return res(null);
      try { res(JSON.parse(out)); } catch (e) { res(null); }
    });
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const num = s => parseFloat(String(s).replace(/[,≥\s]/g, ''));
const CRYPTO = ['BTC', 'ETH', 'SOL'];
const M = m => '.mod[data-mod="' + m + '"]';

/* Injected into every page before any script: what the page receives for the flow modules. */
function init() {
  const R = window.__flow = { books: [], trades: [], rest: [], long: [] };
  const NativeWS = window.WebSocket;
  window.WebSocket = class extends NativeWS {
    constructor(...a) {
      super(...a);
      this.addEventListener('message', ev => {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === 'book') { R.books.push(m); if (R.books.length > 80) R.books.shift(); }
          else if (m.type === 'trade') { R.trades.push(m); if (R.trades.length > 6000) R.trades.splice(0, 1000); }
        } catch (e) { /* not ours */ }
      });
    }
  };
  const nativeFetch = window.fetch;
  window.fetch = function (input) {
    const p = nativeFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : input && input.url;
      if (url && /\/api\/(book|trades|footprint)/.test(url)) {
        p.then(r => (r.ok ? r.clone().json() : null)).then(d => { if (d) { R.rest.push({ url, t: Date.now(), d }); if (R.rest.length > 120) R.rest.shift(); } }).catch(() => {});
      }
    } catch (e) { /* ignore */ }
    return p;
  };
  try { new PerformanceObserver(l => l.getEntries().forEach(e => R.long.push({ t: Math.round(e.startTime), d: Math.round(e.duration) }))).observe({ type: 'longtask', buffered: true }); } catch (e) { /* not supported */ }
}

const frame = page => page.evaluate(() => new Promise(res => OT.requestFrame(() => setTimeout(res, 0))));
const tipText = page => page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.textContent : null; });

/* The ladder and the stats as displayed. */
const readBook = page => page.evaluate(() => {
  const sec = document.querySelector('.mod[data-mod="BOOK"]');
  const k = n => sec.querySelector('[data-k="' + n + '"]').textContent;
  return {
    bid: k('bid'), ask: k('ask'), spread: k('spread'), mid: k('mid'), imb: k('imb'),
    rows: Array.from(sec.querySelectorAll('.bk-row')).map(r => ({ cls: r.className, px: r.querySelector('.c-px').textContent, sz: r.querySelector('.c-sz').textContent,
      cum: r.querySelector('.c-cum').textContent, ntl: r.querySelector('.c-ntl').textContent, top: Math.round(r.getBoundingClientRect().top) })),
    tape: Array.from(sec.querySelectorAll('.bk-trow')).map(r => ({ cls: r.className, t: r.querySelector('.t-time').textContent, side: r.querySelector('.t-side').textContent,
      px: r.querySelector('.t-px').textContent, qty: r.querySelector('.t-qty').textContent, ntl: r.querySelector('.t-ntl').textContent, tag: r.querySelector('.t-tag').textContent })),
    group: Array.from(sec.querySelectorAll('.mod-head [data-g]')).map(b => ({ g: b.dataset.g, on: b.getAttribute('aria-selected') === 'true' })),
    follow: (Array.from(sec.querySelectorAll('.mod-head .btn')).find(b => b.textContent === 'FOLLOW') || {}).getAttribute ? Array.from(sec.querySelectorAll('.mod-head .btn')).find(b => b.textContent === 'FOLLOW').getAttribute('aria-pressed') : null,
    foot: sec.querySelector('.bk-foot').textContent, badge: sec.querySelector('.mod-head .badge').textContent, inst: sec.querySelector('.mod-inst-id').textContent,
    mark: sec.querySelector('.bk-mark-txt').textContent,
  };
});

module.exports = {
  name: 'flow', slice: 'flow', init,

  async run(ctx) {
    const { page, check } = ctx;
    // OT_E2E_SECTION=heat,foot runs only the sections whose name contains one of the words (debugging)
    const only = (process.env.OT_E2E_SECTION || '').toLowerCase().split(',').map(x => x.trim()).filter(Boolean);
    const section = async (name, fn) => {
      if (only.length && !only.some(w => name.toLowerCase().indexOf(w) >= 0)) return;
      try { await fn(); } catch (e) { ctx.fail('[' + name + '] threw: ' + (e && e.stack || e)); }
    };
    const uni = await getJson(ctx.base + '/api/universe');
    const withBook = Object.keys(uni.instruments).filter(id => uni.instruments[id].caps.book);
    check(withBook.join() === CRYPTO.join(), 'instruments with a book are ' + withBook.join());
    const notes = [];                                           // numbers for the report (printed at the end)

    /* ══════════════════════════════ BOOK ══════════════════════════════ */
    await section('BOOK truth', async () => {
      await ctx.select('BTC'); await ctx.go('BOOK');
      await ctx.waitFor(() => { const s = document.querySelector('.mod[data-mod="BOOK"]'); return s && s.querySelectorAll('.bk-row.bid').length > 3 && s.querySelectorAll('.bk-row.ask').length > 3 && !s.querySelector('.ot-state'); }, { timeout: 15000, msg: 'BOOK shows a ladder for BTC' });
      await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/book\?/.test(r.url) && /depth=1000/.test(r.url)) && window.__flow.rest.some(r => /\/api\/trades\?/.test(r.url)), { timeout: 15000, msg: 'the page asked /api/book?depth=1000 and /api/trades' });
      await ctx.assertHeaderBadge('BOOK');
      const b0 = await readBook(page);
      check(b0.badge.indexOf('RT') === 0 && /BINANCE/.test(b0.badge), 'BOOK badge is ' + b0.badge);
      check(b0.inst === 'BTC', 'BOOK header instrument is ' + b0.inst);

      // 1. best bid / ask / spread / mid = one of the book frames the websocket delivered (the ladder paints at most every 100 ms)
      const top = await page.evaluate(async () => {
        const out = [];
        for (let i = 0; i < 12; i++) {
          await new Promise(res => OT.requestFrame(() => setTimeout(res, 0)));
          const sec = document.querySelector('.mod[data-mod="BOOK"]'), k = n => sec.querySelector('[data-k="' + n + '"]').textContent;
          const shown = { bid: k('bid'), ask: k('ask'), spread: k('spread'), mid: k('mid') };
          const hit = window.__flow.books.filter(m => m.id === 'BTC').slice(-8).some(m => OT.fmt.px('BTC', m.bids[0][0]) === shown.bid && OT.fmt.px('BTC', m.asks[0][0]) === shown.ask &&
            OT.fmt.px('BTC', m.asks[0][0] - m.bids[0][0]) === shown.spread && OT.fmt.num((m.bids[0][0] + m.asks[0][0]) / 2, 3) === shown.mid);
          out.push({ shown, hit });
          await new Promise(res => setTimeout(res, 120));
        }
        return out;
      });
      const miss = top.filter(t => !t.hit);
      check(!miss.length, 'BOOK best bid/ask/spread/mid shown that no received book frame contains: ' + JSON.stringify(miss.slice(0, 2)));
      const first = top[0].shown.bid + top[0].shown.ask;
      await ctx.waitFor(f => { const sec = document.querySelector('.mod[data-mod="BOOK"]'); return sec.querySelector('[data-k="bid"]').textContent + sec.querySelector('[data-k="ask"]').textContent !== f; }, { arg: first, timeout: 20000, msg: 'BOOK best bid/ask changes within 20 s (alive)' });

      // 2. at one-tick grouping every live row is a level of ONE received frame, size for size
      await page.evaluate(() => document.querySelector('.mod[data-mod="BOOK"] [data-g="0.01"]').click());
      await frame(page);
      const lv = await page.evaluate(async () => {
        const res = [];
        for (let i = 0; i < 10; i++) {
          await new Promise(r => OT.requestFrame(() => setTimeout(r, 0)));
          const rows = Array.from(document.querySelectorAll('.mod[data-mod="BOOK"] .bk-row.live')).filter(r => !r.classList.contains('gap') && !r.classList.contains('beyond'))
            .map(r => ({ px: r.querySelector('.c-px').textContent, sz: r.querySelector('.c-sz').textContent, side: r.classList.contains('ask') ? 'asks' : 'bids' }));
          const frames = window.__flow.books.filter(m => m.id === 'BTC').slice(-8);
          const ok = frames.some(m => rows.every(r => { const l = m[r.side].find(x => OT.fmt.px('BTC', x[0]) === r.px); return r.sz === '·' ? !l : !!l && OTF.qty(l[1]) === r.sz; }));
          res.push({ n: rows.length, ok, sample: rows.slice(0, 3) });
          await new Promise(r => setTimeout(r, 150));
        }
        return res;
      });
      check(lv.every(x => x.n >= 4), 'one-tick ladder shows fewer than 4 live rows: ' + JSON.stringify(lv.map(x => x.n)));
      check(lv.filter(x => x.ok).length >= lv.length - 1, 'one-tick ladder: live rows are not the levels of a received frame in ' + lv.filter(x => !x.ok).length + ' of ' + lv.length + ' samples: ' + JSON.stringify(lv.find(x => !x.ok)));

      // 3. cumulative columns add up; every row is one-sided; asks above bids
      await page.evaluate(() => { const b = document.querySelectorAll('.mod[data-mod="BOOK"] [data-g]'); b[Math.min(2, b.length - 1)].click(); });
      await frame(page); await sleep(300);
      const b1 = await readBook(page);
      const asks = b1.rows.filter(r => /\bask\b/.test(r.cls)), bids = b1.rows.filter(r => /\bbid\b/.test(r.cls));
      check(asks.length >= 3 && bids.length >= 3, 'grouped ladder rows: ' + asks.length + ' asks, ' + bids.length + ' bids');
      check(asks.every(a => bids.every(b => num(a.px) > num(b.px))), 'an ask row is not above every bid row');
      check(asks.every((a, i) => !i || num(asks[i - 1].px) > num(a.px)) && bids.every((b, i) => !i || num(bids[i - 1].px) > num(b.px)), 'ladder prices are not strictly descending');
      const g = num(b1.group.find(x => x.on).g);
      check(b1.rows.every((r, i) => !i || Math.abs(num(b1.rows[i - 1].px) - num(r.px) - g) < 1e-6), 'ladder rows are not one price step (' + g + ') apart');
      const cumOk = side => side.every((r, i, a) => { if (/beyond/.test(r.cls) || /≥/.test(r.cum)) return true; const inner = side === asks ? a[i + 1] : a[i - 1]; if (!inner || /beyond/.test(inner.cls)) return true;
        const want = num(inner.cum) + (r.sz === '·' ? 0 : num(r.sz)); return Math.abs(num(r.cum) - want) <= Math.max(0.02, want * 0.002); });
      check(cumOk(bids) && cumOk(asks), 'cumulative size is not the running sum of the sizes: ' + JSON.stringify(bids.slice(0, 4)));
      notes.push('BOOK BTC shown ' + b1.bid + ' / ' + b1.ask + ' spread ' + b1.spread + ' mid ' + b1.mid + ' imbalance ' + b1.imb + ' group ' + g);

      // 4. the gateway asked directly, and Binance itself, at the same moment
      const [gw, bn] = await Promise.all([getJson(ctx.base + '/api/book?id=BTC&depth=5'), binance('ticker/bookTicker?symbol=BTCUSDT')]);
      const shown = await readBook(page);
      const midShown = (num(shown.bid) + num(shown.ask)) / 2;
      check(gw.id === 'BTC' && gw.source === 'binance' && gw.bids.length === 5 && gw.asks.length === 5 && gw.bids[0][0] < gw.asks[0][0], '/api/book?id=BTC&depth=5 answered ' + JSON.stringify(gw).slice(0, 200));
      check(Math.abs(gw.mid - midShown) / midShown < 0.0015, 'BOOK mid on screen ' + midShown + ' vs gateway /api/book ' + gw.mid + ' (more than 0.15 % apart)');
      if (bn && bn.bidPrice) {
        const bm = (parseFloat(bn.bidPrice) + parseFloat(bn.askPrice)) / 2;
        check(Math.abs(bm - midShown) / bm < 0.0015, 'BOOK mid on screen ' + midShown + ' vs Binance bookTicker ' + bm + ' (more than 0.15 % apart)');
        notes.push('BOOK vs upstream: screen ' + shown.bid + '/' + shown.ask + ' · gateway(' + gw.via + ') ' + gw.best_bid + '/' + gw.best_ask + ' · Binance REST ' + bn.bidPrice + '/' + bn.askPrice);
      } else notes.push('BOOK vs upstream: Binance could not be reached from the test (gateway ' + gw.best_bid + '/' + gw.best_ask + ', screen ' + shown.bid + '/' + shown.ask + ')');
    });

    await section('BOOK tape', async () => {
      // every print on screen is a trade the page received (stream frame or the /api/trades seed), field for field
      const r = await page.evaluate(async () => {
        await new Promise(res => OT.requestFrame(() => setTimeout(res, 0)));
        const rows = Array.from(document.querySelectorAll('.mod[data-mod="BOOK"] .bk-trow')).filter(x => x.querySelector('.t-px').textContent)
          .map(x => ({ t: x.querySelector('.t-time').textContent, side: x.querySelector('.t-side').textContent, px: x.querySelector('.t-px').textContent, qty: x.querySelector('.t-qty').textContent, ntl: x.querySelector('.t-ntl').textContent }));
        const seed = window.__flow.rest.filter(x => /\/api\/trades\?id=BTC/.test(x.url)).flatMap(x => x.d.trades);
        const known = new Set(window.__flow.trades.filter(m => m.id === 'BTC').concat(seed).map(m => {
          const d = new Date(m.t * 1000), ms = d.getUTCMilliseconds();
          return OT.fmt.utc(m.t).slice(0, 8) + '.' + String(ms).padStart(3, '0') + '|' + (m.buy ? 'BUY' : 'SELL') + '|' + OT.fmt.px('BTC', m.px) + '|' + OTF.qty(m.qty) + '|' + OTF.money(m.px * m.qty);
        }));
        const unknown = rows.filter(x => !known.has(x.t + '|' + x.side + '|' + x.px + '|' + x.qty + '|' + x.ntl));
        return { n: rows.length, unknown: unknown.slice(0, 3), nUnknown: unknown.length, seed: seed.length, ws: window.__flow.trades.length, times: rows.map(x => x.t) };
      });
      check(r.n >= 8, 'tape shows ' + r.n + ' prints');
      check(r.nUnknown === 0, r.nUnknown + ' tape row(s) are not a trade the page received: ' + JSON.stringify(r.unknown));
      check(r.times.every((t, i) => !i || r.times[i - 1] >= t), 'tape is not newest-first');
      check(r.seed >= 50, 'the tape was not seeded from /api/trades (' + r.seed + ' trades)');
      // the same prints at Binance: the gateway's /api/trades tail must be in Binance's own aggTrades
      const [gw, bn] = await Promise.all([getJson(ctx.base + '/api/trades?id=BTC&limit=20'), binance('aggTrades?symbol=BTCUSDT&limit=1000')]);
      check(gw.trades.length === 20 && gw.trades.every((t, i, a) => !i || a[i - 1].t <= t.t), '/api/trades?limit=20 answered ' + gw.trades.length + ' trades, ascending: ' + gw.trades.every((t, i, a) => !i || a[i - 1].t <= t.t));
      if (bn && Array.isArray(bn)) {
        const set = new Set(bn.map(t => t.T + '|' + parseFloat(t.p) + '|' + parseFloat(t.q) + '|' + !t.m));
        const inWindow = gw.trades.filter(t => Math.round(t.t * 1000) >= bn[0].T);
        const found = inWindow.filter(t => set.has(Math.round(t.t * 1000) + '|' + t.px + '|' + t.qty + '|' + t.buy)).length;
        check(inWindow.length >= 5 && found === inWindow.length, 'gateway trades not found in Binance aggTrades: ' + found + ' of ' + inWindow.length + ' (via ' + gw.via + ')');
        notes.push('TRADES vs upstream: ' + found + ' of ' + inWindow.length + ' gateway prints (via ' + gw.via + ') are identical Binance aggTrades (time, price, size, side)');
      } else notes.push('TRADES vs upstream: Binance could not be reached from the test');

      // LARGE marks follow the chosen threshold; the "Show" filter hides smaller prints
      const sel = M('BOOK') + ' .bk-tape-head select';
      await page.selectOption(sel + ' >> nth=1', '10000'); await frame(page);
      const marks = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="BOOK"] .bk-trow')).filter(x => x.querySelector('.t-px').textContent).map(x => {
        const ntl = parseFloat(x.querySelector('.t-px').textContent.replace(/,/g, '')) * parseFloat(x.querySelector('.t-qty').textContent.replace(/,/g, ''));
        return { ntl, large: x.classList.contains('large'), tag: x.querySelector('.t-tag').textContent };
      }));
      check(marks.every(m => (m.ntl >= 10300) ? m.large && m.tag === 'LARGE' : (m.ntl <= 9700 ? !m.large && !m.tag : true)), 'LARGE marks do not follow the 10K threshold: ' + JSON.stringify(marks.filter(m => m.large !== (m.ntl >= 10000)).slice(0, 3)));
      await page.selectOption(sel + ' >> nth=0', '1000'); await frame(page);
      const shown = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="BOOK"] .bk-trow')).filter(x => x.querySelector('.t-px').textContent).map(x =>
        parseFloat(x.querySelector('.t-px').textContent.replace(/,/g, '')) * parseFloat(x.querySelector('.t-qty').textContent.replace(/,/g, ''))));
      check(shown.every(v => v >= 990), 'Show ≥ 1K still lists smaller prints: ' + shown.filter(v => v < 990).slice(0, 3));
      await page.selectOption(sel + ' >> nth=0', '0'); await page.selectOption(sel + ' >> nth=1', '50000'); await frame(page);

      // hovering the tape pauses it; leaving resumes
      await ctx.mouse.hover(M('BOOK') + ' .bk-trow >> nth=3');
      const before = (await readBook(page)).tape.slice(0, 6).map(x => x.t + x.px + x.qty).join();
      await sleep(1500);
      const during = await readBook(page);
      check(during.tape.slice(0, 6).map(x => x.t + x.px + x.qty).join() === before, 'the tape moved under the pointer (it should pause)');
      check(await page.evaluate(() => { const p = document.querySelector('.mod[data-mod="BOOK"] .bk-pause'); return !p.hidden && /PAUSED/.test(p.textContent); }), 'no PAUSED notice while the pointer is on the tape');
      await page.mouse.move(700, 30); await sleep(1200);
      const after = await readBook(page);
      check(after.tape.slice(0, 6).map(x => x.t + x.px + x.qty).join() !== before, 'the tape did not resume after the pointer left (BTC prints several times a second)');
      check(await page.evaluate(() => document.querySelector('.mod[data-mod="BOOK"] .bk-pause').hidden), 'PAUSED notice still shown after the pointer left');
    });

    await section('BOOK interaction', async () => {
      // rows are stable: the node under the pointer stays the same node with the same price, sizes change in place
      const rowSel = M('BOOK') + ' .bk-row.bid >> nth=2';
      await ctx.mouse.hover(rowSel);
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /Cumulative from the best bid/.test(t.textContent); }, { msg: 'ladder row tooltip with the cumulative notional' });
      const tip = await tipText(page);
      check(/USDT/.test(tip) && /% from the mid/.test(tip) && /Resting here/.test(tip), 'ladder tooltip: ' + JSON.stringify(tip));
      const stable = await page.evaluate(async () => {
        const at = () => document.elementFromPoint(window.__mx, window.__my);
        const rows = () => Array.from(document.querySelectorAll('.mod[data-mod="BOOK"] .bk-row'));
        const first = rows(), px0 = first.map(r => r.querySelector('.c-px').textContent), sz0 = first.map(r => r.querySelector('.c-sz').textContent);
        let pxChanges = 0, szChanges = 0, sameNodes = true, prevPx = px0.join(), prevSz = sz0.join();
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 100));
          const now = rows();
          if (now.length !== first.length || now.some((r, j) => r !== first[j])) sameNodes = false;
          const px = now.map(r => r.querySelector('.c-px').textContent).join(), sz = now.map(r => r.querySelector('.c-sz').textContent).join();
          if (px !== prevPx) { pxChanges++; prevPx = px; }
          if (sz !== prevSz) { szChanges++; prevSz = sz; }
        }
        return { pxChanges, szChanges, sameNodes };
      });
      check(stable.sameNodes, 'ladder row nodes were replaced within 3 s (rows must be patched in place)');
      check(stable.pxChanges <= 3, 'the price grid changed ' + stable.pxChanges + ' times in 3 s (the ladder jumps)');
      check(stable.szChanges >= 2, 'no size changed in 3 s (' + stable.szChanges + '): the ladder looks dead');
      notes.push('BOOK stability over 3 s at 10 Hz: price grid changed ' + stable.pxChanges + 'x, sizes changed ' + stable.szChanges + 'x, same DOM nodes: ' + stable.sameNodes);

      // wheel scrolls the grid and turns FOLLOW off; double-click returns
      const b0 = await readBook(page);
      await ctx.mouse.wheel(M('BOOK') + ' .bk-rows', 240, 2); await frame(page);
      const b1 = await readBook(page);
      check(num(b1.rows[0].px) < num(b0.rows[0].px), 'wheel over the ladder did not scroll the price grid (' + b0.rows[0].px + ' → ' + b1.rows[0].px + ')');
      check(b1.follow === 'false' && /FOLLOW is off/.test(b1.foot), 'scrolling the ladder did not turn FOLLOW off (aria-pressed ' + b1.follow + ')');
      await ctx.mouse.wheel(M('BOOK') + ' .bk-rows', 240, 14); await frame(page);
      const b2 = await readBook(page);
      check(/MARKET/.test(b2.mark), 'market out of view: the marker should say where it is, got ' + JSON.stringify(b2.mark));
      await page.mouse.dblclick((await ctx.mouse.point(M('BOOK') + ' .bk-rows')).x, (await ctx.mouse.point(M('BOOK') + ' .bk-rows')).y); await frame(page);
      const b3 = await readBook(page);
      check(b3.follow === 'true' && /SPREAD/.test(b3.mark) && b3.rows.some(r => /\bask\b/.test(r.cls)) && b3.rows.some(r => /\bbid\b/.test(r.cls)), 'double-click did not bring the ladder back to the market: ' + b3.mark);

      // grouping: the choice is applied, and it survives a switch to another instrument and back
      const opts = b3.group.map(x => x.g);
      const pick = opts[opts.length - 1];
      await page.evaluate(g => document.querySelector('.mod[data-mod="BOOK"] [data-g="' + g + '"]').click(), pick); await frame(page);
      const b4 = await readBook(page);
      check(b4.rows.every(r => Math.abs(num(r.px) / +pick - Math.round(num(r.px) / +pick)) < 1e-6), 'group ' + pick + ': a row price is not a multiple of it');
      await ctx.select('SOL'); await ctx.waitFor(() => document.querySelectorAll('.mod[data-mod="BOOK"] .bk-row.bid').length > 2 && parseFloat(document.querySelector('.mod[data-mod="BOOK"] [data-k="bid"]').textContent.replace(/,/g, '')) < 1000, { timeout: 15000, msg: 'BOOK shows SOL after the switch' });
      const sol = await readBook(page);
      check(sol.inst === 'SOL' && sol.rows.every(r => num(r.px) < 1000) && sol.tape.filter(t => t.px).every(t => num(t.px) < 1000), 'after switching to SOL a BTC price is still on screen: ' + sol.inst + ' rows ' +
        JSON.stringify(sol.rows.filter(r => !(num(r.px) < 1000)).slice(0, 3)) + ' tape ' + JSON.stringify(sol.tape.filter(t => t.px && !(num(t.px) < 1000)).slice(0, 3)));
      const solTruth = await page.evaluate(() => { const sec = document.querySelector('.mod[data-mod="BOOK"]'), bid = sec.querySelector('[data-k="bid"]').textContent;
        return window.__flow.books.filter(m => m.id === 'SOL').slice(-10).some(m => OT.fmt.px('SOL', m.bids[0][0]) === bid); });
      check(solTruth, 'SOL best bid on screen is not in a received SOL book frame');
      await ctx.select('BTC'); await ctx.waitFor(() => parseFloat(document.querySelector('.mod[data-mod="BOOK"] [data-k="bid"]').textContent.replace(/,/g, '')) > 1000, { timeout: 15000, msg: 'BOOK shows BTC again' });
      await frame(page); await sleep(400);
      const back = await readBook(page);
      check((back.group.find(x => x.on) || {}).g === pick, 'grouping ' + pick + ' did not survive the instrument switch (now ' + JSON.stringify(back.group.find(x => x.on)) + ')');
      check(back.tape.filter(t => t.px).every(t => num(t.px) > 1000), 'a SOL print is in the BTC tape after switching back');
      await page.evaluate(() => { const b = document.querySelectorAll('.mod[data-mod="BOOK"] [data-g]'); b[Math.min(2, b.length - 1)].click(); });

      // fast switching: the last instrument wins, nothing of the others is left
      for (let i = 0; i < 12; i++) await page.evaluate(id => OT.select(id), CRYPTO[i % 3]);
      await page.evaluate(() => OT.select('ETH'));
      await ctx.waitFor(() => { const s = document.querySelector('.mod[data-mod="BOOK"]'), v = parseFloat(s.querySelector('[data-k="bid"]').textContent.replace(/,/g, '')); return s.querySelector('.mod-inst-id').textContent === 'ETH' && v > 200 && v < 20000 && !s.querySelector('.ot-state'); }, { timeout: 15000, msg: 'BOOK shows ETH after fast switching' });
      await sleep(500);
      const eth = await readBook(page);
      const mid = num(eth.bid);
      check(eth.rows.every(r => Math.abs(num(r.px) - mid) / mid < 0.2) && eth.tape.filter(t => t.px).every(t => Math.abs(num(t.px) - mid) / mid < 0.2), 'after fast switching, ETH BOOK shows a price of another instrument: rows ' +
        JSON.stringify(eth.rows.filter(r => !(Math.abs(num(r.px) - mid) / mid < 0.2)).slice(0, 3)) + ' tape ' + JSON.stringify(eth.tape.filter(t => t.px && !(Math.abs(num(t.px) - mid) / mid < 0.2)).slice(0, 3)));
      await ctx.shot('flow_book_ETH');
      await ctx.select('BTC');
      await ctx.waitFor(() => parseFloat(document.querySelector('.mod[data-mod="BOOK"] [data-k="bid"]').textContent.replace(/,/g, '')) > 20000 && document.querySelectorAll('.mod[data-mod="BOOK"] .bk-row.bid').length > 3, { timeout: 15000, msg: 'BOOK shows BTC' });
      await sleep(1200);
      await ctx.shot('flow_book_BTC');
      await ctx.assertNoOverflow('BOOK');
    });

    await section('no source', async () => {
      // an instrument without a book: the core's NO LIVE SOURCE state, and the page does not even ask the flow routes
      const n0 = await page.evaluate(() => window.__flow.rest.length);
      for (const m of ['BOOK', 'HEAT', 'FOOT']) {
        await ctx.select('CL'); await ctx.go(m);
        const st = await page.evaluate(mod => { const s = document.querySelector('.mod[data-mod="' + mod + '"] .mod-cover:not([hidden]) .ot-state'); return s ? { kind: s.dataset.kind, text: s.textContent, picks: Array.from(s.querySelectorAll('[data-pick]')).map(b => b.dataset.pick) } : null; }, m);
        check(st && st.kind === 'nosource' && st.picks.join() === CRYPTO.join() && /NO LIVE SOURCE/.test(st.text), m + ' for CL: ' + JSON.stringify(st));
      }
      await sleep(600);
      const asked = await page.evaluate(n => window.__flow.rest.slice(n).map(r => r.url), n0);
      check(!asked.some(u => /id=CL/.test(u)), 'a flow route was asked for CL: ' + asked.join());
      const topics = await page.evaluate(() => OT.topics());
      check(!topics.some(t => /:CL$/.test(t)), 'subscribed to a CL flow topic: ' + topics.join());
      for (const p of ['/api/book?id=CL', '/api/trades?id=AAPL', '/api/book/history?id=EURUSD', '/api/footprint?id=SPX']) {
        const r = await getJson(ctx.base + p);
        check(r.error && r.error.code === 'no_source' && /BTC · ETH · SOL/.test(r.error.message), p + ' → ' + JSON.stringify(r).slice(0, 160));
      }
      const bad = await getJson(ctx.base + '/api/book?id=DOGE');
      check(bad.error && bad.error.code === 'bad_request', '/api/book?id=DOGE → ' + JSON.stringify(bad).slice(0, 120));
      await ctx.select('BTC');
    });

    /* ══════════════════════════════ HEAT ══════════════════════════════ */
    const heatCanvas = M('HEAT') + ' .fl-canvas canvas';
    const heatTip = async (fx, fy) => {                      // hover at a fraction of the plot; returns the parsed read-out
      const box = await page.locator(heatCanvas).boundingBox();
      const x = box.x + (box.width - 96) * fx, y = box.y + (box.height - 26) * fy;
      await page.mouse.move(x - 3, y - 3); await page.mouse.move(x, y, { steps: 2 });
      await frame(page); await sleep(60);
      const t = await tipText(page);
      if (!t) return null;
      const L = t.split('\n');
      const pr = /Price ([\d,.]+) – ([\d,.]+)/.exec(t), sz = /Resting size ([\d,.KM]+)/.exec(t), snap = /snapshot of (\d\d:\d\d:\d\dZ)/.exec(t);
      return { text: t, time: L[0].slice(0, 9), lo: pr ? num(pr[1]) : null, hi: pr ? num(pr[2]) : null, size: sz ? sz[1] : null, snap: snap ? snap[1] : null, top: /best 20 levels/.test(t), none: /not sampled/.test(t) };
    };
    await section('HEAT', async () => {
      await ctx.select('BTC'); await ctx.go('HEAT');
      await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/book\/history\?id=BTC/.test(r.url) && r.d.deep.length > 0) && !document.querySelector('.mod[data-mod="HEAT"] .mod-body > .ot-state'), { timeout: 20000, msg: 'HEAT loaded BTC history with at least one deep column' });
      await sleep(1500);
      await ctx.assertHeaderBadge('HEAT');
      const info = await page.evaluate(() => {
        const sec = document.querySelector('.mod[data-mod="HEAT"]');
        const cv = sec.querySelector('.fl-canvas canvas'), g = cv.getContext('2d'), d = g.getImageData(0, 0, cv.width, cv.height).data;
        const seen = new Set(); for (let i = 0; i < d.length; i += 4 * 37) seen.add((d[i] >> 3) + ',' + (d[i + 1] >> 3) + ',' + (d[i + 2] >> 3));
        return { colours: seen.size, legend: sec.querySelector('.ht-legend').textContent, note: sec.querySelector('.ht-note').textContent, badge: sec.querySelector('.mod-head .badge').textContent,
          hist: window.__flow.rest.filter(r => /\/api\/book\/history\?id=BTC/.test(r.url)).map(r => ({ deep: r.d.deep.length, top: r.d.top.length, step: r.d.step, rec: r.d.recording })) };
      });
      check(info.colours > 12, 'HEAT canvas has only ' + info.colours + ' distinct colours: nothing drawn?');
      check(/Resting size, BTC per [\d.]+ USDT of price/.test(info.legend) && /\d\+/.test(info.legend) && /not sampled/.test(info.legend) && /mid price/.test(info.legend) && /bought/.test(info.legend), 'HEAT legend lacks units / keys: ' + JSON.stringify(info.legend.slice(0, 200)));
      check(/since \d\d:\d\d:\d\dZ/.test(info.note) && /30 min/.test(info.note) && /every 5 s/.test(info.note), 'HEAT does not say how much history is held and since when: ' + JSON.stringify(info.note));
      check(/RT/.test(info.badge) && /BINANCE/.test(info.badge), 'HEAT badge ' + info.badge);
      const rec = info.hist[info.hist.length - 1].rec;

      // read-outs = the recorded data. Hover a grid of points; each read-out must be a value the page received.
      let checked = 0, matched = 0, kinds = { snap: 0, top: 0, none: 0 }, firstBad = null;
      for (const fx of [0.55, 0.7, 0.8, 0.9, 0.96]) for (const fy of [0.15, 0.3, 0.42, 0.5, 0.58, 0.7, 0.85]) {
        const r = await heatTip(fx, fy);
        if (!r || r.lo === null) continue;
        if (r.none) { kinds.none++; continue; }
        checked++;
        const ok = await page.evaluate(q => {
          const hist = window.__flow.rest.filter(x => /\/api\/book\/history\?id=BTC/.test(x.url)).map(x => x.d);
          const step = hist[0].step, k = Math.round(q.lo / step);
          const fits = (k0, arr) => k0 !== null && k >= k0 && k < k0 + arr.length && OTF.qty(arr[k - k0]) === q.size;
          if (q.snap) return hist.some(d => d.deep.some(c => OT.fmt.utc(c.t) === q.snap && fits(c.k0, c.q)));
          // the stream's own second: the gateway's `top` row of that second, or the frames the page folded itself
          if (hist.some(d => d.top.some(r => OT.fmt.utc(r[0]).slice(0, 8) === q.time.slice(0, 8) && fits(r[3], r[4])))) return true;
          const st = Math.round(step * 100);
          return window.__flow.books.filter(m => m.id === 'BTC' && OT.fmt.utc(m.t).slice(0, 8) === q.time.slice(0, 8)).some(m => {
            let v = 0; for (const side of [m.bids, m.asks]) for (const l of side) if (Math.floor(Math.round(l[0] * 100) / st) === k) v += l[1];
            return OTF.qty(v) === q.size;
          });
        }, r);
        if (ok) matched++; else if (!firstBad) firstBad = r.text;
        kinds[r.snap ? 'snap' : 'top']++;
      }
      check(checked >= 8, 'HEAT: only ' + checked + ' read-outs with a size out of 35 hover points (none: ' + kinds.none + ')');
      check(matched >= checked - 1, 'HEAT read-out shows a size the page never received: ' + matched + ' of ' + checked + ' matched; e.g. ' + JSON.stringify(firstBad));
      notes.push('HEAT read-outs: ' + matched + ' of ' + checked + ' equal a received value (' + kinds.snap + ' deep snapshot, ' + kinds.top + ' stream second), ' + kinds.none + ' "not sampled"');
      const one = await heatTip(0.9, 0.5);
      check(one && /^\d\d:\d\d:\d\dZ/.test(one.text) && /Price [\d,.]+ – [\d,.]+ USDT/.test(one.text) && /Mid then/.test(one.text), 'HEAT read-out lacks time / price / mid: ' + JSON.stringify(one && one.text));

      // the gateway asked directly: the newest deep column's touch is the real market, and Binance's own depth agrees on the walls
      const [gw, bn] = await Promise.all([getJson(ctx.base + '/api/book/history?id=BTC&since=' + (Date.now() / 1000 - 30).toFixed(0)), binance('depth?symbol=BTCUSDT&limit=1000')]);
      const col = gw.deep[gw.deep.length - 1];
      check(!!col && col.q.length > 50 && gw.step > 0 && col.bid < col.ask, '/api/book/history: newest deep column ' + JSON.stringify(col).slice(0, 120));
      if (col && bn && bn.bids) {
        const mid = (parseFloat(bn.bids[0][0]) + parseFloat(bn.asks[0][0])) / 2;
        check(Math.abs((col.bid + col.ask) / 2 - mid) / mid < 0.003, 'history column mid ' + (col.bid + col.ask) / 2 + ' vs Binance depth now ' + mid);
        // same buckets from Binance's reply taken a few seconds later: the big walls must be there (sizes move, walls stay)
        const st = Math.round(gw.step * 100), sums = new Map();
        for (const side of [bn.bids, bn.asks]) for (const l of side) { const k = Math.floor(Math.round(parseFloat(l[0]) * 100) / st); sums.set(k, (sums.get(k) || 0) + parseFloat(l[1])); }
        const idx = col.q.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, 8).filter(x => sums.has(col.k0 + x[1]) && Math.abs((col.k0 + x[1]) * gw.step - mid) / mid > 0.0004);
        const near = idx.filter(x => { const v = sums.get(col.k0 + x[1]); return v > x[0] * 0.5 && v < x[0] * 2; }).length;
        check(idx.length < 3 || near >= Math.ceil(idx.length * 0.6), 'the largest buckets of the newest history column are not in Binance’s own depth: ' + near + ' of ' + idx.length);
        notes.push('HEAT vs upstream: newest deep column (t ' + col.t + ') mid ' + ((col.bid + col.ask) / 2).toFixed(2) + ' vs Binance depth mid ' + mid.toFixed(2) + '; ' + near + ' of its ' + idx.length + ' largest buckets (away from the touch) are within 0.5–2x in Binance’s own 1000-level depth');
      } else notes.push('HEAT vs upstream: Binance could not be reached from the test');

      // wheel = zoom time (the left edge moves later), wheel on the price scale = zoom price, drag = move (FOLLOW off), double-click = reset
      await ctx.shot('flow_heat_BTC');
      const leftBefore = await heatTip(0.02, 0.5);
      const box = await page.locator(heatCanvas).boundingBox();
      await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
      for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, -300); await sleep(40); }
      await frame(page);
      const leftAfter = await heatTip(0.02, 0.5);
      check(leftBefore && leftAfter && leftAfter.time > leftBefore.time, 'wheel did not zoom the time axis: left edge ' + (leftBefore && leftBefore.time) + ' → ' + (leftAfter && leftAfter.time));
      const topBefore = await heatTip(0.5, 0.03);
      await page.mouse.move(box.x + box.width - 40, box.y + box.height * 0.5);
      for (let i = 0; i < 5; i++) { await page.mouse.wheel(0, -300); await sleep(40); }
      await frame(page);
      const topAfter = await heatTip(0.5, 0.03);
      check(topBefore && topAfter && topAfter.lo < topBefore.lo, 'wheel on the price scale did not zoom the price axis: top ' + (topBefore && topBefore.lo) + ' → ' + (topAfter && topAfter.lo));
      const follow = () => page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="HEAT"] .mod-head .btn')).find(b => b.textContent === 'FOLLOW').getAttribute('aria-pressed'));
      check(await follow() === 'true', 'HEAT FOLLOW should still be on after zooming');
      await ctx.shot('flow_heat_BTC_zoom');
      const midBefore = await heatTip(0.5, 0.5);
      await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
      await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.5 + 160, box.y + box.height * 0.5 + 60, { steps: 6 }); await page.mouse.up();
      await frame(page);
      const midAfter = await heatTip(0.5, 0.5);
      check(await follow() === 'false', 'dragging the heatmap did not turn FOLLOW off');
      check(midBefore && midAfter && midAfter.time < midBefore.time && midAfter.lo > midBefore.lo, 'drag did not move the view: ' + (midBefore && midBefore.time + ' ' + midBefore.lo) + ' → ' + (midAfter && midAfter.time + ' ' + midAfter.lo));
      await page.mouse.dblclick(box.x + box.width * 0.5, box.y + box.height * 0.5); await frame(page);
      check(await follow() === 'true', 'double-click did not reset the heatmap view (FOLLOW still off)');
      const leftReset = await heatTip(0.02, 0.5);
      check(leftReset && leftBefore && leftReset.time <= leftAfter.time, 'reset did not restore the time span');
      await page.mouse.move(700, 30);

      // alive: the canvas changes while nothing is touched
      const sig = () => page.evaluate(() => { const cv = document.querySelector('.mod[data-mod="HEAT"] .fl-canvas canvas'), d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data; let h = 0; for (let i = 0; i < d.length; i += 4 * 53) h = (h * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) >>> 0; return h; });
      const s1 = await sig(); await sleep(2500); const s2 = await sig();
      check(s1 !== s2, 'the heatmap did not change in 2.5 s (dead?)');

      // history survives leaving the module, another instrument, and a page reload (the gateway holds it)
      await ctx.go('MON'); await ctx.go('HEAT'); await frame(page);
      check(await page.evaluate(() => !document.querySelector('.mod[data-mod="HEAT"] .mod-body > .ot-state')), 'HEAT shows a loading state again after switching away and back (the history should be kept)');
      await ctx.select('SOL');
      await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/book\/history\?id=SOL/.test(r.url) && r.d.deep.length > 0) && /SOL per/.test(document.querySelector('.mod[data-mod="HEAT"] .ht-legend').textContent), { timeout: 20000, msg: 'HEAT loaded SOL' });
      const solTip = await heatTip(0.95, 0.5);
      check(solTip && solTip.lo !== null && solTip.lo < 1000, 'HEAT read-out for SOL shows a price of ' + (solTip && solTip.lo));
      await ctx.select('BTC'); await frame(page);
      await page.reload({ waitUntil: 'domcontentloaded' }); await ctx.ready();
      await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/book\/history\?id=BTC/.test(r.url) && r.d.deep.length > 0), { timeout: 20000, msg: 'HEAT reloaded BTC history after a page reload' });
      const rec2 = await page.evaluate(() => { const all = window.__flow.rest.filter(r => /\/api\/book\/history\?id=BTC/.test(r.url)); return { rec: all[all.length - 1].d.recording, n: all.reduce((a, r) => a + r.d.deep.length, 0), mod: OT.state.mod, sel: OT.state.sel }; });
      check(rec2.mod === 'HEAT' && rec2.sel === 'BTC', 'after reload the page is on ' + rec2.mod + '/' + rec2.sel);
      // (a history that already fills the 30 minutes loses its oldest columns as time passes: its start may only move with the window)
      const windowStart = Date.now() / 1000 - 1800;
      check(rec2.rec.deep_since !== null && rec.deep_since !== null && rec2.rec.deep_since <= Math.max(rec.deep_since + 1, windowStart + 90) && rec2.n >= 3, 'history did not survive the page reload: deep_since ' + rec.deep_since + ' → ' + rec2.rec.deep_since + ', ' + rec2.n + ' columns');
      notes.push('HEAT history after a page reload: deep columns held since ' + new Date(rec2.rec.deep_since * 1000).toISOString().slice(11, 19) + 'Z (' + rec2.n + ' columns fetched), best-20 since ' + new Date(rec2.rec.top_since * 1000).toISOString().slice(11, 19) + 'Z');
      await ctx.assertNoOverflow('HEAT');
    });

    /* ══════════════════════════════ FOOT ══════════════════════════════ */
    const footCanvas = M('FOOT') + ' .fl-canvas canvas';
    const footState = () => page.evaluate(() => {
      const sec = document.querySelector('.mod[data-mod="FOOT"]');
      const all = window.__flow.rest.filter(r => /\/api\/footprint\?id=/.test(r.url));
      const sel = sec.querySelector('.mod-head select');
      return { cap: sec.querySelector('.fl-strip').textContent, note: sec.querySelector('.ht-note').textContent, step: sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '', stepOpts: Array.from(sel.options).map(o => o.value),
        tf: (sec.querySelector('.mod-head .ot-tab[aria-selected="true"]') || {}).textContent, follow: Array.from(sec.querySelectorAll('.mod-head .btn')).find(b => b.textContent === 'FOLLOW').getAttribute('aria-pressed'),
        last: all.length ? all[all.length - 1] : null, n: all.length, loading: !!sec.querySelector('.mod-body > .ot-state') };
    });
    await section('FOOT', async () => {
      await ctx.select('BTC'); await ctx.go('FOOT');
      await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/footprint\?id=BTC/.test(r.url) && r.d.bars.length > 0) && !document.querySelector('.mod[data-mod="FOOT"] .mod-body > .ot-state'), { timeout: 25000, msg: 'FOOT loaded BTC bars' });
      await sleep(800);
      await ctx.assertHeaderBadge('FOOT');
      const s0 = await footState();
      check(/volume sold × bought \(BTC\) within [\d.]+ USDT of price/.test(s0.cap) && /POC/.test(s0.cap) && /value area/.test(s0.cap), 'FOOT caption lacks units / keys: ' + JSON.stringify(s0.cap.slice(0, 160)));
      check(/Tape held since \d\d:\d\d:\d\dZ/.test(s0.note) && /keeps 6 h/.test(s0.note), 'FOOT does not say how much tape is held: ' + JSON.stringify(s0.note));
      check(s0.tf === '1m' && /^auto \(/.test(s0.step) && s0.stepOpts.length >= 4, 'FOOT controls: tf ' + s0.tf + ', step ' + s0.step + ', options ' + s0.stepOpts.join());
      await ctx.shot('flow_foot_BTC');

      // a cell on screen = the trades the page received. Hover the POC row of closed bars and compare with the payload
      // (the gateway's fold at the finest step, folded here to the display step).
      const box = await page.locator(footCanvas).boundingBox();
      const plotW = box.width - 96;
      const stepShown = num(/\(([\d.]+)\)/.exec(s0.step)[1]);
      let cells = 0, cellOk = 0, bad = null, bars = 0, barOk = 0;
      for (const fx of [0.3, 0.45, 0.6, 0.75]) for (const fy of [0.3, 0.4, 0.5, 0.6, 0.7]) {
        await page.mouse.move(box.x + plotW * fx - 2, box.y + 20 + (box.height - 106) * fy); await page.mouse.move(box.x + plotW * fx, box.y + 20 + (box.height - 106) * fy, { steps: 2 });
        await frame(page); await sleep(50);
        const t = await tipText(page);
        if (!t) continue;
        const r = await page.evaluate(([text, step]) => {
          const all = window.__flow.rest.filter(x => /\/api\/footprint\?id=BTC/.test(x.url) && /tf=1m/.test(x.url));
          const hm = /(\d\d:\d\d) – /.exec(text), pr = /Price ([\d,.]+) – /.exec(text), sb = /Sold ([\d,.KM<]+) × bought ([\d,.KM<]+)/.exec(text), vol = /Volume ([\d,.KM]+) BTC \(bought ([\d,.KM]+) · sold ([\d,.KM]+)\) in ([\d,]+) prints/.exec(text);
          if (!hm) return null;
          // the newest answer that contains this bar
          let bar = null;
          for (let i = all.length - 1; i >= 0 && !bar; i--) bar = all[i].d.bars.find(b => OT.fmt.hm(b.t).slice(0, 5) === hm[1]) || null;
          if (!bar || /\(open\)/.test(text)) return { skip: true };
          const out = { skip: false, bar: !!vol && OTF.qty(bar.vol) === vol[1] && OTF.qty(bar.buy) === vol[2] && OTF.qty(bar.sell) === vol[3] && OT.fmt.num(bar.n, 0) === vol[4], hasVol: !!vol, cell: null };
          if (pr) {
            const lo = parseFloat(pr[1].replace(/,/g, '')), st = Math.round(step * 100);
            let s = 0, b = 0;
            for (const c of bar.cells) if (Math.floor(Math.round(c[0] * 100) / st) * st === Math.round(lo * 100)) { s += c[1]; b += c[2]; }
            out.cell = sb ? OTF.qty(s) === sb[1] && OTF.qty(b) === sb[2] : (s + b === 0 && /No trade at this price/.test(text));
            out.want = OTF.qty(s) + ' × ' + OTF.qty(b);
          }
          return out;
        }, [t, stepShown]);
        if (!r || r.skip) continue;
        if (r.hasVol) { bars++; if (r.bar) barOk++; else if (!bad) bad = t; }
        if (r.cell !== null) { cells++; if (r.cell) cellOk++; else if (!bad) bad = t + ' (payload: ' + r.want + ')'; }
      }
      check(cells >= 8 && cellOk === cells, 'FOOT cell read-out differs from the payload the page received: ' + cellOk + ' of ' + cells + ' — ' + JSON.stringify(bad));
      check(bars >= 6 && barOk === bars, 'FOOT bar totals differ from the payload: ' + barOk + ' of ' + bars + ' — ' + JSON.stringify(bad));
      notes.push('FOOT read-outs: ' + cellOk + ' of ' + cells + ' cells and ' + barOk + ' of ' + bars + ' bar totals equal the received /api/footprint payload (closed bars)');

      // the gateway asked directly + Binance's own 1m klines: a complete bar's volume and taker-buy volume are the kline's
      const [fp, kl] = await Promise.all([getJson(ctx.base + '/api/footprint?id=BTC&tf=1m&limit=8&step=' + s0.last.d.base_step), binance('klines?symbol=BTCUSDT&interval=1m&limit=10')]);
      const closed = fp.bars.filter(b => !b.open && !b.partial && b.n > 0);
      check(fp.bars.length >= 2 && fp.bars.every(b => Math.abs(b.buy + b.sell - b.vol) < 1e-5 && Math.abs(b.buy - b.sell - b.delta) < 1e-5 && Math.abs(b.cells.reduce((a, c) => a + c[1] + c[2], 0) - b.vol) < 1e-4), '/api/footprint bars are not self-consistent (vol = buy + sell = sum of the cells)');
      check(closed.length >= 1, '/api/footprint has no complete closed bar among the last 8: ' + JSON.stringify(fp.coverage));
      if (kl && Array.isArray(kl) && closed.length) {
        let cmp = 0, same = 0; const ex = [];
        for (const b of closed) {
          const k = kl.find(x => x[0] === b.t * 1000);
          if (!k) continue;
          cmp++;
          const okv = Math.abs(parseFloat(k[5]) - b.vol) < 1e-4, okb = Math.abs(parseFloat(k[9]) - b.buy) < 1e-4, okn = k[8] >= b.n;
          if (okv && okb && okn) same++;
          ex.push(new Date(b.t * 1000).toISOString().slice(11, 16) + ' vol ' + b.vol + '/' + k[5] + ' buy ' + b.buy + '/' + k[9]);
        }
        check(cmp >= 1 && same === cmp, 'footprint bars differ from Binance 1m klines (volume, taker-buy volume): ' + same + ' of ' + cmp + ' — ' + ex.join(' | '));
        notes.push('FOOT vs upstream: ' + same + ' of ' + cmp + ' complete 1m bars equal Binance klines (volume and taker-buy volume, gateway/Binance): ' + ex.slice(-2).join(' | '));
      } else notes.push('FOOT vs upstream: Binance could not be reached from the test (or no complete bar yet)');

      // alive: the open bar grows
      const openVol = () => page.evaluate(() => { const all = window.__flow.trades.filter(m => m.id === 'BTC'); return all.length; });
      const sig = () => page.evaluate(() => { const cv = document.querySelector('.mod[data-mod="FOOT"] .fl-canvas canvas'), d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data; let h = 0; for (let i = 0; i < d.length; i += 4 * 41) h = (h * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) >>> 0; return h; });
      await page.mouse.move(700, 30);
      const n1 = await openVol(), g1 = await sig(); await sleep(3000); const n2 = await openVol(), g2 = await sig();
      check(n2 > n1 && g1 !== g2, 'FOOT did not change in 3 s although ' + (n2 - n1) + ' trades arrived');

      // price step: a chosen step is applied without a request and survives an instrument switch; auto comes back
      const reqs = (await footState()).n;
      const want = s0.stepOpts[s0.stepOpts.length - 2];
      await page.selectOption(M('FOOT') + ' .mod-head select', want); await frame(page); await sleep(250);
      const s1 = await footState();
      check(new RegExp('within ' + String(+want).replace('.', '\\.') + ' USDT').test(s1.cap), 'price step ' + want + ' not applied: ' + s1.cap.slice(0, 90));
      check(s1.n <= reqs + 1, 'changing the price step caused ' + (s1.n - reqs) + ' requests (it is folded in the page)');
      await ctx.select('ETH'); await ctx.waitFor(() => window.__flow.rest.some(r => /\/api\/footprint\?id=ETH/.test(r.url) && r.d.bars.length > 0) && /\(ETH\)/.test(document.querySelector('.mod[data-mod="FOOT"] .fl-strip').textContent), { timeout: 25000, msg: 'FOOT loaded ETH' });
      await sleep(500);
      const eth = await footState();
      check(/^auto/.test(eth.step), 'ETH footprint inherited the BTC price step: ' + eth.step);
      await ctx.shot('flow_foot_ETH');
      await ctx.select('BTC'); await ctx.waitFor(() => /\(BTC\)/.test(document.querySelector('.mod[data-mod="FOOT"] .fl-strip').textContent) && !document.querySelector('.mod[data-mod="FOOT"] .mod-body > .ot-state'), { timeout: 25000, msg: 'FOOT back on BTC' });
      await sleep(400);
      check(new RegExp('within ' + String(+want).replace('.', '\\.') + ' USDT').test((await footState()).cap), 'the BTC price step ' + want + ' did not survive the instrument switch');
      await page.selectOption(M('FOOT') + ' .mod-head select', 'auto'); await frame(page);

      // wheel: more bars, until cells are too small for numbers → coloured cells, never smaller text
      await page.mouse.move(box.x + plotW * 0.5, box.y + box.height * 0.4);
      for (let i = 0; i < 14; i++) { await page.mouse.wheel(0, 300); await sleep(30); }
      await frame(page); await sleep(300);
      const s2 = await footState();
      check(/too small for numbers/.test(s2.cap), 'zoomed out to many bars but the caption does not say the numbers moved to the read-out: ' + s2.cap.slice(0, 160));
      await page.mouse.move(box.x + plotW * 0.8 - 2, box.y + box.height * 0.4); await page.mouse.move(box.x + plotW * 0.8, box.y + box.height * 0.4, { steps: 2 }); await frame(page); await sleep(60);
      const tz = await tipText(page);
      check(tz && /bar/.test(tz) && (/Sold .* × bought/.test(tz) || /No trade/.test(tz)), 'zoomed-out footprint has no hover read-out with the figures: ' + JSON.stringify(tz));
      await ctx.shot('flow_foot_BTC_zoomed_out');
      // drag to older bars turns FOLLOW off; double-click returns to the newest and the default zoom
      await page.mouse.move(box.x + plotW * 0.5, box.y + box.height * 0.4);
      await page.mouse.down(); await page.mouse.move(box.x + plotW * 0.5 + 220, box.y + box.height * 0.4, { steps: 6 }); await page.mouse.up(); await frame(page);
      check((await footState()).follow === 'false', 'dragging the footprint to older bars did not turn FOLLOW off');
      await page.mouse.dblclick(box.x + plotW * 0.5, box.y + box.height * 0.4); await frame(page); await sleep(200);
      const s3 = await footState();
      check(s3.follow === 'true' && !/too small for numbers/.test(s3.cap), 'double-click did not return the footprint to the newest bars at the default zoom');

      // bar period: 5m bars are loaded and start on 5-minute boundaries; back to 1m
      await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="FOOT"] .mod-head .ot-tab')).find(b => b.textContent === '5m').click());
      await ctx.waitFor(() => { const all = window.__flow.rest.filter(r => /\/api\/footprint\?id=BTC/.test(r.url) && /tf=5m/.test(r.url)); return all.length && all[all.length - 1].d.bars.length > 0 && !document.querySelector('.mod[data-mod="FOOT"] .mod-body > .ot-state'); }, { timeout: 20000, msg: 'FOOT loaded 5m bars' });
      const five = await footState();
      check(five.tf === '5m' && five.last.d.tf === '5m' && five.last.d.bars.every(b => b.t % 300 === 0), '5m bars: tf ' + five.tf + ', starts ' + five.last.d.bars.slice(0, 3).map(b => b.t % 300).join());
      await sleep(600); await ctx.shot('flow_foot_BTC_5m');
      await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="FOOT"] .mod-head .ot-tab')).find(b => b.textContent === '1m').click());
      await ctx.waitFor(() => !document.querySelector('.mod[data-mod="FOOT"] .mod-body > .ot-state') && document.querySelector('.mod[data-mod="FOOT"] .mod-head .ot-tab[aria-selected="true"]').textContent === '1m', { timeout: 20000, msg: 'FOOT back on 1m' });
      await page.mouse.move(700, 30);
      await ctx.assertNoOverflow('FOOT');
    });

    /* ══════════════════════════════ layout ══════════════════════════════ */
    await section('layout', async () => {
      // nothing is cut off at 1280x720 and 1366x768: every panel / strip of the three modules lies inside the main area
      for (const [w, hgt] of [[1280, 720], [1366, 768], [1920, 1080]]) {
        await ctx.setViewport(w, hgt);
        for (const m of ['BOOK', 'HEAT', 'FOOT']) {
          await ctx.go(m); await frame(page); await sleep(500);
          const r = await page.evaluate(mod => {
            const sec = document.querySelector('.mod[data-mod="' + mod + '"]'), main = document.getElementById('main').getBoundingClientRect(), out = [];
            sec.querySelectorAll('.panel, .fl-canvas, .fl-strip, .bk-foot, .bk-stats, .mod-head, .bk-row, .bk-trow').forEach(el => {
              const b = el.getBoundingClientRect();
              if (b.width < 1 || b.height < 1) return;
              if (b.left < main.left - 0.5 || b.right > main.right + 0.5 || b.top < main.top - 0.5 || b.bottom > main.bottom + 0.5) out.push(el.className + ' ' + Math.round(b.left) + ',' + Math.round(b.top) + ' ' + Math.round(b.width) + 'x' + Math.round(b.height));
              if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflowX !== 'visible' && !/bk-rows|bk-trows/.test(el.className)) out.push(el.className + ' content wider than its box (' + el.scrollWidth + ' > ' + el.clientWidth + ')');
            });
            const body = sec.querySelector('.mod-body:not(.mod-cover)');
            if (body.scrollHeight > body.clientHeight + 1) out.push('module body scrolls (' + body.scrollHeight + ' > ' + body.clientHeight + ')');
            const cv = sec.querySelector('.fl-canvas canvas'), area = cv ? cv.clientWidth * cv.clientHeight / (main.width * main.height) : null;
            return { out, area };
          }, m);
          check(!r.out.length, m + ' @' + w + 'x' + hgt + ': ' + r.out.slice(0, 4).join(' | '));
          if (r.area !== null) check(r.area > 0.6, m + ' @' + w + 'x' + hgt + ': the canvas covers only ' + Math.round(r.area * 100) + ' % of the main area');
          await ctx.assertNoOverflow(m + ' @' + w + 'x' + hgt);
        }
      }
      await ctx.setViewport(1920, 1080);
    });

    /* ══════════════════════════════ smooth while streaming ══════════════════════════════ */
    await section('long tasks', async () => {
      // 20 s per module at 1920x1080 with the real stream: no task of the page may block the main thread for more than 50 ms
      for (const m of ['BOOK', 'HEAT', 'FOOT']) {
        await ctx.select('BTC'); await ctx.go(m);
        await sleep(3000);                                    // loading is over
        const t0 = await page.evaluate(() => { window.__flow.long.length = 0; window.__frames = 0; const tick = () => { window.__frames++; window.__raf = requestAnimationFrame(tick); }; tick(); return performance.now(); });
        await sleep(20000);
        const r = await page.evaluate(start => { cancelAnimationFrame(window.__raf); return { long: window.__flow.long.filter(e => e.t >= start), frames: window.__frames, ms: performance.now() - start }; }, t0);
        const worst = r.long.reduce((a, e) => Math.max(a, e.d), 0);
        check(r.long.length <= 1 && worst <= 80, m + ': ' + r.long.length + ' long task(s) in 20 s while streaming (worst ' + worst + ' ms): ' + JSON.stringify(r.long.slice(0, 6)));
        notes.push(m + ' 20 s at 1920x1080: ' + r.long.length + ' long tasks (worst ' + worst + ' ms), ' + Math.round(r.frames / (r.ms / 1000)) + ' frames/s available');
      }
    });

    /* ══════════════════════════════ failure paths (second pages) ══════════════════════════════ */
    await section('stream silent → REST, stale payload, upstream error', async () => {
      // book / trade frames never arrive (the Binance feed is down but the gateway is up): BOOK shows the REST snapshot and says so
      const p = await ctx.open({ ws: true, hash: 'BOOK/BTC', init });
      p.ws.rewrite(m => (m.type === 'book' || m.type === 'trade' ? null : undefined));
      await p.goto('BOOK/BTC');
      await p.waitFor(() => { const s = document.querySelector('.mod[data-mod="BOOK"]'); return s && s.querySelectorAll('.bk-row.bid').length > 3 && /Stream not delivering/.test(s.querySelector('.bk-foot').textContent); }, { timeout: 20000, msg: 'BOOK falls back to the REST snapshot when no book frame arrives' });
      const r = await p.page.evaluate(() => {
        const sec = document.querySelector('.mod[data-mod="BOOK"]'), d = window.__flow.rest.filter(x => /\/api\/book\?/.test(x.url)).slice(-1)[0].d;
        return { bid: sec.querySelector('[data-k="bid"]').textContent, want: OT.fmt.px('BTC', d.bids[0][0]), badge: sec.querySelector('.mod-head .badge').textContent, live: sec.querySelectorAll('.bk-row.live').length, via: d.via,
          tip: sec.querySelector('.mod-head .badge').getAttribute('data-tip') };
      });
      p.check(r.bid === r.want && r.via === 'rest', 'REST-only BOOK: best bid ' + r.bid + ' but the last /api/book answer says ' + r.want + ' (via ' + r.via + ')');
      p.check(r.live === 0 && /RT/.test(r.badge) && /not delivering/.test(r.tip), 'REST-only BOOK: ' + r.live + ' rows marked live, badge ' + r.badge + ', tooltip ' + JSON.stringify(r.tip));
      // … and the snapshot itself is an old answer served because the refresh failed: STALE with the gateway's reason
      await p.page.route(/\/api\/book\?/, async route => {
        const res = await route.fetch(), d = await res.json();
        d.stale = true; d.error = { code: 'upstream_error', message: 'HTTP 503 Service Unavailable', source: 'binance' }; d.as_of -= 40;
        await route.fulfill({ response: res, json: d });
      });
      await p.waitFor(() => { const b = document.querySelector('.mod[data-mod="BOOK"] .mod-head .badge'); return /STALE/.test(b.textContent) && /HTTP 503/.test(b.getAttribute('data-tip') || ''); }, { timeout: 15000, msg: 'a stale /api/book answer shows STALE with the reason' });
      p.check(await p.page.evaluate(() => document.querySelector('.mod[data-mod="BOOK"] .mod-body').classList.contains('is-stale') && /STALE/.test(document.querySelector('.mod[data-mod="BOOK"] .bk-foot').textContent)), 'stale book: the ladder is not dimmed / the footer does not say STALE');
      await p.shotNow('flow_book_stale_1920x1080');
      await p.page.unroute(/\/api\/book\?/);
      await p.close();

      // the routes fail outright (Binance down, nothing cached): each module says what failed and keeps trying
      const q = await ctx.open({ ws: true, hash: 'BOOK/ETH', init });
      q.ws.rewrite(m => (m.type === 'book' || m.type === 'trade' ? null : undefined));
      q.allow(/502/); q.allow(/Failed to load resource/);
      const fail = route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'cannot reach the source ([Errno 111] Connection refused)', source: 'binance' } }) });
      await q.page.route(/\/api\/(book|trades|footprint)/, fail);
      await q.goto('BOOK/ETH');
      for (const m of ['BOOK', 'HEAT', 'FOOT']) {
        await q.go(m);
        await q.waitFor(mod => { const s = document.querySelector('.mod[data-mod="' + mod + '"] .mod-body > .ot-state'); return s && s.dataset.kind === 'error' && /cannot reach the source/.test(s.textContent) && /binance/.test(s.textContent); }, { arg: m, timeout: 15000, msg: m + ' shows the upstream error' });
        q.check(await q.page.evaluate(mod => !/NaN|undefined/.test(document.querySelector('.mod[data-mod="' + mod + '"]').textContent), m), m + ': bad text in the error state');
      }
      await q.shotNow('flow_error_1920x1080');
      await q.page.unroute(/\/api\/(book|trades|footprint)/);
      await q.go('FOOT');
      await q.waitFor(() => !document.querySelector('.mod[data-mod="FOOT"] .mod-body > .ot-state') && window.__flow.rest.some(r => /\/api\/footprint\?id=ETH/.test(r.url)), { timeout: 20000, msg: 'FOOT recovers by itself when the route answers again' });
      await q.go('HEAT');
      await q.waitFor(() => !document.querySelector('.mod[data-mod="HEAT"] .mod-body > .ot-state'), { timeout: 20000, msg: 'HEAT recovers by itself when the route answers again' });
      await q.close();
    });

    await section('gateway stopped and restarted', async () => {
      const proxy = ctx.tcpProxy(+new URL(ctx.base).port);
      await proxy.start();
      const p = await ctx.open({ base: proxy.base, init });
      p.allow(/ERR_|Failed to load|net::|WebSocket|502|504/);
      await p.goto('BOOK/BTC');
      await p.waitFor(() => document.querySelectorAll('.mod[data-mod="BOOK"] .bk-row.bid').length > 3, { timeout: 20000, msg: 'BOOK up through the relay' });
      for (const m of ['HEAT', 'FOOT', 'BOOK']) { await p.go(m); await sleep(2500); }
      await proxy.stop();
      await p.waitFor(() => OT.state.gateway === 'offline' && !document.getElementById('offline').hidden, { timeout: 20000, msg: 'GATEWAY OFFLINE banner after the gateway stopped' });
      await sleep(DEADLINE_MS);
      const off = await p.page.evaluate(() => { const sec = document.querySelector('.mod[data-mod="BOOK"]'); return { badge: sec.querySelector('.mod-head .badge').textContent, stale: sec.querySelector('.mod-body').classList.contains('is-stale'), foot: sec.querySelector('.bk-foot').textContent,
        colour: getComputedStyle(sec.querySelector('[data-k="bid"]')).color, t3: getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim() }; });
      p.check(/STALE/.test(off.badge) && off.stale && /NOT UPDATING/.test(off.foot), 'gateway stopped: BOOK still looks live (badge ' + off.badge + ', footer ' + JSON.stringify(off.foot.slice(0, 60)) + ')');
      for (const m of ['HEAT', 'FOOT']) {
        await p.go(m); await sleep(1500);
        const b = await p.page.evaluate(mod => document.querySelector('.mod[data-mod="' + mod + '"] .mod-head .badge').textContent, m);
        p.check(/STALE/.test(b), 'gateway stopped: ' + m + ' badge is ' + b);
      }
      await p.shotNow('flow_gateway_offline_1920x1080');
      await proxy.start();
      await p.waitFor(() => OT.state.gateway === 'online' && OT.state.stream === 'live', { timeout: 30000, msg: 'page back online after the gateway returned' });
      for (const m of ['FOOT', 'HEAT', 'BOOK']) {
        await p.go(m);
        await p.waitFor(mod => /RT/.test(document.querySelector('.mod[data-mod="' + mod + '"] .mod-head .badge').textContent) && !document.querySelector('.mod[data-mod="' + mod + '"] .mod-body > .ot-state'), { arg: m, timeout: 25000, msg: m + ' is live again after the gateway returned' });
      }
      await frame(p.page); await sleep(400);                 // the first book after the return has been painted
      const on = await p.page.evaluate(() => { const sec = document.querySelector('.mod[data-mod="BOOK"]'), bid = sec.querySelector('[data-k="bid"]').textContent;
        return { ok: window.__flow.books.filter(m => m.id === 'BTC').slice(-10).some(m => OT.fmt.px('BTC', m.bids[0][0]) === bid), stale: sec.querySelector('.mod-body').classList.contains('is-stale') }; });
      p.check(on.ok && !on.stale, 'after the gateway returned BOOK does not show the live book again');
      await p.close();
      await proxy.stop();
    });

    await ctx.select('BTC'); await ctx.go('BOOK');
    console.log('      flow notes:\n        ' + notes.join('\n        '));
  },
};
const DEADLINE_MS = 13500;             // BOOK / HEAT call themselves STALE after 12 s without data
