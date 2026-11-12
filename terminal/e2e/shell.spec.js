/* Shell spec (FF): header, rail, tabs, command line, keyboard, hash routing, gateway truth,
   layout at four viewports, fonts, core helpers and OT.GeoMap. Works against the real gateway
   and against a mock: it only compares the page with what the gateway itself answers. */
'use strict';
const http = require('http');

const NUM = /^-?\d{1,3}(,\d{3})*(\.\d+)?$/;
const PCT = /^[+-]?\d+(\.\d+)?%$/;
const VIEWPORTS = [[1280, 720], [1366, 768], [1920, 1080], [2560, 1440]];

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
const railRows = page => page.evaluate(() => Array.from(document.querySelectorAll('#watch .wrow')).map(r => ({
  id: r.dataset.id, name: r.querySelector('.w-name').textContent, last: r.querySelector('.w-last').textContent,
  pct: r.querySelector('.w-pct').textContent, dot: r.querySelector('.dot').className, sel: r.classList.contains('sel'),
  tip: r._tip ? r._tip() : '', spark: (() => { const c = r.querySelector('canvas'); try { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; } catch (e) { return -1; } })(),
})));
const st = page => page.evaluate(() => ({ sel: OT.state.sel, mod: OT.state.mod, gw: OT.state.gateway, ws: OT.state.ws, hash: location.hash }));

/* THE PRICE GATE. A page that shows a number the gateway did not give it must fail — exactly, not "within 2 %":
     1. every rail row and the header show exactly OT.fmt.px(id, OT.quotes[id].last) (read in the frame that painted them);
     2. what the page holds is what the gateway said: an /api/quotes answer fetched by the page is compared with
        OT.quotes, and wherever the source time `ts` is the same the price must be identical; every polled
        instrument must be matched that way at least once;
     3. a streamed price must be one of the `quote` frames the websocket delivered (or a REST answer);
     4. for four seconds every change of a displayed price is checked against what was received for that
        instrument: a value nobody sent, or a repaint that alters it, fails. */
async function truth(ctx, allIds, uni, label) {
  const { page, check } = ctx;
  const r = await page.evaluate(async ids => {
    const out = { rows: [], held: [], unmatched: [], foreign: [], invented: [], changes: 0, hdr: null };
    const frame = () => new Promise(res => OT.requestFrame(() => res()));
    const received = id => {
      const s = new Set();
      window.__otWsRecv.forEach(f => { if (f.id === id && typeof f.last === 'number') s.add(OT.fmt.px(id, f.last)); });
      window.__otRestQuotes.forEach(a => { const v = a.quotes[id]; if (typeof v === 'number') s.add(OT.fmt.px(id, v)); });
      return s;
    };
    const want = id => { const q = OT.quotes[id]; return q && typeof q.last === 'number' && isFinite(q.last) && q.status !== 'none' ? OT.fmt.px(id, q.last) : '—'; };
    // 1. displayed == held, read inside the frame that painted
    await frame();
    document.querySelectorAll('#watch .wrow').forEach(row => { const id = row.dataset.id, shown = row.querySelector('.w-last').textContent; if (shown !== want(id)) out.rows.push(id + ' shows ' + shown + ' but the page holds ' + want(id)); });
    const sel = OT.state.sel, hl = document.getElementById('hdr-last').textContent;
    if (hl !== want(sel)) out.hdr = sel + ' header shows ' + hl + ' but the page holds ' + want(sel);
    // 2. held == what /api/quotes says, wherever the source time is the same
    const matched = new Set();
    for (let attempt = 0; attempt < 6 && matched.size < ids.length; attempt++) {
      if (attempt) await new Promise(res => setTimeout(res, 1000));
      const body = await (await fetch('/api/quotes', { cache: 'no-store' })).json();
      ids.forEach(id => {
        const g = body.quotes[id], p = OT.quotes[id];
        if (!g || !p) return;
        if (g.status === 'none' && (p.status === 'none' || p.last == null)) { matched.add(id); return; }
        if (g.ts !== p.ts) return;                                   // the page already holds a newer (or older) observation
        matched.add(id);
        if (g.last !== p.last) out.held.push(id + ': the page holds ' + p.last + ' for ts ' + p.ts + ' but /api/quotes says ' + g.last);
      });
    }
    ids.forEach(id => { if (!matched.has(id) && !window.__otWsRecv.some(f => f.id === id && f.last === OT.quotes[id].last)) out.unmatched.push(id); });
    // 3. a price on screen was delivered by the stream or by REST
    await frame();
    ids.forEach(id => { const shown = document.querySelector('#watch .wrow[data-id="' + id + '"] .w-last').textContent; if (shown !== '—' && !received(id).has(shown)) out.foreign.push(id + ' shows ' + shown + ', which no quote frame and no /api/quotes answer contained'); });
    // 4. four seconds of changes: each one must be a value that was received
    const obs = new MutationObserver(list => {
      const rows = new Set();
      list.forEach(m => { const el = m.target.nodeType === 1 ? m.target : m.target.parentElement; const row = el && el.closest && el.closest('.wrow'); if (row && el.closest('.w-last')) rows.add(row); if (el && el.closest && el.closest('#hdr-last')) rows.add('hdr'); });
      rows.forEach(row => {
        out.changes++;
        const id = row === 'hdr' ? OT.state.sel : row.dataset.id;
        const shown = row === 'hdr' ? document.getElementById('hdr-last').textContent : row.querySelector('.w-last').textContent;
        if (shown !== '—' && !received(id).has(shown)) out.invented.push(id + (row === 'hdr' ? ' (header)' : '') + ' changed to ' + shown + ', a value that was never received');
      });
    });
    obs.observe(document.getElementById('watch'), { subtree: true, childList: true, characterData: true });
    obs.observe(document.getElementById('hdr-last'), { subtree: true, childList: true, characterData: true });
    await new Promise(res => setTimeout(res, 4000));
    obs.disconnect();
    return out;
  }, allIds);
  check(!r.rows.length && !r.hdr, label + ': displayed price differs from the quote the page holds: ' + r.rows.concat(r.hdr || []).slice(0, 6).join(' | '));
  check(!r.held.length, label + ': the page holds a price the gateway did not give: ' + r.held.slice(0, 6).join(' | '));
  check(!r.unmatched.length, label + ': could not match these quotes against /api/quotes or a websocket frame: ' + r.unmatched.join());
  check(!r.foreign.length, label + ': ' + r.foreign.slice(0, 6).join(' | '));
  check(!r.invented.length, label + ': ' + r.invented.slice(0, 6).join(' | '));
  if (allIds.some(id => uni.instruments[id].cls === 'crypto')) check(r.changes > 0, label + ': no displayed price changed in 4 s although the crypto stream is live');
  console.log('      ' + label + ': prices exact; ' + r.changes + ' live changes in 4 s, all of them values the gateway sent');
}

