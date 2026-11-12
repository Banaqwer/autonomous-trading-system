/* OPEN TERMINAL — Playwright e2e harness (spec §7).
 *
 * A spec file is  terminal/e2e/<slice>.spec.js  exporting  { name, slice, run(ctx) }.
 * run.js opens the page, waits for OT.ready and hands the spec a ctx:
 *
 *   ctx.page, ctx.base, ctx.out                      Playwright page, gateway base URL, output dir
 *   await ctx.go('CHRT')  await ctx.select('BTC')    module / instrument (through OT.go / OT.select)
 *   ctx.check(cond, 'message')                       soft assertion: records a failure, keeps going
 *   await ctx.waitFor(() => pageFn, {timeout, msg})  poll a page-side predicate (records a failure on timeout)
 *   await ctx.assertNoBadText(scope?)                no NaN | undefined | Infinity | [object in visible text — DOM
 *                                                    (the whole text node, not its first characters) AND canvas
 *   await ctx.assertMinFontPx(12, scope?)            every visible DOM text run >= 12 px
 *   await ctx.assertContrast(4.5, scope?)            every visible DOM text run >= 4.5:1 on its real background
 *   await ctx.assertCanvasText(12)                   every canvas fillText/strokeText so far: >= 12 px as declared AND
 *                                                    as displayed (transform x CSS/backing ratio), the mono stack,
 *                                                    >= 4.5:1 against the pixels under it, no bad text
 *   await ctx.assertHeaderBadge('CHRT')              module header shows a source/freshness badge and an age
 *   await ctx.assertNoOverflow()                     the page itself does not scroll and no shell region is cut off
 *   await ctx.shot('name')                           screenshots at 1920x1080 and 1366x768 into ctx.out
 *   await ctx.mouse.hover(sel|{x,y}) .click(…) .wheel(sel, deltaY, times) .drag(sel, dx, dy)   real mouse input
 *   ctx.allow(/regex/)                               expected console / network error (e.g. a deliberate 404)
 *   await ctx.open({viewport, hash, base, block, init, dpr, ws})   a second page with the same checks attached
 *        ws: true → the page's websocket goes through the test (page.ws.mute(true), .rewrite(fn), .inject(msg))
 *   await ctx.capture(async () => { … })             run checks that are EXPECTED to fail; returns their messages
 *   ctx.errors()                                     console errors, "[OT…" console warnings, page errors, failed
 *                                                    requests and HTTP >= 400 not allowed so far
 *
 * After run() the RUNNER itself (run.js → audit()) re-visits every module the spec went to, at 1920x1080 and
 * 1366x768, and applies §7.3: bad text, 12 px, contrast, canvas text, overflow, header badge; it requires at
 * least one screenshot, collects the errors of EVERY page the spec opened, and makes one pass at
 * devicePixelRatio 1.25. A spec cannot pass by not checking.
 * Localhost only: start node with NO_PROXY=127.0.0.1,localhost.
 */
'use strict';
const fs = require('fs');
const net = require('net');
const path = require('path');
const { chromium } = require('playwright');

