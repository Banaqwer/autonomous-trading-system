/* Options spec (S3): OPTS, VOLS, GREEKS, GEX against the real gateway.
   What is on screen is compared with what the gateway answered (the page's own copy of the payload AND a
   second fetch by this test), with real mouse input for hover / wheel / drag / click, and with the failure
   paths: an instrument without options, a stale payload altered in transit, an upstream error, a gateway
   that stops and comes back. Upstream-vs-gateway comparisons are done once by hand (see the slice report):
   this spec only needs the gateway. */
'use strict';
const http = require('http');

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res({ status: r.statusCode, body: JSON.parse(b) }); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
/* the page's number format, written out again here on purpose (en-US grouping, fixed decimals, never "-0") */
function num(v, d) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  let s = v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  if (/^-0(\.0*)?$/.test(s)) s = s.slice(1);
  return s;
}
const iv = v => (typeof v === 'number' ? num(v * 100, 1) + '%' : '—');
function qty(v) {
  if (typeof v !== 'number') return '—';
  const a = Math.abs(v);
  if (a >= 1e6) return num(v / 1e6, 1) + 'M';
  if (a >= 10000) return num(v / 1e3, 1) + 'K';
  return num(v, Number.isInteger(v) || a >= 100 ? 0 : 1);
}
function gexFmt(v) {
  if (typeof v !== 'number') return '—';
  if (v === 0) return '0';
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : a >= 0.1 ? 3 : 4, s = num(v, d);
  return v > 0 && !/^0(\.0*)?$/.test(s) ? '+' + s : s;
}
const strikeFmt = k => num(k, Number.isInteger(k) ? 0 : Number.isInteger(Math.round(k * 10 * 1e6) / 1e6) ? 1 : Number.isInteger(Math.round(k * 100 * 1e6) / 1e6) ? 2 : 3);

