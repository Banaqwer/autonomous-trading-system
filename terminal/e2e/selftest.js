#!/usr/bin/env node
/* OPEN TERMINAL — self-test of the e2e gate.
 *
 *   NO_PROXY=127.0.0.1,localhost node terminal/e2e/selftest.js --base http://127.0.0.1:8080 [--out <dir>]
 *
 * Runs the runner (run.js) on the deliberately defective specs in e2e/selftest/ and checks that it REJECTS each
 * of them for the right reason, and passes the one correct page. A gate that passes a broken page is worse
 * than no gate: run this after changing lib.js / run.js / the price gate in shell.spec.js.
 * Exit code 0 = the gate behaves, 1 = it let something through (or rejected the good page).
 */
'use strict';
const path = require('path');
const { spawnSync } = require('child_process');

const EXPECT = {
  good:        { pass: true },
  nobadge:     { pass: false, must: /header has no source\/freshness badge/ },
  pollbug:     { pass: false, must: /\[OT\.poll\] callback failed/ },
  canvasnan:   { pass: false, must: /bad text drawn on a canvas: "high NaN"/ },
  canvassmall: { pass: false, must: /canvas text below 12 px/ },
  canvasfaint: { pass: false, must: /canvas text below 4\.5:1/ },
  unsized:     { pass: false, must: /\[OT\.canvas\.fit\] a canvas has no CSS size/ },
  noshot:      { pass: false, must: /took no screenshot/ },
  novisit:     { pass: false, must: /took no screenshot/ },
  fabricated:  { pass: false, must: /displayed price differs from the quote the page holds|a value that was never received/ },
  heldwrong:   { pass: false, must: /the page holds a price the gateway did not give|which no quote frame and no \/api\/quotes answer contained/ },
};

const argv = process.argv.slice(2);
const arg = k => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : null; };
const base = arg('--base') || 'http://127.0.0.1:8080';
const out = arg('--out') || path.join(require('os').tmpdir(), 'ot-e2e-selftest');
const r = spawnSync(process.execPath, [path.join(__dirname, 'run.js'), '--base', base, '--dir', path.join(__dirname, 'selftest'), '--out', out], { encoding: 'utf8', env: process.env, timeout: 900000 });
const text = (r.stdout || '') + (r.stderr || '');
const blocks = {};
let cur = null;
text.split('\n').forEach(line => {
  const m = /^(PASS|FAIL)\s+(\S+)/.exec(line);
  if (m) { cur = m[2]; blocks[cur] = { pass: m[1] === 'PASS', text: line + '\n' }; }
  else if (cur) blocks[cur].text += line + '\n';
});
let bad = 0;
Object.keys(EXPECT).forEach(name => {
  const want = EXPECT[name], got = blocks[name];
  let verdict = 'ok  ';
  let why = want.pass ? 'passed, as a correct page must' : 'rejected: ' + want.must;
  if (!got) { verdict = 'BAD '; why = 'the runner printed no result for it'; }
  else if (got.pass !== want.pass) { verdict = 'BAD '; why = want.pass ? 'a correct page was REJECTED:\n' + got.text : 'a defective page PASSED'; }
  else if (!want.pass && !want.must.test(got.text)) { verdict = 'BAD '; why = 'rejected, but not for the expected reason ' + want.must + ':\n' + got.text; }
  if (verdict === 'BAD ') bad++;
  console.log(verdict + name.padEnd(12) + ' ' + why);
});
const extra = Object.keys(blocks).filter(n => !EXPECT[n]);
if (extra.length) console.log('note: specs without an expectation: ' + extra.join(', '));
console.log(bad ? bad + ' of ' + Object.keys(EXPECT).length + ' expectations NOT met — the gate is not trustworthy' : 'the gate rejected all ' + (Object.keys(EXPECT).length - 1) + ' defective pages and passed the correct one');
if (bad && process.env.OT_SELFTEST_VERBOSE) console.log(text);
process.exit(bad ? 1 : 0);