const BAD_TEXT = /NaN|undefined|Infinity|\[object/;
const SHOT_SIZES = [[1920, 1080], [1366, 768]];
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Runs in the page before any script. Records
     - every distinct canvas text draw (text, font, colour, transform, the pixels under it) so canvas text can be
       audited like DOM text: bad strings, size as DISPLAYED, contrast;
     - every WebSocket frame the page sends (subscription checks) and every `quote` frame it receives, and every
       /api/quotes answer: the page may only show prices it was given. */
function initScript() {
  const fonts = window.__otCanvasText = new Map();          // font string -> {n, sample}
  const draws = window.__otCanvasDraws = [];
  const seen = new Set();
  let ids = 0, samples = 0;
  const wrap = name => {
    const orig = CanvasRenderingContext2D.prototype[name];
    CanvasRenderingContext2D.prototype[name] = function (text, x, y) {
      const str = String(text);
      if (str.trim() !== '') {
        const f = this.font, e = fonts.get(f);
        if (e) e.n++; else fonts.set(f, { n: 1, sample: str.slice(0, 40) });
        try {
          const cv = this.canvas, style = name === 'fillText' ? this.fillStyle : this.strokeStyle;
          const key = (cv.__otId || (cv.__otId = ++ids)) + '|' + f + '|' + (typeof style === 'string' ? style : 'obj') + '|' + str;
          if (!seen.has(key) && draws.length < 8000) {
            seen.add(key);
            const m = this.getTransform();
            const px = parseFloat((/(\d+(?:\.\d+)?)px/.exec(f) || [0, 0])[1]);
            const w = this.measureText(str).width, al = this.textAlign, bl = this.textBaseline;
            const left = x - (al === 'center' ? w / 2 : al === 'right' || al === 'end' ? w : 0);
            const top = y - (bl === 'top' || bl === 'hanging' ? 0 : bl === 'middle' ? px * 0.5 : bl === 'bottom' || bl === 'ideographic' ? px : px * 0.8);
            let bg = null;
            if (cv.width && cv.height && samples < 2500 && Math.abs(m.b) < 1e-6 && Math.abs(m.c) < 1e-6 && w > 0 && px > 0) {
              /* What the text is about to be painted on: one read of its box (before the draw), sampled on a
                 6 x 4 grid. If an earlier frame's glyphs are still there (a canvas that is not cleared), most
                 of the grid is background all the same — the audit takes a high percentile. */
              samples++;
              const x0 = Math.max(0, Math.floor(m.a * left + m.e)), y0 = Math.max(0, Math.floor(m.d * top + m.f));
              const x1 = Math.min(cv.width, Math.ceil(m.a * (left + w) + m.e)), y1 = Math.min(cv.height, Math.ceil(m.d * (top + px) + m.f));
              if (x1 - x0 >= 2 && y1 - y0 >= 2) {
                const img = this.getImageData(x0, y0, x1 - x0, y1 - y0), d = img.data;
                bg = [];
                for (let gy = 0; gy < 4; gy++) for (let gx = 0; gx < 6; gx++) {
                  const ix = Math.min(img.width - 1, Math.floor((gx + 0.5) / 6 * img.width)), iy = Math.min(img.height - 1, Math.floor((gy + 0.5) / 4 * img.height)), o = (iy * img.width + ix) * 4;
                  bg.push([d[o], d[o + 1], d[o + 2], d[o + 3] / 255]);
                }
              }
            }
            draws.push({ canvas: cv, text: str, font: f, px, scale: Math.hypot(m.b, m.d), style: typeof style === 'string' ? style : null, alpha: this.globalAlpha, bg });
          }
        } catch (err) { /* tainted canvas etc.: the font summary above still has the draw */ }
      }
      return orig.apply(this, arguments);
    };
  };
  wrap('fillText'); wrap('strokeText');

  const sent = window.__otWsSent = [];
  const recv = window.__otWsRecv = [];                       // {t, id, last} of every `quote` frame received
  window.__otWsOpened = 0;
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { sent.push({ t: Date.now(), d: String(d) }); return send.apply(this, arguments); };
  const NativeWS = window.WebSocket;
  window.WebSocket = class extends NativeWS {
    constructor(...a) {
      super(...a);
      window.__otWsOpened++;
      this.addEventListener('message', ev => {
        try { const m = JSON.parse(ev.data); if (m && m.type === 'quote' && m.id) { recv.push({ t: Date.now(), id: m.id, last: m.last }); if (recv.length > 20000) recv.splice(0, 5000); } } catch (e) { /* not ours */ }
      });
    }
  };
  const rest = window.__otRestQuotes = [];                   // {t, quotes: {id: last}} of every /api/quotes answer
  const nativeFetch = window.fetch;
  window.fetch = function (input) {
    const p = nativeFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : input && input.url;
      if (url && /\/api\/quotes(\?|$)/.test(url)) {
        p.then(r => r.clone().json()).then(d => { const q = {}; Object.keys(d.quotes || {}).forEach(id => { q[id] = d.quotes[id] && d.quotes[id].last; }); rest.push({ t: Date.now(), quotes: q }); if (rest.length > 200) rest.shift(); }).catch(() => {});
      }
    } catch (e) { /* ignore */ }
    return p;
  };
}