const MOD = m => '.mod[data-mod="' + m + '"]';
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* rows of the OPTS table as shown: {key: {col: text}} + row classes */
const tableRows = page => page.evaluate(() => {
  const out = {};
  document.querySelectorAll('.mod[data-mod="OPTS"] table.opt-chain tbody tr').forEach(tr => {
    const r = { _atm: tr.classList.contains('atm'), _sel: tr.classList.contains('sel'), _itm: [] };
    tr.querySelectorAll('td').forEach(td => { r[td.dataset.col] = td.dataset.col === 'k' ? td.firstChild.textContent : td.textContent; if (td.classList.contains('itm')) r._itm.push(td.dataset.col); });
    out[tr.dataset.key] = r;
  });
  return out;
});
const pageChain = (page, id) => page.evaluate(i => {
  const list = OT.opt.peek('/api/options', { id: i });
  const key = OT.opt.pick(i, list);
  return { list, key, chain: OT.opt.peek('/api/options/chain', { id: i, expiry: key }) };
}, id);
const waitChain = (ctx, id, extra) => ctx.waitFor(i => {
  const s = document.querySelector('.mod[data-mod="OPTS"]');
  return OT.state.sel === i && s && !s.querySelector('.mod-body > .ot-state') && s.querySelectorAll('table.opt-chain tbody tr').length > 3 &&
    s.querySelector('.mod-inst-id').textContent === i && !!OT.opt.peek('/api/options', { id: i });
}, Object.assign({ arg: id, timeout: 30000, msg: 'OPTS shows a chain for ' + id }, extra || {}));
const waitMod = (ctx, mod, id, timeout) => ctx.waitFor(([m, i]) => {
  const s = document.querySelector('.mod[data-mod="' + m + '"]');
  return OT.state.sel === i && OT.state.mod === m && s && !s.querySelector('.mod-body > .ot-state') && !s.querySelector('.mod-src[hidden]') &&
    s.querySelector('.opt-stat-v') && s.querySelector('.opt-stat-v').textContent !== '—';
}, { arg: [mod, id], timeout: timeout || 30000, msg: mod + ' shows data for ' + id });
const painted = (page, sel) => page.evaluate(s => {
  const c = document.querySelector(s);
  if (!c || !c.width) return 0;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 16) if (d[i]) n++;
  return n;
}, sel);
const tipText = page => page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.textContent : ''; });
module.exports = {
  name: 'options', slice: 'options',
  async run(ctx) {
    const { page, check } = ctx;
    const section = async (name, fn) => { try { await fn(); } catch (e) { ctx.fail('[' + name + '] threw: ' + (e && e.stack || e)); } };
    const reqs = [];
    page.on('request', r => { const u = r.url(); if (/\/api\/(options|vol_surface|gex)/.test(u)) reqs.push({ t: Date.now(), u: u.replace(/^.*\/api/, '/api') }); });
    /* canvas of a named plot + the page coordinates of a data x at a fraction of the plot height */
    const at = (name, x, fy) => page.evaluate(([n, xv, f]) => {
      const p = OT.opt.plots[n];
      if (!p || !p.box) return null;
      const labels = { smile: 'Volatility smile', term: 'ATM term structure', gex: 'Gamma exposure by strike', profile: 'Gamma exposure against the underlying level',
        delta: 'Delta against strike', gamma: 'Gamma against strike', vega: 'Vega against strike', theta: 'Theta against strike',
        oi: 'Open interest by strike', volume: 'Volume by strike' };
      const cv = document.querySelector('canvas[aria-label="' + labels[n] + '"]'), r = cv.getBoundingClientRect();
      return { x: r.left + (xv === null ? p.box.l + p.box.w / 2 : p.px(xv)), y: r.top + p.box.t + p.box.h * f, view: p.view ? p.view.slice() : null, cross: p.cross };
    }, [name, x === undefined ? null : x, fy === undefined ? 0.5 : fy]);
    const view = name => page.evaluate(n => { const p = OT.opt.plots[n]; return p.view ? p.view.slice() : null; }, name);

    /* ───────────────────────── OPTS: Deribit chain ───────────────────────── */
    await section('OPTS chain', async () => {
      await ctx.select('BTC');
      await ctx.go('OPTS');
      // the previous instrument's chain is never on screen while the new one loads; the header says what is loading
      await waitChain(ctx, 'BTC');
      const pc = await pageChain(page, 'BTC');
      check(pc.chain && pc.chain.rows.length > 10 && pc.chain.source === 'deribit', 'OPTS/BTC: the page holds no Deribit chain');
      const gw = (await getJson(ctx.base + '/api/options/chain?id=BTC&expiry=' + pc.key)).body;
      if (gw.as_of === pc.chain.as_of) {
        check(JSON.stringify(gw.rows) === JSON.stringify(pc.chain.rows), 'OPTS/BTC: the chain the page holds differs from the gateway answer of the same as_of');
      } else check(Math.abs(gw.as_of - pc.chain.as_of) < 40, 'OPTS/BTC: page chain and gateway chain are ' + Math.abs(gw.as_of - pc.chain.as_of) + ' s apart');
      // every cell shown = the payload, formatted
      const shown = await tableRows(page), d = pc.chain, dec = d.fwd >= 10000 ? 1 : 2;
      const strikes = Array.from(new Set(d.rows.map(r => r.strike))).sort((a, b) => a - b);
      check(Object.keys(shown).length === strikes.length, 'OPTS/BTC: ' + Object.keys(shown).length + ' rows shown, the chain has ' + strikes.length + ' strikes');
      const bad = [];
      strikes.forEach(k => {
        const row = shown[String(k)];
        if (!row) { bad.push('strike ' + k + ' missing'); return; }
        if (row.k !== strikeFmt(k)) bad.push(k + ' strike shows ' + row.k);
        [['C', 'c_'], ['P', 'p_']].forEach(([cp, pre]) => {
          const c = d.rows.find(r => r.strike === k && r.type === cp);
          const want = c ? { bid: num(c.bid, dec), ask: num(c.ask, dec), mark: num(c.mark, dec), iv: iv(c.iv), delta: num(c.delta, 3), oi: qty(c.oi), volume: qty(c.volume) }
            : { bid: '—', ask: '—', mark: '—', iv: '—', delta: '—', oi: '—', volume: '—' };
          Object.keys(want).forEach(f => { if (row[pre + f] !== want[f]) bad.push(k + ' ' + cp + ' ' + f + ': shows ' + row[pre + f] + ', payload says ' + want[f]); });
          const itm = cp === 'C' ? k < d.fwd : k > d.fwd;
          if ((row._itm.indexOf(pre + 'mark') >= 0) !== itm) bad.push(k + ' ' + cp + ': in-the-money shading is ' + !itm + ', should be ' + itm);
        });
      });
      check(!bad.length, 'OPTS/BTC: cells differ from the payload: ' + bad.slice(0, 6).join(' | '));
      // prices are USD: mark = native mark x forward (Deribit quotes BTC options in BTC)
      const sample = d.rows.find(r => r.strike === d.atm_strike && r.type === 'C');
      check(sample && Math.abs(sample.mark - sample.mark_native * d.fwd) / sample.mark < 1e-3 && sample.mark > 10, 'OPTS/BTC: the ATM call mark is not USD (mark ' + (sample && sample.mark) + ', native ' + (sample && sample.mark_native) + ')');
      // ATM row: exactly one, the gateway's, and scrolled into view
      const atm = await page.evaluate(() => {
        const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), rows = box.querySelectorAll('tr.atm'), tr = rows[0];
        if (!tr) return { n: 0 };
        const b = box.getBoundingClientRect(), r = tr.getBoundingClientRect();
        return { n: rows.length, key: tr.dataset.key, tag: tr.querySelector('.k-atm') && !tr.querySelector('.k-atm').hidden, inView: r.top >= b.top + 40 && r.bottom <= b.bottom, scroll: box.scrollTop, canScroll: box.scrollHeight > box.clientHeight + 4 };
      });
      check(atm.n === 1 && atm.key === String(d.atm_strike) && atm.tag, 'OPTS/BTC: ATM row is ' + JSON.stringify(atm) + ', gateway says ' + d.atm_strike);
      check(atm.inView, 'OPTS/BTC: the ATM row is not inside the visible part of the table');
      // header: badge + age that counts; summary strip from the payload
      const hb = await ctx.assertHeaderBadge('OPTS');
      check(/RT/.test(hb.badge || '') && /DERIBIT/.test(hb.badge || ''), 'OPTS/BTC: header badge is ' + hb.badge);
      const strip = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="OPTS"] .opt-stat')).map(s => [s.firstChild.textContent, s.lastChild.textContent]));
      const stat = n => (strip.find(s => s[0] === n) || [])[1];
      check(stat('ATM IV') === iv(d.atm_iv), 'OPTS/BTC: ATM IV shows ' + stat('ATM IV') + ', payload ' + iv(d.atm_iv));
      check(stat('Index') === num(d.spot, 2) && stat('Forward') === num(d.fwd, 2), 'OPTS/BTC: level/forward show ' + stat('Index') + ' / ' + stat('Forward') + ', payload ' + d.spot + ' / ' + d.fwd);
      await ctx.assertNoBadText(MOD('OPTS'), 'OPTS/BTC');
      await ctx.shot('opts_BTC');
    });

    await section('OPTS interaction', async () => {
      // row hover: the full contract of the side under the pointer
      const pc = await pageChain(page, 'BTC'), d = pc.chain;
      const cell = side => page.evaluate(([k, s]) => { const td = document.querySelector('.mod[data-mod="OPTS"] tr[data-key="' + k + '"] td[data-col="' + s + '_mark"]'), r = td.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, [String(d.atm_strike), side]);
      await ctx.mouse.hover(await cell('c'));
      let tip = await tipText(page);
      const call = d.rows.find(r => r.strike === d.atm_strike && r.type === 'C'), put = d.rows.find(r => r.strike === d.atm_strike && r.type === 'P');
      check(tip.indexOf(call.name) >= 0 && /Gamma/.test(tip) && /Theta/.test(tip) && /Open interest/.test(tip) && /DERIVED/.test(tip), 'OPTS: hover on the ATM call shows ' + JSON.stringify(tip.slice(0, 120)));
      await ctx.mouse.hover(await cell('p'));
      tip = await tipText(page);
      check(tip.indexOf(put.name) >= 0, 'OPTS: hover on the put side does not show the put contract: ' + JSON.stringify(tip.slice(0, 80)));
      // click selects the row; keyboard moves the focus
      await ctx.mouse.click(await cell('c'));
      check((await tableRows(page))[String(d.atm_strike)]._sel, 'OPTS: a click does not select the row');
      await page.keyboard.press('ArrowDown');
      const focusKey = await page.evaluate(() => document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null);
      const strikes = Array.from(new Set(d.rows.map(r => r.strike))).sort((a, b) => a - b);
      check(focusKey === String(strikes[strikes.indexOf(d.atm_strike) + 1]), 'OPTS: ArrowDown moves the focus to ' + focusKey);
      await page.mouse.move(5, 5);
      // sticky header while scrolling inside the panel
      const sticky = await page.evaluate(async () => {
        const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), th = box.querySelector('thead tr.cols th'), grp = box.querySelector('thead tr.grp th');
        const before = box.scrollTop;
        box.scrollTop = before + 120;
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const b = box.getBoundingClientRect();
        const out = { moved: box.scrollTop !== before, grp: Math.round(grp.getBoundingClientRect().top - b.top), cols: Math.round(th.getBoundingClientRect().top - b.top), canScroll: box.scrollHeight > box.clientHeight + 130 };
        return out;
      });
      check(!sticky.canScroll || (sticky.moved && sticky.grp === 0 && sticky.cols >= 20 && sticky.cols <= 30), 'OPTS: the header does not stay at the top while scrolling: ' + JSON.stringify(sticky));
      // wide screen: open interest and volume of the expiry by strike, beside the chain
      const side = await page.evaluate(() => getComputedStyle(document.querySelector('.mod[data-mod="OPTS"] .opt-side')).display);
      check(side === 'flex', 'OPTS @1920: the open-interest / volume charts are not shown (' + side + ')');
      await page.evaluate(() => window.__otT.raf(2));
      check((await painted(page, 'canvas[aria-label="Open interest by strike"]')) > 300, 'OPTS: the open-interest chart is empty');
      const withOi = d.rows.filter(r => r.oi > 0 && Math.abs(r.strike / d.fwd - 1) < 0.08).sort((a, b) => b.oi - a.oi)[0];
      if (withOi) {
        const kk = withOi.strike, cc = d.rows.find(r => r.strike === kk && r.type === 'C'), pp = d.rows.find(r => r.strike === kk && r.type === 'P');
        const pt = await at('oi', kk, 0.5);
        await ctx.mouse.hover(pt);
        await page.evaluate(() => window.__otT.raf(2));
        tip = await tipText(page);
        check(tip.indexOf('Strike ' + strikeFmt(kk)) >= 0 && tip.indexOf('calls ' + qty(cc ? cc.oi : null) + ' · puts ' + qty(pp ? pp.oi : null)) >= 0, 'OPTS: open-interest hover at ' + kk + ' reads ' + JSON.stringify(tip.slice(0, 100)) + ', payload ' + (cc && cc.oi) + ' / ' + (pp && pp.oi));
        await ctx.mouse.click(pt);
        await page.evaluate(() => window.__otT.raf(2));
        const seld = await page.evaluate(() => { const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), tr = box.querySelector('tr.sel'); if (!tr) return null; const b = box.getBoundingClientRect(), r = tr.getBoundingClientRect(); return { key: tr.dataset.key, inView: r.top >= b.top + 40 && r.bottom <= b.bottom }; });
        check(seld && seld.key === String(kk) && seld.inView, 'OPTS: a click on the open-interest chart does not select and show strike ' + kk + ': ' + JSON.stringify(seld));
        await page.mouse.move(5, 5);
      }
      // strike range: fewer rows, ATM still there; back to all
      const all = Object.keys(await tableRows(page)).length;
      await page.locator(MOD('OPTS') + ' .ot-tab[data-id="10"]').click();
      await page.evaluate(() => window.__otT.raf(2));
      let rows = await tableRows(page);
      check(Object.keys(rows).length <= all && rows[String(d.atm_strike)] && rows[String(d.atm_strike)]._atm, 'OPTS: ±10% shows ' + Object.keys(rows).length + ' of ' + all + ' rows / ATM missing');
      const ks = Object.keys(rows).map(Number).sort((a, b) => a - b), ia = ks.indexOf(d.atm_strike);
      check(ks.every((k, i) => Math.abs(k / d.fwd - 1) <= 0.1 || Math.abs(i - ia) <= 8), 'OPTS: ±10% shows strikes outside the band: ' + ks.join(' '));
      await page.locator(MOD('OPTS') + ' .ot-tab[data-id="all"]').click();
      await page.evaluate(() => window.__otT.raf(2));
      check(Object.keys(await tableRows(page)).length === all, 'OPTS: "All" does not bring every strike back');
      // expiry selector: one option per listed expiry, with dte, ATM IV and OI
      const opts = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="OPTS"] select.opt-expiry option')).map(o => [o.value, o.textContent]));
      check(opts.length === pc.list.expiries.length && opts.every((o, i) => o[0] === pc.list.expiries[i].key), 'OPTS: the expiry selector has ' + opts.length + ' options, the gateway lists ' + pc.list.expiries.length);
      check(opts.every(o => /\d(h|d) · ATM (—|[\d.]+%) · OI /.test(o[1])), 'OPTS: expiry options lack dte / ATM IV / OI: ' + JSON.stringify(opts[0]));
      check((await page.evaluate(() => document.querySelector('.mod[data-mod="OPTS"] select.opt-expiry').value)) === pc.list.default_expiry, 'OPTS: the selector does not start on the gateway default expiry');
    });

    let chosen = null;
    await section('OPTS expiry change + shared state', async () => {
      const pc = await pageChain(page, 'BTC');
      const target = pc.list.expiries.filter(e => e.key !== pc.key && e.n >= 20 && e.dte > 20)[0] || pc.list.expiries[pc.list.expiries.length - 1];
      chosen = target.key;
      const mark = await page.evaluate(() => { const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'); box.scrollTop = 0; return box.scrollTop; });
      await page.selectOption(MOD('OPTS') + ' select.opt-expiry', chosen);
      await ctx.waitFor(k => { const c = OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: k }); const tr = document.querySelector('.mod[data-mod="OPTS"] tr.atm'); return c && tr && tr.dataset.key === String(c.atm_strike); }, { arg: chosen, timeout: 15000, msg: 'OPTS shows the chosen expiry ' + chosen });
      const now = await pageChain(page, 'BTC');
      check(now.key === chosen && now.chain.expiry.key === chosen, 'OPTS: after choosing ' + chosen + ' the page holds ' + now.key);
      const shown = await tableRows(page);
      const strikes = Array.from(new Set(now.chain.rows.map(r => r.strike)));
      check(Object.keys(shown).length === strikes.length && strikes.every(k => shown[String(k)]), 'OPTS: rows after the expiry change are not the strikes of ' + chosen);
      const atmIn = await page.evaluate(() => { const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), tr = box.querySelector('tr.atm'), b = box.getBoundingClientRect(), r = tr.getBoundingClientRect(); return r.top >= b.top + 40 && r.bottom <= b.bottom; });
      check(atmIn, 'OPTS: the ATM row is not scrolled into view after an expiry change (scrollTop was ' + mark + ')');
      // the step buttons
      await page.locator(MOD('OPTS') + ' .opt-step[aria-label="Nearer expiry"]').click();
      const i = now.list.expiries.findIndex(e => e.key === chosen);
      await ctx.waitFor(k => document.querySelector('.mod[data-mod="OPTS"] select.opt-expiry').value === k, { arg: now.list.expiries[i - 1].key, msg: 'the ‹ button steps to the nearer expiry' });
      await page.locator(MOD('OPTS') + ' .opt-step[aria-label="Further expiry"]').click();
      await ctx.waitFor(k => document.querySelector('.mod[data-mod="OPTS"] select.opt-expiry').value === k && document.querySelector('.mod[data-mod="OPTS"] tr.atm'), { arg: chosen, msg: 'the › button steps back' });
      // the same expiry in GREEKS, VOLS — and marked in the GEX table
      for (const m of ['GREEKS', 'VOLS']) {
        await ctx.go(m);
        await waitMod(ctx, m, 'BTC');
        const v = await page.evaluate(mm => document.querySelector('.mod[data-mod="' + mm + '"] select.opt-expiry').value, m);
        check(v === chosen, m + ': shows expiry ' + v + ' after ' + chosen + ' was chosen in OPTS');
      }
      await ctx.go('GEX');
      await waitMod(ctx, 'GEX', 'BTC');
      const sel = await page.evaluate(() => { const tr = document.querySelector('.mod[data-mod="GEX"] .gex-table tr.sel'); return tr ? tr.dataset.key : null; });
      check(sel === chosen, 'GEX: the by-expiry table marks ' + sel + ', the shared expiry is ' + chosen);
    });

    /* ───────────────────────── GREEKS ───────────────────────── */
    await section('GREEKS', async () => {
      await ctx.go('GREEKS');
      await waitMod(ctx, 'GREEKS', 'BTC');
      await page.evaluate(() => window.__otT.raf(3));
      const pc = await pageChain(page, 'BTC'), d = pc.chain;
      check(pc.key === chosen, 'GREEKS: expiry ' + pc.key + ' instead of ' + chosen);
      for (const n of ['Delta', 'Gamma', 'Vega', 'Theta']) check((await painted(page, 'canvas[aria-label="' + n + ' against strike"]')) > 400, 'GREEKS: the ' + n + ' chart is empty');
      const txt = await page.evaluate(() => document.querySelector('.mod[data-mod="GREEKS"]').textContent);
      check(/DERIVED/.test(txt) && /Black-76/.test(txt), 'GREEKS/BTC: not labelled DERIVED (Black-76)');
      check(/\$ per 1 vol point/.test(txt) && /\$ per calendar day/.test(txt) && /per \$1/.test(txt), 'GREEKS: the units (vega per vol point, theta per day, gamma per $1) are not stated');
      // hover: the read-out is the payload row of the nearest strike, on all four charts
      const k = d.atm_strike, call = d.rows.find(r => r.strike === k && r.type === 'C'), put = d.rows.find(r => r.strike === k && r.type === 'P');
      for (const name of ['delta', 'theta']) {
        const p = await at(name, k, 0.6);
        await ctx.mouse.hover(p);
        await page.evaluate(() => window.__otT.raf(2));
        const tip = await tipText(page);
        check(tip.indexOf('Strike ' + strikeFmt(k)) >= 0 && tip.indexOf('delta ' + num(call.delta, 3)) >= 0 && tip.indexOf('delta ' + num(put.delta, 3)) >= 0 && /ATM/.test(tip),
          'GREEKS: hover on ' + name + ' at ' + k + ' reads ' + JSON.stringify(tip.slice(0, 140)) + ' — payload call delta ' + call.delta + ', put delta ' + put.delta);
        const crosses = await page.evaluate(() => ['delta', 'gamma', 'vega', 'theta'].map(n => OT.opt.plots[n].cross));
        check(crosses.every(c => c === k), 'GREEKS: the crosshair is not shared by the four charts: ' + JSON.stringify(crosses));
      }
      // an ATM call delta near one half, gamma positive, theta negative: the old page drew 1e-84 everywhere
      check(call.delta > 0.3 && call.delta < 0.7 && put.delta < -0.3 && put.delta > -0.7 && call.gamma > 0 && call.theta < 0, 'GREEKS: ATM greeks are degenerate: ' + JSON.stringify([call.delta, put.delta, call.gamma, call.theta]));
      // wheel zoom about the pointer, shared x view, drag pan, double click resets; the view survives leaving and coming back
      const p0 = await at('gamma', k, 0.5);
      check(p0.view === null, 'GREEKS: the charts do not start on the default view');
      await ctx.mouse.wheel(p0, -240, 3);
      await page.evaluate(() => window.__otT.raf(2));
      const v1 = await view('gamma');
      check(v1 && v1[0] < k && v1[1] > k, 'GREEKS: the wheel does not zoom about the pointer: ' + JSON.stringify(v1));
      const others = await page.evaluate(() => ['delta', 'vega', 'theta'].map(n => OT.opt.plots[n].view));
      check(v1 && others.every(o => o && Math.abs(o[0] - v1[0]) < 1e-6 && Math.abs(o[1] - v1[1]) < 1e-6), 'GREEKS: zooming one chart does not zoom the other three');
      await ctx.mouse.drag(await at('gamma', k, 0.5), 80, 0);
      await page.evaluate(() => window.__otT.raf(2));
      const v2 = await view('gamma');
      check(v1 && v2 && v2[0] < v1[0] - 1e-6 && Math.abs((v2[1] - v2[0]) - (v1[1] - v1[0])) < 1e-6 * Math.abs(v1[1]), 'GREEKS: dragging does not pan: ' + JSON.stringify([v1, v2]));
      await ctx.go('OPTS'); await ctx.go('GREEKS');
      await waitMod(ctx, 'GREEKS', 'BTC');
      const v3 = await view('gamma');
      check(v2 && v3 && Math.abs(v3[0] - v2[0]) < 1e-6 && Math.abs(v3[1] - v2[1]) < 1e-6, 'GREEKS: the zoom is lost when leaving the tab and coming back');
      const pm = await at('gamma', null, 0.5);
      await page.mouse.dblclick(pm.x, pm.y);
      await page.evaluate(() => window.__otT.raf(2));
      check((await view('gamma')) === null, 'GREEKS: a double click does not reset the view');
      // calls only / puts only
      await page.locator(MOD('GREEKS') + ' .ot-tab[data-id="calls"]').click();
      check((await page.evaluate(() => OT.opt.plots.delta.data.lines.map(l => l.label).join())) === 'Calls', 'GREEKS: "Calls" still draws ' + (await page.evaluate(() => OT.opt.plots.delta.data.lines.map(l => l.label).join())));
      await page.locator(MOD('GREEKS') + ' .ot-tab[data-id="both"]').click();
      await page.mouse.move(5, 5);
      await ctx.shot('greeks_BTC');
    });

    /* ───────────────────────── VOLS ───────────────────────── */
    await section('VOLS', async () => {
      await ctx.go('VOLS');
      await waitMod(ctx, 'VOLS', 'BTC');
      await page.evaluate(() => window.__otT.raf(3));
      const pc = await pageChain(page, 'BTC'), d = pc.chain;
      const surf = await page.evaluate(() => OT.opt.peek('/api/vol_surface', { id: 'BTC' }));
      check(surf && surf.expiries.length === pc.list.expiries.length && surf.iv.length === surf.expiries.length && surf.iv.every(r => r.length === surf.moneyness.length), 'VOLS: the surface payload does not match the expiry list');
      for (const n of ['Volatility smile', 'ATM term structure', 'Implied volatility surface']) check((await painted(page, 'canvas[aria-label="' + n + '"]')) > 400, 'VOLS: the ' + n + ' canvas is empty');
      const texts = await page.evaluate(() => (window.__otCanvasDraws || []).map(x => x.text));
      for (const need of ['Implied volatility, %', 'Strike, USD', 'Time to expiry (log scale)', 'IV, %', 'IV']) check(texts.indexOf(need) >= 0, 'VOLS: axis title / legend "' + need + '" is not drawn');
      check(texts.some(t => /^Strike ÷ forward of the expiry/.test(t)) && texts.some(t => /^\d+%$/.test(t)), 'VOLS: the surface has no axis title or no colour legend labels');
      // smile hover = the payload IV of that strike
      const k = d.atm_strike, call = d.rows.find(r => r.strike === k && r.type === 'C'), put = d.rows.find(r => r.strike === k && r.type === 'P');
      await ctx.mouse.hover(await at('smile', k, 0.5));
      await page.evaluate(() => window.__otT.raf(2));
      let tip = await tipText(page);
      check(tip.indexOf('Strike ' + strikeFmt(k)) >= 0 && tip.indexOf('Call IV ' + iv(call.iv)) >= 0 && tip.indexOf('Put IV ' + iv(put.iv)) >= 0, 'VOLS: smile hover at ' + k + ' reads ' + JSON.stringify(tip.slice(0, 120)) + ', payload ' + iv(call.iv) + ' / ' + iv(put.iv));
      // strip
      const strip = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="VOLS"] .opt-stat')).map(s => [s.firstChild.textContent, s.lastChild.textContent]));
      const cur = surf.expiries.find(e => e.key === pc.key);
      check((strip.find(s => s[0] === 'ATM IV') || [])[1] === iv(d.atm_iv) && (strip.find(s => s[0] === '25-delta put') || [])[1] === iv(cur.p25), 'VOLS: strip ' + JSON.stringify(strip) + ' vs payload atm ' + d.atm_iv + ' p25 ' + cur.p25);
      // x axis: strike <-> strike / forward
      await page.locator(MOD('VOLS') + ' .ot-tab[data-id="mon"]').click();
      await page.evaluate(() => window.__otT.raf(2));
      const x1 = await page.evaluate(() => [OT.opt.plots.smile.data.x0, OT.opt.plots.smile.data.x1]);
      check(x1[0] > 0.05 && x1[1] < 20 && x1[0] < 1 && x1[1] > 1, 'VOLS: the strike ÷ forward axis is ' + JSON.stringify(x1));
      await page.locator(MOD('VOLS') + ' .ot-tab[data-id="strike"]').click();
      await page.evaluate(() => window.__otT.raf(2));          // the chart is back on the strike axis before it is measured
      // wheel zoom on the smile keeps the strike under the pointer
      const ps = await at('smile', k, 0.5);
      await ctx.mouse.wheel(ps, -240, 2);
      await page.evaluate(() => window.__otT.raf(2));
      const vs = await view('smile');
      check(vs && vs[0] < k && vs[1] > k, 'VOLS: the smile does not zoom about the pointer: ' + JSON.stringify(vs));
      // term structure: hover reads the expiry; a click chooses it (shared state)
      const other = surf.expiries.filter(e => e.key !== pc.key && e.atm_iv && e.dte > 3)[0];
      const pt = await at('term', other.dte, 0.5);
      const py = await page.evaluate(([n, x, y]) => { const p = OT.opt.plots.term, L = p.data.lines[2], pt2 = L.pts.find(q => q[0] === x); return pt2 ? pt2[1] : null; }, ['term', other.dte]);
      await ctx.mouse.hover(pt);
      await page.evaluate(() => window.__otT.raf(2));
      tip = await tipText(page);
      check(tip.indexOf('ATM IV ' + iv(other.atm_iv)) >= 0, 'VOLS: term-structure hover reads ' + JSON.stringify(tip.slice(0, 100)) + ', payload ATM ' + iv(other.atm_iv) + ' (' + py + ')');
      await ctx.mouse.click(pt);
      await ctx.waitFor(kk => document.querySelector('.mod[data-mod="VOLS"] select.opt-expiry').value === kk && OT.opt.expiry('BTC') === kk, { arg: other.key, timeout: 15000, msg: 'a click on the term structure chooses ' + other.key });
      // surface: hover reads the cell, click chooses the row
      const cellInfo = await page.evaluate(kk => {
        const s = OT.opt.peek('/api/vol_surface', { id: 'BTC' }), i = s.expiries.findIndex(e => e.key === kk), j = s.moneyness.indexOf(1);
        return { i, j, v: s.iv[i][j], n: s.expiries.length, m: s.moneyness.length };
      }, chosen);
      const heat = await page.locator('canvas[aria-label="Implied volatility surface"]').boundingBox();
      const hb = await page.evaluate(() => { const c = document.querySelector('canvas[aria-label="Implied volatility surface"]'); const texts = (window.__otCanvasDraws || []).filter(x => x.canvas === c); return texts.length; });
      check(hb > 5, 'VOLS: the surface draws no labels');
      // find the cell geometry by probing: the hover read-out says which expiry is under the pointer
      let hit = null;
      for (let fy = 0.03; fy < 0.9 && !hit; fy += 0.02) {
        await page.mouse.move(heat.x + heat.width * 0.5, heat.y + heat.height * fy);
        await page.evaluate(() => window.__otT.raf(1));
        const t = await tipText(page);
        if (t && /Strike ÷ forward/.test(t)) {
          const lab = await page.evaluate(kk => { const s = OT.opt.peek('/api/vol_surface', { id: 'BTC' }), l = OT.opt.peek('/api/options', { id: 'BTC' }); const e = s.expiries.find(x => x.key === kk); return OT.opt.expiryLabel(e, l.multi_root); }, chosen);
          if (t.indexOf(lab) === 0) hit = { fy, t };
        }
      }
      check(!!hit && /IV [\d.]+%|no strike listed/.test(hit.t), 'VOLS: no surface cell reads the expiry ' + chosen + ' on hover');
      if (hit) {
        await page.mouse.down(); await page.mouse.up();
        await ctx.waitFor(kk => document.querySelector('.mod[data-mod="VOLS"] select.opt-expiry').value === kk, { arg: chosen, timeout: 15000, msg: 'a click on a surface row chooses ' + chosen });
      }
      await page.mouse.move(5, 5);
      const pm = await at('smile', null, 0.5);
      await page.mouse.dblclick(pm.x, pm.y);
      await page.mouse.move(5, 5);
      await ctx.assertNoBadText(MOD('VOLS'), 'VOLS/BTC');
      await ctx.shot('vols_BTC');
    });

    /* ───────────────────────── GEX ───────────────────────── */
    await section('GEX', async () => {
      await ctx.go('GEX');
      await waitMod(ctx, 'GEX', 'BTC');
      await page.evaluate(() => window.__otT.raf(3));
      const d = await page.evaluate(() => OT.opt.peek('/api/gex', { id: 'BTC' }));
      const gw = (await getJson(ctx.base + '/api/gex?id=BTC')).body;
      check(gw.as_of !== d.as_of || (gw.total === d.total && gw.flip === d.flip), 'GEX: the page payload differs from the gateway answer of the same as_of');
      check(d.unit === '$mm per 1% move' && d.expiries_listed >= 6 && d.oi_covered_pct > 90, 'GEX/BTC: unit / expiries / coverage: ' + JSON.stringify([d.unit, d.expiries_listed, d.oi_covered_pct]));
      const sum = a => a.reduce((x, y) => x + y, 0);
      check(Math.abs(sum(d.gex) - d.total) < 1e-3 * Math.max(1, Math.abs(d.total)) && Math.abs(sum(d.by_expiry.map(e => e.net)) - d.total) < 1e-3 * Math.max(1, Math.abs(d.total)),
        'GEX: by-strike (' + sum(d.gex) + ') and by-expiry (' + sum(d.by_expiry.map(e => e.net)) + ') sums do not equal the total ' + d.total);
      const strip = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="GEX"] .opt-stat')).map(s => [s.firstChild.textContent, s.lastChild.textContent]));
      const stat = n => (strip.find(s => s[0] === n) || [])[1];
      check(stat('Net, $mm per 1%') === gexFmt(d.total) && stat('Calls') === gexFmt(d.call_total) && stat('Puts') === gexFmt(d.put_total), 'GEX: totals show ' + JSON.stringify(strip.slice(0, 3)) + ', payload ' + [d.total, d.call_total, d.put_total]);
      check((stat('Call wall') || '') === (d.call_wall ? strikeFmt(d.call_wall.strike) : '—') && (stat('Put wall') || '') === (d.put_wall ? strikeFmt(d.put_wall.strike) : '—'), 'GEX: walls show ' + stat('Call wall') + ' / ' + stat('Put wall'));
      check(d.flip === null ? /none/.test(stat('Zero gamma')) : stat('Zero gamma').indexOf(num(d.flip, 2)) === 0, 'GEX: zero gamma shows ' + stat('Zero gamma') + ', payload ' + d.flip);
      check(stat('Open interest used').indexOf(num(d.oi_covered_pct, 1) + '%') === 0 && stat('Open interest used').indexOf(d.expiries_used + '/' + d.expiries_listed) > 0, 'GEX: coverage shows ' + stat('Open interest used'));
      const txt = await page.evaluate(() => document.querySelector('.mod[data-mod="GEX"]').textContent);
      check(/Sign convention/.test(txt) && /long calls/.test(txt) && /short puts/.test(txt) && /assumption/.test(txt), 'GEX: the sign convention is not stated on the page');
      check(/Formula/.test(txt) && /gamma × call OI/.test(txt) && /1%/.test(txt) && /Black-76/.test(txt) && /DERIVED/.test(txt), 'GEX: the formula / DERIVED label is not on the page');
      const texts = await page.evaluate(() => (window.__otCanvasDraws || []).map(x => x.text));
      check(texts.indexOf('$mm per 1% move') >= 0 && texts.indexOf('Strike, USD') >= 0 && texts.indexOf('Underlying level, USD') >= 0, 'GEX: axis titles with units are not drawn');
      check(texts.some(t => /^Index [\d,.]+$/.test(t)) && (d.flip === null || texts.some(t => /^Zero gamma [\d,.]+$/.test(t))) && texts.some(t => /^Call wall /.test(t)) && texts.some(t => /^Put wall /.test(t)), 'GEX: spot / zero-gamma / wall markers are not labelled on the chart');
      // by-expiry table = the payload
      const rows = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="GEX"] .gex-table tbody tr')).map(tr => ({ key: tr.dataset.key, net: tr.querySelector('td[data-col="net"]').textContent, oi: tr.querySelector('td[data-col="oi_pct"]').textContent })));
      check(rows.length === d.by_expiry.length && rows.every((r, i) => r.key === d.by_expiry[i].key && r.net === gexFmt(d.by_expiry[i].net) && r.oi === num(d.by_expiry[i].oi_pct, 1)), 'GEX: the by-expiry table differs from the payload: ' + JSON.stringify(rows.slice(0, 2)));
      // hover a bar: the payload of that strike
      const i = d.strikes.indexOf(d.put_wall ? d.put_wall.strike : d.strikes[Math.floor(d.strikes.length / 2)]), k = d.strikes[i];
      await ctx.mouse.hover(await at('gex', k, 0.5));
      await page.evaluate(() => window.__otT.raf(2));
      const tip = await tipText(page);
      check(tip.indexOf('Strike ' + strikeFmt(k)) >= 0 && tip.indexOf('Net ' + gexFmt(d.gex[i])) >= 0 && tip.indexOf('Puts ' + gexFmt(d.put_gex[i])) >= 0, 'GEX: bar hover at ' + k + ' reads ' + JSON.stringify(tip.slice(0, 120)) + ', payload net ' + d.gex[i]);
      // profile hover
      const j = d.profile.pct.indexOf(5);
      await ctx.mouse.hover(await at('profile', d.profile.spots[j], 0.5));
      await page.evaluate(() => window.__otT.raf(2));
      const tip2 = await tipText(page);
      check(tip2.indexOf('Total exposure ' + gexFmt(d.profile.gex[j])) >= 0, 'GEX: profile hover reads ' + JSON.stringify(tip2.slice(0, 100)) + ', payload ' + d.profile.gex[j]);
      // zoom + pan + reset on the bars; net mode
      const p0 = await at('gex', d.spot, 0.5);
      await ctx.mouse.wheel(p0, -240, 3);
      await page.evaluate(() => window.__otT.raf(2));
      const v1 = await view('gex');
      check(v1 && v1[0] < d.spot && v1[1] > d.spot, 'GEX: wheel zoom about the level: ' + JSON.stringify(v1));
      await ctx.mouse.drag(await at('gex', d.spot, 0.7), -60, 0);
      const v2 = await view('gex');
      check(v1 && v2 && v2[0] > v1[0], 'GEX: drag does not pan: ' + JSON.stringify([v1, v2]));
      await page.locator(MOD('GEX') + ' .ot-tab[data-id="net"]').click();
      check((await page.evaluate(() => OT.opt.plots.gex.data.bars.sets.map(s => s.label).join())) === 'Net positive,Net negative', 'GEX: net mode');
      const v3 = await view('gex');
      check(v2 && v3 && Math.abs(v3[0] - v2[0]) < 1e-9, 'GEX: changing the bar mode loses the zoom');
      await page.locator(MOD('GEX') + ' .ot-tab[data-id="split"]').click();
      await page.evaluate(() => window.__otT.raf(2));
      const pm = await at('gex', null, 0.5);
      await page.mouse.dblclick(pm.x, pm.y);
      await page.mouse.move(5, 5);
      await page.evaluate(() => window.__otT.raf(2));
      check((await view('gex')) === null, 'GEX: double click does not reset the view');
      // a click on a by-expiry row chooses the shared expiry
      const pick = d.by_expiry[d.by_expiry.length - 1].key;
      await page.locator(MOD('GEX') + ' .gex-table tr[data-key="' + pick + '"]').click();
      check((await page.evaluate(() => OT.opt.expiry('BTC'))) === pick, 'GEX: a click on a by-expiry row does not choose that expiry');
      await ctx.assertNoBadText(MOD('GEX'), 'GEX/BTC');
      await ctx.shot('gex_BTC');
    });

    /* ───────────────────────── auto-refresh: in place, at the cache TTL ───────────────────────── */
    await section('refresh in place', async () => {
      await ctx.go('OPTS');
      await waitChain(ctx, 'BTC');
      const before = await page.evaluate(() => {
        const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), tr = box.querySelector('tr.atm');
        tr.click();
        window.__s3Row = tr; window.__s3Cell = tr.querySelector('td[data-col="c_mark"]');
        box.scrollTop = Math.max(0, box.scrollTop - 37);
        const key = OT.opt.pick('BTC', OT.opt.peek('/api/options', { id: 'BTC' }));
        return { scroll: box.scrollTop, key, as_of: OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: key }).as_of, sel: tr.dataset.key };
      });
      const t0 = Date.now(), n0 = reqs.length;
      await ctx.waitFor(([k, a]) => { const c = OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: k }); return c && c.as_of > a; }, { arg: [before.key, before.as_of], timeout: 40000, polling: 500, msg: 'OPTS refreshes by itself (a newer as_of within 40 s)' });
      await page.evaluate(() => window.__otT.raf(2));
      const after = await page.evaluate(() => {
        const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), tr = box.querySelector('tr.atm');
        const key = OT.opt.pick('BTC', OT.opt.peek('/api/options', { id: 'BTC' })), c = OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: key });
        const row = c.rows.find(r => String(r.strike) === window.__s3Row.dataset.key && r.type === 'C');
        return { scroll: box.scrollTop, sameRow: window.__s3Row.isConnected, sameCell: window.__s3Cell.isConnected && window.__s3Cell.parentNode === window.__s3Row, sel: window.__s3Row.classList.contains('sel'),
          cell: window.__s3Cell.textContent, mark: row ? row.mark : null, fwd: c.fwd, age: document.querySelector('.mod[data-mod="OPTS"] .mod-asof').textContent };
      });
      check(after.sameRow && after.sameCell, 'OPTS: a refresh rebuilds the rows instead of patching them in place');
      check(Math.abs(after.scroll - before.scroll) < 2, 'OPTS: a refresh moves the scroll position (' + before.scroll + ' → ' + after.scroll + ')');
      check(after.sel, 'OPTS: a refresh drops the selected row');
      check(after.cell === num(after.mark, after.fwd >= 10000 ? 1 : 2), 'OPTS: after the refresh the cell shows ' + after.cell + ', the new payload says ' + after.mark);
      check(/as of \d\d:\d\d:\d\dZ · \d+s ago/.test(after.age), 'OPTS: the age label reads ' + JSON.stringify(after.age));
      // leaving the tab and coming back: same rows, same scroll position, the selection still there, no loading cover
      const s0 = await page.evaluate(() => { const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'); box.scrollTop = Math.max(0, box.scrollTop - 61); return box.scrollTop; });
      await ctx.go('GEX');
      const back = await page.evaluate(() => { OT.go('OPTS'); const s = document.querySelector('.mod[data-mod="OPTS"]'); return { cover: !!s.querySelector('.mod-body > .ot-state'), rows: s.querySelectorAll('table.opt-chain tbody tr').length, badge: !!s.querySelector('.mod-src:not([hidden])') }; });
      check(!back.cover && back.rows > 3 && back.badge, 'OPTS: coming back to the tab hides the chain behind a loading state: ' + JSON.stringify(back));
      await page.evaluate(() => window.__otT.raf(3));
      await sleep(300);
      const s1 = await page.evaluate(() => ({ scroll: document.querySelector('.mod[data-mod="OPTS"] .opt-box').scrollTop, sel: window.__s3Row.isConnected && window.__s3Row.classList.contains('sel') }));
      check(Math.abs(s1.scroll - s0) < 2 && s1.sel, 'OPTS: leaving the tab and coming back loses the scroll position or the selection (' + s0 + ' → ' + s1.scroll + ', selected ' + s1.sel + ')');
      // never faster than the cache TTL: at most one request per route and expiry per 15 s
      await sleep(1500);
      const mine = reqs.slice(n0).filter(r => /\/api\/options\/chain/.test(r.u));
      const span = (Date.now() - t0) / 1000;
      check(mine.length <= Math.floor(span / 15) + 1, 'OPTS: ' + mine.length + ' chain requests in ' + span.toFixed(0) + ' s (cache TTL is 15 s)');
      const a1 = await page.evaluate(() => document.querySelector('.mod[data-mod="OPTS"] .mod-asof').textContent);
      await sleep(2100);
      const a2 = await page.evaluate(() => document.querySelector('.mod[data-mod="OPTS"] .mod-asof').textContent);
      check(a1 !== a2, 'OPTS: the age in the header does not count (' + a1 + ')');
    });

    /* ───────────────────────── instrument switching ───────────────────────── */
    await section('instrument switching', async () => {
      await ctx.go('OPTS');
      const n0 = reqs.length;
      // fast: four instruments in four frames; only the last one may end up on screen
      await page.evaluate(async () => { for (const id of ['ETH', 'SOL', 'AAPL', 'ETH']) { OT.select(id); await new Promise(r => requestAnimationFrame(r)); } });
      await waitChain(ctx, 'ETH');
      await sleep(1200);                                     // late answers of SOL / AAPL must not repaint the table
      const pc = await pageChain(page, 'ETH');
      const shown = await tableRows(page);
      const strikes = Array.from(new Set(pc.chain.rows.map(r => r.strike)));
      check(pc.chain.id === 'ETH' && Object.keys(shown).length === strikes.length && strikes.every(k => shown[String(k)]), 'OPTS: after fast switching the table is not the ETH chain (' + Object.keys(shown).length + ' rows, ETH has ' + strikes.length + ')');
      const head = await page.evaluate(() => document.querySelector('.mod[data-mod="OPTS"] .mod-inst-id').textContent);
      check(head === 'ETH', 'OPTS: header instrument is ' + head);
      // the first paint of a new instrument never shows the previous one's rows
      const flash = await page.evaluate(() => { OT.select('SOL'); const s = document.querySelector('.mod[data-mod="OPTS"]'); return { rows: s.querySelectorAll('table.opt-chain tbody tr').length, state: (s.querySelector('.mod-body > .ot-state') || { dataset: {} }).dataset.kind || null, badge: !!s.querySelector('.mod-src:not([hidden])') }; });
      check(flash.rows === 0 && flash.state === 'loading' && !flash.badge, 'OPTS: right after selecting SOL the page still shows the ETH chain / badge: ' + JSON.stringify(flash));
      await waitChain(ctx, 'SOL');
      const sol = await pageChain(page, 'SOL');
      check(sol.chain.native === 'USDC' && sol.chain.rows.every(r => /^SOL_USDC-/.test(r.name)) && sol.chain.spot > 1 && sol.chain.spot < 2000, 'OPTS/SOL: not the SOL_USDC chain');
      // expiry choice is per instrument: BTC still has the one picked in GEX, SOL its default
      check(sol.key === sol.list.default_expiry, 'OPTS/SOL: starts on ' + sol.key + ', default is ' + sol.list.default_expiry);
      await ctx.select('BTC');
      await waitChain(ctx, 'BTC');
      const btc = await pageChain(page, 'BTC');
      check(btc.key === (await page.evaluate(() => OT.opt.expiry('BTC'))) && btc.key !== btc.list.default_expiry, 'OPTS/BTC: the chosen expiry did not survive switching instruments (' + btc.key + ')');
      // every route asked at most once per instrument and expiry while switching
      const seen = {};
      reqs.slice(n0).forEach(r => { seen[r.u] = (seen[r.u] || 0) + 1; });
      const dup = Object.keys(seen).filter(u => seen[u] > 2);
      check(!dup.length, 'switching instruments re-requests: ' + dup.map(u => u + ' x' + seen[u]).join(', '));
    });

    /* ───────────────────────── CBOE: equity + index ───────────────────────── */
    await section('CBOE chain (AAPL)', async () => {
      await ctx.select('AAPL');
      await ctx.go('OPTS');
      await waitChain(ctx, 'AAPL');
      const pc = await pageChain(page, 'AAPL'), d = pc.chain;
      check(d.source === 'cboe' && d.greeks.origin === 'source' && d.rows.every(r => r.derived === false), 'OPTS/AAPL: CBOE chain should carry source greeks');
      const hb = await ctx.assertHeaderBadge('OPTS');
      check(/CBOE/.test(hb.badge || '') && /^(D15|CLOSED)/.test(hb.badge || ''), 'OPTS/AAPL: badge is ' + hb.badge);
      const shown = await tableRows(page);
      const c = d.rows.find(r => r.strike === d.atm_strike && r.type === 'C'), row = shown[String(d.atm_strike)];
      check(row && row.c_bid === num(c.bid, 2) && row.c_ask === num(c.ask, 2) && row.c_iv === iv(c.iv) && row.c_delta === num(c.delta, 3) && row.c_oi === qty(c.oi), 'OPTS/AAPL: ATM call row ' + JSON.stringify(row) + ' vs payload ' + JSON.stringify(c));
      check(row && row._atm, 'OPTS/AAPL: ATM row not marked');
      await ctx.go('GREEKS');
      await waitMod(ctx, 'GREEKS', 'AAPL');
      const txt = await page.evaluate(() => document.querySelector('.mod[data-mod="GREEKS"]').textContent);
      check(/SOURCE/.test(txt) && !/DERIVED/.test(txt.replace(/how it is computed/g, '')), 'GREEKS/AAPL: CBOE greeks must be labelled SOURCE, not DERIVED');
      await ctx.shot('greeks_AAPL');
      await ctx.go('GEX');
      await waitMod(ctx, 'GEX', 'AAPL');
      const g = await page.evaluate(() => OT.opt.peek('/api/gex', { id: 'AAPL' }));
      check(g.contract_size === 100 && g.oi_covered_pct > 80 && g.oi_covered_pct <= 100 && g.expiries_listed >= 8 && typeof g.source_gamma_total === 'number', 'GEX/AAPL: ' + JSON.stringify([g.contract_size, g.oi_covered_pct, g.expiries_listed, g.source_gamma_total]));
      const foot = await page.evaluate(() => document.querySelector('.mod[data-mod="GEX"] .opt-foot').textContent);
      check(/Cross-check with CBOE/.test(foot), 'GEX/AAPL: the cross-check with CBOE’s own gammas is not shown');
      await ctx.shot('gex_AAPL');
    });

    await section('CBOE index (SPX): a large chain', async () => {
      await ctx.select('SPX');
      await ctx.go('OPTS');
      const t0 = Date.now();
      await waitChain(ctx, 'SPX', { timeout: 45000 });
      const pc = await pageChain(page, 'SPX'), d = pc.chain;
      const strikes = new Set(d.rows.map(r => r.strike)).size;
      check(pc.list.expiries.length > 20 && pc.list.multi_root === true, 'OPTS/SPX: ' + pc.list.expiries.length + ' expiries, multi_root ' + pc.list.multi_root);
      check(Object.keys(await tableRows(page)).length === strikes, 'OPTS/SPX: not all ' + strikes + ' strikes are in the table');
      const atm = await page.evaluate(() => { const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box'), tr = box.querySelector('tr.atm'); if (!tr) return null; const b = box.getBoundingClientRect(), r = tr.getBoundingClientRect(); return { key: tr.dataset.key, inView: r.top >= b.top + 40 && r.bottom <= b.bottom }; });
      check(atm && atm.key === String(d.atm_strike) && atm.inView, 'OPTS/SPX: ATM row ' + JSON.stringify(atm) + ' (gateway ' + d.atm_strike + ') not in view in a ' + strikes + '-strike chain');
      // scrolling a long chain stays responsive: 20 wheel steps, frames keep coming
      const perf = await page.evaluate(async () => {
        const box = document.querySelector('.mod[data-mod="OPTS"] .opt-box');
        let frames = 0, worst = 0, last = performance.now(), run = true;
        const tick = t => { frames++; worst = Math.max(worst, t - last); last = t; if (run) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
        const t0 = performance.now();
        for (let i = 0; i < 20; i++) { box.scrollTop += 140; await new Promise(r => requestAnimationFrame(r)); }
        run = false;
        return { ms: performance.now() - t0, frames, worst };
      });
      check(perf.worst < 250, 'OPTS/SPX: scrolling the chain stalls (worst frame ' + Math.round(perf.worst) + ' ms)');
      const lvl = await page.evaluate(() => Array.from(document.querySelectorAll('.mod[data-mod="OPTS"] .opt-stat')).map(s => [s.firstChild.textContent, s.lastChild.textContent])[0]);
      check(d.spot_kind === 'implied' ? lvl[0] === 'Implied level' : lvl[0] === 'Underlying', 'OPTS/SPX: the level is labelled ' + lvl[0] + ' for spot_kind ' + d.spot_kind);
      await ctx.shot('opts_SPX');
      await ctx.go('VOLS');
      await waitMod(ctx, 'VOLS', 'SPX', 45000);
      await page.evaluate(() => window.__otT.raf(3));
      check((await painted(page, 'canvas[aria-label="Implied volatility surface"]')) > 2000, 'VOLS/SPX: surface empty');
      await ctx.shot('vols_SPX');
      await ctx.go('GEX');
      await waitMod(ctx, 'GEX', 'SPX', 45000);
      const g = await page.evaluate(() => OT.opt.peek('/api/gex', { id: 'SPX' }));
      check(g.expiries_listed > 20 && g.expiries_used > 20 && g.oi_covered_pct > 95, 'GEX/SPX: covers ' + g.oi_covered_pct + '% over ' + g.expiries_used + '/' + g.expiries_listed + ' expiries');
      await page.evaluate(() => window.__otT.raf(3));
      await ctx.shot('gex_SPX');
      console.log('      SPX: chain on screen ' + (Date.now() - t0) + ' ms after selecting it (first load of the CBOE file included)');
    });

    /* ───────────────────────── no options source ───────────────────────── */
    await section('instrument without options', async () => {
      const n0 = reqs.length;
      for (const id of ['CL', 'EURUSD', 'US10Y']) {
        await ctx.select(id);
        for (const m of ['OPTS', 'VOLS', 'GREEKS', 'GEX']) {
          await ctx.go(m);
          const s = await page.evaluate(mm => {
            const sec = document.querySelector('.mod[data-mod="' + mm + '"]'), st = sec.querySelector('.mod-cover:not([hidden]) .ot-state');
            return { kind: st ? st.dataset.kind : null, text: st ? st.textContent : '', picks: st ? Array.from(st.querySelectorAll('[data-pick]')).map(b => b.dataset.pick) : [], bodyHidden: sec.querySelector('.mod-body:not(.mod-cover)').hidden, badge: !!sec.querySelector('.mod-src:not([hidden])') };
          }, m);
          check(s.kind === 'nosource' && s.bodyHidden && !s.badge && /NO LIVE SOURCE/.test(s.text), m + '/' + id + ': expected the NO LIVE SOURCE state, got ' + JSON.stringify(s).slice(0, 160));
          check(s.picks.indexOf('BTC') >= 0 && s.picks.indexOf('SPX') >= 0 && s.picks.indexOf('AAPL') >= 0 && s.picks.length === 11, m + '/' + id + ': picks are ' + s.picks.join(' '));
        }
      }
      check(reqs.length === n0, 'an instrument without options still asks the gateway: ' + reqs.slice(n0).map(r => r.u).join(', '));
      // a pick is clickable and brings the module back with data
      await ctx.go('GEX');
      await page.locator(MOD('GEX') + ' .mod-cover [data-pick="ETH"]').click();
      await waitMod(ctx, 'GEX', 'ETH');
      check((await page.evaluate(() => OT.opt.peek('/api/gex', { id: 'ETH' }).id)) === 'ETH', 'GEX: the ETH pick did not load ETH');
    });

    /* ───────────────────────── failure paths (a second page; answers altered in transit) ───────────────────────── */
    await section('stale payload', async () => {
      const p2 = await ctx.open({ tag: 'stale' });
      const stale = async route => {
        const res = await route.fetch(), body = await res.json();
        body.stale = true; body.error = { code: 'upstream_error', message: 'HTTP 503 Service Unavailable', source: 'deribit' };
        await route.fulfill({ response: res, json: body });
      };
      await p2.page.route(/\/api\/(options|gex|vol_surface)/, stale);
      await p2.goto('OPTS/BTC');
      for (const m of ['OPTS', 'GEX']) {
        await p2.go(m);
        await p2.waitFor(mm => { const b = document.querySelector('.mod[data-mod="' + mm + '"] .mod-src:not([hidden]) .badge'); return b && /STALE/.test(b.textContent); }, { arg: m, timeout: 30000, msg: m + ': a stale answer is labelled STALE in the header' });
        const b = await p2.page.evaluate(mm => { const el = document.querySelector('.mod[data-mod="' + mm + '"] .mod-src .badge'); return { text: el.textContent, tip: el.getAttribute('data-tip') }; }, m);
        check(/HTTP 503/.test(b.tip || '') && /deribit/i.test(b.tip || ''), m + ': the STALE badge does not say why: ' + JSON.stringify(b));
      }
      const rows = await p2.page.evaluate(() => document.querySelectorAll('.mod[data-mod="OPTS"] table.opt-chain tbody tr').length);
      check(rows > 3, 'a stale chain should still be shown (with the STALE label)');
      await p2.close();
    });

    await section('source without quotes (blank file)', async () => {
      /* seen live 14:42-14:46 UTC on 2026-11-12: CBOE's delayed files carried no bid, ask or IV at all */
      const p5 = await ctx.open({ tag: 'blank' });
      const note = 'The source holds no implied volatility for any contract right now and no bid or ask: CBOE empties its delayed file';
      await p5.page.route(/\/api\/(options|gex|vol_surface)/, async route => {
        const res = await route.fetch(), body = await res.json();
        body.blank = true; body.note = note;
        (body.rows || []).forEach(r => { r.bid = r.ask = r.mark = r.iv = r.delta = r.gamma = r.vega = r.theta = null; });
        (body.expiries || []).forEach(e => { e.atm_iv = null; e.c25 = null; e.p25 = null; });
        if (body.expiry) body.atm_iv = null;
        if (body.iv) { body.moneyness = []; body.iv = body.iv.map(() => []); }
        if (body.strikes) { body.strikes = []; body.gex = []; body.call_gex = []; body.put_gex = []; body.call_oi = []; body.put_oi = []; body.total = body.call_total = body.put_total = null; body.flip = null; body.flip_pct = null;
          body.call_wall = body.put_wall = null; body.oi_covered_pct = 0; body.expiries_used = 0; body.profile = { spots: [], pct: [], gex: [], at_spot: null, note: body.profile.note }; body.by_expiry.forEach(e => { e.call = e.put = e.net = 0; }); }
        await route.fulfill({ response: res, json: body });
      });
      await p5.goto('GEX/AAPL');
      for (const m of ['GEX', 'VOLS', 'GREEKS']) {
        await p5.go(m);
        await p5.waitFor(mm => { const s = document.querySelector('.mod[data-mod="' + mm + '"] .ot-state[data-kind="empty"]'); return s && /NO QUOTES RIGHT NOW/.test(s.textContent) && /CBOE empties its delayed file/.test(s.textContent); }, { arg: m, timeout: 30000, msg: m + ': a file without quotes is reported as such over the charts' });
        const r = await p5.page.evaluate(mm => { const s = document.querySelector('.mod[data-mod="' + mm + '"]'); return { badge: !!s.querySelector('.mod-src:not([hidden]) .badge'), stats: Array.from(s.querySelectorAll('.opt-stat')).map(x => x.firstChild.textContent + '=' + x.lastChild.textContent) }; }, m);
        check(r.badge, m + ' (blank file): the header still says where the answer is from');
        if (m === 'GEX') check(r.stats.slice(0, 3).every(x => /=—$/.test(x)), 'GEX (blank file): totals must read "—", not 0: ' + r.stats.slice(0, 3).join(' '));
        await p5.assertNoBadText(MOD(m), m + ' blank');
      }
      await p5.go('OPTS');
      await waitChain(p5, 'AAPL');
      const o = await p5.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="OPTS"]'); const tr = s.querySelector('tr.atm') || s.querySelector('tbody tr'); return { note: s.querySelector('.mod-note').textContent, bid: tr.querySelector('td[data-col="c_bid"]').textContent, oi: Array.from(s.querySelectorAll('td[data-col="c_oi"]')).some(td => /\d/.test(td.textContent)) }; });
      check(/no quotes and no IV/.test(o.note) && o.bid === '—' && o.oi, 'OPTS (blank file): ' + JSON.stringify(o));
      await p5.shot('blank_OPTS_AAPL');
      await p5.go('GEX');
      await p5.shot('blank_GEX_AAPL');
      await p5.close();
    });

    await section('upstream error, then recovery', async () => {
      const p3 = await ctx.open({ tag: 'error' });
      p3.allow(/502|Failed to load resource/);
      let fail = true, hits = 0;
      await p3.page.route(/\/api\/(options|gex|vol_surface)/, async route => {
        hits++;
        if (!fail) return route.continue();
        await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'no answer within 15 s', source: 'deribit' } }) });
      });
      await p3.goto('GEX/BTC');
      await p3.waitFor(() => { const s = document.querySelector('.mod[data-mod="GEX"] .mod-body > .ot-state'); return s && s.dataset.kind === 'error'; }, { timeout: 20000, msg: 'GEX shows the error state when the gateway answers 502' });
      const st = await p3.page.evaluate(() => { const s = document.querySelector('.mod[data-mod="GEX"] .mod-body > .ot-state'); return { text: s.textContent, retry: !!s.querySelector('button'), badge: !!document.querySelector('.mod[data-mod="GEX"] .mod-src:not([hidden])'), stats: Array.from(document.querySelectorAll('.mod[data-mod="GEX"] .opt-stat-v')).map(v => v.textContent).join('') }; });
      check(/no answer within 15 s/.test(st.text) && /deribit/.test(st.text) && st.retry && !st.badge, 'GEX error state: ' + JSON.stringify(st).slice(0, 200));
      check(/^—*$/.test(st.stats), 'GEX: numbers are on screen although nothing was loaded: ' + st.stats);
      for (const m of ['OPTS', 'VOLS', 'GREEKS']) {
        await p3.go(m);
        await p3.waitFor(mm => { const s = document.querySelector('.mod[data-mod="' + mm + '"] .mod-body > .ot-state'); return s && s.dataset.kind === 'error'; }, { arg: m, timeout: 20000, msg: m + ' shows the error state' });
      }
      await p3.go('GEX');
      fail = false;
      await p3.page.locator(MOD('GEX') + ' .mod-body > .ot-state button').click();
      await waitMod(p3, 'GEX', 'BTC');
      check((await p3.page.evaluate(() => document.querySelector('.mod[data-mod="GEX"] .opt-stat-v').textContent)) !== '—', 'GEX: "Retry now" does not load the data once the source answers again');
      // data on screen, then the refresh fails: the data stays, the header says so
      fail = true;
      await p3.page.evaluate(() => { OT.go('OPTS'); });
      await p3.waitFor(() => { const s = document.querySelector('.mod[data-mod="OPTS"] .mod-body > .ot-state'); return s && s.dataset.kind === 'error'; }, { timeout: 20000, msg: 'OPTS error state (nothing cached for it)' });
      await p3.close();
    });

    await section('gateway stopped and restarted', async () => {
      const target = new URL(ctx.base);
      const proxy = ctx.tcpProxy(+target.port, target.hostname);
      await proxy.start();
      const p4 = await ctx.open({ tag: 'offline', base: proxy.base });
      p4.allow(/ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_SOCKET|WebSocket connection|Failed to load resource|net::|\/api\/|\/ws/);
      await p4.goto('OPTS/BTC');
      await waitChain(p4, 'BTC');
      const before = await p4.page.evaluate(() => { const k = OT.opt.pick('BTC', OT.opt.peek('/api/options', { id: 'BTC' })); return { k, as_of: OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: k }).as_of, rows: document.querySelectorAll('.mod[data-mod="OPTS"] table.opt-chain tbody tr').length }; });
      await proxy.stop();
      await p4.waitFor(() => OT.state.gateway === 'offline' && !document.getElementById('offline').hidden, { timeout: 20000, msg: 'the offline banner appears when the gateway stops' });
      const off = await p4.page.evaluate(() => {
        const td = document.querySelector('.mod[data-mod="OPTS"] tr.atm td[data-col="c_mark"]');
        return { rows: document.querySelectorAll('.mod[data-mod="OPTS"] table.opt-chain tbody tr').length, color: getComputedStyle(td).color, grey: getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim(), body: document.body.classList.contains('gw-offline') };
      });
      check(off.rows === before.rows && off.body, 'OPTS offline: the chain should stay on screen, greyed: ' + JSON.stringify(off));
      const hex = c => { const m = /rgba?\((\d+), (\d+), (\d+)/.exec(c); return m ? '#' + [1, 2, 3].map(i => (+m[i]).toString(16).padStart(2, '0')).join('') : c; };
      check(hex(off.color) === off.grey.toLowerCase(), 'OPTS offline: values are not greyed (' + off.color + ' vs ' + off.grey + ')');
      await sleep(17000);                                   // one poll fails while the gateway is down
      const note = await p4.page.evaluate(() => { const n = document.querySelector('.mod[data-mod="OPTS"] .mod-note'); return n && !n.hidden ? n.textContent : ''; });
      check(/refresh failed/.test(note) && /last answer/.test(note), 'OPTS offline: the header does not say that the refresh failed: ' + JSON.stringify(note));
      check((await p4.page.evaluate(() => document.querySelectorAll('.mod[data-mod="OPTS"] table.opt-chain tbody tr').length)) === before.rows, 'OPTS offline: the chain disappeared after a failed refresh');
      await proxy.start();
      await p4.waitFor(([k, a]) => { const c = OT.opt.peek('/api/options/chain', { id: 'BTC', expiry: k }); const n = document.querySelector('.mod[data-mod="OPTS"] .mod-note'); return OT.state.gateway === 'online' && c && c.as_of > a && n.hidden; }, { arg: [before.k, before.as_of], timeout: 60000, polling: 500, msg: 'OPTS recovers by itself after the gateway is back (newer chain, note cleared)' });
      await proxy.stop();
      await p4.close();
    });

    /* ───────────────────────── layout ───────────────────────── */
    await section('layout', async () => {
      await ctx.select('BTC');
      for (const [w, hgt] of [[1920, 1080], [1366, 768], [1280, 720]]) {
        await ctx.setViewport(w, hgt);
        for (const m of ['OPTS', 'VOLS', 'GREEKS', 'GEX']) {
          await ctx.go(m);
          await (m === 'OPTS' ? waitChain(ctx, 'BTC') : waitMod(ctx, m, 'BTC'));
          await page.evaluate(() => window.__otT.raf(3));
          const r = await page.evaluate(mm => {
            const sec = document.querySelector('.mod[data-mod="' + mm + '"]'), body = sec.querySelector('.mod-body:not(.mod-cover)'), wrap = sec.querySelector('.opt-wrap');
            const b = body.getBoundingClientRect(), out = { cut: [], empty: 0 };
            if (body.scrollWidth > body.clientWidth + 1 || body.scrollHeight > body.clientHeight + 1) out.cut.push('the module body scrolls (' + body.scrollWidth + 'x' + body.scrollHeight + ' in ' + body.clientWidth + 'x' + body.clientHeight + ')');
            sec.querySelectorAll('.opt-strip, .opt-foot, .panel, .opt-box, canvas').forEach(el => {
              const q = el.getBoundingClientRect();
              if (q.width < 1) return;
              if (q.right > b.right + 1 || q.bottom > b.bottom + 1 || q.left < b.left - 1) out.cut.push(el.className + ' sticks out');
              if (el.tagName === 'CANVAS' && (q.width < 120 || q.height < 70)) out.cut.push('canvas ' + el.getAttribute('aria-label') + ' is ' + Math.round(q.width) + 'x' + Math.round(q.height));
            });
            const box = sec.querySelector('.opt-box');
            if (box && box.scrollWidth > box.clientWidth + 1) out.cut.push('the chain needs horizontal scrolling (' + box.scrollWidth + ' > ' + box.clientWidth + ')');
            sec.querySelectorAll('.panel-head, .opt-strip').forEach(el => { if (el.scrollWidth > el.clientWidth + 1 && !el.querySelector('.opt-hint')) out.cut.push(el.className + ' content cut'); });
            // empty area: the part of the body not covered by the strip, a panel, the table or the foot
            let used = 0;
            sec.querySelectorAll('.opt-strip, .opt-foot, .gex-formula, .panel, .opt-box').forEach(el => { const q = el.getBoundingClientRect(); used += q.width * q.height; });
            out.empty = 1 - used / (b.width * b.height);
            const t = sec.querySelector('.opt-box table');
            if (t) { const tb = t.getBoundingClientRect(), bb = sec.querySelector('.opt-box').getBoundingClientRect(); out.tableFill = Math.min(1, tb.height / bb.height); }
            return out;
          }, m);
          check(!r.cut.length, m + ' @' + w + 'x' + hgt + ': ' + r.cut.slice(0, 4).join('; '));
          check(r.empty < 0.25, m + ' @' + w + 'x' + hgt + ': ' + Math.round(r.empty * 100) + '% of the module area is empty');
          await ctx.assertNoOverflow(m + ' @' + w + 'x' + hgt);
        }
      }
      await ctx.setViewport(1920, 1080);
    });

    /* leave every module on an instrument with data: the runner audits them as they were last used */
    await section('final', async () => {
      await ctx.select('SPX'); await ctx.go('OPTS'); await waitChain(ctx, 'SPX', { timeout: 45000 });
      await ctx.select('AAPL'); await ctx.go('GREEKS'); await waitMod(ctx, 'GREEKS', 'AAPL');
      await ctx.select('ETH'); await ctx.go('VOLS'); await waitMod(ctx, 'VOLS', 'ETH');
      await ctx.shot('vols_ETH');
      await ctx.select('SOL'); await ctx.go('GEX'); await waitMod(ctx, 'GEX', 'SOL');
      await ctx.shot('gex_SOL');
      await ctx.assertCanvasText(12, 'options canvases');
    });
  },
};