module.exports = {
  name: 'shell', slice: 'shell', truth,
  async run(ctx) {
    const { page, check } = ctx;
    // OT_E2E_SECTION=geomap,rail runs only the sections whose name contains one of the words (debugging; default: all)
    const only = (process.env.OT_E2E_SECTION || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean);
    const section = async (name, fn) => {
      if (only.length && !only.some(w => name.toLowerCase().indexOf(w) >= 0)) return;
      try { await fn(); } catch (e) { ctx.fail(name + ' threw: ' + (e && e.stack || e)); }
    };
    const uni = await getJson(ctx.base + '/api/universe');
    const allIds = [].concat(...uni.groups.map(g => g.ids));
    const withCap = cap => allIds.filter(id => uni.instruments[id].caps && uni.instruments[id].caps[cap]);
    const without = cap => allIds.filter(id => !(uni.instruments[id].caps && uni.instruments[id].caps[cap]));

    await section('boot', async () => {
      const s = await st(page);
      check(s.gw === 'online', 'gateway state after boot is ' + s.gw);
      check(/^#[A-Z]+\/[A-Z0-9]+$/.test(s.hash), 'hash after boot is ' + s.hash);
      check(await page.evaluate(() => document.doctype && document.doctype.name === 'html' && document.compatMode === 'CSS1Compat'), 'page is not in standards mode');
      check(await page.evaluate(() => !document.querySelector('script:not([src])') && !document.querySelector('style')), 'openterm.html contains inline script or style');
      check(await page.evaluate(() => typeof window.LightweightCharts === 'object' && typeof window.LightweightCharts.createChart === 'function' && /^4\.1\.3/.test(window.LightweightCharts.version())), 'vendored lightweight-charts 4.1.3 is not loaded');
      check(await page.evaluate(() => typeof window.Plotly === 'undefined'), 'Plotly must not be loaded by the shell');
      check(await page.evaluate(() => Array.from(document.scripts).every(sc => sc.src.indexOf(location.origin) === 0) && Array.from(document.querySelectorAll('link[href]')).every(l => l.href.indexOf(location.origin) === 0)), 'a script or stylesheet is not served by the gateway (CDN?)');
      const clock = await page.evaluate(() => ({ t: document.getElementById('clock-time').textContent, d: document.getElementById('clock-date').textContent }));
      const now = new Date();
      const m = /^(\d\d):(\d\d):(\d\d)Z$/.exec(clock.t);
      check(!!m, 'clock text is ' + clock.t);
      if (m) {
        const shown = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), +m[1], +m[2], +m[3]);
        check(Math.abs(shown - now.getTime()) < 3000 || Math.abs(shown - now.getTime()) > 86397000, 'header clock is not real UTC: shows ' + clock.t + ' at ' + now.toISOString());
      }
      check(clock.d.indexOf(now.toISOString().slice(0, 10)) === 0, 'clock date is ' + clock.d);
      await ctx.assertNoBadText(null, 'boot');
      await ctx.assertMinFontPx(12, null, 'boot');
      await ctx.assertContrast(4.5, null, 'boot');
    });

    await section('rail', async () => {
      check(allIds.length === 23, 'the gateway universe has ' + allIds.length + ' instruments, expected 23');
      const q = (await getJson(ctx.base + '/api/quotes')).quotes;
      await page.evaluate(() => OT.refreshQuotes()); await page.evaluate(() => window.__otT.raf(3));
      const rows = await railRows(page);
      check(rows.length === allIds.length, 'rail has ' + rows.length + ' rows for ' + allIds.length + ' instruments');
      check(rows.map(r => r.id).join() === allIds.join(), 'rail order differs from /api/universe groups');
      let real = 0, none = 0;
      for (const r of rows) {
        const g = q[r.id] || {}, inst = uni.instruments[r.id];
        check(r.name === (inst.short || inst.name), r.id + ': rail name ' + JSON.stringify(r.name) + ' != universe short name ' + JSON.stringify(inst.short));
        check(r.tip.indexOf(inst.name) >= 0, r.id + ': the row tooltip does not give the full name ' + JSON.stringify(inst.name));
        if (typeof g.last === 'number' && g.status !== 'none') {
          real++;
          check(NUM.test(r.last), r.id + ': rail last is ' + JSON.stringify(r.last));
          const dec = (r.last.split('.')[1] || '').length;
          check(dec === (inst.decimals == null ? 2 : inst.decimals), r.id + ': shown with ' + dec + ' decimals, universe says ' + inst.decimals);
          if (typeof g.chg_pct === 'number') check(PCT.test(r.pct), r.id + ': rail chg % is ' + JSON.stringify(r.pct));
          check(!/d-none/.test(r.dot), r.id + ': has a value but the dot says "none"');
          if (g.status === 'stale') check(/d-stale/.test(r.dot), r.id + ': status stale but dot is ' + r.dot);
          if (Array.isArray(g.spark) && g.spark.length >= 2) check(r.spark > 20, r.id + ': quote has ' + g.spark.length + ' spark points but the sparkline canvas is empty');
          else check(r.spark === 0, r.id + ': no spark data but the sparkline canvas has ink (invented line?)');
          check(r.tip.indexOf(String(g.source || '').toUpperCase()) >= 0, r.id + ': hover text does not name the source: ' + JSON.stringify(r.tip));
        } else {
          none++;
          check(r.last === '—', r.id + ': no value from the gateway but rail shows ' + JSON.stringify(r.last));
          check(/no source|error/.test(r.pct), r.id + ': none-state label is ' + JSON.stringify(r.pct));
          check(/d-none/.test(r.dot), r.id + ': none-state dot is ' + r.dot);
          check(/NO SOURCE/.test(r.tip) && r.tip.length > 30, r.id + ': hover does not explain why there is no value: ' + JSON.stringify(r.tip));
          check(r.spark === 0, r.id + ': no value but the sparkline has ink');
        }
      }
      check(real + none === rows.length, 'rows neither real nor none');
      console.log('      rail: ' + real + ' rows with real values, ' + none + ' explicit none-state rows');
      await truth(ctx, allIds, uni, 'rail');
      // hover shows the reason / source in a tooltip (real mouse)
      const target = rows.find(r => r.last === '—') || rows[0];
      await ctx.mouse.hover('#watch .wrow[data-id="' + target.id + '"]');
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && t.textContent.length > 20; }, { msg: 'tooltip on rail row hover' });
      const tipText = await page.evaluate(() => (document.querySelector('.ot-tip') || {}).textContent || '');
      check(tipText.indexOf(target.id) === 0, 'rail tooltip text: ' + JSON.stringify(tipText.slice(0, 80)));
      await page.mouse.move(900, 500);
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return !t || t.hidden; }, { msg: 'tooltip hides when the mouse leaves' });
      // values patch in place: same DOM nodes after a refresh, selection survives
      await page.evaluate(() => { window.__rowEl = document.querySelector('#watch .wrow'); window.__lastEl = window.__rowEl.querySelector('.w-last'); return OT.refreshQuotes(); });
      check(await page.evaluate(() => window.__rowEl === document.querySelector('#watch .wrow') && window.__lastEl.isConnected), 'rail rows were rebuilt on refresh instead of patched in place');
    });

    await section('click rows', async () => {
      const noneRow = (await railRows(page)).find(r => r.last === '—');
      for (const id of [allIds[3], allIds[allIds.length - 1], allIds[8]].concat(noneRow ? [noneRow.id] : [])) {
        await ctx.mouse.click('#watch .wrow[data-id="' + id + '"]');
        await page.evaluate(() => window.__otT.raf(2));
        const s = await st(page);
        check(s.sel === id, 'clicking rail row ' + id + ' selected ' + s.sel);
        check(s.hash === '#' + s.mod + '/' + id, 'hash after click is ' + s.hash);
        const h = await page.evaluate(() => ({ id: document.getElementById('hdr-id').textContent, name: document.getElementById('hdr-name').textContent, last: document.getElementById('hdr-last').textContent,
          chg: document.getElementById('hdr-chg').textContent, pct: document.getElementById('hdr-pct').textContent, badge: document.querySelector('#hdr-badge .badge').textContent, age: document.getElementById('hdr-age').textContent,
          selRows: Array.from(document.querySelectorAll('#watch .wrow.sel')).map(r => r.dataset.id), railLast: document.querySelector('#watch .wrow.sel .w-last').textContent }));
        check(h.id === id && h.name.indexOf(uni.instruments[id].name) === 0, 'header shows ' + h.id + ' / ' + h.name + ' for ' + id);
        check(h.selRows.join() === id, 'selected rail rows: ' + h.selRows.join());
        check(h.badge.length >= 2, id + ': header badge is empty');
        if (h.last !== '—') {
          check(NUM.test(h.last), id + ': header last is ' + h.last);
          check(/^[+-]?\d/.test(h.chg) && PCT.test(h.pct), id + ': header chg / chg % are ' + h.chg + ' / ' + h.pct);
          check(/as of .*ago/.test(h.age), id + ': header age is ' + JSON.stringify(h.age));
          if (uni.instruments[id].cls !== 'crypto') check(h.last === h.railLast, id + ': header last ' + h.last + ' != rail last ' + h.railLast);
        } else check(/NO SOURCE/.test(h.badge), id + ': no value but header badge is ' + h.badge);
      }
      await ctx.assertNoBadText('#hdr', 'header');
      // lamps explain themselves on hover (real mouse)
      await ctx.mouse.hover('.lamp[data-lamp="gateway"]');
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /ONLINE/.test(t.textContent) && t.textContent.indexOf(location.origin) >= 0 && /WebSocket open/.test(t.textContent); }, { msg: 'gateway lamp tooltip explains state and age' });
      const lamps = await page.evaluate(() => Array.from(document.querySelectorAll('.lamp')).map(l => ({ k: l.dataset.lamp, s: l.dataset.state, tip: l._tip() })));
      check(lamps.length >= 2 && lamps[0].k === 'gateway' && lamps[1].k === 'quotes', 'lamps: ' + lamps.map(l => l.k).join());
      check(lamps.every(l => l.tip && l.tip.length > 15), 'a lamp has no explanation: ' + JSON.stringify(lamps));
      const st0 = await getJson(ctx.base + '/api/status');
      const feedNames = Object.keys(st0.feeds || {}).sort().map(n => 'feed:' + n).join();
      check(lamps.slice(2).map(l => l.k).join() === feedNames, 'feed lamps ' + lamps.slice(2).map(l => l.k).join() + ' != /api/status feeds ' + feedNames);
      await page.mouse.move(900, 500);
    });

    await section('tabs + capability states', async () => {
      const tabs = await page.evaluate(() => Array.from(document.querySelectorAll('#tabs .tab')).map(b => ({ mod: b.dataset.mod, title: b.title, group: b.closest('.tgroup').dataset.group })));
      const expect = ['MON', 'CHRT', 'BOOK', 'HEAT', 'FOOT', 'OPTS', 'VOLS', 'GREEKS', 'GEX', 'WX', 'SAT', 'SHIP', 'SPLC', 'NEWS', 'ECO', 'TNT', 'HELP'];
      check(tabs.map(t => t.mod).join() === expect.join(), 'tabs are ' + tabs.map(t => t.mod).join());
      check([...new Set(tabs.map(t => t.group))].join() === 'MARKETS,FLOW,OPTIONS,WORLD,MACRO,TOOLS', 'tab groups are ' + [...new Set(tabs.map(t => t.group))].join());
      check(tabs.every(t => /press (Shift\+)?\d/.test(t.title)), 'a tab has no shortcut documented in its title attribute');
      const noBook = without('book')[0], hasBook = withCap('book')[0];
      if (noBook) await ctx.select(noBook);
      for (const t of tabs) {
        await ctx.mouse.click('#tabs .tab[data-mod="' + t.mod + '"]');
        await page.evaluate(() => window.__otT.raf(2));
        const r = await page.evaluate(m => {
          const vis = Array.from(document.querySelectorAll('#main .mod')).filter(s => !s.hidden).map(s => s.dataset.mod || s.id);
          const sec = document.querySelector('.mod[data-mod="' + m + '"]');
          const b = sec && sec.getBoundingClientRect();
          return { vis, sel: (document.querySelector('#tabs .tab[aria-selected="true"]') || {}).dataset.mod, title: sec && sec.querySelector('.mod-title').textContent,
            w: b ? b.width : 0, h: b ? b.height : 0, text: sec ? sec.querySelector('.mod-body:not([hidden])').textContent.trim().length : 0,
            kind: sec && sec.querySelector('.mod-body:not([hidden]) .ot-state') ? sec.querySelector('.mod-body:not([hidden]) .ot-state').dataset.kind : null, needs: (OT.state.mod === m), hash: location.hash };
        }, t.mod);
        check(r.vis.join() === t.mod, t.mod + ': visible sections after click: ' + r.vis.join());
        check(r.sel === t.mod, t.mod + ': selected tab is ' + r.sel);
        check(r.hash.indexOf('#' + t.mod + '/') === 0, t.mod + ': hash is ' + r.hash);
        check(r.w > 600 && r.h > 300, t.mod + ': module area is ' + Math.round(r.w) + 'x' + Math.round(r.h));
        check(r.text > 10, t.mod + ': module body is blank');
        check(!!r.title, t.mod + ': module header has no title');
      }
      await ctx.assertNoBadText('#main', 'modules');
      // capability check → NO SOURCE with clickable picks (never a silent substitute)
      if (noBook && hasBook) {
        await ctx.select(noBook); await ctx.go('BOOK');
        const ns = await page.evaluate(() => { const s = document.querySelector('.mod[data-mod="BOOK"] .mod-cover:not([hidden]) .ot-state'); return s ? { kind: s.dataset.kind, text: s.textContent, picks: Array.from(s.querySelectorAll('[data-pick]')).map(b => b.dataset.pick), inst: document.querySelector('.mod[data-mod="BOOK"] .mod-inst-id').textContent } : null; });
        check(ns && ns.kind === 'nosource', 'BOOK for ' + noBook + ' does not show the NO SOURCE state');
        if (ns) {
          check(ns.picks.join() === withCap('book').join(), 'NO SOURCE picks ' + ns.picks.join() + ' != instruments with a book ' + withCap('book').join());
          check(ns.text.indexOf(noBook) >= 0 && /NO LIVE SOURCE/.test(ns.text), 'NO SOURCE text: ' + JSON.stringify(ns.text.slice(0, 160)));
          const why = (uni.instruments[noBook].why || {}).book;
          if (why) check(ns.text.indexOf(why.replace(/\.$/, '')) >= 0, 'NO SOURCE state does not give the gateway’s reason: ' + why);
          check(ns.inst === noBook, 'module header instrument is ' + ns.inst);
          await ctx.shotNow('shell_nosource_1920x1080');
          await ctx.assertContrast(4.5, '#main', 'nosource');
          await ctx.mouse.click('.mod[data-mod="BOOK"] [data-pick="' + hasBook + '"]');
          await page.evaluate(() => window.__otT.raf(2));
          const after = await page.evaluate(() => ({ sel: OT.state.sel, cover: !document.querySelector('.mod[data-mod="BOOK"] .mod-cover').hidden, body: !document.querySelector('.mod[data-mod="BOOK"] .mod-body').hidden }));
          check(after.sel === hasBook && !after.cover && after.body, 'clicking pick ' + hasBook + ' → ' + JSON.stringify(after));
        }
        // fast switching must not throw or leave two sections visible
        for (let i = 0; i < 40; i++) await page.evaluate(([m, id]) => { OT.go(m); OT.select(id); }, [expect[i % expect.length], allIds[(i * 7) % allIds.length]]);
        check(await page.evaluate(() => Array.from(document.querySelectorAll('#main .mod')).filter(s => !s.hidden).length) === 1, 'more than one module visible after fast switching');
      }
    });

    await section('command line', async () => {
      await ctx.go('MON'); await ctx.select(allIds[0]);
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.keyboard.press('/');
      check(await page.evaluate(() => document.activeElement.id) === 'cmd', '"/" did not focus the command line');
      check(await page.evaluate(() => document.getElementById('cmd').value) === '', '"/" was typed into the command line when focusing it');
      await page.keyboard.type('a/b');
      check(await page.evaluate(() => document.getElementById('cmd').value) === 'a/b', '"/" cannot be typed inside the command line (value ' + await page.evaluate(() => document.getElementById('cmd').value) + ')');
      await page.keyboard.press('Escape');
      check(await page.evaluate(() => document.activeElement.id !== 'cmd' && document.getElementById('cmd').value === ''), 'Esc did not leave and clear the command line');
      const run = async text => { await page.keyboard.press('/'); await page.keyboard.type(text); const hint = await page.evaluate(() => document.getElementById('cmd-hint').textContent); await page.keyboard.press('Enter'); await page.evaluate(() => window.__otT.raf(2)); return Object.assign(await st(page), { hint, hintAfter: await page.evaluate(() => document.getElementById('cmd-hint').textContent), focus: await page.evaluate(() => document.activeElement.id) }); };
      const has = id => allIds.indexOf(id) >= 0;
      if (has('CL') && has('BTC') && has('GC')) {
        let r = await run('BTC');
        check(r.sel === 'BTC' && r.mod === 'MON', '"BTC" → ' + r.sel + '/' + r.mod);
        r = await run('CHRT');
        check(r.sel === 'BTC' && r.mod === 'CHRT', '"CHRT" → ' + r.sel + '/' + r.mod);
        r = await run('cl mon');
        check(r.sel === 'CL' && r.mod === 'MON', '"cl mon" → ' + r.sel + '/' + r.mod);
        check(/CL/.test(r.hint) && /MON/.test(r.hint), 'preview hint for "cl mon" was ' + JSON.stringify(r.hint));
        r = await run('GEX GC');
        check(r.sel === 'GC' && r.mod === 'GEX', '"GEX GC" → ' + r.sel + '/' + r.mod);
        r = await run('gold');
        check(r.sel === 'GC', 'fuzzy "gold" → ' + r.sel);
        r = await run('bitcoin book');
        check(r.sel === 'BTC' && r.mod === 'BOOK', 'fuzzy "bitcoin book" → ' + r.sel + '/' + r.mod);
        r = await run('crude chart');
        check(r.sel === 'CL' && r.mod === 'CHRT', 'fuzzy "crude chart" → ' + r.sel + '/' + r.mod);
        r = await run('news');
        check(r.sel === 'CL' && r.mod === 'NEWS', '"news" → ' + r.sel + '/' + r.mod);
        r = await run('zzqx');
        check(r.sel === 'CL' && r.mod === 'NEWS' && /No instrument or module/.test(r.hintAfter) && r.focus === 'cmd', 'unknown command "zzqx" → ' + JSON.stringify(r));
        await page.keyboard.press('Escape');
        check(await page.evaluate(() => /Press \/ to focus/.test(document.getElementById('cmd').title)), 'command line has no usage title');
      }
      // "/" must not be stolen from another input
      await page.evaluate(() => { const i = document.createElement('input'); i.id = 'e2e-input'; i.className = 'input'; document.querySelector('.mod:not([hidden]) .mod-controls').appendChild(i); i.focus(); });
      await page.keyboard.type('1/2?[');
      const v = await page.evaluate(() => { const i = document.getElementById('e2e-input'); const o = { v: i.value, focus: document.activeElement.id, mod: OT.state.mod }; i.remove(); return o; });
      check(v.v === '1/2?[' && v.focus === 'e2e-input', 'shortcuts steal keys from a focused input: value ' + JSON.stringify(v.v) + ', focus ' + v.focus);
    });

    await section('keyboard', async () => {
      await ctx.go('MON'); await ctx.select(allIds[0]);
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      await page.keyboard.press('2'); check((await st(page)).mod === 'CHRT', 'key 2 → ' + (await st(page)).mod);
      await page.keyboard.press('0'); check((await st(page)).mod === 'WX', 'key 0 → ' + (await st(page)).mod);
      await page.keyboard.press('Shift+Digit7'); check((await st(page)).mod === 'HELP', 'Shift+7 → ' + (await st(page)).mod);
      await page.keyboard.press(']'); check((await st(page)).mod === 'MON', '] from HELP → ' + (await st(page)).mod);
      await page.keyboard.press('['); check((await st(page)).mod === 'HELP', '[ from MON → ' + (await st(page)).mod);
      await page.keyboard.press('1');
      await page.keyboard.press('Shift+Slash'); check((await st(page)).mod === 'HELP', '? → ' + (await st(page)).mod);
      await page.keyboard.press('1');
      await page.keyboard.press('ArrowDown'); check((await st(page)).sel === allIds[1], 'ArrowDown → ' + (await st(page)).sel);
      await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowUp'); check((await st(page)).sel === allIds[1], 'ArrowDown, ArrowUp → ' + (await st(page)).sel);
      await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp'); check((await st(page)).sel === allIds[0], 'ArrowUp stops at the first row: ' + (await st(page)).sel);
      // focus inside the rail: arrows move focus + selection, Enter selects, Tab order reaches it
      await page.focus('#watch .wrow[data-id="' + allIds[4] + '"]');
      await page.keyboard.press('Enter'); check((await st(page)).sel === allIds[4], 'Enter on a focused rail row → ' + (await st(page)).sel);
      await page.keyboard.press('ArrowDown');
      const f = await page.evaluate(() => ({ sel: OT.state.sel, focus: document.activeElement.dataset.id }));
      check(f.sel === allIds[5] && f.focus === allIds[5], 'ArrowDown in the rail → sel ' + f.sel + ', focus ' + f.focus);
      await page.keyboard.press('End'); check((await st(page)).sel === allIds[allIds.length - 1], 'End → ' + (await st(page)).sel);
      check(await page.evaluate(() => { const r = document.querySelector('#watch .wrow.sel').getBoundingClientRect(), w = document.getElementById('watch').getBoundingClientRect(); return r.top >= w.top - 1 && r.bottom <= w.bottom + 1; }), 'selected row is not scrolled into view');
      await page.keyboard.press('Home'); check((await st(page)).sel === allIds[0], 'Home → ' + (await st(page)).sel);
      // tabs by keyboard
      await page.focus('#tabs .tab[aria-selected="true"]');
      await page.keyboard.press('ArrowRight'); check((await st(page)).mod === 'CHRT', 'ArrowRight on tabs → ' + (await st(page)).mod);
      const ring = await page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return { w: cs.outlineWidth, s: cs.outlineStyle, tag: document.activeElement.className }; });
      check(ring.s !== 'none' && parseFloat(ring.w) >= 2, 'no visible focus ring on a keyboard-focused tab: ' + JSON.stringify(ring));
      await page.evaluate(() => document.activeElement.blur());
    });

    await section('hash routing', async () => {
      const a = allIds[allIds.length - 2], b = allIds[5];
      await page.evaluate(h => { location.hash = h; }, '#GEX/' + a);
      await ctx.waitFor(([m, i]) => OT.state.mod === m && OT.state.sel === i, { arg: ['GEX', a], msg: 'hashchange to #GEX/' + a });
      await page.evaluate(h => { location.hash = h; }, '#NOPE/' + b);
      await ctx.waitFor(([m, i]) => OT.state.mod === m && OT.state.sel === i, { arg: ['GEX', b], msg: 'unknown module in the hash keeps the module, applies the instrument' });
      const p2 = await ctx.open({ tag: 'deep-link' });
      await p2.goto('SHIP/' + a);
      const s = await st(p2.page);
      check(s.mod === 'SHIP' && s.sel === a, 'opening #SHIP/' + a + ' gave ' + s.mod + '/' + s.sel);
      check((await p2.page.evaluate(() => Array.from(document.querySelectorAll('#main .mod')).filter(x => !x.hidden).map(x => x.dataset.mod).join())) === 'SHIP', 'deep link did not show SHIP');
      p2.errors().forEach(e => ctx.fail(e));
      await p2.close();
    });

    await section('layout', async () => {
      await ctx.go('MON'); await ctx.select(allIds[0]);
      for (const [w, h] of VIEWPORTS) {
        await ctx.setViewport(w, h);
        const tag = w + 'x' + h;
        await ctx.assertNoOverflow(tag);
        await ctx.assertMinFontPx(12, null, tag);
        const r = await page.evaluate(() => {
          const out = [], vw = innerWidth;
          const kids = Array.from(document.querySelectorAll('#hdr > *')).map(e => e.getBoundingClientRect());
          for (let i = 1; i < kids.length; i++) if (kids[i].left < kids[i - 1].right - 0.5) out.push('header blocks ' + (i - 1) + ' and ' + i + ' overlap');
          Array.from(document.querySelectorAll('#hdr-inst > *, #lamps > *, .clock > *')).forEach(e => { const b = e.getBoundingClientRect(), p = document.getElementById('hdr').getBoundingClientRect(); if (b.right > vw + 0.5 || b.top < p.top - 0.5 || b.bottom > p.bottom + 0.5) out.push('header item outside the header: ' + e.className); });
          const nav = document.getElementById('tabs');
          Array.from(nav.querySelectorAll('.tab')).forEach(t => { const b = t.getBoundingClientRect(), n = nav.getBoundingClientRect(); if (b.right > n.right + 0.5 || b.left < n.left - 0.5) out.push('tab ' + t.dataset.mod + ' is outside the tab bar'); });
          const rail = document.getElementById('rail').getBoundingClientRect();
          Array.from(document.querySelectorAll('#watch .wrow > *')).forEach(c => { const b = c.getBoundingClientRect(); if (b.right > rail.right + 0.5 || b.left < rail.left - 0.5) out.push('rail cell outside the rail in row ' + c.parentNode.dataset.id); });
          Array.from(document.querySelectorAll('#watch .w-r')).forEach(c => { const l = c.querySelector('.w-last'); if (l.getBoundingClientRect().width > c.getBoundingClientRect().width + 1) out.push('rail price is clipped in row ' + c.parentNode.dataset.id); });
          const watch = document.getElementById('watch');
          const main = document.getElementById('main').getBoundingClientRect();
          return { out, railScrolls: watch.scrollHeight > watch.clientHeight + 1, railW: rail.width, mainW: main.width, mainH: main.height, fs: getComputedStyle(document.body).fontSize };
        });
        check(!r.out.length, tag + ': ' + r.out.slice(0, 6).join('; '));
        check(r.railW >= 279 && r.railW <= 381, tag + ': rail width ' + r.railW);
        if (h >= 1080) check(!r.railScrolls, tag + ': the watchlist needs scrolling although the screen is tall enough');
        check(r.mainW / w > 0.7 && r.mainH / h > 0.72, tag + ': main area is only ' + Math.round(r.mainW) + 'x' + Math.round(r.mainH));
        console.log('      ' + tag + ': rail ' + Math.round(r.railW) + 'px, main ' + Math.round(r.mainW) + 'x' + Math.round(r.mainH) + ', base font ' + r.fs + (r.railScrolls ? ', watchlist scrolls internally' : ''));
      }
      await ctx.setViewport(1920, 1080);
      await ctx.shot('shell');
      await ctx.shot('shell', { sizes: [[1280, 720], [2560, 1440]] });
    });

    await section('fonts', async () => {
      const f = await page.evaluate(async () => {
        await document.fonts.ready;
        const c = document.createElement('canvas').getContext('2d');
        const w = (font, s) => { c.font = font; return c.measureText(s).width; };
        const S = 'iiiiWWWW.,10';
        const span = document.createElement('span'); span.textContent = S; span.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-size:20px';
        document.body.appendChild(span); const domW = span.getBoundingClientRect().width; span.remove();
        return {
          check400: document.fonts.check('400 13px "JetBrains Mono"'), check700: document.fonts.check('700 13px "JetBrains Mono"'),
          faces: Array.from(document.fonts).map(x => x.family.replace(/"/g, '') + ' ' + x.weight + ' ' + x.status),
          family: getComputedStyle(document.body).fontFamily, otFont: OT.canvas.font(20), small: OT.canvas.font(9),
          jb: w(OT.canvas.font(20), S), serif: w('20px serif', S), generic: w('20px monospace', S), explicit: w('20px "JetBrains Mono"', S),
          i: w(OT.canvas.font(20), 'iiii'), W: w(OT.canvas.font(20), 'WWWW'), domW,
        };
      });
      check(f.check400 && f.check700, 'document.fonts.check says JetBrains Mono is not available (400: ' + f.check400 + ', 700: ' + f.check700 + ')');
      check(f.faces.filter(x => /JetBrains Mono (400|700) loaded/.test(x)).length === 2, 'font faces: ' + f.faces.join(', '));
      check(/^"?JetBrains Mono/.test(f.family), 'body font-family is ' + f.family);
      check(/JetBrains Mono/.test(f.otFont) && /monospace$/.test(f.otFont), 'OT.canvas.font has no fallback stack: ' + f.otFont);
      check(/ 12px /.test(f.small), 'OT.canvas.font(9) must clamp to 12 px, got ' + f.small);
      check(Math.abs(f.jb - f.serif) > 5, 'canvas text measures like the serif fallback (' + f.jb + ' vs ' + f.serif + '): the font is not used on canvas');
      check(Math.abs(f.jb - f.explicit) < 0.01, 'OT.canvas.font does not resolve to JetBrains Mono on canvas (' + f.jb + ' vs ' + f.explicit + ')');
      check(Math.abs(f.i - f.W) < 0.01, 'canvas font is not monospaced (iiii ' + f.i + ' vs WWWW ' + f.W + ')');
      check(Math.abs(f.jb - 12 * 20 * 0.6) < 0.5, 'JetBrains Mono advance should be 0.6 em: 12 chars at 20px = ' + f.jb);
      check(Math.abs(f.domW - f.jb) < 0.5, 'DOM text (' + f.domW + ') and canvas text (' + f.jb + ') do not use the same font');
      console.log('      fonts: canvas JetBrains Mono ' + f.jb + 'px vs serif ' + f.serif + 'px vs generic monospace ' + f.generic + 'px; DOM ' + f.domW + 'px');
      await ctx.assertCanvasText(12, 'shell canvases');

      // font file blocked: the fallback must still be a monospace face, in DOM and on canvas
      const p2 = await ctx.open({ tag: 'font-blocked', block: '**/*.woff2' });
      p2.allow(/\.woff2/);
      await p2.goto('');
      const g = await p2.page.evaluate(async () => {
        const c = document.createElement('canvas').getContext('2d');
        const w = (font, s) => { c.font = font; return c.measureText(s).width; };
        const dom = s => { const e = document.createElement('span'); e.textContent = s; e.style.cssText = 'position:absolute;visibility:hidden;white-space:pre'; document.querySelector('#watch').appendChild(e); const r = e.getBoundingClientRect().width; e.remove(); return r; };
        // this machine may have JetBrains Mono installed system-wide: also measure the stack WITHOUT it (what a PC without the font gets)
        const tail = OT.canvas.font(20).replace('"JetBrains Mono"', '"No Such Font 0451"');
        const domTail = s => { const e = document.createElement('span'); e.textContent = s; e.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-size:20px;font-family:' + getComputedStyle(document.documentElement).getPropertyValue('--mono').replace('"JetBrains Mono"', '"No Such Font 0451"'); document.body.appendChild(e); const r = e.getBoundingClientRect().width; e.remove(); return r; };
        return { tailI: w(tail, 'iiiiiiii'), tailW: w(tail, 'WWWWWWWW'), domTailI: domTail('iiiiiiii'), domTailW: domTail('WWWWWWWW'), local: document.fonts.check('20px "JetBrains Mono"'),
          loaded: Array.from(document.fonts).filter(x => x.status === 'loaded').length, i: w(OT.canvas.font(20), 'iiiiiiii'), W: w(OT.canvas.font(20), 'WWWWWWWW'), serifI: w('20px serif', 'iiiiiiii'), serifW: w('20px serif', 'WWWWWWWW'),
          domI: dom('iiiiiiii'), domW: dom('WWWWWWWW'), rows: document.querySelectorAll('#watch .wrow').length };
      });
      check(g.loaded === 0, 'font-blocked page still loaded ' + g.loaded + ' font face(s): the block did not work');
      check(Math.abs(g.i - g.W) < 0.01, 'font blocked: canvas fallback is proportional (iiiiiiii ' + g.i + ' vs WWWWWWWW ' + g.W + ')');
      check(Math.abs(g.domI - g.domW) < 0.01, 'font blocked: DOM fallback is proportional (' + g.domI + ' vs ' + g.domW + ')');
      check(Math.abs(g.serifI - g.serifW) > 5, 'sanity: serif should be proportional here');
      check(Math.abs(g.tailI - g.tailW) < 0.01 && g.tailI > 0, 'the fallback stack after "JetBrains Mono" is not monospace on canvas (' + g.tailI + ' vs ' + g.tailW + ')');
      check(Math.abs(g.domTailI - g.domTailW) < 0.01 && g.domTailI > 0, 'the fallback stack after "JetBrains Mono" is not monospace in the DOM (' + g.domTailI + ' vs ' + g.domTailW + ')');
      console.log('      font blocked: fallback stack without JetBrains Mono: canvas ' + g.tailI + ' = ' + g.tailW + 'px, DOM ' + g.domTailI + ' = ' + g.domTailW + 'px' + (g.local ? '  (note: JetBrains Mono is installed on this machine, so the blocked page still renders with the local copy)' : ''));
      check(g.rows === allIds.length, 'font blocked: page did not boot (' + g.rows + ' rows)');
      await p2.assertCanvasText(12, 'font-blocked canvases');
      await p2.assertNoOverflow('font-blocked');
      await p2.shotNow('shell_font_blocked_1920x1080');
      console.log('      font blocked: canvas iiiiiiii ' + g.i + 'px = WWWWWWWW ' + g.W + 'px (serif would be ' + g.serifI + ' vs ' + g.serifW + ')');
      p2.errors().forEach(e => ctx.fail(e));
      await p2.close();
    });

    await section('core helpers', async () => {
      const r = await page.evaluate(async () => {
        const out = {}, F = OT.fmt, bad = [NaN, undefined, null, Infinity, -Infinity, 'abc', '', {}, []];
        out.fmtBad = [];
        ['num', 'signed', 'pct', 'compact', 'age', 'utc', 'date', 'hm', 'asof'].forEach(k => bad.forEach(v => { const s = F[k](v); if (s !== '—') out.fmtBad.push(k + '(' + String(v) + ')=' + s); }));
        bad.forEach(v => { const s = F.px('CL', v); if (s !== '—') out.fmtBad.push('px(' + String(v) + ')=' + s); });
        out.fmt = [F.num(1234.567, 2), F.num(-0.0001, 2), F.signed(0.5, 2), F.signed(-0.5, 2), F.signed(0, 2), F.pct(1.234), F.compact(1234567), F.compact(999), F.compact(2.5e9), F.utc(0.001 + 1731400000), F.date(1731400000), F.age(Date.now() / 1000 - 125), F.age(Date.now() / 1000 - 7300), F.age(Date.now() - 5000), F.px('NOPE', 1.23456)].join(' | ');
        // API: dedupe + ApiError
        const p1 = OT.api('/api/universe'), p2 = OT.api('/api/universe');
        out.dedupe = p1 === p2; await p1;
        try { await OT.api('/api/quote', { id: 'NO_SUCH_ID_' }); out.apiErr = 'resolved'; } catch (e) { out.apiErr = { isApi: e instanceof OT.ApiError, status: e.status, code: e.code, msg: e.message }; }
        try { await OT.api('/api/definitely_not_a_route'); out.api404 = 'resolved'; } catch (e) { out.api404 = { isApi: e instanceof OT.ApiError, status: e.status, code: e.code, msg: e.message }; }
        // event bus + sub ref counting
        let n = 0; const off = OT.on('e2e', () => n++); OT.emit('e2e'); off(); OT.emit('e2e'); out.bus = n;
        const u1 = OT.sub('trades:E2E'), u2 = OT.sub('trades:E2E'); u1(); out.sub1 = OT.topics().indexOf('trades:E2E') >= 0; u1(); u2(); out.sub2 = OT.topics().indexOf('trades:E2E') >= 0;
        // poll: never overlaps, pauses while the tab is hidden
        let running = 0, maxRun = 0, calls = 0;
        const stop = OT.poll(async () => { running++; calls++; maxRun = Math.max(maxRun, running); await new Promise(r => setTimeout(r, 420)); running--; }, 250, { module: null });
        await new Promise(r => setTimeout(r, 1500));
        out.pollCalls = calls; out.pollMax = maxRun;
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'));
        await new Promise(r => setTimeout(r, 700)); const c1 = calls; await new Promise(r => setTimeout(r, 1200)); out.pollHiddenCalls = calls - c1;
        delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));
        await new Promise(r => setTimeout(r, 900)); out.pollResumed = calls - c1; stop();
        const c2 = calls; await new Promise(r => setTimeout(r, 900)); out.pollAfterStop = calls - c2;
        // table: keyed in-place patching, sorting, selection survives
        const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:400px;top:200px;width:500px;height:300px;overflow:auto;z-index:50'; host.id = 'e2e-table'; document.body.appendChild(host);
        let clicked = null;
        const t = OT.ui.table(host, { key: 'id', sortable: true, onRowClick: row => { clicked = row.id; }, columns: [{ id: 'id', label: 'Id' }, { id: 'v', label: 'Value', num: true, fmt: v => OT.fmt.num(v, 2), flash: true, cls: v => (v > 0 ? 'up' : 'down') }] });
        t.update([{ id: 'a', v: 3 }, { id: 'b', v: -1 }, { id: 'c', v: null }, { id: 'd', v: 7 }]);
        const tdB = host.querySelector('tr[data-key="b"] td:nth-child(2)'), trB = tdB.parentNode;
        t.select('b');
        t.update([{ id: 'a', v: 3 }, { id: 'b', v: 5 }, { id: 'c', v: null }, { id: 'd', v: 7 }]);
        out.tablePatched = host.querySelector('tr[data-key="b"] td:nth-child(2)') === tdB && tdB.textContent === '5.00' && trB.classList.contains('sel') && tdB.className.indexOf('up') >= 0;
        out.tableText = host.textContent;
        host.querySelector('th[data-col="v"]').click();
        out.sortDesc = Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        out.aria = host.querySelector('th[data-col="v"]').getAttribute('aria-sort');
        host.querySelector('th[data-col="v"]').click();
        out.sortAsc = Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        host.querySelector('th[data-col="v"]').click();
        out.sortNone = Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        t.update([{ id: 'd', v: 7 }, { id: 'b', v: 5 }]);
        out.removed = Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('') === 'db' && host.querySelector('tr[data-key="b"]') === trB;
        host.querySelector('tr[data-key="d"] td').click(); out.clicked = clicked;
        const cs = getComputedStyle(host.querySelector('td.num')), ch = getComputedStyle(host.querySelector('th.num'));
        out.align = cs.textAlign + '/' + ch.textAlign + '/' + cs.fontVariantNumeric;
        out.sticky = getComputedStyle(host.querySelector('th')).position;
        // states
        const sh = document.createElement('div'); sh.style.cssText = 'position:relative;height:120px'; host.appendChild(sh);
        out.states = ['loading', 'nosource', 'error', 'closed', 'empty'].map(k => { const s = OT.ui.state(sh, { kind: k, title: 'T ' + k, detail: 'D', picks: k === 'nosource' ? [Object.keys(OT.universe.instruments)[0]] : null, retryIn: k === 'error' ? 5 : undefined }); const o = k + ':' + sh.querySelectorAll('.ot-state').length + ':' + (s.el.classList.contains('strip') ? 'strip' : 'cover') + ':' + s.el.querySelectorAll('[data-pick]').length; return o; }).join(' ');
        OT.ui.state(sh, null); out.stateCleared = sh.querySelectorAll('.ot-state').length === 0;
        // badges
        out.badges = ['RT', 'D10', 'D15', 'EOD', 'CLOSED', 'STALE', 'none'].map(b => { const e = OT.ui.badge(b, 'src'); return e.className.replace('badge ', '') + '=' + e.textContent; }).join(' ');
        // age label keeps counting
        const born = Date.now() / 1000 - 58; const al = OT.ui.ageLabel(() => born); host.appendChild(al); out.age0 = al.textContent;
        await new Promise(r => setTimeout(r, 3200)); out.age1 = al.textContent;
        // canvas fit is DPR-correct
        const cvs = document.createElement('canvas'); cvs.className = 'ot-canvas'; const box = document.createElement('div'); box.style.cssText = 'width:300px;height:100px'; box.appendChild(cvs); host.appendChild(box);
        const fit = OT.canvas.fit(cvs); out.fit = [fit.w, fit.h, cvs.width, cvs.height, fit.dpr, window.devicePixelRatio].join(',');
        host.remove();
        return out;
      });
      check(!r.fmtBad.length, 'formatters print something other than — for bad input: ' + r.fmtBad.slice(0, 8).join(', '));
      check(r.fmt === '1,234.57 | 0.00 | +0.50 | -0.50 | 0.00 | +1.23% | 1.23M | 999 | 2.50B | 08:26:40Z | 2024-11-12 | 2m | 2h | 5s | 1.23', 'formatter output: ' + r.fmt);
      check(r.dedupe === true, 'OT.api does not dedupe in-flight requests');
      check(r.apiErr && r.apiErr.isApi && r.apiErr.status >= 400 && typeof r.apiErr.code === 'string' && r.apiErr.msg, 'OT.api error for an unknown instrument: ' + JSON.stringify(r.apiErr));
      check(r.api404 && r.api404.isApi && r.api404.status === 404, 'OT.api error for an unknown route: ' + JSON.stringify(r.api404));
      check(r.bus === 1, 'event bus off() does not work');
      check(r.sub1 === true && r.sub2 === false, 'OT.sub ref counting is wrong: ' + r.sub1 + ', ' + r.sub2);
      check(r.pollMax === 1 && r.pollCalls >= 2 && r.pollCalls <= 3, 'OT.poll overlapped or mis-timed: max concurrency ' + r.pollMax + ', ' + r.pollCalls + ' calls in 1.5 s (fn takes 420 ms, interval 250 ms)');
      check(r.pollHiddenCalls === 0, 'OT.poll ran ' + r.pollHiddenCalls + ' time(s) while the tab was hidden');
      check(r.pollResumed >= 1, 'OT.poll did not resume when the tab became visible');
      check(r.pollAfterStop === 0, 'OT.poll kept running after stop()');
      check(r.tablePatched, 'OT.ui.table did not patch in place (cell node replaced, or selection/class lost)');
      check(!/NaN|undefined|null/.test(r.tableText), 'table printed a bad value: ' + r.tableText);
      check(r.sortDesc === 'dbac' && r.aria === 'descending' && r.sortAsc === 'abdc' && r.sortNone === 'abcd', 'table sorting: desc ' + r.sortDesc + ' (' + r.aria + '), asc ' + r.sortAsc + ', none ' + r.sortNone);
      check(r.removed, 'table did not remove/reorder rows by key');
      check(r.clicked === 'd', 'table row click gave ' + r.clicked);
      check(r.align === 'right/right/tabular-nums' && r.sticky === 'sticky', 'numeric cells: ' + r.align + ', header position ' + r.sticky);
      check(r.states === 'loading:1:cover:0 nosource:1:cover:1 error:1:cover:0 closed:1:strip:0 empty:1:cover:0' && r.stateCleared, 'OT.ui.state: ' + r.states + ' cleared=' + r.stateCleared);
      check(r.badges === 'b-rt=RTSRC b-delayed=D10SRC b-delayed=D15SRC b-eod=EODSRC b-closed=CLOSEDSRC b-stale=STALESRC b-none=NO SOURCESRC', 'badges: ' + r.badges);
      check(r.age0 === '58s' && r.age1 === '1m', 'age label did not keep counting: ' + r.age0 + ' → ' + r.age1);
      const fit = r.fit.split(',').map(Number);
      check(fit[0] === 300 && fit[1] === 100 && fit[2] === Math.round(300 * fit[5]) && fit[3] === Math.round(100 * fit[5]), 'OT.canvas.fit: ' + r.fit);
      ctx.allow(/NO_SUCH_ID_|definitely_not_a_route|trades:E2E/);
    });

    await section('module lifecycle', async () => {
      const noBook = without('book')[0], b1 = withCap('book')[0], b2 = withCap('book')[1];
      if (!noBook || !b1 || !b2) { console.log('      module lifecycle: skipped (needs instruments with and without a book)'); return; }
      const p4 = await ctx.open({ tag: 'lifecycle' });
      p4.allow(/ZBOOM/);
      await p4.goto('MON/' + noBook);
      const pg = p4.page;
      await pg.evaluate(() => {
        const L = window.__L = { mount: 0, show: [], hide: 0, frame: 0, cleaned: 0, poll: 0, ctx: null, guards: [] };
        OT.register({ id: 'ZTEST', title: 'Lifecycle Test', group: 'TOOLS', needs: 'book',
          mount(root, ctx) { L.mount++; L.ctx = ctx; root.appendChild(OT.ui.h('div', { id: 'ztest-body' }, 'mounted')); OT.poll(() => { L.poll++; }, 300); },
          show(id) { L.show.push(id); L.ctx.own(() => { L.cleaned++; }); L.guards.push(L.ctx.guard()); L.ctx.header.setSource({ freshness: 'RT', source: 'test', ts: Date.now() / 1000 - 3 }); },
          hide() { L.hide++; },
          frame() { L.frame++; } });
        OT.register({ id: 'ZBOOM', title: 'Crash Test', group: 'TOOLS', needs: null, mount() { throw new Error('ZBOOM deliberate'); } });
      });
      const L = () => pg.evaluate(() => { const l = window.__L; return { mount: l.mount, show: l.show.join(), hide: l.hide, frame: l.frame, cleaned: l.cleaned, poll: l.poll, guards: l.guards.map(g => g()).join(), visible: l.ctx ? l.ctx.visible : null }; });
      const raf = () => pg.evaluate(() => window.__otT.raf(3));
      check(await pg.evaluate(() => document.querySelectorAll('#tabs .tab').length) === 19, 'late OT.register did not add tabs');
      await p4.go('ZTEST'); await raf();
      let l = await L();
      check(l.mount === 0 && l.show === '', 'module without the capability was mounted/shown: ' + JSON.stringify(l));
      check(await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="ZTEST"] .mod-cover .ot-state'); return !!s && s.dataset.kind === 'nosource'; }), 'lifecycle: NO SOURCE state missing');
      await p4.select(b1); await raf();
      l = await L();
      check(l.mount === 1 && l.show === b1 && l.visible === true, 'after selecting ' + b1 + ': ' + JSON.stringify(l));
      await p4.assertHeaderBadge('ZTEST');
      const asof = await pg.evaluate(() => document.querySelector('.mod[data-mod="ZTEST"] .mod-asof').textContent);
      await p4.sleep(2100);
      check(asof !== await pg.evaluate(() => document.querySelector('.mod[data-mod="ZTEST"] .mod-asof').textContent), 'module header "as of" age does not keep counting: ' + asof);
      await p4.select(b2); await raf();
      l = await L();
      check(l.mount === 1 && l.show === b1 + ',' + b2 && l.cleaned === 1 && l.hide === 0 && l.guards === 'false,true', 'instrument change while visible: ' + JSON.stringify(l));
      await raf(); await p4.sleep(200);
      const f0 = (await L()).frame;                    // frames so far come from show/resize, which mark the module dirty
      await pg.evaluate(() => { window.__L.ctx.dirty(); window.__L.ctx.dirty(); }); await raf();
      check((await L()).frame === f0 + 1, 'ctx.dirty() twice should give exactly one frame(): ' + ((await L()).frame - f0));
      await raf(); await p4.sleep(400);
      check((await L()).frame === f0 + 1, 'frame() runs without ctx.dirty()');
      const pollBefore = (await L()).poll;
      await p4.go('MON'); await raf();
      l = await L();
      check(l.hide === 1 && l.cleaned === 2 && l.visible === false && l.guards === 'false,false', 'after leaving the module: ' + JSON.stringify(l));
      await pg.evaluate(() => window.__L.ctx.dirty()); await raf(); await p4.sleep(900);
      l = await L();
      check(l.frame === f0 + 1, 'frame() was called while the module is hidden');
      check(l.poll - pollBefore <= 1, 'a module-bound OT.poll keeps running while the module is hidden (' + (l.poll - pollBefore) + ' calls in 0.9 s)');
      check(pollBefore >= 2, 'module-bound poll did not run while visible (' + pollBefore + ')');
      await p4.go('ZTEST'); await raf(); await p4.sleep(700);
      const l2 = await L();
      check(l2.mount === 1 && l2.show === [b1, b2, b2].join() && l2.frame === f0 + 2, 'coming back: mount once, show again, exactly one frame for the pending dirty + the new layout: ' + JSON.stringify(l2) + ' f0=' + f0);
      check(l2.poll > l.poll, 'module-bound poll did not resume when the module was shown again');
      await p4.select(noBook); await raf();
      l = await L();
      check(l.hide === 2 && l.cleaned === 3 && l.visible === false, 'switching to an instrument without the capability must hide the module: ' + JSON.stringify(l));
      await p4.go('ZBOOM'); await raf();
      check(await pg.evaluate(() => { const s = document.querySelector('.mod[data-mod="ZBOOM"] .mod-cover:not([hidden]) .ot-state'); return !!s && s.dataset.kind === 'error' && /ZBOOM deliberate/.test(s.textContent); }), 'a module that throws in mount() must show an error state, not a blank panel');
      await p4.go('MON');
      check((await st(pg)).mod === 'MON', 'shell unusable after a module crashed');
      p4.errors().forEach(e => ctx.fail(e));
      await p4.close();
    });

    await section('geomap', async () => {
      await ctx.go('MON');
      await page.evaluate(() => {
        const host = document.createElement('div'); host.id = 'e2e-map'; host.style.cssText = 'position:fixed;left:360px;top:140px;width:1200px;height:700px;z-index:60;border:1px solid #33455f';
        const c = document.createElement('canvas'); c.className = 'ot-canvas'; host.appendChild(c); document.body.appendChild(host);
        window.__views = 0;
        window.__map = OT.GeoMap(c, { proj: 'eq', graticule: true, overlay(m, x) { const p = m.pt(-95.0, 29.7); x.fillStyle = OT.theme.amber; x.beginPath(); x.arc(p[0], p[1], 5, 0, 7); x.fill(); x.font = OT.canvas.font(12); x.fillStyle = OT.theme.text; x.fillText('Houston 29.7N 95.0W', p[0] + 8, p[1] + 4); } });
        window.__hover = 'html';
        window.__map.onHover((lon, lat) => (Math.abs(lon + 95) < 3 && Math.abs(lat - 29.7) < 3
          ? (window.__hover === 'html' ? { html: '<b>Houston</b> test point' } : 'MV <img src=x onerror="window.__pwn=1"> ' + (window.__hoverN = (window.__hoverN || 0) + 1)) : null));
        window.__map.onView(() => { window.__views++; });
        return window.__map.ready;
      });
      await ctx.waitFor(() => window.__map.state === 'ready' && window.__map.detail === '110m', { msg: 'basemap land-110m loaded' });
      await page.evaluate(() => window.__otT.raf(3));
      const ink = () => page.evaluate(() => { const c = document.querySelector('#e2e-map canvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; const sea = [d[0], d[1], d[2]]; const cols = new Map(); for (let i = 0; i < d.length; i += 16) { const k = d[i] + ',' + d[i + 1] + ',' + d[i + 2]; cols.set(k, (cols.get(k) || 0) + 1); } const top = Array.from(cols).sort((a, b) => b[1] - a[1]); return { n: d.length / 16, top: top.slice(0, 3), w: c.width, h: c.height }; });
      const world = await ink();
      const landShare = world.top[1] ? world.top[1][1] / world.n : 0;
      check(landShare > 0.15 && landShare < 0.5, 'world view: second most common colour covers ' + (landShare * 100).toFixed(1) + '% (land should be roughly 20–35% of an equirectangular world)');
      const geo = await page.evaluate(() => { const m = window.__map; const p = m.pt(-95, 29.7), q = m.inv(p[0], p[1]); return { p, q, zoom: m.zoom, scale: m.scale, land: (() => { const c = document.querySelector('#e2e-map canvas').getContext('2d'); const px = (lon, lat) => { const a = m.pt(lon, lat); return Array.from(c.getImageData(Math.round(a[0]), Math.round(a[1]), 1, 1).data).slice(0, 3).join(); }; return { sahara: px(10, 22), pacific: px(-140, 5), brazil: px(-52, -10), atlantic: px(-35, 33), siberia: px(100, 62), southernOcean: px(65, -55) }; })() }; });
      check(Math.abs(geo.q[0] + 95) < 1e-6 && Math.abs(geo.q[1] - 29.7) < 1e-6, 'pt/inv round trip: ' + JSON.stringify(geo.q));
      check(geo.land.sahara === geo.land.brazil && geo.land.sahara === geo.land.siberia && geo.land.pacific === geo.land.atlantic && geo.land.pacific === geo.land.southernOcean && geo.land.sahara !== geo.land.pacific, 'land/sea pixels are wrong (not a real coastline?): ' + JSON.stringify(geo.land));
      // hover (real mouse) → tooltip
      const box = await page.locator('#e2e-map canvas').boundingBox();
      const cur = { x: Math.round(box.x + geo.p[0]), y: Math.round(box.y + geo.p[1]) };           // integer px: what the event will report
      await page.mouse.move(cur.x, cur.y, { steps: 5 });
      const under = await page.evaluate(([x, y]) => window.__map.inv(x, y), [cur.x - box.x, cur.y - box.y]);
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /Houston/.test(t.textContent) && !!t.querySelector('b'); }, { msg: 'map hover tooltip ({html} is rendered as markup)' });
      // a plain string from a hit test is TEXT: an upstream name with markup in it cannot run script
      await page.evaluate(() => { window.__hover = 'text'; window.__pwn = 0; });
      await page.mouse.move(cur.x + 1, cur.y, { steps: 2 });
      await ctx.waitFor(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden && /MV <img/.test(t.textContent); }, { msg: 'a string hit is shown literally' });
      await ctx.sleep(150);
      check(await page.evaluate(() => window.__pwn === 0 && !document.querySelector('.ot-tip img')), 'a hover string was parsed as HTML: markup from an upstream string ran in the page');
      // …and the read-out is re-evaluated while the mouse rests (after a redraw, and once a second)
      const n0 = await page.evaluate(() => window.__hoverN);
      await page.evaluate(async () => { window.__map.invalidate(); await window.__otT.raf(3); });
      const n1 = await page.evaluate(() => window.__hoverN);
      await ctx.sleep(1300);
      const n2 = await page.evaluate(() => window.__hoverN);
      check(n1 > n0 && n2 > n1, 'map hover read-out is frozen at the moment of hover: evaluated ' + n0 + ' → ' + n1 + ' (after a redraw) → ' + n2 + ' (after 1.3 s)');
      await page.evaluate(() => { window.__hover = 'html'; });
      await page.mouse.move(cur.x, cur.y, { steps: 2 });
      // wheel zoom at the cursor keeps the point under the cursor
      for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, -200); await ctx.sleep(25); }
      await page.evaluate(() => window.__otT.raf(2));
      const z = await page.evaluate(u => { const m = window.__map; return { zoom: m.zoom, p: m.pt(-95, 29.7), u: m.pt(u[0], u[1]), views: window.__views }; }, under);
      check(z.zoom > geo.zoom * 5, 'wheel did not zoom: ' + geo.zoom + ' → ' + z.zoom);
      check(Math.abs(z.u[0] - (cur.x - box.x)) < 0.5 && Math.abs(z.u[1] - (cur.y - box.y)) < 0.5, 'zoom is not anchored at the cursor: the point under the cursor moved from ' + [cur.x - box.x, cur.y - box.y] + ' to ' + z.u);
      check(z.views >= 8, 'onView fired ' + z.views + ' times for 8 wheel steps');
      await ctx.waitFor(() => window.__map.detail === '50m', { msg: 'land-50m loads once zoomed in', timeout: 15000 });
      await page.evaluate(() => window.__otT.raf(3));
      await ctx.shotNow('shell_geomap_gulf_50m');
      // drag pans by exactly the dragged distance
      await page.mouse.down(); await page.mouse.move(cur.x - 240, cur.y + 120, { steps: 10 }); await page.mouse.up();
      await page.evaluate(() => window.__otT.raf(2));
      const d = await page.evaluate(() => window.__map.pt(-95, 29.7));
      check(Math.abs(d[0] - (z.p[0] - 240)) < 1.5 && Math.abs(d[1] - (z.p[1] + 120)) < 1.5, 'drag did not pan by the dragged distance: ' + z.p + ' → ' + d);
      // fit(bbox) across the antimeridian + Mercator
      const fitr = await page.evaluate(async () => { const m = window.__map; m.setProj('merc'); m.fit([150, -10, -150, 40]); await window.__otT.raf(3); const a = m.pt(150, 40), b = m.pt(-150, -10), dl = m.pt(179, 0), dr = m.pt(-179, 0); return { a, b, w: m.width, h: m.height, proj: m.proj, wrap: dr[0] - dl[0], scale: m.scale }; });
      check(fitr.proj === 'merc' && fitr.a[0] >= 0 && fitr.a[0] < fitr.b[0] && fitr.b[0] <= fitr.w && fitr.a[1] >= 0 && fitr.b[1] <= fitr.h, 'fit([150,-10,-150,40]) does not contain the box: ' + JSON.stringify(fitr));
      check(Math.abs(fitr.wrap - 2 * fitr.scale) < 0.01, 'antimeridian: 179E and 179W should be 2° apart on screen, got ' + fitr.wrap + ' px at ' + fitr.scale + ' px/deg');
      await ctx.waitFor(() => window.__map.detail === '50m', { msg: '50m path for Mercator built', timeout: 15000 });
      await page.evaluate(() => window.__otT.raf(3));
      await ctx.shotNow('shell_geomap_pacific_merc');
      // land must be continuous across 180° (Chukotka), sea stays sea (Bering Sea); redraws stay fast
      const am = await page.evaluate(async () => { const m = window.__map; m.setView({ lon: 180, lat: 62, zoom: 5 }); await window.__otT.raf(3); const c = document.querySelector('#e2e-map canvas').getContext('2d'); const px = (lon, lat) => { const a = m.pt(lon, lat); return Array.from(c.getImageData(Math.round(a[0]), Math.round(a[1]), 1, 1).data).slice(0, 3).join(); }; let worst = 0; for (let i = 0; i < 12; i++) { m.setView({ lon: 180 + i * 0.7 }); m.draw(); worst = Math.max(worst, m.drawMs); } m.setView({ lon: 180 }); await window.__otT.raf(2); return { west: px(177, 67), east: px(-177, 66.5), sea: px(-178, 57), worst, detail: m.detail }; });
      check(am.west === geo.land.sahara && am.east === geo.land.sahara && am.sea === geo.land.pacific, 'antimeridian: land/sea pixels around 180° are wrong: ' + JSON.stringify(am));
      check(am.worst < 30, 'map redraw (JS side) took up to ' + am.worst.toFixed(1) + ' ms at ' + am.detail);
      await ctx.shotNow('shell_geomap_antimeridian');
      // keyboard + idle: no redraw when nothing changes
      const idle = await page.evaluate(async () => { const m = window.__map; let n = 0; const d0 = m.draw; m.draw = function () { n++; return d0.apply(this, arguments); }; await new Promise(r => setTimeout(r, 600)); const idleDraws = n; m.setView({ lon: 0, lat: 20, zoom: 1 }); await window.__otT.raf(3); const one = n; m.draw = d0; return { idleDraws, one }; });
      check(idle.idleDraws === 0 && idle.one === 1, 'map redraws when idle (' + idle.idleDraws + ') or not exactly once per change (' + idle.one + ')');
      await ctx.shotNow('shell_geomap_world');
      await ctx.assertCanvasText(12, 'geomap');
      /* Land must be land and sea must be sea wherever the view is. The Eurasia–Africa ring of world-atlas starts
         on the antimeridian; drawn a whole turn away it vanished as soon as the view moved west of 0° or zoomed
         in, and its hole — the Caspian Sea — was painted as land. Every city below is sampled at every view in
         which it is on screen, in both projections, on the 110m and the 50m coastline. */
      const LAND = { Sahara: [10, 22], Kazakhstan: [67, 48], Brazil: [-52, -10], Moscow: [37.6, 55.75], Delhi: [77.2, 28.6], Egypt: [30, 26], Kinshasa: [15.3, -4.3],
        Johannesburg: [28, -26], Paris: [2.35, 48.85], Madrid: [-3.7, 40.4], Riyadh: [46.7, 24.7], Beijing: [116.4, 39.9], Yakutsk: [129.7, 62], Kansas: [-98, 38.5], Alice: [133.9, -23.7], Tehran: [51.4, 35.7] };
      const SEA = { Caspian: [51, 42], Atlantic: [-35, 33], Pacific: [-140, 5], Indian: [75, -20], BlackSea: [35, 43.3], Mediterranean: [18, 35], ArabianSea: [64, 18], Bengal: [88, 15] };
      const VIEWS = [['eq', 0, 0, 1], ['eq', -26.9, 0, 1], ['eq', -100, 20, 1], ['eq', 150, 0, 1], ['eq', -179, 0, 1], ['eq', 60, 40, 2.4], ['eq', -60, 40, 2.4], ['eq', 60, 40, 2.6],
        ['eq', 50, 30, 6.05], ['eq', 50, 30, 12], ['merc', 50, 30, 12], ['merc', -26.9, 20, 1], ['merc', 100, 45, 3], ['eq', 20, 0, 4]];
      const lands = await page.evaluate(async ([LAND, SEA, VIEWS]) => {
        const m = window.__map, cvs = document.querySelector('#e2e-map canvas'), c = cvs.getContext('2d'), dpr = cvs.width / cvs.clientWidth;
        const land = OT.theme.line.toLowerCase(), hex = d => '#' + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, '0')).join('');
        const out = { wrong: [], n: 0, views: 0, seaShare: [] };
        for (const [proj, lon, lat, zoom] of VIEWS) {
          m.setProj(proj); m.setView({ lon, lat, zoom });
          const until = performance.now() + 15000, need = zoom >= 2.5 ? '50m' : '110m';
          while (m.detail !== need && performance.now() < until) await new Promise(r => setTimeout(r, 50));
          await window.__otT.raf(3);
          out.views++;
          const sample = (name, ll, wantLand) => {
            const p = m.pt(ll[0], ll[1]);
            if (p[0] < 6 || p[1] < 6 || p[0] > m.width - 6 || p[1] > m.height - 6) return;         // not in this view
            // a 5x5 block around the point: land if most of it has the land colour (a border or grid line may cross it)
            const d = c.getImageData(Math.round(p[0] * dpr) - 2, Math.round(p[1] * dpr) - 2, 5, 5).data;
            let n = 0; for (let i = 0; i < d.length; i += 4) if (hex([d[i], d[i + 1], d[i + 2]]) === land) n++;
            out.n++;
            if ((n >= 13) !== wantLand) out.wrong.push(name + ' is drawn as ' + (n >= 13 ? 'land' : 'sea') + ' at ' + proj + ' centre ' + lon + ',' + lat + ' zoom ' + zoom + ' (' + m.detail + ')');
          };
          Object.keys(LAND).forEach(k => sample(k, LAND[k], true));
          Object.keys(SEA).forEach(k => sample(k, SEA[k], false));
        }
        m.setProj('eq'); m.setView({ lon: 0, lat: 20, zoom: 1 }); await window.__otT.raf(3);
        return out;
      }, [LAND, SEA, VIEWS]);
      check(!lands.wrong.length, 'GeoMap paints land/sea wrongly: ' + lands.wrong.slice(0, 8).join(' | ') + (lands.wrong.length > 8 ? ' … ' + lands.wrong.length + ' in all' : ''));
      check(lands.n > 150 && lands.views === VIEWS.length, 'land/sea sampling covered only ' + lands.n + ' points in ' + lands.views + ' views');
      console.log('      geomap: ' + lands.n + ' land/sea samples over ' + lands.views + ' views (centres west of 0°, zooms into 50E/30N, both projections), ' + lands.wrong.length + ' wrong');
      // a real drag to the right at zoom 1 (centre moves west): the Old World stays on the map
      await page.mouse.move(box.x + 600, box.y + 350, { steps: 2 });
      await page.mouse.down(); await page.mouse.move(box.x + 720, box.y + 350, { steps: 6 }); await page.mouse.up();
      await page.evaluate(() => window.__otT.raf(3));
      const dragged = await page.evaluate(() => { const m = window.__map, cvs = document.querySelector('#e2e-map canvas'), c = cvs.getContext('2d'); const px = (lon, lat) => { const a = m.pt(lon, lat); return Array.from(c.getImageData(Math.round(a[0]), Math.round(a[1]), 1, 1).data).slice(0, 3).join(); }; return { centre: m.center[0], sahara: px(10, 22), kazakhstan: px(67, 48), brazil: px(-52, -10), pacific: px(-140, 5) }; });
      check(dragged.centre < -20 && dragged.sahara === dragged.brazil && dragged.kazakhstan === dragged.brazil && dragged.brazil !== dragged.pacific, 'after dragging the world 120 px to the right the Old World is gone: ' + JSON.stringify(dragged));
      await ctx.shotNow('shell_geomap_dragged_west');
      await page.evaluate(() => { window.__map.destroy(); document.getElementById('e2e-map').remove(); });
      await page.mouse.move(5, 5);
    });

    await section('stale and no-source rows', async () => {
      /* The real gateway normally answers 23/23 ok, so these two labels would never be exercised. Here two REAL
         quotes get their status changed in transit by this test (never by the page): one becomes "stale" (the
         source stopped), one becomes "none" (no source answered). Everything else is the gateway's own answer. */
      const staleId = allIds.find(id => uni.instruments[id].cls === 'fx') || allIds[1];
      const noneId = allIds.find(id => id !== staleId && uni.instruments[id].quote && uni.instruments[id].quote.delay === 'EOD') || allIds.find(id => id !== staleId);
      const p2 = await ctx.open({ tag: 'stale' });
      await p2.page.route('**/api/quotes', async route => {
        let res;
        try { res = await route.fetch(); } catch (e) { return route.abort(); }
        const body = await res.json();
        const a = body.quotes[staleId], b = body.quotes[noneId];
        a.status = 'stale'; a.note = 'e2e: ' + a.source + ' has not answered (status changed in transit by the test)';
        Object.assign(b, { last: null, prev_close: null, chg: null, chg_pct: null, bid: null, ask: null, open: null, high: null, low: null, volume: null, ts: null, source: null, status: 'none', note: 'e2e: no source answered (changed in transit by the test)', spark: [] });
        await route.fulfill({ response: res, json: body });
      });
      await p2.goto('MON/' + staleId);
      const pg = p2.page;
      await p2.waitFor(([a, b]) => { const r = document.querySelector('#watch .wrow[data-id="' + a + '"]'); return r && r.classList.contains('stale') && OT.quotes[b] && OT.quotes[b].status === 'none'; }, { arg: [staleId, noneId], timeout: 45000, msg: 'the altered quotes reached the rail' });
      await pg.evaluate(() => window.__otT.raf(3));
      const read = () => pg.evaluate(([a, b]) => {
        const row = id => { const r = document.querySelector('#watch .wrow[data-id="' + id + '"]'); const age = r.querySelector('.w-age');
          return { last: r.querySelector('.w-last').textContent, pct: r.querySelector('.w-pct').textContent, dot: r.querySelector('.dot').className, cls: r.className,
            age: getComputedStyle(age).display === 'none' ? null : age.textContent, spark: getComputedStyle(r.querySelector('.w-spark')).display !== 'none', tip: r._tip() }; };
        return { a: row(a), b: row(b), others: Array.from(document.querySelectorAll('#watch .wrow.stale')).map(r => r.dataset.id).join(),
          badge: document.querySelector('#hdr-badge .badge').dataset.fresh, hdrAge: document.getElementById('hdr-age').textContent, stq: document.getElementById('st-q').textContent };
      }, [staleId, noneId]);
      let r = await read();
      check(r.a.pct === 'STALE' && /\bstale\b/.test(r.a.cls) && r.a.dot === 'dot d-stale', staleId + ' stale row: ' + JSON.stringify(r.a));
      check(/^\d+[smhd]$/.test(r.a.age || '') && !r.a.spark, staleId + ' stale row shows the age of its value instead of the sparkline: age=' + r.a.age + ' spark=' + r.a.spark);
      check(NUM.test(r.a.last), staleId + ' stale row keeps its last real value: ' + r.a.last);
      check(/STALE/.test(r.a.tip) && r.a.tip.indexOf('has not answered') >= 0, staleId + ' stale tooltip: ' + JSON.stringify(r.a.tip));
      check(r.others === staleId, 'rows marked stale: ' + r.others + ' (expected only ' + staleId + ')');
      check(r.badge === 'STALE' && /ago/.test(r.hdrAge), 'header for a stale instrument: badge ' + r.badge + ', age ' + JSON.stringify(r.hdrAge));
      check(r.b.last === '—' && r.b.pct === 'no source' && r.b.dot === 'dot d-none' && !/\bstale\b/.test(r.b.cls), noneId + ' none row: ' + JSON.stringify(r.b));
      check(/NO SOURCE/.test(r.b.tip) && r.b.tip.indexOf('no source answered') >= 0, noneId + ' none tooltip: ' + JSON.stringify(r.b.tip));
      const lagging = await pg.evaluate(() => Object.keys(OT.quotes).filter(id => OT.ui.quoteLag(OT.quotes[id])).length);
      check(r.stq.indexOf((allIds.length - 2 - lagging) + '/' + allIds.length + ' ok') >= 0 && /2 without a current value/.test(r.stq), 'status bar counts (' + lagging + ' lagging): ' + r.stq);
      await p2.assertMinFontPx(12, '#rail', 'stale rail'); await p2.assertContrast(4.5, '#rail', 'stale rail');
      await p2.assertContrast(4.5, '#hdr', 'stale header'); await p2.assertNoBadText(null, 'stale page');
      await p2.shotNow('shell_stale_1920x1080');
      await p2.select(noneId);
      r = await read();
      check(r.badge === 'NO' && pg.url().indexOf('/' + noneId) > 0, 'header badge for an instrument without a source: ' + r.badge);
      check(await pg.evaluate(() => document.getElementById('hdr-last').textContent === '—' && document.getElementById('hdr-badge').textContent.indexOf('NO SOURCE') >= 0), 'header shows — and NO SOURCE for the none-state instrument');
      await p2.assertContrast(4.5, '#hdr', 'none header'); await p2.assertContrast(4.5, '#rail', 'none rail');
      console.log('      stale row ' + staleId + ': ' + r.a.last + ' ' + r.a.pct + ' age ' + r.a.age + ' | none row ' + noneId + ': ' + r.b.last + ' ' + r.b.pct + ' | ' + r.stq);
      p2.errors().forEach(e => ctx.fail(e));
      await p2.close();
    });

    await section('gateway offline / reconnect', async () => {
      const target = new URL(ctx.base);
      const proxy = ctx.tcpProxy(+target.port, target.hostname);
      await proxy.start();
      const p2 = await ctx.open({ tag: 'offline', base: proxy.base });
      p2.allow(/ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_SOCKET|WebSocket connection|Failed to load resource|net::|\/api\/status|\/api\/quotes|\/ws/);
      await p2.goto('MON/' + (withCap('book')[0] || allIds[0]));
      const pg = p2.page;
      const unsub = withCap('trades')[0] ? 'trades:' + withCap('trades')[0] : 'trades:BTC';
      await pg.evaluate(t => { window.__unsub = OT.sub(t); }, unsub);
      await p2.waitFor(() => OT.state.gateway === 'online' && OT.state.ws === 'open' && document.querySelector('.lamp[data-lamp="gateway"]').classList.contains('ok'), { msg: 'online through the proxy' });
      await p2.waitFor(t => window.__otWsSent.some(f => f.d.indexOf(t) >= 0), { arg: unsub, msg: 'subscription frame sent' });
      const before = await pg.evaluate(() => ({ banner: document.getElementById('offline').hidden, lamps: Array.from(document.querySelectorAll('.lamp')).map(l => l.dataset.lamp + '=' + l.dataset.state).join(' '), sent: window.__otWsSent.length, color: getComputedStyle(document.querySelector('#watch .w-last')).color }));
      check(before.banner === true, 'offline banner visible while online');
      console.log('      lamps online: ' + before.lamps);
      const t0 = Date.now();
      await proxy.stop();                                          // the gateway "dies"
      const wentOff = await p2.waitFor(() => OT.state.gateway === 'offline', { timeout: 20000, msg: 'gateway state → offline after the gateway stopped' });
      const offMs = Date.now() - t0;
      await pg.evaluate(() => window.__otT.raf(2)); await ctx.sleep(1200);
      const off = await pg.evaluate(() => ({ banner: !document.getElementById('offline').hidden, text: document.getElementById('offline').textContent.replace(/\s+/g, ' '), cls: document.body.classList.contains('gw-offline'),
        lamps: Array.from(document.querySelectorAll('.lamp')).map(l => l.dataset.lamp + '=' + l.dataset.state).join(' '), green: document.querySelectorAll('.lamp.ok').length,
        color: getComputedStyle(document.querySelector('#watch .w-last')).color, hdrColor: getComputedStyle(document.getElementById('hdr-last')).color, text3: getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim(),
        ages: Array.from(document.querySelectorAll('#watch .w-age')).filter(a => a.getBoundingClientRect().width > 0 && /\d+[smh]/.test(a.textContent)).length, sparks: Array.from(document.querySelectorAll('#watch .w-spark')).filter(c => c.getBoundingClientRect().width > 0).length,
        st: document.getElementById('st-gw').textContent, stCls: document.getElementById('st-gw').className, dots: Array.from(document.querySelectorAll('#watch .dot')).filter(d => getComputedStyle(d).backgroundColor !== 'rgba(0, 0, 0, 0)').length, ws: OT.state.ws }));
      if (wentOff) {
        check(offMs < 15000, 'offline detected only after ' + offMs + ' ms');
        check(off.banner && /GATEWAY OFFLINE/.test(off.text) && off.text.indexOf(proxy.base) >= 0 && /Retrying/.test(off.text), 'offline banner: ' + JSON.stringify(off));
        check(/Last data received \d+[sm] ago/.test(off.text), 'offline banner does not say how old the data is: ' + off.text);
        check(off.green === 0, 'a lamp is still green while the gateway is offline: ' + off.lamps);
        check(/gateway=bad/.test(off.lamps), 'gateway lamp while offline: ' + off.lamps);
        check(off.cls && off.color !== before.color && off.hdrColor === off.color, 'values are not dimmed while offline (rail ' + before.color + ' → ' + off.color + ', header ' + off.hdrColor + ')');
        check(off.ages >= 1 && off.sparks === 0, 'offline rail should show last-update ages instead of sparklines: ' + off.ages + ' ages, ' + off.sparks + ' sparklines');
        check(off.dots === 0, off.dots + ' freshness dots are still filled while offline');
        check(/OFFLINE/.test(off.st) && off.stCls === 'st-bad', 'status bar while offline: ' + off.st);
        console.log('      offline detected after ' + offMs + ' ms; lamps offline: ' + off.lamps);
        await p2.assertContrast(4.5, null, 'offline');
        await p2.assertMinFontPx(12, null, 'offline');
        await p2.assertNoBadText(null, 'offline');
        await p2.assertNoOverflow('offline');
        await p2.shot('shell_offline');
      }
      await pg.evaluate(() => { window.__sentBefore = window.__otWsSent.length; window.__msgBefore = OT.state.lastMsgAt; });
      const t1 = Date.now();
      await proxy.start();                                         // the gateway comes back
      const back = await p2.waitFor(() => OT.state.gateway === 'online' && OT.state.ws === 'open' && OT.state.lastMsgAt > window.__msgBefore, { timeout: 30000, msg: 'reconnect after the gateway came back' });
      if (back) {
        const backMs = Date.now() - t1;
        await p2.waitFor(t => window.__otWsSent.slice(window.__sentBefore).some(f => f.d.indexOf(t) >= 0), { arg: unsub, timeout: 5000, msg: 'topic subscriptions re-sent after reconnect' });
        await p2.waitFor(() => document.getElementById('offline').hidden && !document.body.classList.contains('gw-offline') && document.querySelector('.lamp[data-lamp="gateway"]').classList.contains('ok'), { timeout: 5000, msg: 'banner gone and gateway lamp green after reconnect' });
        await p2.waitFor(() => Array.from(document.querySelectorAll('#watch .w-spark')).filter(c => c.getBoundingClientRect().width > 0).length > 0, { timeout: 5000, msg: 'sparklines back after reconnect' });
        const a = await pg.evaluate(() => ({ color: getComputedStyle(document.querySelector('#watch .w-last')).color, lamps: Array.from(document.querySelectorAll('.lamp')).map(l => l.dataset.lamp + '=' + l.dataset.state).join(' ') }));
        check(a.color === before.color, 'values still dimmed after reconnect');
        console.log('      reconnected after ' + backMs + ' ms; lamps: ' + a.lamps);
      }
      // a HUNG gateway (sockets open, nothing moving) must also turn the page offline: never green when nothing flows
      const t2 = Date.now();
      proxy.freeze();
      const froze = await p2.waitFor(() => OT.state.gateway === 'offline', { timeout: 25000, msg: 'gateway state → offline when the gateway hangs (open sockets, no data)' });
      if (froze) {
        const hung = await pg.evaluate(() => ({ green: document.querySelectorAll('.lamp.ok').length, banner: !document.getElementById('offline').hidden, lamps: Array.from(document.querySelectorAll('.lamp')).map(l => l.dataset.lamp + '=' + l.dataset.state).join(' ') }));
        check(hung.green === 0 && hung.banner, 'hung gateway: ' + JSON.stringify(hung));
        console.log('      hung gateway detected after ' + (Date.now() - t2) + ' ms; lamps: ' + hung.lamps);
      }
      await proxy.stop(); await proxy.start();
      await p2.waitFor(() => OT.state.gateway === 'online' && OT.state.ws === 'open' && document.getElementById('offline').hidden, { timeout: 40000, msg: 'recovery after the hung gateway was restarted' });
      // boot while the instrument list is unreachable: explicit state, then recovery without a reload
      const p3 = await ctx.open({ tag: 'boot-retry', base: proxy.base });
      p3.allow(/api\/universe|Failed to load resource|net::/);
      let blocked = 0;
      await p3.page.route('**/api/universe', r => (blocked++ < 2 ? r.abort() : r.continue()));
      await p3.goto('', { noWait: true });
      await p3.waitFor(() => { const s = document.querySelector('#boot .ot-state'); return s && s.dataset.kind === 'error' && /Retrying/.test(s.textContent); }, { msg: 'boot shows an error state with a retry countdown while /api/universe fails' });
      check(await p3.page.evaluate(() => document.querySelectorAll('#watch .wrow').length) === 0, 'rail shows rows although the universe never loaded');
      await p3.ready(30000).catch(() => p3.fail('page did not recover after /api/universe started answering'));
      check(await p3.page.evaluate(() => document.querySelectorAll('#watch .wrow').length) === allIds.length && await p3.page.evaluate(() => document.getElementById('boot').hidden), 'boot retry did not build the rail');
      p2.errors().concat(p3.errors()).forEach(e => ctx.fail(e));
      await p2.close(); await p3.close();
      await proxy.stop();
    });

    await section('final', async () => {
      await ctx.setViewport(1920, 1080);
      await ctx.go('MON'); await ctx.select(allIds[0]);
      await ctx.assertNoBadText(null, 'final');
      await ctx.assertContrast(4.5, null, 'final');
      await ctx.assertCanvasText(12, 'final');
    });
  },
};