/* Page-side helpers, injected once per page as window.__otT. */
function pageHelpers() {
  if (window.__otT) return;
  const T = window.__otT = {};
  const parse = c => {
    const m = /rgba?\(([^)]+)\)/.exec(c || '');
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const over = (fg, bg) => { const a = fg[3]; return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1]; };
  T.bgOf = el => {                       // effective background colour behind el (solid colours only)
    const stack = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) { stack.push(c); if (c[3] >= 1) break; }
    }
    let bg = [255, 255, 255, 1];
    for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
    return bg;
  };
  T.opacityOf = el => { let o = 1; for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity); return o; };
  T.ratio = (fg, bg) => { const a = lum(fg), b = lum(bg); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const parseCanvasColor = c => {                    // canvas fillStyle: "#rrggbb" or "rgba(r, g, b, a)"
    const m = /^#([0-9a-f]{6})$/i.exec(c || '');
    if (m) { const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255, 1]; }
    return /rgba?\(/.test(c || '') ? parse(c) : null;
  };
  const modOf = el => { const s = el.closest && el.closest('.mod[data-mod]'); return s ? s.dataset.mod : (el.closest && el.closest('#rail') ? 'rail' : el.id || (el.parentElement && el.parentElement.id) || 'page'); };
  /* Canvas text drawn so far (recorded by the init script): what is wrong with it.
     cssPx = declared px x the transform's scale x (CSS height / backing height): the size it is DISPLAYED at. */
  T.canvasAudit = (minPx, minRatio, badSource) => {
    const bad = new RegExp(badSource), out = { n: 0, small: [], faint: [], badText: [], measured: 0 };
    const dpr = window.devicePixelRatio || 1;
    (window.__otCanvasDraws || []).forEach(d => {
      out.n++;
      const cv = d.canvas, r = cv.getBoundingClientRect();
      const k = cv.height && r.height ? r.height / cv.height : 1 / dpr;
      const cssPx = Math.round(d.px * d.scale * k * 100) / 100;
      const where = modOf(cv) + ' canvas';
      if (bad.test(d.text)) out.badText.push({ text: d.text.slice(0, 80), where });
      if (cssPx < minPx - 0.05) out.small.push({ text: d.text.slice(0, 40), cssPx, font: d.font, where, backing: cv.width + 'x' + cv.height, css: Math.round(r.width) + 'x' + Math.round(r.height) });
      const fg0 = d.style ? parseCanvasColor(d.style) : null;
      if (fg0 && d.bg && d.bg.length) {
        const dom = cv.isConnected ? T.bgOf(cv) : [0, 0, 0, 1];
        const ratios = d.bg.map(b => { const under = over(b, dom); return T.ratio(over([fg0[0], fg0[1], fg0[2], fg0[3] * d.alpha], under), under); }).sort((a, b) => a - b);
        const ratio = Math.round(ratios[Math.floor((ratios.length - 1) * 0.7)] * 100) / 100;    // 70th percentile: grid lines / old glyphs under the box do not decide
        out.measured++;
        if (ratio < minRatio - 0.005) out.faint.push({ text: d.text.slice(0, 40), ratio, color: d.style, where });
      }
    });
    return out;
  };
  T.runs = (scope, badSource) => {       // visible text runs: [{text, bad, px, ratio, where}]  (bad: tested on the WHOLE text)
    const bad = badSource ? new RegExp(badSource) : null;
    const root = scope ? document.querySelector(scope) : document.body;
    const out = [];
    if (!root) return out;
    const where = el => {
      const parts = [];
      for (let n = el, i = 0; n && n.nodeType === 1 && i < 4; n = n.parentElement, i++) parts.unshift(n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (typeof n.className === 'string' && n.className ? '.' + n.className.trim().split(/\s+/).join('.') : ''));
      return parts.join(' > ');
    };
    const add = (el, text) => {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') return;
      const op = T.opacityOf(el);
      if (op === 0) return;
      const bg = T.bgOf(el), fg0 = parse(cs.color);
      const fg = over([fg0[0], fg0[1], fg0[2], fg0[3] * op], bg);
      const full = text.trim(), hit = bad ? bad.exec(full) : null;
      out.push({ text: hit ? full.slice(Math.max(0, hit.index - 30), hit.index + 30) : full.slice(0, 60), bad: !!hit,
        px: parseFloat(cs.fontSize), ratio: Math.round(T.ratio(fg, bg) * 100) / 100, where: where(el), family: cs.fontFamily });
    };
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.nodeValue || !n.nodeValue.trim()) continue;
      const el = n.parentElement;
      if (!el || el.closest('script,style,noscript,template,[hidden]')) continue;
      range.selectNodeContents(n);
      const r = range.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      add(el, n.nodeValue);
    }
    root.querySelectorAll('input,textarea,select').forEach(el => {
      if (el.closest('[hidden]')) return;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      const text = el.tagName === 'SELECT' ? (el.selectedOptions[0] ? el.selectedOptions[0].textContent : '') : (el.value || el.placeholder || '');
      if (text && text.trim()) add(el, text);
    });
    return out;
  };
  T.raf = n => new Promise(res => { const step = () => (n-- > 0 ? requestAnimationFrame(step) : res()); step(); });
}

