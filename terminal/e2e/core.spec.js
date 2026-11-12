/* Core spec (FF): the behaviours fixed after the adversarial review of the foundation (2026-11-12).
   Runs with `--only shell` next to shell.spec.js. Each section names the defect it guards against; every one of
   them fails when its fix is reverted. Works against the real gateway: faults are made in transit by the test
   (websocket routed through the test, answers altered with page.route) — never by the page. */
'use strict';
const http = require('http');
const lib = require('./lib.js');

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

module.exports = {
  name: 'core', slice: 'shell',
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
    const crypto = allIds.filter(id => uni.instruments[id].cls === 'crypto');
    const polled = allIds.filter(id => uni.instruments[id].cls === 'future');
    await ctx.go('MON'); await ctx.select(allIds[0]);

    /* ───────────────────────── websocket silent but open ─────────────────────────
       A fan-out task that dies while uvicorn keeps serving REST: the socket stays open, nothing arrives. The page
       kept GATEWAY green, "STREAM OPEN", 23/23 ok and RT dots for 25 s at a time. */
    await section('silent stream', async () => {
      if (!crypto.length) return;
      const id = crypto[0];
      const p = await ctx.open({ tag: 'silent', ws: true });
      await p.goto('MON/' + id);
      const pg = p.page;
      await p.waitFor(i => OT.state.stream === 'live' && OT.quotes[i] && OT.quotes[i].status === 'ok' && document.querySelector('#watch .wrow[data-id="' + i + '"] .dot').className === 'dot d-rt', { arg: id, timeout: 20000, msg: 'stream live and ' + id + ' real-time before the test' });
      const read = () => pg.evaluate(i => ({ stream: OT.state.stream, gw: OT.state.gateway, ws: OT.state.ws, opened: window.__otWsOpened,
        stws: document.getElementById('st-ws').textContent, stwsCls: document.getElementById('st-ws').className, stq: document.getElementById('st-q').textContent,
        lamp: document.querySelector('.lamp[data-lamp="gateway"]').dataset.state, lampTip: document.querySelector('.lamp[data-lamp="gateway"]')._tip(),
        dot: document.querySelector('#watch .wrow[data-id="' + i + '"] .dot').className, row: document.querySelector('#watch .wrow[data-id="' + i + '"]').className,
        age: document.querySelector('#watch .wrow[data-id="' + i + '"] .w-age').textContent, badge: document.querySelector('#hdr-badge .badge').dataset.fresh,
        banner: !document.getElementById('offline').hidden }), id);
      const before = await read();
      check(before.stws === 'STREAM OPEN' && before.stwsCls === 'st-ok' && before.lamp === 'ok' && before.badge === 'RT', 'before muting: ' + JSON.stringify(before));
      p.ws.mute(true);                                             // the socket stays open; nothing arrives any more
      const t0 = Date.now(), log = [];
      while (Date.now() - t0 < 16000) { await sleep(500); log.push(Object.assign({ t: Date.now() - t0 }, await read())); }
      const at = ms => log.filter(s => s.t >= ms);
      const silent = log.find(s => /^STREAM SILENT \d+s$/.test(s.stws));
      check(silent && silent.t <= 9000, 'the status bar never said STREAM SILENT within 9 s of the stream going quiet: ' + JSON.stringify(log.slice(0, 18).map(s => s.t + ':' + s.stws)));
      check(at(9000).every(s => s.stws !== 'STREAM OPEN' && s.stwsCls !== 'st-ok'), 'the status bar shows a green STREAM OPEN while nothing arrives: ' + JSON.stringify(at(9000).map(s => s.t + ':' + s.stws + '/' + s.stwsCls)));
      check(at(9000).every(s => s.lamp !== 'ok'), 'the GATEWAY lamp is green while the stream is silent: ' + JSON.stringify(at(9000).map(s => s.t + ':' + s.lamp)));
      check(log.some(s => /STREAM SILENT/.test(s.lampTip)), 'the GATEWAY lamp tooltip does not explain the silent stream');
      check(log.every(s => s.gw === 'online' && !s.banner), 'REST still answers: the page must not claim GATEWAY OFFLINE: ' + JSON.stringify(log.filter(s => s.gw !== 'online').map(s => s.t)));
      check(at(11500).every(s => s.dot !== 'dot d-rt' && /\blag\b/.test(s.row) && s.badge === 'LAG'), id + ' still looks real-time after 11.5 s without a stream: ' + JSON.stringify(at(11500).map(s => s.t + ':' + s.dot + '/' + s.badge)));
      check(at(11500).every(s => /^\d+[sm]$/.test(s.age)), id + ' lagging row does not show the age of its value: ' + JSON.stringify(at(11500).map(s => s.age)));
      check(at(11500).every(s => /lagging/.test(s.stq) && s.stq.indexOf(allIds.length + '/' + allIds.length + ' ok') < 0), 'the status bar still counts stream-fed quotes as current: ' + JSON.stringify(at(11500).map(s => s.stq).slice(0, 3)));
      const replaced = log.find(s => s.opened > before.opened);
      check(replaced && replaced.t <= 13500, 'the silent socket was not replaced within 13.5 s (was: 25 s): ' + JSON.stringify(log.map(s => s.t + ':' + s.opened).slice(-8)));
      await p.shotNow('core_stream_silent_1920x1080');
      p.ws.mute(false);
      await p.waitFor(i => OT.state.stream === 'live' && document.getElementById('st-ws').textContent === 'STREAM OPEN' && document.querySelector('.lamp[data-lamp="gateway"]').dataset.state === 'ok'
        && document.querySelector('#watch .wrow[data-id="' + i + '"] .dot').className === 'dot d-rt' && document.querySelector('#hdr-badge .badge').dataset.fresh === 'RT', { arg: id, timeout: 20000, msg: 'recovery once the stream delivers again' });
      console.log('      silent stream: STREAM SILENT after ' + (silent ? silent.t : '?') + ' ms, socket replaced after ' + (replaced ? replaced.t : '?') + ' ms, ' + id + ' shown as LAG; recovered');
      // gateway-side warnings (no time-zone database) reach the status bar
      p.ws.inject({ type: 'status', feeds: {}, quotes: { ok: true, age: 1, counts: { ok: 23, stale: 0, none: 0 } }, warnings: ['No IANA time-zone database on this machine (run: pip install tzdata).'] });
      await p.waitFor(() => { const w = document.getElementById('st-warn'); return w && !w.hidden && /NO TIME-ZONE DATA/.test(w.textContent) && /pip install tzdata/.test(w.getAttribute('data-tip') || ''); }, { timeout: 3000, msg: 'a gateway warning in the status event is shown in the status bar' });
      await p.close();
    });

    /* ───────────────────────── a quote that is behind, or steps back ─────────────────────────
       Yahoo FX was labelled RT with a source time minutes old, and `ts` went backwards. */
    await section('lagging and backward quotes', async () => {
      const fx = allIds.find(id => uni.instruments[id].cls === 'fx');
      const cl = polled[0];
      const p = await ctx.open({ tag: 'lag', ws: true });
      const shift = q => { if (q && typeof q.ts === 'number') q.ts -= 400; };
      p.ws.rewrite(m => { if (m.type === 'quote' && m.id === fx) shift(m); return m; });
      await p.page.route('**/api/quotes', async route => {
        let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
        const body = await res.json(); shift(body.quotes[fx]);
        await route.fulfill({ response: res, json: body });
      });
      await p.goto('MON/' + fx);
      const pg = p.page;
      await pg.evaluate(() => window.__otT.raf(3));
      const q = await pg.evaluate(i => OT.quotes[i], fx);
      if (q.status === 'ok' && q.session === 'open') {
        const r = await pg.evaluate(i => { const row = document.querySelector('#watch .wrow[data-id="' + i + '"]'), b = document.querySelector('#hdr-badge .badge');
          return { badge: b.dataset.fresh, text: b.textContent, cls: b.className, tip: b.getAttribute('data-tip'), dot: row.querySelector('.dot').className, row: row.className, age: row.querySelector('.w-age').textContent,
            spark: getComputedStyle(row.querySelector('.w-spark')).display, rowTip: row._tip(), stq: document.getElementById('st-q').textContent }; }, fx);
        check(r.badge === 'LAG' && /^LAG \d+m/.test(r.text) && /b-lag/.test(r.cls), fx + ' is ' + q.freshness + ' with a source time 7 minutes old in an open session, but the header badge says ' + JSON.stringify(r.text));
        check(/source time/.test(r.tip) && /LAG/.test(r.rowTip), fx + ': the tooltips do not explain the lag: ' + JSON.stringify(r.tip));
        check(r.dot === 'dot d-lag' && /\blag\b/.test(r.row) && /^\d+m$/.test(r.age) && r.spark === 'none', fx + ' rail row: ' + JSON.stringify(r));
        check(/lagging/.test(r.stq), 'status bar does not count the lagging quote: ' + r.stq);
        await p.assertContrast(4.5, '#hdr', 'lag header'); await p.assertContrast(4.5, '#rail', 'lag rail');
        await p.shotNow('core_lag_1920x1080');
      } else console.log('      lagging quote: ' + fx + ' is ' + q.status + '/' + q.session + ' right now — the LAG badge needs an open FX session; not exercised');
      // a delta whose source time is OLDER than the one on screen does not move the price back
      const held = await pg.evaluate(i => ({ last: OT.quotes[i].last, ts: OT.quotes[i].ts, source: OT.quotes[i].source }), cl);
      p.ws.inject({ type: 'quote', id: cl, last: held.last + 5, bid: null, ask: null, ts: held.ts - 100, source: held.source });
      await sleep(300);
      const after = await pg.evaluate(i => ({ last: OT.quotes[i].last, ts: OT.quotes[i].ts, shown: document.querySelector('#watch .wrow[data-id="' + i + '"] .w-last').textContent, want: OT.fmt.px(i, OT.quotes[i].last) }), cl);
      check(after.last === held.last && after.ts === held.ts && after.shown === after.want, cl + ' stepped back in time: an event with ts ' + (held.ts - 100) + ' replaced the quote of ts ' + held.ts + ' (' + held.last + ' → ' + after.last + ')');
      // …but another source answering (a fallback) is a different quote and is taken whole
      p.ws.inject({ type: 'quote', id: cl, last: held.last + 1, bid: null, ask: null, ts: held.ts - 100, source: 'lse', freshness: 'EOD' });
      await sleep(300);
      const sw = await pg.evaluate(i => ({ last: OT.quotes[i].last, source: OT.quotes[i].source }), cl);
      check(sw.source === 'lse' && sw.last === held.last + 1, cl + ': a change of source with an older ts must be applied whole: ' + JSON.stringify(sw));
      await p.close();
    });

    /* ───────────────────────── the harness must see what a person sees ─────────────────────────
       Canvas text was only audited by its font string; DOM text was cut at 60 characters before the bad-text test. */
    await section('harness self-test', async () => {
      const p = await ctx.open({ tag: 'harness' });
      await p.goto('MON/' + allIds[0]);
      const pg = p.page;
      const host = (id, w, h) => pg.evaluate(([i, ww, hh]) => { const d = document.createElement('div'); d.id = i; d.style.cssText = 'position:fixed;left:420px;top:200px;z-index:70;width:' + ww + 'px;height:' + hh + 'px;background:' + OT.theme.bg; document.body.appendChild(d); }, [id, w, h]);
      // control: a correct canvas label raises nothing
      await host('h-ok', 300, 80);
      await pg.evaluate(() => { const c = document.createElement('canvas'); c.className = 'ot-canvas'; document.getElementById('h-ok').appendChild(c); const f = OT.canvas.fit(c); f.ctx.fillStyle = OT.theme.bg; f.ctx.fillRect(0, 0, f.w, f.h); f.ctx.font = OT.canvas.font(13); f.ctx.fillStyle = OT.theme.text2; f.ctx.fillText('wind 85 kt  pressure 970 mb', 8, 40); });
      let got = await p.capture(async () => { await p.assertCanvasText(12, 'control'); await p.assertNoBadText(null, 'control'); });
      check(got.length === 0, 'harness control: a correct canvas label was reported: ' + got.join(' | '));
      const audit0 = await p.canvasAudit(12, 4.5);
      check(audit0.measured >= 1, 'harness control: the contrast of the canvas label could not be measured (' + JSON.stringify(audit0) + ')');
      // 1. bad text painted on a canvas
      await pg.evaluate(() => { const c = document.querySelector('#h-ok canvas'), x = c.getContext('2d'); x.font = OT.canvas.font(13); x.fillStyle = OT.theme.text2; x.fillText('wind NaN kt  pressure undefined mb', 8, 64); });
      got = await p.capture(async () => { await p.assertNoBadText(null, 'x'); });
      check(got.some(m => /canvas/.test(m) && /NaN/.test(m)), 'harness: "NaN … undefined" drawn on a canvas was not reported by assertNoBadText: ' + JSON.stringify(got));
      // 2. canvas text in --line on --bg (about 1.3:1)
      await host('h-faint', 300, 80);
      await pg.evaluate(() => { const c = document.createElement('canvas'); c.className = 'ot-canvas'; document.getElementById('h-faint').appendChild(c); const f = OT.canvas.fit(c); f.ctx.fillStyle = OT.theme.bg; f.ctx.fillRect(0, 0, f.w, f.h); f.ctx.font = OT.canvas.font(13); f.ctx.fillStyle = OT.theme.line; f.ctx.fillText('axis label nobody can read', 8, 40); });
      got = await p.capture(async () => { await p.assertCanvasText(12, 'x'); });
      check(got.some(m => /below 4.5:1/.test(m) && /nobody can read/.test(m)), 'harness: 1.3:1 canvas text was not reported: ' + JSON.stringify(got));
      // 3. 12 px text on a 600x160 backing store shown at 300x80: 6 px on screen
      await host('h-small', 300, 80);
      await pg.evaluate(() => { const c = document.createElement('canvas'); c.width = 600; c.height = 160; c.style.cssText = 'width:300px;height:80px;display:block'; document.getElementById('h-small').appendChild(c); const x = c.getContext('2d'); x.fillStyle = OT.theme.bg; x.fillRect(0, 0, 600, 160); x.font = OT.canvas.font(12); x.fillStyle = OT.theme.text; x.fillText('half size label', 10, 60); });
      got = await p.capture(async () => { await p.assertCanvasText(12, 'x'); });
      check(got.some(m => /DISPLAYED below 12 px/.test(m) && /half size label/.test(m) && /\b6 px/.test(m)), 'harness: canvas text displayed at 6 CSS px was not reported: ' + JSON.stringify(got));
      // 4. DOM text with the bad value after the 60th character
      await pg.evaluate(() => { const d = document.createElement('div'); d.id = 'h-long'; d.style.cssText = 'position:fixed;left:420px;top:420px;z-index:70;background:' + OT.theme.bg; d.textContent = 'Refinery utilisation in PADD 3 rose for the third week in a row and now stands at NaN% of capacity'; document.body.appendChild(d); });
      got = await p.capture(async () => { await p.assertNoBadText(null, 'x'); });
      check(got.some(m => /NaN%/.test(m)), 'harness: "NaN%" after the 60th character of a text node was not reported: ' + JSON.stringify(got));
      // 5. "[OT…" console warnings are failures (a swallowed poll error must not pass)
      const e0 = p.errors().length;
      await pg.evaluate(async () => { const stop = OT.poll(() => { throw new TypeError('d.totals is undefined'); }, 300, { module: null }); await new Promise(r => setTimeout(r, 500)); stop(); });
      const errs = p.errors().slice(e0);
      check(errs.some(e => /\[OT\.poll\] callback failed/.test(e) && /d\.totals is undefined/.test(e)), 'a TypeError thrown inside an OT.poll callback did not reach the console as an error: ' + JSON.stringify(errs));
      p.allow(/d\.totals is undefined/);
      await p.close();
    });

    /* ───────────────────────── stale-on-error payloads ─────────────────────────
       core/cache.py serves the last good answer with stale:true + error when a refresh fails; the documented
       example showed it with the badge of a healthy answer. */
    await section('stale payload', async () => {
      const id = crypto[0] || allIds[0];
      const p = await ctx.open({ tag: 'stalepayload' });
      let alter = true;
      await p.page.route('**/api/candles*', async route => {
        let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
        const body = await res.json();
        if (alter) Object.assign(body, { stale: true, error: { code: 'upstream_error', message: 'HTTP 503 Service Unavailable', source: body.source }, as_of: body.as_of - 600 });
        await route.fulfill({ response: res, json: body });
      });
      await p.goto('MON/' + id);
      const pg = p.page;
      await pg.evaluate(() => {
        let ctx, note;
        OT.register({ id: 'ZPAY', title: 'Payload Test', group: 'TOOLS', needs: 'candles',
          mount(root, c) { ctx = c; note = OT.ui.h('div', { id: 'zpay-note', class: 'note', style: 'padding:12px' }); root.append(note); },
          show(i) {
            const ok = ctx.guard();
            ctx.poll(async () => { const d = await OT.api('/api/candles', { id: i, tf: '1d', limit: 2 }, { noDedupe: true }); if (!ok()) return;
              const b = d.candles[d.candles.length - 1];
              ctx.header.setPayload(d);                                             // the pattern DEV_NOTES documents
              note.replaceChildren('1d bar ' + OT.fmt.date(b.t) + ' close ', OT.ui.h('span', { class: 'val' }, OT.fmt.px(i, b.c)), ' ', OT.ui.badge.forPayload(d)); }, 60000);
          } });
      });
      await p.go('ZPAY');
      await p.waitFor(() => document.querySelector('#zpay-note .badge'), { msg: 'payload module rendered' });
      const r = await pg.evaluate(() => { const hb = document.querySelector('.mod[data-mod="ZPAY"] .mod-head .badge'), nb = document.querySelector('#zpay-note .badge');
        return { head: hb.dataset.fresh, headCls: hb.className, headTip: hb.getAttribute('data-tip'), asof: document.querySelector('.mod[data-mod="ZPAY"] .mod-asof').textContent, note: nb.dataset.fresh, noteTip: nb.getAttribute('data-tip') }; });
      check(r.head === 'STALE' && /b-stale/.test(r.headCls), 'a stale:true payload is shown in the module header as ' + r.head + ' (must be STALE)');
      check(/HTTP 503/.test(r.headTip) && /could not refresh/.test(r.headTip), 'the STALE badge does not give the gateway’s reason: ' + JSON.stringify(r.headTip));
      check(/1\dm ago|10m ago/.test(r.asof), 'the header age of a stale payload must be the age of the old answer (about 10 m): ' + r.asof);
      check(r.note === 'STALE' && /HTTP 503/.test(r.noteTip), 'OT.ui.badge.forPayload(stale payload) shows ' + r.note);
      await p.assertContrast(4.5, '.mod[data-mod="ZPAY"]', 'stale payload');
      await p.shotNow('core_stale_payload_1920x1080');
      // the same module with a healthy answer shows the real freshness
      alter = false;
      await p.select(crypto[1] || allIds[1]);
      await p.waitFor(() => { const b = document.querySelector('.mod[data-mod="ZPAY"] .mod-head .badge'); return b && b.dataset.fresh !== 'STALE' && b.dataset.fresh !== 'NO'; }, { msg: 'healthy payload shows its own freshness' });
      await p.close();
    });

    /* ───────────────────────── page-side helpers ───────────────────────── */
    await section('badges, formatters, tables, polls', async () => {
      const p = await ctx.open({ tag: 'helpers' });
      await p.goto('MON/' + allIds[0]);
      const pg = p.page;
      const r = await pg.evaluate(() => {
        const out = {};
        const b = x => { const e = OT.ui.badge(x, 'eia'); return e.className.replace('badge ', '') + '|' + e.querySelector('.b-f').textContent + '|' + (/No source answered/.test(e.getAttribute('data-tip')) ? 'NOSRC' : 'tip-ok'); };
        out.badges = ['WEEKLY', 'MONTHLY', 'ANNUAL', 'MODEL', '~RT', 'RT ', ' d15', 'delayed', 'H1', 'D1', null, 'none', ''].map(b);
        out.labelled = (() => { const e = OT.ui.badge('WEEKLY', 'eia', { label: 'WEEKLY 11-06' }); return e.className + '|' + e.textContent + '|' + e.getAttribute('data-tip'); })();
        const F = OT.fmt;
        out.fmt = [F.utc(1e21), F.hm(1e21), F.date(1e21), F.asof(1e21), F.age(1e21), F.utc(1731400000e9), F.utc(1731400000e6), F.utc(1731400000e3), F.utc(new Date(1731400000000)),
          F.compact(999999), F.compact(999994), F.compact(999999999), F.compact(1234567), F.compact(999), F.compact(-999999)];
        return out;
      });
      check(r.badges.join(' ') === 'b-eod|WEEKLY|tip-ok b-eod|MONTHLY|tip-ok b-eod|ANNUAL|tip-ok b-derived|MODEL|tip-ok b-delayed|~RT|tip-ok b-rt|RT|tip-ok b-delayed|D15|tip-ok b-delayed|DELAYED|tip-ok b-other|H1|tip-ok b-delayed|D1|tip-ok b-none|NO SOURCE|NOSRC b-none|NO SOURCE|NOSRC b-none|NO SOURCE|NOSRC',
        'OT.ui.badge classes: ' + r.badges.join(' '));
      check(/b-eod/.test(r.labelled) && /WEEKLY 11-06EIA/.test(r.labelled) && !/No source/.test(r.labelled), 'badge with a label: ' + r.labelled);
      check(r.fmt.join(' ') === '— — — — — 08:26:40Z 08:26:40Z 08:26:40Z 08:26:40Z 1.00M 999.99K 1.00B 1.23M 999 -1.00M', 'formatters: ' + r.fmt.join(' '));
      // OT.poll without an owning module / table rows without keys: said out loud
      let n0 = p.errors().length;
      await pg.evaluate(async () => { await Promise.resolve(); const stop = OT.poll(() => {}, 5000); stop(); const ok = OT.poll(() => {}, 5000, { module: null }); ok(); });
      let got = p.errors().slice(n0);
      check(got.length === 1 && /\[OT\.poll\] created without an owning module/.test(got[0]), 'OT.poll created outside mount()/show() must warn once (and not with {module:null}): ' + JSON.stringify(got));
      n0 = p.errors().length;
      const drawn = await pg.evaluate(() => { const h = document.createElement('div'); h.style.cssText = 'position:fixed;left:400px;top:200px;width:300px;z-index:60'; document.body.appendChild(h);
        const t = OT.ui.table(h, { columns: [{ id: 'name', label: 'Name' }] }); t.update([{ name: 'a' }, { name: 'b' }, { name: 'c' }]);
        const t2 = OT.ui.table(h, { key: 'k', columns: [{ id: 'k', label: 'K' }] }); t2.update([{ k: 1 }, { k: 1 }, { k: 2 }]);
        const n = [h.querySelectorAll('table')[0].querySelectorAll('tbody tr').length, h.querySelectorAll('table')[1].querySelectorAll('tbody tr').length]; h.remove(); return n; });
      got = p.errors().slice(n0);
      check(got.length === 2 && /\[OT\.ui\.table\] a row has no key/.test(got[0]) && /duplicate row key "1"/.test(got[1]), 'tables with missing / duplicate keys must warn: ' + JSON.stringify(got) + ' rows drawn ' + drawn);
      p.allow(/\[OT\.poll\] created without|\[OT\.ui\.table\]/);
      // /api/quotes failing: visible in the QUOTES lamp and the status bar
      await pg.route('**/api/quotes', route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'injected by the test: yahoo HTTP 503', source: 'yahoo' } }) }));
      p.allow(/api\/quotes/);
      await pg.evaluate(() => OT.refreshQuotes());
      const q = await pg.evaluate(() => ({ lamp: document.querySelector('.lamp[data-lamp="quotes"]').dataset.state, tip: document.querySelector('.lamp[data-lamp="quotes"]')._tip(), stq: document.getElementById('st-q').textContent }));
      check(q.lamp === 'warn' && /\/api\/quotes failed/.test(q.tip) && /injected by the test/.test(q.tip), 'a failing /api/quotes is not shown in the QUOTES lamp: ' + JSON.stringify(q));
      check(/\/api\/quotes failed/.test(q.stq), 'a failing /api/quotes is not shown in the status bar: ' + q.stq);
      await pg.unroute('**/api/quotes');
      await pg.evaluate(() => OT.refreshQuotes());
      check(await pg.evaluate(() => !/failed/.test(document.querySelector('.lamp[data-lamp="quotes"]')._tip()) && !/failed/.test(document.getElementById('st-q').textContent)), 'the /api/quotes error stays on screen after a successful refresh');
      // shared building blocks go grey with the gateway (CSS only: the class is what the core toggles)
      const dim = await pg.evaluate(() => { const body = document.querySelector('.mod:not([hidden]) .mod-body'); const box = OT.ui.h('div', { id: 'dim', style: 'position:relative;z-index:9' },
          OT.ui.h('div', { class: 'stat-value' }, '85.91'), OT.ui.h('dl', { class: 'kv' }, OT.ui.h('dt', {}, 'High'), OT.ui.h('dd', {}, '86.20')), OT.ui.h('span', { class: 'up' }, '+0.64%'), OT.ui.h('span', { class: 'down' }, '-0.10'));
        body.appendChild(box);
        const cols = () => ['.stat-value', '.kv dd', '.up', '.down'].map(s => getComputedStyle(box.querySelector(s)).color);
        const on = cols(); document.body.classList.add('gw-offline'); const off = cols(); document.body.classList.toggle('gw-offline', OT.state.gateway === 'offline'); box.remove();
        const t3 = getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim().toLowerCase();
        const hex = c => '#' + c.match(/\d+/g).slice(0, 3).map(v => (+v).toString(16).padStart(2, '0')).join('');
        return { on: on.map(hex), off: off.map(hex), t3 }; });
      check(dim.off.every(c => c === dim.t3) && dim.on.every(c => c !== dim.t3), '.stat-value / .kv dd / .up / .down stay bright while the gateway is offline: ' + JSON.stringify(dim));
      await p.close();
    });

    /* ───────────────────────── module lifecycle ─────────────────────────
       One exception in show() killed a module for every instrument; a ctx.state overlay outlived its activation
       and its retry fired in a hidden module. */
    await section('module failures and states belong to an activation', async () => {
      const a = allIds[0], b = crypto[0] || allIds[1];
      const p = await ctx.open({ tag: 'activation' });
      p.allow(/ZSHOW deliberate/);
      await p.goto('MON/' + b);
      const pg = p.page;
      await pg.evaluate(bad => {
        const L = window.__L = { show: [], boom: true, retry: [], ctx: null };
        OT.register({ id: 'ZSHOW', title: 'Show Test', group: 'TOOLS', needs: null, instrument: true,
          mount(root) { root.appendChild(OT.ui.h('div', { id: 'zshow-body', style: 'padding:12px' }, '')); },
          show(id) { L.show.push(id); if (id === bad && L.boom) throw new Error('ZSHOW deliberate for ' + id); document.getElementById('zshow-body').textContent = 'data for ' + id; } });
        OT.register({ id: 'ZSTATE', title: 'State Test', group: 'TOOLS', needs: null, instrument: true,
          mount(root, ctx) { L.ctx = ctx; root.appendChild(OT.ui.h('div', { id: 'zstate-body', style: 'padding:12px' }, '')); },
          show(id) { document.getElementById('zstate-body').textContent = 'data for ' + id;
            if (id === bad) L.ctx.state({ kind: 'error', title: id + ' failed', detail: 'test', retryIn: 2, onRetry() { L.retry.push({ mod: OT.state.mod, visible: L.ctx.visible }); } }); } });
      }, a);
      const view = m => pg.evaluate(mm => { const s = document.querySelector('.mod[data-mod="' + mm + '"]'); const cover = s.querySelector('.mod-cover'), body = s.querySelector('.mod-body:not(.mod-cover)');
        return { cover: !cover.hidden, coverText: cover.hidden ? '' : cover.textContent, body: !body.hidden, text: body.textContent, states: body.querySelectorAll('.ot-state').length, retryBtn: !!cover.querySelector('[data-retry]'), show: window.__L.show.join(), retry: window.__L.retry }; }, m);
      await p.go('ZSHOW');
      let v = await view('ZSHOW');
      check(v.body && !v.cover && /data for/.test(v.text), 'ZSHOW for ' + b + ': ' + JSON.stringify(v));
      await p.select(a);
      v = await view('ZSHOW');
      check(v.cover && /ERROR/.test(v.coverText) && v.coverText.indexOf(a) >= 0 && v.retryBtn, 'show() throwing for ' + a + ' must show an error with a Try again button: ' + JSON.stringify(v));
      await p.select(b);
      v = await view('ZSHOW');
      check(v.show === [b, a, b].join() && v.body && !v.cover && v.text === 'data for ' + b, 'after one failed show() the module is dead for every instrument: ' + JSON.stringify(v));
      await p.select(a); await p.go('MON'); await p.go('ZSHOW');
      v = await view('ZSHOW');
      check(v.show === [b, a, b, a, a].join() && v.cover, 'coming back to the tab must try show() again: ' + JSON.stringify(v));
      await pg.evaluate(() => { window.__L.boom = false; });
      await p.mouse.click('.mod[data-mod="ZSHOW"] [data-retry]');
      await pg.evaluate(() => window.__otT.raf(2));
      v = await view('ZSHOW');
      check(v.body && !v.cover && v.text === 'data for ' + a, '"Try again" did not re-run show(): ' + JSON.stringify(v));
      // ctx.state belongs to the activation
      await p.go('ZSTATE');
      v = await view('ZSTATE');
      check(v.states === 1, 'ctx.state error overlay for ' + a + ' missing: ' + JSON.stringify(v));
      await p.select(b);
      v = await view('ZSTATE');
      check(v.states === 0 && v.text === 'data for ' + b, 'the error overlay of ' + a + ' still covers the module after selecting ' + b + ': ' + JSON.stringify(v));
      await p.select(a);
      await p.go('MON');
      await pg.evaluate(() => { window.__L.retry = []; });
      await sleep(3600);
      v = await view('ZSTATE');
      check(v.retry.length === 0, 'the retry of a hidden module fired (it calls the gateway while nobody looks): ' + JSON.stringify(v.retry));
      await p.go('ZSTATE');
      await p.waitFor(() => window.__L.retry.length === 1 && window.__L.retry[0].visible === true && window.__L.retry[0].mod === 'ZSTATE', { timeout: 6000, msg: 'the retry countdown runs (once) while the module is visible' });
      await p.close();
    });

    /* ───────────────────────── canvas on a 125 % display ─────────────────────────
       OT.canvas.fit grew a canvas without CSS size on every call when devicePixelRatio was not 1. */
    await section('canvas fit at devicePixelRatio 1.25', async () => {
      const p = await ctx.open({ tag: 'dpr', dpr: 1.25, viewport: { width: 1536, height: 864 } });
      await p.goto('MON/' + allIds[0]);
      const r = await p.page.evaluate(() => {
        const out = { dpr: window.devicePixelRatio };
        const hostB = document.createElement('div'); hostB.style.cssText = 'position:fixed;left:400px;top:200px;z-index:60'; document.body.appendChild(hostB);
        const bare = document.createElement('canvas'); hostB.appendChild(bare);            // no class, no style: its CSS size is its width/height attributes
        out.bare = []; for (let i = 0; i < 8; i++) { OT.canvas.fit(bare); out.bare.push(bare.width + 'x' + bare.height + '/' + bare.clientWidth + 'x' + bare.clientHeight); }
        hostB.remove();
        const box = document.createElement('div'); box.style.cssText = 'position:fixed;left:400px;top:200px;width:333px;height:111px'; const c = document.createElement('canvas'); c.className = 'ot-canvas'; box.appendChild(c); document.body.appendChild(box);
        out.sized = []; for (let i = 0; i < 4; i++) { const f = OT.canvas.fit(c); out.sized.push(c.width + 'x' + c.height + '/' + f.w + 'x' + f.h); }
        out.pinned = c.style.width; box.remove();
        return out;
      });
      check(r.dpr === 1.25, 'this page should run at devicePixelRatio 1.25, got ' + r.dpr);
      check(new Set(r.bare).size === 1 && r.bare[0] === '375x188/300x150', 'OT.canvas.fit on a canvas without CSS size grows it call after call at DPR 1.25: ' + r.bare.join(' → '));
      check(new Set(r.sized).size === 1 && r.sized[0] === '416x139/333x111' && r.pinned === '', 'a CSS-sized canvas at DPR 1.25: ' + r.sized.join(' → ') + ' pinned=' + r.pinned);
      const warned = p.errors().filter(e => /\[OT\.canvas\.fit\] a canvas has no CSS size/.test(e));
      check(warned.length === 1, 'the unsized canvas must be reported once in the console: ' + JSON.stringify(p.errors()));
      p.allow(/\[OT\.canvas\.fit\]/);
      await p.close();
    });

    /* ───────────────────────── selection, focus and tooltips survive live updates (spec rule 5) ───────────────────────── */
    await section('selection, focus, tooltips', async () => {
      if (!crypto.length) return;
      const id = crypto[0];
      const p = await ctx.open({ tag: 'select' });
      await p.goto('MON/' + id);
      const pg = p.page;
      await p.waitFor(i => OT.state.stream === 'live' && OT.quotes[i] && OT.quotes[i].status === 'ok', { arg: id, msg: 'live quotes' });
      const ticks = () => pg.evaluate(i => window.__otWsRecv.filter(f => f.id === i).length, id);
      // header price: select it with a real double click, let it tick
      const box = await pg.locator('#hdr-last').boundingBox();
      await pg.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { clickCount: 3 });
      const sel0 = await pg.evaluate(() => getSelection().toString().trim());
      const n0 = await ticks();
      await p.waitFor(([i, n]) => window.__otWsRecv.filter(f => f.id === i).length >= n + 4, { arg: [id, n0], timeout: 15000, msg: 'four more ' + id + ' ticks while the header price is selected' });
      const s1 = await pg.evaluate(i => ({ sel: getSelection().toString().trim(), shown: document.getElementById('hdr-last').textContent, held: OT.fmt.px(i, OT.quotes[i].last),
        flag: document.body.dataset.held || '', bar: getComputedStyle(document.getElementById('status'), '::before').content }), id);
      check(sel0.length >= 3 && s1.sel === sel0, 'the selection of the header price did not survive live updates: ' + JSON.stringify(sel0) + ' → ' + JSON.stringify(s1.sel));
      if (s1.shown !== s1.held) check(+s1.flag >= 1 && /SELECTION HOLDS/.test(s1.bar), 'a value is held back for the selection (' + s1.shown + ' shown, ' + s1.held + ' current) but the status bar does not say so: ' + JSON.stringify([s1.flag, s1.bar]));
      await pg.mouse.click(900, 500);                                      // selection gone: the value catches up at once
      await pg.evaluate(() => window.__otT.raf(3));
      const s2 = await pg.evaluate(i => ({ shown: document.getElementById('hdr-last').textContent, held: OT.fmt.px(i, OT.quotes[i].last), sel: getSelection().toString() }), id);
      check(s2.sel === '' && s2.shown === s2.held, 'after the selection is released the header must show the current price: ' + JSON.stringify(s2));
      check(await pg.evaluate(() => !('held' in document.body.dataset)), 'the "selection holds" notice stays after the selection was released');
      // a rail price can be selected (to copy it) and stays selected
      const rb = await pg.locator('#watch .wrow[data-id="' + id + '"] .w-last').boundingBox();
      await pg.mouse.click(rb.x + rb.width / 2, rb.y + rb.height / 2, { clickCount: 3 });
      const r0 = await pg.evaluate(() => getSelection().toString().trim());
      await sleep(2200);
      const r1 = await pg.evaluate(() => getSelection().toString().trim());
      check(r0.length >= 3 && r1 === r0, 'a rail price cannot be selected, or loses its selection on the next tick: ' + JSON.stringify(r0) + ' → ' + JSON.stringify(r1));
      await pg.mouse.click(900, 500);
      // table: a focused row keeps focus through a re-sort; a selected cell is not rewritten under the selection
      const t = await pg.evaluate(async () => {
        const out = {};
        const host = document.createElement('div'); host.id = 'e2e-t'; host.style.cssText = 'position:fixed;left:420px;top:220px;width:420px;z-index:60;background:' + OT.theme.bg; document.body.appendChild(host);
        const tb = OT.ui.table(host, { key: 'id', sortable: true, onRowClick() {}, sort: { col: 'v', dir: -1 }, columns: [{ id: 'id', label: 'Id' }, { id: 'v', label: 'Value', num: true, fmt: v => OT.fmt.num(v, 2) }] });
        tb.update([{ id: 'a', v: 4 }, { id: 'b', v: 3 }, { id: 'c', v: 2 }, { id: 'd', v: 1 }]);
        const trB = host.querySelector('tr[data-key="b"]'); trB.focus();
        tb.update([{ id: 'a', v: 4 }, { id: 'b', v: 9 }, { id: 'c', v: 2 }, { id: 'd', v: 1 }]);            // b jumps to the top
        out.order = Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        out.focus = document.activeElement === trB;
        const tdC = host.querySelector('tr[data-key="c"] td:nth-child(2)');
        getSelection().selectAllChildren(tdC);
        await new Promise(r => setTimeout(r, 30));
        tb.update([{ id: 'a', v: 4 }, { id: 'b', v: 9 }, { id: 'c', v: 20 }, { id: 'd', v: 1 }]);           // c changes and would move to the top
        out.held = tdC.textContent + '/' + getSelection().toString() + '/' + Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        getSelection().removeAllRanges();
        await new Promise(r => setTimeout(r, 60));
        out.released = tdC.textContent + '/' + Array.from(host.querySelectorAll('tbody tr')).map(x => x.dataset.key).join('');
        trB.focus();
        return out;
      });
      check(t.order === 'bacd' && t.focus, 'a focused table row loses focus when the table re-sorts: order ' + t.order + ', focus kept ' + t.focus);
      check(t.held === '2.00/2.00/bacd', 'a table cell was rewritten (or its row moved) under a text selection: ' + t.held);
      check(t.released === '20.00/cbad', 'the held table update was not applied once the selection was released: ' + t.released);
      const selBefore = await pg.evaluate(() => OT.state.sel);
      await pg.keyboard.press('ArrowDown');
      const k = await pg.evaluate(() => ({ focus: document.activeElement.dataset.key, sel: OT.state.sel }));
      check(k.focus === 'a' && k.sel === selBefore, 'ArrowDown on a focused table row moved the rail selection instead of the table focus: ' + JSON.stringify(k));
      await pg.evaluate(() => document.getElementById('e2e-t').remove());
      // tooltips keep counting while the mouse rests
      await pg.evaluate(() => { let n = 0; const el = OT.ui.h('span', { id: 'e2e-tip', class: 'btn', style: 'position:fixed;left:600px;top:300px;z-index:60', tip: () => 'evaluated ' + (++n) + ' times' }, 'hover me'); document.body.appendChild(el);
        const box = OT.ui.h('div', { id: 'e2e-cv', style: 'position:fixed;left:600px;top:360px;width:200px;height:80px;z-index:60;background:' + OT.theme.panel }); const c = OT.ui.h('canvas', { class: 'ot-canvas' }); box.appendChild(c); document.body.appendChild(box);
        let k = 0; window.__hoverOff = OT.canvas.hover(c, () => 'hit ' + (++k)); });
      await p.mouse.hover('#e2e-tip');
      await p.waitFor(() => { const tt = document.querySelector('.ot-tip'); return tt && !tt.hidden && /evaluated 1 times/.test(tt.textContent); }, { msg: 'tooltip on hover' });
      await sleep(2300);
      const tip1 = await pg.evaluate(() => document.querySelector('.ot-tip').textContent);
      check(/evaluated [3-9] times/.test(tip1), 'an open tooltip is frozen at the moment of hover (after 2.3 s it still says ' + JSON.stringify(tip1) + ')');
      await p.mouse.hover('#e2e-cv canvas');
      await p.waitFor(() => { const tt = document.querySelector('.ot-tip'); return tt && !tt.hidden && /^hit \d+$/.test(tt.textContent); }, { msg: 'canvas hover read-out' });
      const h0 = await pg.evaluate(() => document.querySelector('.ot-tip').textContent);
      await pg.evaluate(() => window.__hoverOff.refresh());
      const h1 = await pg.evaluate(() => document.querySelector('.ot-tip').textContent);
      await sleep(1300);
      const h2 = await pg.evaluate(() => document.querySelector('.ot-tip').textContent);
      check(h1 !== h0 && h2 !== h1, 'a canvas hover read-out is not re-evaluated by refresh() / once a second: ' + [h0, h1, h2].join(' → '));
      await pg.mouse.move(5, 5);
      await p.close();
    });

    /* ───────────────────────── hash, history, command line, waking up ───────────────────────── */
    await section('hash and history', async () => {
      const p = await ctx.open({ tag: 'hash' });
      await p.page.goto(p.base + '/app/openterm.html#%', { waitUntil: 'domcontentloaded' });
      await p.ready(20000).catch(() => p.fail('the page does not boot with a malformed hash (#%)'));
      const pg = p.page;
      let s = await pg.evaluate(() => ({ rows: document.querySelectorAll('#watch .wrow').length, hash: location.hash, mod: OT.state.mod, sel: OT.state.sel }));
      check(s.rows === allIds.length && s.hash === '#' + s.mod + '/' + s.sel, 'boot with "#%": ' + JSON.stringify(s));
      await pg.evaluate(() => { location.hash = '#%E0%A4%A'; });
      await sleep(300);
      s = await pg.evaluate(() => ({ hash: location.hash, mod: OT.state.mod, sel: OT.state.sel }));
      check(s.hash === '#' + s.mod + '/' + s.sel, 'a hash that cannot be decoded must be rewritten to the current state: ' + JSON.stringify(s));
      await pg.evaluate(i => { location.hash = '#NOPE/' + i; }, allIds[6]);
      await sleep(300);
      s = await pg.evaluate(() => ({ hash: location.hash, mod: OT.state.mod, sel: OT.state.sel }));
      check(s.sel === allIds[6] && s.hash === '#' + s.mod + '/' + allIds[6], 'an unknown module in the hash stays in the address bar: ' + JSON.stringify(s));
      // Back returns to the previous module / instrument instead of leaving the terminal
      await p.mouse.click('#tabs .tab[data-mod="CHRT"]');
      await p.mouse.click('#tabs .tab[data-mod="HELP"]');
      await p.mouse.click('#watch .wrow[data-id="' + allIds[2] + '"]');
      const st = () => pg.evaluate(() => OT.state.mod + '/' + OT.state.sel + ' ' + location.hash + ' ' + /openterm\.html/.test(location.href));
      check(await st() === 'HELP/' + allIds[2] + ' #HELP/' + allIds[2] + ' true', 'after three clicks: ' + await st());
      const want = ['HELP/' + allIds[6], 'CHRT/' + allIds[6], s.mod + '/' + allIds[6]];
      for (const w of want) {
        await pg.goBack(); await sleep(250);
        const now = await st();
        check(now === w + ' #' + w + ' true', 'Back should lead to ' + w + ' and stay in the terminal, got ' + now);
      }
      await pg.goForward(); await sleep(250);
      check((await st()).indexOf('CHRT/' + allIds[6]) === 0, 'Forward should lead to CHRT/' + allIds[6] + ', got ' + await st());
      // command line: everyday words, either word order, no accidental matches
      const cmd = await pg.evaluate(() => { const f = t => { const r = OT.parseCommand(t); return r.error ? 'ERR' : (r.id || '-') + '/' + (r.mod || '-'); };
        return ['oil', 'wti', 'spy', 'es', 'nq', 'gold', '10y', 'bund', 'mon mon', 'chart crude', 'crude chart', 'bitcoin book', 'tesla opts', 'cl chrt', 'oi', 'zzqx', 'eth'].map(t => t + '=' + f(t)).join(' '); });
      check(cmd === 'oil=CL/- wti=CL/- spy=SPX/- es=SPX/- nq=NDX/- gold=GC/- 10y=US10Y/- bund=DE10Y/- mon mon=-/MON chart crude=CL/CHRT crude chart=CL/CHRT bitcoin book=BTC/BOOK tesla opts=TSLA/OPTS cl chrt=CL/CHRT oi=ERR zzqx=ERR eth=ETH/-',
        'command line: ' + cmd);
      await p.close();
    });

    await section('waking up after a pause', async () => {
      const p = await ctx.open({ tag: 'resume' });
      await p.goto('MON/' + allIds[0]);
      const pg = p.page;
      await p.waitFor(() => OT.state.stream === 'live', { msg: 'stream live' });
      await pg.clock.install();
      await pg.evaluate(() => { window.__gw = []; OT.on('status', () => window.__gw.push(OT.state.gateway + '/' + OT.state.ws)); window.__opened0 = window.__otWsOpened; });
      await sleep(1500);
      await pg.clock.fastForward(60000);                                 // the laptop slept for a minute
      await sleep(4000);
      const r = await pg.evaluate(() => ({ log: window.__gw, opened: window.__otWsOpened - window.__opened0, gw: OT.state.gateway, stream: OT.state.stream, banner: !document.getElementById('offline').hidden }));
      check(r.log.every(x => x.indexOf('offline') < 0) && !r.banner && r.gw === 'online', 'waking after 60 s flashed GATEWAY OFFLINE although the gateway was fine: ' + JSON.stringify(r.log));
      check(r.opened === 0 && r.stream === 'live', 'waking after 60 s dropped a healthy websocket (' + r.opened + ' new socket(s), stream ' + r.stream + ')');
      await p.close();
    });

    /* ───────────────────────── names fit; the page holds below 1280 px ───────────────────────── */
    await section('rail names and narrow layouts', async () => {
      const p = await ctx.open({ tag: 'narrow' });
      await p.goto('MON/' + allIds[0]);
      const pg = p.page;
      for (const [w, h] of [[1280, 720], [1366, 768], [1920, 1080], [2560, 1440]]) {
        await p.setViewport(w, h);
        const cut = await pg.evaluate(() => Array.from(document.querySelectorAll('#watch .w-name')).filter(e => e.scrollWidth > e.clientWidth + 0.5).map(e => e.closest('.wrow').dataset.id + ' "' + e.textContent + '"'));
        check(!cut.length, w + 'x' + h + ': ' + cut.length + ' rail name(s) are cut off: ' + cut.slice(0, 6).join(', '));
      }
      const names = await pg.evaluate(() => Array.from(document.querySelectorAll('#watch .wrow')).map(r => r.dataset.id + '=' + r.querySelector('.w-name').textContent));
      check(names.every(n => { const [id, nm] = n.split('='); return nm === uni.instruments[id].short && nm.length <= 12; }), 'rail names are not the universe’s short names: ' + names.join(', '));
      for (const [w, h] of [[1093, 614], [1024, 576]]) {                  // 1366x768 and 1280x720 at Windows 125 %
        await p.setViewport(w, h);
        const tag = w + 'x' + h;
        await p.assertNoOverflow(tag);
        await p.assertMinFontPx(12, null, tag);
        const r = await pg.evaluate(() => {
          const out = [], vw = innerWidth;
          const vis = e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
          const kids = Array.from(document.querySelectorAll('#hdr > *')).filter(vis).map(e => e.getBoundingClientRect());
          for (let i = 1; i < kids.length; i++) if (kids[i].left < kids[i - 1].right - 0.5) out.push('header blocks ' + (i - 1) + ' and ' + i + ' overlap');
          const inst = Array.from(document.querySelectorAll('#hdr-inst > *')).filter(vis).map(e => e.getBoundingClientRect());
          for (let i = 1; i < inst.length; i++) if (inst[i].left < inst[i - 1].right - 0.5) out.push('header instrument items ' + (i - 1) + ' and ' + i + ' overlap');
          const lamps = document.getElementById('lamps').getBoundingClientRect(), meta = document.querySelector('.hi-meta').getBoundingClientRect();
          if (meta.right > lamps.left + 0.5) out.push('the "as of" text runs into the lamps');
          Array.from(document.querySelectorAll('#hdr *')).filter(vis).forEach(e => { const b = e.getBoundingClientRect(); if (b.right > vw + 0.5) out.push('header item outside the page: ' + (e.id || e.className)); });
          const nav = document.getElementById('tabs').getBoundingClientRect();
          Array.from(document.querySelectorAll('#tabs .tab')).forEach(t => { const b = t.getBoundingClientRect(); if (b.right > nav.right + 0.5 || b.left < nav.left - 0.5 || b.bottom > nav.bottom + 0.5 || b.right > vw) out.push('tab ' + t.dataset.mod + ' is cut off'); });
          const main = document.getElementById('main').getBoundingClientRect();
          return { out, mainH: main.height, mainW: main.width };
        });
        check(!r.out.length, tag + ': ' + [...new Set(r.out)].slice(0, 6).join('; '));
        check(r.mainH > 300 && r.mainW > 700, tag + ': main area is only ' + Math.round(r.mainW) + 'x' + Math.round(r.mainH));
        // every tab can be clicked
        await p.mouse.click('#tabs .tab[data-mod="HELP"]');
        check(await pg.evaluate(() => OT.state.mod) === 'HELP', tag + ': the HELP tab cannot be clicked');
        await p.mouse.click('#tabs .tab[data-mod="MON"]');
        await p.shotNow('core_narrow_' + tag);
      }
      await p.close();
    });

    await section('final', async () => {
      await ctx.go('MON'); await ctx.select(allIds[0]);
      await ctx.shot('core');
    });
  },
};
