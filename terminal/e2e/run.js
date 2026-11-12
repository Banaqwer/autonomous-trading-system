#!/usr/bin/env node
/* OPEN TERMINAL — e2e runner (spec §7).
 *
 *   NO_PROXY=127.0.0.1,localhost node terminal/e2e/run.js --base http://127.0.0.1:8080 [--only <slice|name>[,…]] [--out <dir>]
 *
 * Runs every terminal/e2e/*.spec.js that exports { name, slice, run(ctx) } (see lib.js for ctx).
 * Optional exports: before(ctx) — runs before the page is opened (install page.route handlers there);
 * init — a function injected into every page of the spec before any script (lib.js initScript style).
 * `--dir <folder>` takes the specs from another folder (e2e/selftest.js uses it to prove that the runner
 * rejects defective pages).
 * Each spec gets a fresh browser context at 1920x1080 with the page already open and OT.ready.
 * A spec fails if it recorded a failed check, threw, or left console errors / "[OT…" console warnings /
 * page errors / failed requests / HTTP >= 400 responses that it did not ctx.allow() — on ANY page it opened.
 * After run() the runner itself applies spec §7.3 to every module the spec visited (lib.audit): bad text,
 * 12 px, contrast, canvas text, overflow and header badge at 1920x1080 and 1366x768, at least one
 * screenshot, and one pass at devicePixelRatio 1.25. `--no-audit` skips that (debugging only).
 * Exit code: 0 = all passed, 1 = at least one failure, 2 = could not start.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const lib = require('./lib.js');

function args(argv) {
  const a = { base: 'http://127.0.0.1:8080', only: null, out: null, hash: null, audit: true };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--base') a.base = argv[++i];
    else if (k === '--only') a.only = String(argv[++i] || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    else if (k === '--out') a.out = argv[++i];
    else if (k === '--no-audit') a.audit = false;
    else if (k === '--dir') a.dir = path.resolve(argv[++i]);
    else if (k === '--name') a.name = String(argv[++i] || '').toLowerCase();       // exactly one spec, by its name
    else if (k === '--help' || k === '-h') { console.log('usage: node run.js --base URL [--only slice[,slice]] [--out dir]'); process.exit(0); }
    else { console.error('unknown argument: ' + k); process.exit(2); }
  }
  if (!a.out) a.out = path.join(require('os').tmpdir(), 'ot-e2e');
  return a;
}

(async () => {
  const a = args(process.argv);
  fs.mkdirSync(a.out, { recursive: true });
  const dir = a.dir || __dirname;
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.spec.js')).sort();
  const specs = [];
  for (const f of files) {
    let s;
    try { s = require(path.join(dir, f)); } catch (e) { console.error('FAIL  ' + f + ': cannot load: ' + e.message); process.exitCode = 1; continue; }
    if (!s || typeof s.run !== 'function') { console.error('FAIL  ' + f + ': does not export {name, slice, run}'); process.exitCode = 1; continue; }
    s.file = f; s.name = s.name || f.replace(/\.spec\.js$/, ''); s.slice = s.slice || s.name;
    if (a.only && a.only.indexOf(String(s.slice).toLowerCase()) < 0 && a.only.indexOf(String(s.name).toLowerCase()) < 0) continue;
    if (a.name && String(s.name).toLowerCase() !== a.name) continue;
    specs.push(s);
  }
  if (!specs.length) { console.error('no spec matches' + (a.only ? ' --only ' + a.only.join(',') : '')); process.exit(2); }

  console.log('OPEN TERMINAL e2e — base ' + a.base + ' — out ' + a.out);
  let browser;
  try { browser = await lib.launch(); } catch (e) { console.error('cannot launch chromium: ' + e.message); process.exit(2); }
  let failed = 0;
  for (const s of specs) {
    const shared = { base: a.base, out: a.out, failures: [], checks: { n: 0 }, shots: [], allowed: [], contexts: [], ctxs: [] };
    const t0 = Date.now();
    let ctx = null;
    try {
      shared.init = s.init || null;
      ctx = await lib.makeCtx(browser, { init: s.init }, shared);
      if (typeof s.before === 'function') await s.before(ctx);
      await ctx.goto(s.hash || '');
      await s.run(ctx);
    } catch (e) {
      shared.failures.push('threw: ' + (e && e.stack || e));
      if (ctx) { try { await ctx.shotNow('FAILED_' + s.name); } catch (e2) { /* page gone */ } }
    }
    if (ctx && a.audit) {
      try { await lib.audit(ctx, s, shared); } catch (e) { shared.failures.push('runner audit threw: ' + (e && e.stack || e)); }
    } else if (!a.audit) console.log('      (runner audit skipped: --no-audit)');
    (shared.ctxs || []).forEach(c => c.errors().forEach(e => shared.failures.push(e)));     // every page, not only the first
    shared.failures = Array.from(new Set(shared.failures));
    for (const c of shared.contexts) { try { await c.close(); } catch (e) { /* already closed */ } }
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    if (shared.failures.length) {
      failed++;
      console.log('FAIL  ' + s.name + ' [' + s.slice + ']  ' + shared.failures.length + ' failure(s), ' + shared.checks.n + ' checks, ' + secs + 's');
      shared.failures.slice(0, 40).forEach(f => console.log('   - ' + String(f).split('\n').slice(0, 6).join('\n     ')));
      if (shared.failures.length > 40) console.log('   … ' + (shared.failures.length - 40) + ' more');
    } else {
      console.log('PASS  ' + s.name + ' [' + s.slice + ']  ' + shared.checks.n + ' checks, ' + shared.shots.length + ' screenshots, ' + secs + 's');
    }
  }
  await browser.close();
  console.log(failed ? failed + ' of ' + specs.length + ' spec(s) FAILED' : 'all ' + specs.length + ' spec(s) passed');
  process.exit(failed || process.exitCode ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