function tcpProxy(targetPort, targetHost) {
  /* A dumb TCP relay in front of the gateway: stop() makes the gateway "die" for pages opened through it
     (HTTP and WebSocket alike) without touching the gateway itself; start() brings it back on the same port. */
  targetHost = targetHost || '127.0.0.1';
  let server = null, port = 0, frozen = false;
  const socks = new Set();
  const listen = () => new Promise((res, rej) => {
    frozen = false;
    server = net.createServer(c => {
      if (frozen) { socks.add(c); c.on('error', () => {}); c.on('close', () => socks.delete(c)); return; }   // accepted, never answered
      const u = net.connect(targetPort, targetHost);
      socks.add(c); socks.add(u);
      const kill = () => { c.destroy(); u.destroy(); socks.delete(c); socks.delete(u); };
      c.on('error', kill); u.on('error', kill); c.on('close', kill); u.on('close', kill);
      c.pipe(u); u.pipe(c);
    });
    server.on('error', rej);
    server.listen(port, '127.0.0.1', () => { port = server.address().port; res(port); });
  });
  return {
    get port() { return port; },
    get base() { return 'http://127.0.0.1:' + port; },
    start: listen,
    /* freeze(): connections stay open but nothing moves any more (a hung gateway, not a dead one) */
    freeze() { frozen = true; socks.forEach(s => { try { s.unpipe(); s.pause(); } catch (e) { /* ignore */ } }); },
    stop: () => new Promise(res => { socks.forEach(s => s.destroy()); socks.clear(); if (server) server.close(() => res()); else res(); server = null; }),
  };
}

async function makeCtx(browser, opt, shared) {
  const base = (opt.base || shared.base).replace(/\/+$/, '');
  const viewport = opt.viewport || { width: 1920, height: 1080 };
  const context = await browser.newContext({ viewport, deviceScaleFactor: opt.dpr || 1 });
  shared.contexts.push(context);
  await context.addInitScript(initScript);
  if (opt.init) await context.addInitScript(opt.init);
  const page = await context.newPage();
  const allowed = [];                      // per page: what one sub-test allows does not excuse another
  const raw = [];
  const tag = opt.tag ? '[' + opt.tag + '] ' : '';
  page.on('console', m => {
    const where = m.location() && m.location().url ? ' @ ' + m.location().url : '';
    if (m.type() === 'error') raw.push({ kind: 'console', text: tag + m.text() + where });
    // the core warns "[OT…" about things a module does wrong (poll without an owner, refused topic, unsized
    // canvas, swallowed poll error, duplicate table keys): in a test that is a failure, not a remark
    else if (m.type() === 'warning' && /^\[OT/.test(m.text())) raw.push({ kind: 'warning', text: tag + m.text() + where });
  });
  page.on('pageerror', e => raw.push({ kind: 'pageerror', text: tag + (e && e.stack || String(e)) }));
  page.on('requestfailed', r => raw.push({ kind: 'requestfailed', text: tag + r.method() + ' ' + r.url() + ' — ' + (r.failure() ? r.failure().errorText : 'failed') }));
  page.on('response', r => { if (r.status() >= 400) raw.push({ kind: 'http', text: tag + r.status() + ' ' + r.url() }); });
  if (opt.block) await page.route(opt.block, r => r.abort());

  /* opt.ws: the page's websocket passes through the test. ws.mute(true) swallows server -> page frames while
     the socket stays open (a fan-out task that died); ws.rewrite(fn) alters frames in transit; ws.inject(obj)
     sends the page a frame of the test's own. */
  const wsCtl = { muted: false, fn: null, sockets: [], opened: 0,
    mute(on) { wsCtl.muted = !!on; }, rewrite(fn) { wsCtl.fn = fn; },
    inject(obj) { wsCtl.sockets.forEach(w => { try { w.send(JSON.stringify(obj)); } catch (e) { /* closed */ } }); } };
  if (opt.ws) {
    await page.routeWebSocket(/\/ws$/, ws => {
      const server = ws.connectToServer();
      wsCtl.opened++; wsCtl.sockets.push(ws);
      ws.onMessage(m => { try { server.send(m); } catch (e) { /* closed */ } });
      server.onMessage(m => {
        if (wsCtl.muted) return;
        let out = m;
        if (wsCtl.fn && typeof m === 'string') { try { const o = wsCtl.fn(JSON.parse(m)); if (o === null) return; if (o !== undefined) out = JSON.stringify(o); } catch (e) { out = m; } }
        try { ws.send(out); } catch (e) { /* closed */ }
      });
      ws.onClose(() => { const i = wsCtl.sockets.indexOf(ws); if (i >= 0) wsCtl.sockets.splice(i, 1); try { server.close(); } catch (e) { /* closed */ } });
    });
  }

  const visited = new Map();               // module id -> instrument selected when the spec was last there
  const note = async () => { try { const s = await page.evaluate(() => (window.OT && OT.state ? [OT.state.mod, OT.state.sel] : null)); if (s && s[0]) visited.set(s[0], s[1]); } catch (e) { /* page gone */ } };
  const ctx = {
    browser, page, base, out: shared.out, failures: shared.failures, checks: shared.checks, sleep, tcpProxy, tag, visited, ws: wsCtl,
    url: (hash) => base + '/app/openterm.html' + (hash ? '#' + hash.replace(/^#/, '') : ''),
    check(cond, msg) { shared.checks.n++; if (!cond) shared.failures.push(tag + msg); return !!cond; },
    fail(msg) { shared.checks.n++; shared.failures.push(tag + msg); },
    allow(re) { allowed.push(re); },
    errors() { return raw.filter(e => !allowed.some(re => re.test(e.text))).map(e => e.kind + ': ' + e.text); },
    clearErrors() { raw.length = 0; },
    /* Run checks that are expected to FAIL (harness self-tests, deliberate defects): their failure messages are
       returned instead of being recorded against the spec. */
    async capture(fn) {
      const keep = shared.failures.splice(0, shared.failures.length);
      let got = [];
      try { await fn(); } finally { got = shared.failures.splice(0, shared.failures.length); shared.failures.push(...keep); }
      return got;
    },
    async ready(timeout) {
      await page.waitForFunction(() => window.OT && window.OT.ready === true, null, { timeout: timeout || 30000 });
      await page.evaluate(pageHelpers);
      await note();
    },
    async goto(hash, o) {
      await page.goto(ctx.url(hash), { waitUntil: 'domcontentloaded' });
      if (!(o && o.noWait)) await ctx.ready(o && o.timeout);
    },
    async go(mod) {
      const ok = await page.evaluate(m => window.OT.go(m), mod);
      ctx.check(ok, 'OT.go(' + mod + ') returned false: module not registered');
      await page.evaluate(() => window.__otT.raf(2));
      await note();
    },
    async select(id) {
      const ok = await page.evaluate(i => window.OT.select(i), id);
      ctx.check(ok, 'OT.select(' + id + ') returned false: instrument not in the universe');
      await page.evaluate(() => window.__otT.raf(2));
      await note();
    },
    async waitFor(fn, o) {
      o = o || {};
      try { await page.waitForFunction(fn, o.arg, { timeout: o.timeout || 10000, polling: o.polling || 100 }); return true; }
      catch (e) { ctx.fail('timed out (' + (o.timeout || 10000) + ' ms) waiting for: ' + (o.msg || String(fn).slice(0, 120))); return false; }
    },
    async runs(scope) { await page.evaluate(pageHelpers); return page.evaluate(([s, b]) => window.__otT.runs(s, b), [scope || null, BAD_TEXT.source]); },
    async canvasAudit(minPx, minRatio) { await page.evaluate(pageHelpers); return page.evaluate(([a, b, c]) => window.__otT.canvasAudit(a, b, c), [minPx || 12, minRatio || 4.5, BAD_TEXT.source]); },
    async assertNoBadText(scope, label) {
      const bad = (await ctx.runs(scope)).filter(r => r.bad);
      ctx.check(!bad.length, (label || scope || 'page') + ': bad text on screen: ' + bad.slice(0, 5).map(r => JSON.stringify(r.text) + ' in ' + r.where).join(' | '));
      const cv = (await ctx.canvasAudit()).badText;         // text painted on a canvas is on screen too
      ctx.check(!cv.length, (label || scope || 'page') + ': bad text drawn on a canvas: ' + cv.slice(0, 5).map(r => JSON.stringify(r.text) + ' on the ' + r.where).join(' | '));
      return bad.concat(cv);
    },
    async assertMinFontPx(min, scope, label) {
      min = min || 12;
      const runs = await ctx.runs(scope);
      const bad = runs.filter(r => r.px < min - 0.01);
      ctx.check(runs.length > 0, (label || scope || 'page') + ': no visible text found to measure');
      ctx.check(!bad.length, (label || scope || 'page') + ': ' + bad.length + ' text run(s) below ' + min + ' px: ' + bad.slice(0, 5).map(r => r.px + 'px ' + JSON.stringify(r.text) + ' in ' + r.where).join(' | '));
      return { n: runs.length, min: runs.reduce((m, r) => Math.min(m, r.px), 1e9) };
    },
    async assertContrast(min, scope, label) {
      min = min || 4.5;
      const runs = await ctx.runs(scope);
      const bad = runs.filter(r => r.ratio < min - 0.005);
      ctx.check(!bad.length, (label || scope || 'page') + ': ' + bad.length + ' text run(s) below ' + min + ':1 — ' + bad.slice(0, 5).map(r => r.ratio + ':1 ' + JSON.stringify(r.text) + ' in ' + r.where).join(' | '));
      return { n: runs.length, min: runs.reduce((m, r) => Math.min(m, r.ratio), 1e9) };
    },
    async canvasText() { return page.evaluate(() => Array.from(window.__otCanvasText || []).map(([font, v]) => ({ font, n: v.n, sample: v.sample }))); },
    async assertCanvasText(min, label, o) {
      min = min || 12;
      label = label || 'canvas';
      const fonts = await ctx.canvasText();
      const px = f => { const m = /(\d+(?:\.\d+)?)px/.exec(f); return m ? +m[1] : 0; };
      const small = fonts.filter(f => px(f.font) < min - 0.01);
      const noMono = fonts.filter(f => !/JetBrains Mono/.test(f.font) || !/monospace\s*$/.test(f.font));
      ctx.check(!small.length, label + ': canvas text below ' + min + ' px: ' + small.slice(0, 5).map(f => f.font + ' ' + JSON.stringify(f.sample)).join(' | '));
      ctx.check(!noMono.length, label + ': canvas text without the JetBrains Mono + monospace fallback stack (use OT.canvas.font): ' + noMono.slice(0, 5).map(f => f.font + ' ' + JSON.stringify(f.sample)).join(' | '));
      const a = await ctx.canvasAudit(min, (o && o.contrast) || 4.5);
      ctx.check(!a.small.length, label + ': canvas text DISPLAYED below ' + min + ' px (font px x transform x CSS/backing size): ' + a.small.slice(0, 5).map(r => r.cssPx + ' px ' + JSON.stringify(r.text) + ' on the ' + r.where + ' (backing ' + r.backing + ' shown at ' + r.css + ')').join(' | '));
      ctx.check(!a.faint.length, label + ': canvas text below ' + ((o && o.contrast) || 4.5) + ':1 against the pixels under it: ' + a.faint.slice(0, 5).map(r => r.ratio + ':1 ' + JSON.stringify(r.text) + ' in ' + r.color + ' on the ' + r.where).join(' | '));
      ctx.check(!a.badText.length, label + ': bad text drawn on a canvas: ' + a.badText.slice(0, 5).map(r => JSON.stringify(r.text) + ' on the ' + r.where).join(' | '));
      return fonts;
    },
    async assertHeaderBadge(mod) {
      const r = await page.evaluate(m => {
        const head = document.querySelector('.mod[data-mod="' + m + '"] .mod-head');
        if (!head) return { head: false };
        const b = head.querySelector('.mod-src:not([hidden]) .badge'), a = head.querySelector('.mod-src:not([hidden]) .mod-asof');
        return { head: true, badge: b ? b.textContent.trim() : null, asof: a && !a.hidden ? a.textContent.trim() : null, title: (head.querySelector('.mod-title') || {}).textContent };
      }, mod);
      ctx.check(r.head, mod + ': module header not found');
      ctx.check(!!r.badge, mod + ': header has no source/freshness badge (ctx.header.setSource / setPayload)');
      ctx.check(!!r.asof && /\d/.test(r.asof || ''), mod + ': header has no "as of" age (got ' + JSON.stringify(r.asof) + ')');
      return r;
    },
    async assertNoOverflow(label) {
      const r = await page.evaluate(() => {
        const de = document.documentElement, vw = window.innerWidth, vh = window.innerHeight, out = [];
        if (de.scrollWidth > vw + 1) out.push('page scrolls horizontally (' + de.scrollWidth + ' > ' + vw + ')');
        if (de.scrollHeight > vh + 1) out.push('page scrolls vertically (' + de.scrollHeight + ' > ' + vh + ')');
        ['#hdr', '#rail', '#tabs', '#main', '#status'].forEach(s => {
          const el = document.querySelector(s);
          if (!el) { out.push(s + ' missing'); return; }
          const b = el.getBoundingClientRect();
          if (b.left < -0.5 || b.top < -0.5 || b.right > vw + 0.5 || b.bottom > vh + 0.5) out.push(s + ' is outside the viewport');
          if (b.width < 20 || b.height < 16) out.push(s + ' collapsed to ' + Math.round(b.width) + 'x' + Math.round(b.height));
        });
        ['#hdr', '#tabs', '#status'].forEach(s => {
          const el = document.querySelector(s);
          if (el && el.scrollWidth > el.clientWidth + 1) out.push(s + ' content is cut off horizontally (' + el.scrollWidth + ' > ' + el.clientWidth + ')');
        });
        return out;
      });
      ctx.check(!r.length, (label || 'layout') + ': ' + r.join('; '));
      return r;
    },
    async setViewport(w, h) { await page.setViewportSize({ width: w, height: h }); await page.evaluate(() => (window.__otT ? window.__otT.raf(3) : null)); await sleep(120); },
    async shot(name, o) {
      const keep = page.viewportSize();
      const files = [];
      for (const [w, h] of (o && o.sizes) || SHOT_SIZES) {
        await ctx.setViewport(w, h);
        const f = path.join(shared.out, name + '_' + w + 'x' + h + '.png');
        await page.screenshot({ path: f });
        files.push(f);
      }
      await ctx.setViewport(keep.width, keep.height);
      shared.shots.push(...files);
      return files;
    },
    async shotNow(name) { const f = path.join(shared.out, name + '.png'); await page.screenshot({ path: f }); shared.shots.push(f); return f; },
    mouse: {
      async point(target) {
        if (target && typeof target === 'object' && 'x' in target) return target;
        const box = await page.locator(target).first().boundingBox();
        if (!box) throw new Error('no box for ' + target);
        return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      },
      async hover(target) { const p = await ctx.mouse.point(target); await page.mouse.move(p.x, p.y, { steps: 4 }); return p; },
      async click(target) { const p = await ctx.mouse.hover(target); await page.mouse.down(); await page.mouse.up(); return p; },
      async wheel(target, deltaY, times) { const p = await ctx.mouse.hover(target); for (let i = 0; i < (times || 1); i++) { await page.mouse.wheel(0, deltaY); await sleep(30); } return p; },
      async drag(target, dx, dy, steps) {
        const p = await ctx.mouse.hover(target);
        await page.mouse.down(); await page.mouse.move(p.x + dx, p.y + dy, { steps: steps || 8 }); await page.mouse.up();
        return p;
      },
    },
    async open(o) { return makeCtx(browser, o || {}, shared); },
    async close() { ctx.closed = true; await context.close(); },
  };
  (shared.ctxs || (shared.ctxs = [])).push(ctx);
  return ctx;
}

/* What the RUNNER enforces after a spec, whatever the spec itself checked (spec §7.3).
   Every module the spec visited is opened again with the instrument it was used with, at 1920x1080 and
   1366x768: no bad text (DOM and canvas), nothing below 12 px (DOM, canvas as displayed), contrast >= 4.5:1
   (DOM and canvas), no overflow, and a source/freshness badge with an age in the module header — unless the
   module shows the core's NO LIVE SOURCE state or a "being rebuilt" notice, which have no data to label.
   Then one pass at devicePixelRatio 1.25 (Windows 125 % scaling): canvases must not grow frame after frame,
   canvas text must still be >= 12 px as displayed, no errors. */
async function settle(ctx, mod) {
  const page = ctx.page;
  await page.evaluate(() => window.__otT.raf(3));
  try {                                                  // give a module that is loading a moment to show its data
    await page.waitForFunction(m => { const s = document.querySelector('.mod[data-mod="' + m + '"]'); return s && !s.querySelector('.mod-body:not([hidden]) > .ot-state[data-kind="loading"]'); }, mod, { timeout: 8000 });
  } catch (e) { /* still loading after 8 s: checked as it is */ }
  await sleep(150);
}
async function audit(ctx, spec, shared) {
  const mods = Array.from(ctx.visited.entries());
  if (!mods.length) { ctx.fail('runner: the spec visited no module (ctx.go) — there is nothing to verify'); return; }
  if (!shared.shots.length) ctx.fail('runner: the spec took no screenshot (ctx.shot) — spec §7.3 wants 1920x1080 and 1366x768 images to look at');
  if (ctx.closed) { ctx.fail('runner: the spec closed its main page; the module checks could not run'); return; }
  const page = ctx.page;
  for (const [w, h] of SHOT_SIZES) {
    await ctx.setViewport(w, h);
    for (const [mod, id] of mods) {
      const tag = 'runner ' + mod + (id ? '/' + id : '') + ' @' + w + 'x' + h;
      const ok = await page.evaluate(([m, i]) => { if (i) OT.select(i); return OT.go(m); }, [mod, id]);
      if (!ok) continue;                                 // a module the spec registered itself and removed again
      await settle(ctx, mod);
      await ctx.assertNoBadText(null, tag);
      await ctx.assertMinFontPx(12, null, tag);
      await ctx.assertContrast(4.5, null, tag);
      await ctx.assertNoOverflow(tag);
      const st = await page.evaluate(m => {
        const sec = document.querySelector('.mod[data-mod="' + m + '"]');
        const cover = sec && sec.querySelector('.mod-cover:not([hidden]) .ot-state');
        const own = sec && sec.querySelector('.mod-body:not(.mod-cover):not([hidden]) > .ot-state:not(.strip)');
        return { cover: cover ? cover.dataset.kind : null, own: own ? own.dataset.kind : null };
      }, mod);
      if (st.cover !== 'nosource' && st.own !== 'info') await ctx.assertHeaderBadge(mod);
    }
  }
  await ctx.assertCanvasText(12, 'runner: canvases of ' + spec.name);
  await ctx.setViewport(1920, 1080);

  // one pass at 125 % scaling
  const hi = await makeCtx(ctx.browser, { tag: 'dpr1.25', dpr: 1.25, viewport: { width: 1536, height: 864 }, base: ctx.base, init: shared.init }, shared);
  try {
    await hi.goto('');
    for (const [mod, id] of mods) {
      const ok = await hi.page.evaluate(([m, i]) => { if (i) OT.select(i); return OT.go(m); }, [mod, id]);
      if (!ok) continue;
      await settle(hi, mod);
      const sizes = () => hi.page.evaluate(m => Array.from(document.querySelectorAll('.mod[data-mod="' + m + '"] canvas')).filter(c => c.getBoundingClientRect().width > 0).map(c => c.width + 'x' + c.height + ' (css ' + c.clientWidth + 'x' + c.clientHeight + ')'), mod);
      const a = await sizes();
      await hi.page.evaluate(() => window.__otT.raf(12));
      await sleep(250);
      const b = await sizes();
      hi.check(a.join() === b.join(), 'runner ' + mod + ' @dpr 1.25: a canvas changes size from frame to frame (OT.canvas.fit on a canvas without CSS size?): ' + a.join(', ') + ' → ' + b.join(', '));
    }
    await hi.assertCanvasText(12, 'runner @dpr 1.25: canvases of ' + spec.name);
    await hi.assertNoBadText(null, 'runner @dpr 1.25');
    await hi.assertNoOverflow('runner @dpr 1.25 (1536x864)');
  } catch (e) { hi.fail('runner @dpr 1.25 threw: ' + (e && e.stack || e)); }
  await hi.close();
}

async function launch() { return chromium.launch(); }

module.exports = { launch, makeCtx, audit, tcpProxy, sleep, BAD_TEXT, SHOT_SIZES, pageHelpers, initScript, fs, path };
