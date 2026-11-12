/* Geo spec (S4): the WX module against the real gateway.
   What is on screen is compared with what the page received (its own /api answers, captured in transit) and with a
   second fetch by this test; the map is driven with real mouse and keyboard input; the canvas is read back pixel by
   pixel (it must stay readable: every image comes from the gateway's own tile route).
   The drawing code for a full advisory — forecast track with times, cone, wind field, a track and a cone that cross
   the date line, a JTWC system — is proven with backend/tests/fixtures/s4/api_storms_adv22.json served in transit:
   that file is what /api/storms answers for the recorded NHC advisory 22 of Tropical Storm Pena (2026-11-12 03:00 UTC)
   and a unit test keeps it equal to the route's output. Failure paths (no systems, stale, feed down, observations
   down, imagery down, hostile text, gateway stopped and restarted) are provoked by altering answers in transit.
   Upstream-vs-gateway comparisons are done by hand (see the slice report). */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

const FIX = path.join(__dirname, '..', 'backend', 'tests', 'fixtures', 's4', 'api_storms_adv22.json');
const WX = '.mod[data-mod="WX"]';
const CV = WX + ' .wx-cv canvas';
const sleep = ms => new Promise(r => setTimeout(r, ms));
function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res({ status: r.statusCode, body: JSON.parse(b) }); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
/* the page's formats, written out again here on purpose */
const isNum = v => typeof v === 'number' && isFinite(v);
function num(v, d) {
  if (!isNum(v)) return '—';
  let s = v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  if (/^-0(\.0*)?$/.test(s)) s = s.slice(1);
  return s;
}
const p2 = n => (n < 10 ? '0' : '') + n;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const hm = t => { const d = new Date(t * 1000); return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + 'Z'; };
const dayHm = t => { const d = new Date(t * 1000); return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + hm(t); };
const wkHm = t => DAYS[new Date(t * 1000).getUTCDay()] + ' ' + hm(t);
const ll = (lat, lon, d) => Math.abs(lat).toFixed(d === undefined ? 1 : d) + '°' + (lat < 0 ? 'S' : 'N') + ' ' + Math.abs(lon).toFixed(d === undefined ? 1 : d) + '°' + (lon < 0 ? 'W' : 'E');
const compass = deg => (isNum(deg) ? COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16] : '—');
const kt = v => (isNum(v) ? num(v, 0) + ' kt' : '—');
const mercY = lat => -(180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
/* where a lon/lat is on the canvas, from the view alone (independent of the module's own projection code) */
function project(v, lon, lat) {
  lon += 360 * Math.round((v.lon - lon) / 360);
  return { x: (lon - v.lon) * v.scale + v.w / 2, y: (mercY(lat) - mercY(v.lat)) * v.scale + v.h / 2 };
}

function tap(page, re) {
  const got = [];
  page.on('response', async r => { if (re.test(r.url()) && r.status() === 200) { try { got.push(await r.json()); } catch (e) { /* aborted */ } } });
  return got;
}
async function alter(p, glob, fn) {
  await p.page.route(glob, async route => {
    let res; try { res = await route.fetch(); } catch (e) { return route.abort(); }
    let body; try { body = await res.json(); } catch (e) { return route.fulfill({ response: res }); }
    const out = await fn(body, res);
    if (out && out.__status) return route.fulfill({ status: out.__status, contentType: 'application/json', headers: out.__headers || {}, body: JSON.stringify(out.body) });
    await route.fulfill({ response: res, json: out || body });
  });
}
/* page-side readers */
const view = c => c.page.evaluate(sel => document.querySelector(sel).__wx.view(), CV);
const hits = c => c.page.evaluate(sel => document.querySelector(sel).__wx.hits(), CV);
const selected = c => c.page.evaluate(sel => document.querySelector(sel).__wx.sel(), CV);
const cvBox = c => c.page.locator(CV).boundingBox();
const tipText = c => c.page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.innerText : ''; });
const settle = async c => { await c.page.evaluate(() => window.__otT.raf(3)); await sleep(260); await c.page.evaluate(() => window.__otT.raf(2)); };
/* mean colour of a small square of the canvas at css px (x, y); throws if the canvas is tainted */
const pixel = (c, x, y, half) => c.page.evaluate(([sel, px, py, hf]) => {
  const cv = document.querySelector(sel), k = cv.width / cv.clientWidth, g = cv.getContext('2d');
  const d = g.getImageData(Math.round((px - hf) * k), Math.round((py - hf) * k), Math.max(1, Math.round(2 * hf * k)), Math.max(1, Math.round(2 * hf * k))).data;
  let r = 0, gg = 0, b = 0; const n = d.length / 4;
  for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; }
  return [r / n, gg / n, b / n];
}, [CV, x, y, half || 2]);
const lum = p => 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
/* a coarse fingerprint of the whole canvas */
const fingerprint = c => c.page.evaluate(sel => {
  const cv = document.querySelector(sel), g = cv.getContext('2d'), d = g.getImageData(0, 0, cv.width, cv.height).data;
  let s = 0, bright = 0;
  for (let i = 0; i < d.length; i += 4 * 37) { s = (s * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) >>> 0; if (d[i] + d[i + 1] + d[i + 2] > 240) bright++; }
  return { hash: s, bright };
}, CV);
const sysRows = c => c.page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-sys')).map(el => ({ id: el.dataset.id, name: el.querySelector('.wx-sys-name').textContent,
  cls: el.querySelector('.wx-sys-cls').textContent, wind: el.querySelector('.wx-sys-wind').textContent, sub: el.querySelector('.wx-sys-sub').textContent, adv: el.querySelector('.wx-sys-l3').textContent,
  sym: el.querySelector('.wx-sym').textContent, sel: el.classList.contains('sel') })), WX);
const obsRows = c => c.page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-pane:not(.wx-detail) .wx-regions ~ .wx-tblbox tbody tr')).map(tr => ({ id: tr.dataset.key, sel: tr.classList.contains('sel'),
  cells: Array.from(tr.children).map(td => td.textContent) })), WX);
const hubRows = c => c.page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-hub-note ~ .wx-tblbox tbody tr')).map(tr => ({ id: tr.dataset.key, sel: tr.classList.contains('sel'),
  cells: Array.from(tr.children).map(td => td.textContent) })), WX);
const side = (c, id) => c.page.locator(WX + ' .wx-data-head .ot-tab[data-id="' + id + '"]').click();
const kvText = c => c.page.evaluate(sel => { const out = {}; const dl = document.querySelector(sel + ' .wx-kv'); const dts = dl.querySelectorAll('dt'), dds = dl.querySelectorAll('dd'); dts.forEach((dt, i) => { out[dt.textContent] = dds[i].innerText.replace(/\s+/g, ' ').trim(); }); return out; }, WX);
const wxReady = (c, extra) => c.waitFor(sel => { const cv = document.querySelector(sel + ' .wx-cv canvas'); const m = document.querySelector(sel);
  return cv && cv.__wx && m.querySelector('.wx-checked') && /^checked /.test(m.querySelector('.wx-checked').textContent) && m.querySelectorAll('.wx-regions ~ .wx-tblbox tbody tr').length > 0 && !/loading…/.test(m.querySelector('.mod-controls select').textContent); },
  Object.assign({ arg: WX, timeout: 60000, msg: 'WX shows the storm answer, the observations and the imagery list' }, extra || {}));

module.exports = {
  name: 'geo', slice: 'geo',
  async run(ctx) {
    const { page, check } = ctx;
    const only = (process.env.OT_E2E_SECTION || '').split(',').map(s => s.trim()).filter(Boolean);
    const section = async (name, fn) => { if (only.length && !only.some(w => name.indexOf(w) >= 0)) return; console.log('    · ' + name); try { await fn(); } catch (e) { ctx.fail(name + ' threw: ' + (e && e.stack || e)); } };
    const fixture = JSON.parse(fs.readFileSync(FIX, 'utf8'));
    const stormsGot = tap(page, /\/api\/storms/), obsGot = tap(page, /\/api\/wx\/obs/), layersGot = tap(page, /\/api\/satellite\/layers/), hubsGot = tap(page, /\/api\/wx\/hubs/), modelGot = tap(page, /\/api\/wx\/model/);
    const reqs = { storms: 0, obs: 0, layers: 0, model: 0, hubs: 0, tiles: [], foreign: [] };
    const t0 = Date.now();
    page.on('request', r => {
      const u = r.url();
      if (/\/api\/storms/.test(u)) reqs.storms++; else if (/\/api\/wx\/obs/.test(u)) reqs.obs++; else if (/\/api\/satellite\/layers/.test(u)) reqs.layers++;
      else if (/\/api\/wx\/model/.test(u)) reqs.model++; else if (/\/api\/wx\/hubs/.test(u)) reqs.hubs++; else if (/\/api\/satellite\/tile/.test(u)) reqs.tiles.push(u);
      if (!u.startsWith(ctx.base) && !/^(data|blob|about):/.test(u)) reqs.foreign.push(u);
    });
    ctx.allow(/api\/satellite\/tile/);                    // a tile GIBS does not have is HTTP 204 / an image error in the console, counted on the page

    /* ════════════════════════════ the real page ════════════════════════════ */
    let entered = false;
    const enter = async () => { if (entered) return; entered = true; await ctx.select('CL'); await ctx.go('WX'); await wxReady(ctx); await settle(ctx); };
    await section('real: systems are listed exactly as received; nothing moves between two looks', async () => {
      await enter();
      const d = stormsGot[stormsGot.length - 1];
      if (!check(!!d && d.source === 'noaa-nhc' && Array.isArray(d.storms) && Array.isArray(d.checked), 'the page received /api/storms with storms and checked')) return;
      check(d.checked.map(c => c.basin).join() === 'AL,EP,CP,JTWC', 'checked lists the basins that were asked: ' + d.checked.map(c => c.basin + (c.ok ? '' : ' (failed)')).join(' '));
      const rows = await sysRows(ctx);
      check(rows.length === d.storms.length, 'the list shows ' + rows.length + ' systems, received ' + d.storms.length);
      d.storms.forEach(s => {
        const r = rows.find(x => x.id === s.id);
        if (!check(!!r, 'no list row for ' + s.id)) return;
        check(r.name === s.name && r.wind === kt(s.wind_kt), s.id + ': shows ' + r.name + ' ' + r.wind + ', received ' + s.name + ' ' + s.wind_kt + ' kt');
        check(r.sub.indexOf(ll(s.lat, s.lon)) === 0, s.id + ': position shows "' + r.sub + '", received ' + s.lat + ', ' + s.lon);
        check(r.adv.indexOf(dayHm(s.advisory_at)) >= 0 && (!s.advisory_num || r.adv.indexOf('#' + s.advisory_num) >= 0) && / ago/.test(r.adv), s.id + ': advisory line "' + r.adv + '" has the issue time ' + dayHm(s.advisory_at) + ' and an age');
        if (isNum(s.speed_kt)) check(r.sub.indexOf('moving ' + s.heading_txt + ' at ' + kt(s.speed_kt)) >= 0, s.id + ': motion in "' + r.sub + '"');
        if (isNum(s.pressure_mb)) check(r.sub.indexOf(num(s.pressure_mb, 0) + ' mb') >= 0, s.id + ': pressure in "' + r.sub + '"');
      });
      const head = await page.evaluate(sel => { const hd = document.querySelector(sel + ' .mod-head'); return { badge: hd.querySelector('.badge').textContent, fresh: hd.querySelector('.badge').dataset.fresh, asof: hd.querySelector('.mod-asof').textContent, note: hd.querySelector('.mod-note').textContent, tip: hd.querySelector('.badge').getAttribute('data-tip') || '' }; }, WX);
      check(head.fresh === 'ADVISORY' && /NOAA-NHC/i.test(head.badge) && /ago/.test(head.asof), 'header badge ADVISORY · NOAA-NHC with an age: ' + JSON.stringify(head));
      check(d.storms.length ? head.note === d.storms.length + (d.storms.length === 1 ? ' active system' : ' active systems') : head.note === 'no active system', 'header note counts the systems: ' + head.note);
      await ctx.assertHeaderBadge('WX');
      const strip = await page.evaluate(sel => { const s = document.querySelector(sel + ' .wx-strip .ot-state'); return s ? s.innerText.replace(/\s+/g, ' ') : ''; }, WX);
      const checkedLine = await page.evaluate(sel => document.querySelector(sel + ' .wx-checked').textContent, WX);
      check(/^checked Atlantic · Eastern North Pacific · Central North Pacific/.test(checkedLine) && /at \d\d:\d\d:\d\dZ/.test(checkedLine), 'the panel says which basins were checked and when: ' + checkedLine);
      if (!d.storms.length) check(/NO ACTIVE TROPICAL CYCLONES/.test(strip) && /checked Atlantic/.test(strip), 'no system: the strip says so and which basins were checked: ' + strip);
      else check(!/NO ACTIVE/.test(strip), 'systems are active: no "all clear" strip (' + strip + ')');
      /* the symbol is where the advisory put it, and stays there */
      const v = await view(ctx), h1 = await hits(ctx);
      for (const s of d.storms) {
        const hit = h1.find(q => q.type === 'storm' && q.id === s.id), want = project(v, s.lon, s.lat);
        if (!check(!!hit, s.id + ': its symbol is on the map after the first fit')) continue;
        check(Math.abs(hit.x - want.x) < 1 && Math.abs(hit.y - want.y) < 1, s.id + ': symbol at ' + hit.x.toFixed(1) + ',' + hit.y.toFixed(1) + ', advisory position projects to ' + want.x.toFixed(1) + ',' + want.y.toFixed(1));
      }
      await sleep(4000);
      const h2 = await hits(ctx), v2 = await view(ctx);
      check(JSON.stringify(v) === JSON.stringify(v2), 'the view does not drift by itself');
      check(JSON.stringify(h1.filter(q => q.type !== 'obs')) === JSON.stringify(h2.filter(q => q.type !== 'obs')), 'four seconds later every storm symbol, track point and hub is at the same pixel');
      const again = (await getJson(ctx.base + '/api/storms')).body;
      check(JSON.stringify(again.storms.map(s => [s.id, s.lat, s.lon, s.advisory_num])) === JSON.stringify(d.storms.map(s => [s.id, s.lat, s.lon, s.advisory_num])), 'a second fetch of /api/storms gives the same advisory positions');
      console.log('      ' + (d.storms.length ? d.storms.map(s => s.title + ' ' + ll(s.lat, s.lon) + ' ' + s.wind_kt + ' kt, advisory #' + s.advisory_num + ' ' + dayHm(s.advisory_at)).join(' | ') : 'no active system'));
    });

    await section('real: advisory panel of the selected system', async () => {
      await enter();
      const d = stormsGot[stormsGot.length - 1];
      if (!d || !d.storms.length) { console.log('      (no active system: covered by the fixture section)'); return; }
      const s = d.storms[0];
      await page.locator(WX + ' .wx-sys[data-id="' + s.id + '"]').click();
      await settle(ctx);
      const kv = await kvText(ctx);
      check(kv.Position === ll(s.lat, s.lon), 'Position ' + kv.Position + ', received ' + s.lat + ' ' + s.lon);
      check(kv['Max wind'].indexOf(kt(s.wind_kt)) === 0 && (!isNum(s.gust_kt) || kv['Max wind'].indexOf('gusts ' + kt(s.gust_kt)) > 0), 'Max wind "' + kv['Max wind'] + '", received ' + s.wind_kt + ' / gust ' + s.gust_kt);
      check(kv['Min pressure'] === (isNum(s.pressure_mb) ? num(s.pressure_mb, 0) + ' mb' : 'not given'), 'Min pressure ' + kv['Min pressure']);
      check(kv.Advisory.indexOf((s.advisory_num ? '#' + s.advisory_num + ' · ' : '') + 'issued ' + dayHm(s.advisory_at)) === 0 && / ago$/.test(kv.Advisory), 'Advisory "' + kv.Advisory + '"');
      const n = s.next_advisory;
      if (n && n.final) check(/last advisory/.test(kv['Next advisory']), 'a final advisory says there is no next one: ' + kv['Next advisory']);
      else if (n && isNum(n.at)) check(kv['Next advisory'].indexOf(dayHm(n.at)) === 0 && /in |overdue/.test(kv['Next advisory']), 'Next advisory "' + kv['Next advisory'] + '" names ' + dayHm(n.at));
      const near = await page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-near tbody tr')).map(tr => Array.from(tr.children).map(td => td.textContent)), WX);
      check(near.length === s.nearest_hubs.length && near.every((r, i) => r[0] === s.nearest_hubs[i].name && r[1] === num(s.nearest_hubs[i].km, 0) && r[2] === num(s.nearest_hubs[i].nm, 0)), 'nearest hubs table equals the received DERIVED distances: ' + JSON.stringify(near[0]));
      check(await page.locator(WX + ' .wx-near-head .badge').evaluate(b => b.dataset.fresh === 'DERIVED' && /great-circle/.test(b.getAttribute('data-tip') || '')), 'the distances are labelled DERIVED with how they are computed');
    });

    await section('real: marine observations — table equals what was received, region, sort, row click, map marker', async () => {
      await enter();
      const d = obsGot[obsGot.length - 1];
      if (!check(!!d && d.source === 'ndbc' && d.stations.length > 100, 'the page received /api/wx/obs with ' + (d ? d.stations.length : 0) + ' stations')) return;
      await side(ctx, 'obs');
      await page.locator(WX + ' .wx-regions .ot-tab[data-id="gulf"]').click();
      await settle(ctx);
      const want = d.stations.filter(o => o.region === 'gulf');
      const rows = await obsRows(ctx);
      check(rows.length === want.length && rows.length > 20, 'Gulf: ' + rows.length + ' rows shown, ' + want.length + ' received for the region');
      let bad = 0;
      for (const o of want) {
        const r = rows.find(x => x.id === o.id);
        const exp = [null, compass(o.wind_dir), num(o.wind_kt, 1), num(o.gust_kt, 1), num(o.wave_m, 1), num(o.pressure_mb, 1), num(o.water_c, 1), hm(o.t)];
        if (!r || r.cells[0].indexOf(o.id) !== 0 || exp.some((e, i) => e !== null && r.cells[i] !== e)) { bad++; if (bad <= 3) ctx.fail('station ' + o.id + ': shows ' + JSON.stringify(r && r.cells) + ', received ' + JSON.stringify(exp)); }
      }
      check(bad === 0, bad + ' of ' + want.length + ' Gulf rows differ from what was received');
      const meta = await page.evaluate(sel => { const m = document.querySelector(sel + ' .wx-obs-meta'); return { badge: m.querySelector('.badge').textContent, age: m.querySelector('.wx-obs-age').textContent, n: document.querySelector(sel + ' .wx-regions-n').textContent }; }, WX);
      check(/MEASURED/.test(meta.badge) && /NDBC/i.test(meta.badge) && /ago/.test(meta.age), 'the table says MEASURED · NDBC and when the file was read: ' + JSON.stringify(meta));
      check(meta.n.indexOf(String(d.stations.length)) === 0, 'station count ' + meta.n + ' = ' + d.stations.length);
      const again = (await getJson(ctx.base + '/api/wx/obs?region=gulf')).body;
      check(again.count === again.stations.length && again.stations.every(o => o.region === 'gulf'), 'the route filters by region itself (gulf: ' + again.count + ')');
      const badRegion = await getJson(ctx.base + '/api/wx/obs?region=' + encodeURIComponent('x/../y'));
      check(badRegion.status === 400 && badRegion.body.error.code === 'bad_request', 'an unknown region is a 400, not a pass-through: ' + badRegion.status);
      /* sort by wind, descending */
      const th = WX + ' .wx-regions ~ .wx-tblbox th[data-col="wind_kt"]';
      await page.locator(th).click(); await settle(ctx);
      let sorted = (await obsRows(ctx)).map(r => parseFloat(r.cells[2])).filter(isFinite);
      const dir = await page.locator(th).getAttribute('aria-sort');
      if (dir === 'ascending') { await page.locator(th).click(); await settle(ctx); sorted = (await obsRows(ctx)).map(r => parseFloat(r.cells[2])).filter(isFinite); }
      check(sorted.length > 10 && sorted.every((x, i) => !i || x <= sorted[i - 1]), 'a click on the Wind header sorts the rows (strongest first): ' + sorted.slice(0, 5).join(', '));
      /* row click: the map flies to the station, the marker is selected, the tooltip on the marker reads the same numbers */
      const first = (await obsRows(ctx))[0], o = d.stations.find(x => x.id === first.id);
      await page.locator(WX + ' .wx-regions ~ .wx-tblbox tbody tr[data-key="' + first.id + '"]').click();
      await settle(ctx);
      const v = await view(ctx), pt = project(v, o.lon, o.lat), box = await cvBox(ctx);
      check(Math.abs(pt.x - v.w / 2) < 2 && Math.abs(pt.y - v.h / 2) < 2, 'a click on row ' + o.id + ' centres the map on the station (' + pt.x.toFixed(0) + ',' + pt.y.toFixed(0) + ' of ' + v.w + 'x' + v.h + ')');
      check((await selected(ctx)).station === o.id && (await obsRows(ctx)).find(r => r.id === o.id).sel, 'the station is selected in the table and on the map');
      await ctx.mouse.hover({ x: box.x + pt.x, y: box.y + pt.y }); await sleep(250);
      const tip = await tipText(ctx);
      check(tip.indexOf(o.id) === 0 && /MEASURED · NOAA NDBC/.test(tip) && tip.indexOf('observed ' + dayHm(o.t)) > 0, 'hovering the marker reads out the station, the observation time and MEASURED: ' + tip.replace(/\n/g, ' | ').slice(0, 200));
      if (isNum(o.wind_kt)) check(tip.indexOf(num(o.wind_kt, 1) + ' kt (' + num(o.wind_ms, 1) + ' m/s)') > 0, 'the read-out has the wind as received (' + o.wind_kt + ' kt)');
      if (isNum(o.pressure_mb)) check(tip.indexOf(num(o.pressure_mb, 1) + ' hPa') > 0, 'the read-out has the pressure as received');
      /* another region: rows change, the map goes there */
      await page.locator(WX + ' .wx-regions .ot-tab[data-id="east"]').click(); await settle(ctx);
      const east = await obsRows(ctx), ve = await view(ctx);
      check(east.length === d.stations.filter(x => x.region === 'east').length && east.length > 0, 'East Coast: ' + east.length + ' rows');
      check(ve.lon > -81.5 && ve.lon < -60 && ve.lat > 24 && ve.lat < 46, 'the map moved to the East Coast (' + ve.lon.toFixed(1) + ', ' + ve.lat.toFixed(1) + ')');
      await page.locator(WX + ' .wx-regions .ot-tab[data-id="gulf"]').click(); await settle(ctx);
      console.log('      ' + d.stations.length + ' stations, Gulf ' + want.length + ', strongest wind in the Gulf ' + sorted[0] + ' kt at ' + first.id);
    });

    await section('real: energy reference points — nearest observation, MODEL or the reason, hover', async () => {
      await enter();
      const d = hubsGot[hubsGot.length - 1], o = obsGot[obsGot.length - 1], m = modelGot[modelGot.length - 1];
      if (!check(!!d && d.freshness === 'REFERENCE' && d.hubs.length === 20, 'the page received /api/wx/hubs: 20 reference points labelled REFERENCE')) return;
      await ctx.waitFor(() => window.__wxModelSeen || (window.__wxModelSeen = performance.getEntriesByType('resource').some(e => /\/api\/wx\/model/.test(e.name) && e.responseEnd > 0)), { timeout: 40000, msg: '/api/wx/model answered' });
      await side(ctx, 'hubs'); await settle(ctx);
      const rows = await hubRows(ctx);
      check(rows.length === 20, 'the hubs table has ' + rows.length + ' rows');
      const model = modelGot[modelGot.length - 1] || m;
      for (const hb of d.hubs) {
        const r = rows.find(x => x.id === hb.id), link = o.hub_obs[hb.id];
        if (!check(!!r && r.cells[0].indexOf(hb.name) >= 0, 'row for ' + hb.id)) continue;
        if (link) {
          const st = o.stations.find(x => x.id === link.station);
          check(r.cells[4] === link.station + ' ' + link.km + ' km', hb.id + ': From shows "' + r.cells[4] + '", received station ' + link.station + ' at ' + link.km + ' km');
          check(r.cells[1] === (isNum(st.wind_kt) ? num(st.wind_kt, 0) + ' ' + compass(st.wind_dir) : '—') && r.cells[2] === num(st.pressure_mb, 0), hb.id + ': wind/pressure "' + r.cells[1] + '" / ' + r.cells[2] + ' are those of ' + link.station + ' (' + st.wind_kt + ' kt, ' + st.pressure_mb + ')');
        } else {
          const p = model && (model.points || []).find(q => q.id === hb.id), has = p && (isNum(p.wind_kt) || isNum(p.wave_m));
          check(r.cells[4] === (has ? 'MODEL' : 'none'), hb.id + ': no station nearby → "' + r.cells[4] + '" (model value ' + (has ? 'present' : 'absent') + ')');
          if (has) check(r.cells[1] === (isNum(p.wind_kt) ? num(p.wind_kt, 0) + ' ' + compass(p.wind_dir) : '—'), hb.id + ': MODEL wind "' + r.cells[1] + '" = received ' + p.wind_kt);
          else check(r.cells[1] === '—' && r.cells[2] === '—' && r.cells[3] === '—', hb.id + ': nothing measured and no model value → dashes, not numbers: ' + r.cells.slice(1, 4).join(' '));
        }
      }
      const note = await page.evaluate(sel => document.querySelector(sel + ' .wx-hub-note').textContent, WX);
      check(/Reference points \(static/.test(note) && /MODEL only where none exists/.test(note), 'the table says what it is: ' + note.slice(0, 160));
      if (model && (model.parts || []).every(p => !p.ok)) { check(/Open-Meteo MODEL values unavailable/.test(note), 'Open-Meteo is down: the note says so instead of showing numbers: ' + note.slice(-140)); console.log('      Open-Meteo unavailable right now (' + ((model.parts[0].error || {}).message || '') + '): chokepoints show "none"'); }
      /* row click → fly, marker selected; hover → read-out with the reference label and the coordinates */
      await page.locator(WX + ' .wx-hub-note ~ .wx-tblbox tbody tr[data-key="loop"]').click(); await settle(ctx);
      const hb = d.hubs.find(x => x.id === 'loop'), v = await view(ctx), pt = project(v, hb.lon, hb.lat), box = await cvBox(ctx);
      check(Math.abs(pt.x - v.w / 2) < 2 && Math.abs(pt.y - v.h / 2) < 2 && (await selected(ctx)).hub === 'loop', 'a click on LOOP centres the map on it and selects it');
      await ctx.mouse.hover({ x: box.x + pt.x, y: box.y + pt.y }); await sleep(250);
      const tip = await tipText(ctx);
      check(/LOOP/.test(tip) && /reference point/.test(tip) && tip.indexOf(ll(hb.lat, hb.lon, 2)) > 0, 'hovering the hub: ' + tip.replace(/\n/g, ' | ').slice(0, 200));
      await ctx.shot('geo_wx_hubs');
    });

    await section('real: cloud imagery — whitelisted tiles through the gateway, readable canvas, toggle and opacity', async () => {
      await enter();
      const d = layersGot[layersGot.length - 1];
      if (!check(!!d && d.source === 'nasa-gibs' && d.products.length >= 4, 'the page received /api/satellite/layers')) return;
      await page.locator(WX + ' .wx-ctl [data-view="gulf"]').click();
      const sel = WX + ' .mod-controls select';
      await page.locator(sel).selectOption('geocolor');
      await ctx.waitFor(s => { const t = document.querySelector(s).__wx.tiles(); return t && t.need > 0 && t.wait === 0; }, { arg: CV, timeout: 60000, msg: 'the GeoColor tiles of the Gulf view arrived' });
      await settle(ctx);
      const st = await page.evaluate(s => document.querySelector(s).__wx.tiles(), CV);
      check(st.ok > 0, 'imagery tiles drawn: ' + JSON.stringify(st));
      const named = (layer, time) => layersGot.some(L => L.layers[layer] && (L.layers[layer].time === time || (L.layers[layer].older || []).indexOf(time) >= 0));
      const badTile = reqs.tiles.filter(u => { const q = new URL(u).searchParams; return !named(q.get('layer'), q.get('time')); });
      check(reqs.tiles.length > 0 && !badTile.length, reqs.tiles.length + ' tile requests, all for a listed layer at a time the gateway named (the frame, or one of the three before it)' + (badTile.length ? ' — NOT: ' + badTile[0] : ''));
      check(!reqs.foreign.length, 'nothing is loaded from another origin' + (reqs.foreign.length ? ': ' + reqs.foreign[0] : ''));
      const on = await fingerprint(ctx);                  // would throw if the canvas were tainted
      const note = await page.evaluate(s => document.querySelector(s + ' .wx-img').innerText, WX);
      const gw = d.layers.geo_west, ge = d.layers.geo_east;
      check(/GeoColor clouds/.test(note) && /NASA GIBS/.test(note) && (!ge.ok || note.indexOf('GOES-East ' + ge.time.slice(11, 16) + 'Z') > 0) && (!gw.ok || note.indexOf('GOES-West ' + gw.time.slice(11, 16) + 'Z') > 0) && / old/.test(note), 'the imagery line names the product, each satellite frame time, the source and its age: ' + note);
      /* opacity: a real drag of the slider thumb changes the picture */
      const rng = await page.locator(WX + ' .wx-range').boundingBox();
      await page.mouse.click(rng.x + rng.width * 0.97, rng.y + rng.height / 2); await settle(ctx);
      const opq = await fingerprint(ctx), opv = await page.evaluate(s => document.querySelector(s + ' .wx-op-val').textContent, WX);
      check(/^(9\d|100)%$/.test(opv) && opq.hash !== on.hash, 'opacity slider → ' + opv + ', the picture changed');
      /* off: the same view without imagery is a different, darker picture; the coast is still there */
      await page.locator(sel).selectOption('off'); await settle(ctx);
      const off = await fingerprint(ctx), noteOff = await page.evaluate(s => document.querySelector(s + ' .wx-img').innerText, WX);
      check(off.hash !== opq.hash && off.bright < opq.bright, 'imagery off: fewer bright pixels (' + off.bright + ' < ' + opq.bright + ')');
      check(/^Imagery off/.test(noteOff), 'the line says "Imagery off": ' + noteOff);
      /* another product asks for other layers only */
      const n0 = reqs.tiles.length;
      await page.locator(sel).selectOption('ir');
      await ctx.waitFor(s => { const t = document.querySelector(s).__wx.tiles(); return t && t.need > 0 && t.wait === 0; }, { arg: CV, timeout: 60000, msg: 'the infrared tiles arrived' });
      const irReq = reqs.tiles.slice(n0).map(u => new URL(u).searchParams.get('layer'));
      check(irReq.length > 0 && irReq.every(l => /^ir_/.test(l)), 'Infrared: ' + irReq.length + ' new tile requests, all ir_* layers');
      await ctx.shot('geo_wx_infrared');
      await page.locator(sel).selectOption('geocolor');
      await page.mouse.click(rng.x + rng.width * 0.5, rng.y + rng.height / 2);
      await ctx.waitFor(s => { const t = document.querySelector(s).__wx.tiles(); return t && t.need > 0 && t.wait === 0; }, { arg: CV, timeout: 60000, msg: 'GeoColor again' });
      /* the tile route refuses what is not on the list */
      for (const q of ['layer=evil&time=2026-11-11&z=0&x=0&y=0', 'layer=viirs&time=default&z=0&x=0&y=0', 'layer=viirs&time=' + d.layers.viirs.time + '&z=3&x=99&y=0', 'layer=geo_east&time=' + encodeURIComponent('2026-11-12T00:00:00Z/../../x') + '&z=0&x=0&y=0']) {
        const r = await getJson(ctx.base + '/api/satellite/tile?' + q);
        check(r.status === 400 || r.status === 422, 'tile route refuses ' + q.slice(0, 50) + ' → ' + r.status);
      }
      console.log('      GeoColor ' + (ge.time || '') + ' / ' + (gw.time || '') + ' · ' + reqs.tiles.length + ' tile requests · canvas readable');
    });

    await section('real: the map moves with mouse and keyboard, and keeps its state', async () => {
      await enter();
      await page.locator(WX + ' .wx-ctl [data-view="gulf"]').click(); await settle(ctx);
      const box = await cvBox(ctx), v0 = await view(ctx);
      const at = { x: Math.round(box.x + box.width * 0.3), y: Math.round(box.y + box.height * 0.4) };
      const under = v => ({ lon: v.lon + (at.x - box.x - v.w / 2) / v.scale, my: mercY(v.lat) + (at.y - box.y - v.h / 2) / v.scale });
      const u0 = under(v0);
      await ctx.mouse.wheel(at, -240, 4); await settle(ctx);
      const v1 = await view(ctx), u1 = under(v1);
      check(v1.zoom > v0.zoom * 1.5, 'wheel up zooms in (' + v0.zoom.toFixed(2) + ' → ' + v1.zoom.toFixed(2) + ')');
      check(Math.abs(u1.lon - u0.lon) * v0.scale < 1 && Math.abs(u1.my - u0.my) * v0.scale < 1, 'the point under the cursor stays under the cursor while zooming (moved ' + ((u1.lon - u0.lon) * v0.scale).toFixed(2) + ' / ' + ((u1.my - u0.my) * v0.scale).toFixed(2) + ' px)');
      const cur = await page.evaluate(s => { const c = document.querySelector(s + ' .wx-cursor'); return c.hidden ? '' : c.textContent; }, WX);
      check(/^\d+\.\d+°[NS] \d+\.\d+°[EW]$/.test(cur), 'the cursor position is read out: ' + cur);
      await ctx.mouse.drag(at, 160, 90); await settle(ctx);
      const v2 = await view(ctx);
      check(Math.abs((v1.lon - v2.lon) * v2.scale - 160) < 2 && v2.lat > v1.lat, 'drag pans by the dragged distance (' + ((v1.lon - v2.lon) * v2.scale).toFixed(1) + ' px)');
      await page.mouse.dblclick(at.x, at.y); await settle(ctx);
      const v3 = await view(ctx);
      check(Math.abs(v3.zoom / v2.zoom - 2) < 0.01, 'double-click zooms in ×2');
      await page.locator(CV).focus();
      await page.keyboard.press('ArrowRight'); await page.keyboard.press('-'); await settle(ctx);
      const v4 = await view(ctx);
      check(v4.lon > v3.lon && v4.zoom < v3.zoom, 'keyboard: arrow pans, minus zooms out');
      await page.locator(WX + ' .wx-ctl [data-view="in"]').click(); await settle(ctx);
      check((await view(ctx)).zoom > v4.zoom, 'the + button zooms in');
      /* switching the instrument or the module changes nothing here; nothing is asked again */
      const keep = await view(ctx), before = Object.assign({}, reqs, { tiles: reqs.tiles.length });
      for (const id of ['BTC', 'AAPL', 'EURUSD', 'CL']) await ctx.select(id);
      await ctx.go('MON'); await ctx.go('WX'); await ctx.go('HELP'); await ctx.go('WX'); await settle(ctx);
      const back = await view(ctx);
      check(JSON.stringify(keep) === JSON.stringify(back), 'fast instrument switching and leaving / returning keep the zoom and centre');
      check(reqs.storms === before.storms && reqs.obs === before.obs && reqs.layers === before.layers, 'switching away and back asked no route again (storms ' + reqs.storms + ', obs ' + reqs.obs + ', layers ' + reqs.layers + ')');
      await page.locator(WX + ' .wx-ctl [data-view="world"]').click(); await settle(ctx);
      check(Math.abs((await view(ctx)).zoom - 1) < 0.01, 'World shows the whole map');
      await ctx.shot('geo_wx_world');
      await page.locator(WX + ' .wx-ctl [data-view="storms"]').click(); await settle(ctx);
      /* toggles */
      const nObs = (await hits(ctx)).filter(q => q.type === 'obs').length;
      await page.locator(WX + ' .mod-controls [data-toggle="obs"]').click(); await settle(ctx);
      check((await hits(ctx)).filter(q => q.type === 'obs').length === 0, 'Buoys off: no observation markers (' + nObs + ' before)');
      await page.locator(WX + ' .mod-controls [data-toggle="obs"]').click(); await settle(ctx);
      await ctx.assertNoBadText(WX); await ctx.assertMinFontPx(12, WX); await ctx.assertContrast(4.5, WX); await ctx.assertCanvasText(12);
      await ctx.assertNoOverflow('WX');
      await side(ctx, 'obs');
      await ctx.shot('geo_wx_real');
    });

    await section('real: no route is asked more often than its cache time', async () => {
      const mins = (Date.now() - t0) / 60000, cap = n => 1 + Math.floor((Date.now() - t0) / 1000 / n);
      check(reqs.storms <= cap(300) && reqs.obs <= cap(300) && reqs.layers <= cap(300) && reqs.model <= cap(900) && reqs.hubs <= 1,
        'after ' + mins.toFixed(1) + ' min: storms ' + reqs.storms + ', obs ' + reqs.obs + ', layers ' + reqs.layers + ', model ' + reqs.model + ', hubs ' + reqs.hubs + ' requests');
      const uniq = new Set(reqs.tiles);
      check(reqs.tiles.length - uniq.size <= reqs.tiles.length * 0.02, 'tiles are not asked twice (' + reqs.tiles.length + ' requests, ' + uniq.size + ' distinct)');
    });

    /* ════════════════════════════ a full advisory, served in transit ════════════════════════════ */
    await section('fixture advisory 22: forecast track, cone and wind field across the date line; JTWC system', async () => {
      const p = await ctx.open({ tag: 'adv22' });
      p.allow(/api\/satellite\/tile/);
      await p.page.route('**/api/storms', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.assign({}, fixture, { as_of: Date.now() / 1000 - 20 })) }));
      await p.page.addInitScript(() => { try { localStorage.setItem('ot.wx.v1', JSON.stringify({ product: 'off' })); } catch (e) { /* ignore */ } });
      await p.goto('WX/CL'); await wxReady(p); await settle(p);
      const pena = fixture.storms[0], cem = fixture.storms[1];
      const rows = await sysRows(p);
      check(rows.length === 2 && rows[0].name === 'Pena' && rows[0].wind === '40 kt' && rows[0].sym === 'S' && rows[1].name === 'Cempaka' && rows[1].sym === 'H', 'two systems listed with their class symbols: ' + JSON.stringify(rows.map(r => [r.name, r.wind, r.sym])));
      check(/NHC$/.test(rows[0].adv) && /JTWC\/Esri$/.test(rows[1].adv), 'each row names its source: ' + rows[0].adv + ' | ' + rows[1].adv);
      /* first fit: both systems, their tracks and the whole cone are in view although they straddle 180° */
      const v = await view(p), hh = await hits(p), box = await cvBox(p);
      const inView = q => q.x > 0 && q.x < v.w && q.y > 0 && q.y < v.h;
      const storm = id => hh.find(q => q.type === 'storm' && q.id === id);
      check(storm(pena.id) && storm(cem.id) && inView(storm(pena.id)) && inView(storm(cem.id)), 'the first view fits both systems (Pena 170.6°W, Cempaka 154.1°E)');
      const fc = hh.filter(q => q.type === 'fc' && q.id === pena.id).sort((a, b) => a.i - b.i);
      check(fc.length === 4 && fc.every(inView), 'the four forecast points of Pena are drawn and in view');
      const xs = [storm(pena.id).x].concat(fc.map(q => q.x));
      check(xs.every((x, i) => !i || (x < xs[i - 1] && xs[i - 1] - x < 3.5 * v.scale)), 'the forecast track runs west in even steps across 180° (x: ' + xs.map(x => x.toFixed(0)).join(' → ') + '), no jump to the other side of the map');
      fc.forEach((q, i) => { const w = project(v, pena.forecast[i][0], pena.forecast[i][1]); check(Math.abs(q.x - w.x) < 1 && Math.abs(q.y - w.y) < 1, 'forecast point ' + (i + 1) + ' (' + pena.forecast[i][0] + ') is drawn where it projects'); });
      /* hover the system: advisory read-out */
      const sp = storm(pena.id);
      await p.mouse.hover({ x: box.x + sp.x, y: box.y + sp.y }); await sleep(300);
      let tip = await tipText(p);
      check(tip.indexOf('Tropical Storm Pena') === 0 && tip.indexOf(ll(pena.lat, pena.lon)) > 0 && /40 kt · gusts 50 kt/.test(tip) && /1,002 mb/.test(tip) && /W \(270°\) at 13 kt/.test(tip) && tip.indexOf('#22 · ' + dayHm(pena.advisory_at)) > 0 && tip.indexOf(dayHm(pena.next_advisory.at)) > 0,
        'hover on the symbol reads the advisory: ' + tip.replace(/\n/g, ' | ').slice(0, 330));
      await p.mouse.hover({ x: box.x + fc[1].x, y: box.y + fc[1].y }); await sleep(300);
      tip = await tipText(p);
      check(/forecast \+24 h/.test(tip) && tip.indexOf('valid ' + dayHm(pena.forecast[1][2])) > 0 && /30 kt/.test(tip) && tip.indexOf(ll(pena.forecast[1][1], pena.forecast[1][0])) > 0, 'hover on a forecast point: ' + tip.replace(/\n/g, ' | ').slice(0, 240));
      /* click the symbol: the advisory panel */
      await p.mouse.click({ x: box.x + sp.x, y: box.y + sp.y }); await settle(p);
      const kv = await kvText(p);
      check(kv.Position === '9.8°N 170.6°W' && /^40 kt \(46 mph\) · gusts 50 kt$/.test(kv['Max wind']) && kv['Min pressure'] === '1,002 mb' && kv.Motion === 'W (270°) at 13 kt', 'advisory figures: ' + JSON.stringify(kv));
      check(kv.Advisory.indexOf('#22 · issued 12 Nov 03:00Z') === 0 && kv['Next advisory'].indexOf('12 Nov 09:00Z') === 0 && /overdue by|in /.test(kv['Next advisory']) && /Next complete advisory at 1100 PM HST/.test(kv['Next advisory']), 'issue time and the next advisory from the advisory text: ' + kv.Advisory + ' / ' + kv['Next advisory']);
      const fcT = await p.page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-fc tbody tr')).map(tr => Array.from(tr.children).map(td => td.textContent)), WX);
      check(fcT.length === 4 && fcT.every((r, i) => r[0].indexOf(wkHm(pena.forecast[i][2])) === 0 && r[1] === '+' + pena.forecast_detail[i].tau_h && r[2] === ll(pena.forecast[i][1], pena.forecast[i][0]) && r[3] === kt(pena.forecast[i][3])),
        'forecast table = the advisory\'s points (time, +h, position, wind): ' + JSON.stringify(fcT[3]));
      check(fcT[3][2] === '10.3°N 179.1°E', 'the last forecast point is on the far side of the date line: ' + fcT[3][2]);
      const near = await p.page.evaluate(sel => Array.from(document.querySelectorAll(sel + ' .wx-near tbody tr')).map(tr => Array.from(tr.children).map(td => td.textContent)), WX);
      check(near.length === 4 && near[0][0] === pena.nearest_hubs[0].name && near[0][1] === num(pena.nearest_hubs[0].km, 0), 'DERIVED distance table: ' + JSON.stringify(near[0]));
      const head = await p.page.evaluate(sel => document.querySelector(sel + ' .wx-d-head').textContent, WX);
      check(/PENA CONTINUES WESTWARD/.test(head), 'the advisory headline is shown: ' + head);
      await p.shotNow('geo_wx_adv22_1920x1080');
      /* a click in the list flies to the system: cone, wind field and time labels, read back from the canvas */
      await p.page.locator(WX + ' .wx-sys[data-id="' + pena.id + '"]').click(); await settle(p);
      const vz = await view(p), F = pena.forecast.map(q => project(vz, q[0], q[1])), C = project(vz, pena.lon, pena.lat);
      check(vz.zoom > v.zoom && F.every(q => q.x > 0 && q.x < vz.w) && C.x > 0 && C.x < vz.w, 'a click on Pena in the list flies to its track (zoom ' + v.zoom.toFixed(1) + ' → ' + vz.zoom.toFixed(1) + '), all of it in view');
      const seaLum = lum(await pixel(p, 14, vz.h - 60, 3)), up = Math.max(6, 0.35 * vz.scale);
      const eastOfLine = { x: F[2].x + (F[3].x - F[2].x) * 0.3, y: F[2].y - up }, westOfLine = { x: F[2].x + (F[3].x - F[2].x) * 0.8, y: F[3].y - up };
      const lonAt = q => vz.lon + (q.x - vz.w / 2) / vz.scale;
      check(lonAt(eastOfLine) > -180 && lonAt(westOfLine) < -180, 'the two cone samples lie either side of 180° (' + lonAt(eastOfLine).toFixed(2) + ', ' + lonAt(westOfLine).toFixed(2) + ')');
      const cl1 = lum(await pixel(p, eastOfLine.x, eastOfLine.y, 1.5)), cl2 = lum(await pixel(p, westOfLine.x, westOfLine.y, 1.5));
      check(cl1 > seaLum + 12 && cl2 > seaLum + 12, 'inside the cone, on both sides of 180°, the canvas is lighter than open sea (' + cl1.toFixed(1) + ' / ' + cl2.toFixed(1) + ' vs ' + seaLum.toFixed(1) + ')');
      const away = [];
      for (const [fx, fy] of [[0.5, 0.12], [0.23, 0.2], [0.77, 0.2], [0.3, 0.83], [0.72, 0.86], [0.5, 0.9]]) away.push(lum(await pixel(p, vz.w * fx + 3, vz.h * fy + 3, 1.5)));
      check(away.filter(x => Math.abs(x - seaLum) < 6).length >= 5, 'away from the cone (six points above and below it) the sea is untouched: the polygon is not smeared across the map (' + away.map(x => x.toFixed(1)).join(', ') + ' vs ' + seaLum.toFixed(1) + ')');
      const ringTop = pena.wind_radii[0].ring.reduce((a, q) => (q[1] > a[1] ? q : a)), wfPt = project(vz, ringTop[0], (pena.lat + 2 * ringTop[1]) / 3), wf = await pixel(p, wfPt.x, wfPt.y, 1.5);
      check(wf[0] > wf[2] + 12, 'the 34 kt wind field is tinted amber around the centre (rgb ' + wf.map(x => x.toFixed(0)).join(',') + ')');
      const sym = await pixel(p, C.x - 7, C.y, 1), fsym = await pixel(p, F[1].x - 5, F[1].y, 1);
      check(sym[0] > 200 && sym[1] > 130 && sym[2] < 80, 'the storm symbol is amber (tropical storm): rgb ' + sym.map(x => x.toFixed(0)).join(','));
      check(fsym[2] > 200 && fsym[0] < 150, 'the +24 h forecast symbol is blue (depression): rgb ' + fsym.map(x => x.toFixed(0)).join(','));
      const texts = await p.page.evaluate(() => (window.__otCanvasDraws || []).map(dr => dr.text));
      const labels = pena.forecast.map(q => wkHm(q[2]));
      check(texts.some(t => t.indexOf('PENA · TS · 40 kt') === 0), 'the system is labelled on the map: PENA · TS · 40 kt');
      check(labels.every(l => texts.some(t => t.indexOf(l) === 0)), 'every forecast point carries its valid time on the map (' + labels.join(', ') + ')');
      await p.shotNow('geo_wx_adv22_pena_1920x1080');
      /* the other system: a click in the list flies to it; its source is said; nothing invented for what JTWC does not give */
      await p.page.locator(WX + ' .wx-sys[data-id="' + cem.id + '"]').click(); await settle(p);
      const v2 = await view(p), c2 = project(v2, cem.lon, cem.lat);
      check(c2.x > 0 && c2.x < v2.w && c2.y > 0 && c2.y < v2.h && v2.zoom > v.zoom && Math.abs(v2.lon - vz.lon) > 5, 'a click on Cempaka flies to it');
      const kv2 = await kvText(p), badge = await p.page.evaluate(sel => document.querySelector(sel + ' .wx-d-headrow .badge').textContent, WX);
      check(/JTWC/.test(badge) && kv2['Next advisory'] === 'not published by this source' && kv2.Position === ll(cem.lat, cem.lon), 'Cempaka: source ' + badge + ', next advisory "' + kv2['Next advisory'] + '"');
      /* a click on a nearest hub opens the hub table and goes there */
      await p.page.locator(WX + ' .wx-near tbody tr').first().click(); await settle(p);
      const s3 = await selected(p), tab = await p.page.evaluate(sel => document.querySelector(sel + ' .wx-data-head .ot-tab[aria-selected="true"]').dataset.id, WX);
      check(s3.hub === cem.nearest_hubs[0].id && tab === 'hubs', 'click on the nearest hub → ' + s3.hub + ' selected, Energy hubs tab');
      const hrow = (await hubRows(p)).find(r => r.id === cem.nearest_hubs[0].id);
      check(hrow && hrow.cells[5] === 'Cempaka ' + num(cem.nearest_hubs[0].km, 0) + ' km', 'the hub table shows the DERIVED distance to the nearest track: ' + (hrow && hrow.cells[5]));
      /* the Storms button returns to the fit; the picture is identical to the first one → nothing moved meanwhile */
      await p.page.locator(WX + ' .wx-ctl [data-view="storms"]').click(); await settle(p);
      const again = (await hits(p)).filter(q => q.type === 'storm' || q.type === 'fc');
      check(JSON.stringify(again) === JSON.stringify(hh.filter(q => q.type === 'storm' || q.type === 'fc')), 'back on the fitted view every symbol is at the same pixel as before');
      await p.assertNoBadText(WX); await p.assertMinFontPx(12, WX); await p.assertContrast(4.5, WX); await p.assertCanvasText(12);
      await p.setViewport(1366, 768); await settle(p);
      await p.page.locator(WX + ' .wx-sys[data-id="' + pena.id + '"]').click(); await settle(p);
      await p.assertNoOverflow('WX adv22 at 1366x768');
      await p.shotNow('geo_wx_adv22_1366x768');
      await p.close();
    });

    await section('fixture advisory 22 with imagery: readable under the clouds', async () => {
      const p = await ctx.open({ tag: 'adv22-img' });
      p.allow(/api\/satellite\/tile/);
      await p.page.route('**/api/storms', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.assign({}, fixture, { as_of: Date.now() / 1000 - 20 })) }));
      await p.page.addInitScript(() => { try { localStorage.setItem('ot.wx.v1', JSON.stringify({ product: 'ir', opacity: 1 })); } catch (e) { /* ignore */ } });
      await p.goto('WX/CL'); await wxReady(p);
      await p.waitFor(s => { const t = document.querySelector(s).__wx.tiles(); return t && t.need > 0 && t.wait === 0; }, { arg: CV, timeout: 60000, msg: 'infrared tiles under the fixture advisory' });
      await settle(p);
      await p.assertCanvasText(12);
      await p.shotNow('geo_wx_adv22_infrared_1920x1080');
      await p.close();
    });

    /* ════════════════════════════ failure paths ════════════════════════════ */
    await section('no active system: a strip, and the map stays usable', async () => {
      const p = await ctx.open({ tag: 'empty' });
      p.allow(/api\/satellite\/tile/);
      await alter(p, '**/api/storms', body => Object.assign(body, { storms: [], other_basins: Object.assign({}, body.other_basins, { ids: [] }) }));
      await p.goto('WX/CL'); await wxReady(p); await settle(p);
      const r = await p.page.evaluate(sel => { const m = document.querySelector(sel), s = m.querySelector('.wx-strip .ot-state'); return { strip: s ? s.innerText.replace(/\s+/g, ' ') : '', cls: s ? s.className : '', cover: !!m.querySelector('.mod-body > .ot-state'), list: m.querySelectorAll('.wx-sys').length,
        count: m.querySelector('.wx-count').textContent, advTab: m.querySelector('.wx-data-head .ot-tab[data-id="adv"]').hidden, tab: m.querySelector('.wx-data-head .ot-tab[aria-selected="true"]').dataset.id, note: m.querySelector('.mod-note').textContent, rows: m.querySelectorAll('.wx-regions ~ .wx-tblbox tbody tr').length }; }, WX);
      check(/NO ACTIVE TROPICAL CYCLONES/.test(r.strip) && /checked Atlantic · Eastern North Pacific · Central North Pacific/.test(r.strip) && /at \d\d:\d\d:\d\dZ/.test(r.strip) && /strip/.test(r.cls) && !r.cover, 'the empty answer is a strip naming the basins and the time, not a cover: ' + r.strip);
      check(r.list === 0 && r.count === '0' && r.advTab && r.tab === 'obs' && r.rows > 0 && r.note === 'no active system', 'no list rows, count 0, no Advisory tab, observations shown: ' + JSON.stringify(r));
      const v0 = await view(p);
      check(v0.lon > -100 && v0.lon < -50 && v0.lat > 10 && v0.lat < 45, 'the map shows the Atlantic / Gulf home view (' + v0.lon.toFixed(0) + ', ' + v0.lat.toFixed(0) + ')');
      await p.mouse.wheel(CV, -240, 3); await settle(p);
      check((await view(p)).zoom > v0.zoom * 1.3 && (await hits(p)).some(q => q.type === 'hub'), 'the map zooms and shows the reference points while nothing is active');
      await p.shotNow('geo_wx_empty_1920x1080');
      /* GIBS lists a frame whose tiles it does not have yet (seen on the real service): the frame before fills in, and the line says so */
      const L = (await getJson(ctx.base + '/api/satellite/layers')).body.layers.geo_east;
      if (check(L.ok && L.older.length > 0, 'the gateway names the frames before the current one (' + (L.older || []).join(', ') + ')')) {
        await p.page.route('**/api/satellite/tile?**', route => (new URL(route.request().url()).searchParams.get('time') === L.time ? route.fulfill({ status: 204, body: '' }) : route.continue()));
        await p.page.locator(WX + ' .wx-ctl [data-view="gulf"]').click(); await p.mouse.wheel(CV, -240, 2);
        await p.waitFor(s => { const t = document.querySelector(s).__wx.tiles(); return t && t.need > 0 && t.wait === 0 && t.old > 0; }, { arg: CV, timeout: 60000, msg: 'tiles of the frame before are drawn where the current frame has none' });
        const st = await p.page.evaluate(s => document.querySelector(s).__wx.tiles(), CV), line = await p.page.evaluate(s => document.querySelector(s + ' .wx-img').innerText, WX);
        check(st.fail === 0 && st.old > 0 && /tiles from the frame before/.test(line), 'a frame GIBS has not finished: ' + st.old + '/' + st.need + ' tiles come from the frame before, none is left black: ' + line);
        check((await fingerprint(p)).bright > 500, 'the map still shows cloud imagery');
        await p.page.unroute('**/api/satellite/tile?**');
      }
      await p.setViewport(1366, 768); await settle(p); await p.assertNoOverflow('WX empty at 1366x768'); await p.shotNow('geo_wx_empty_1366x768');
      await p.close();
    });

    await section('stale answer, relay down, feed down then back', async () => {
      const p = await ctx.open({ tag: 'stale' });
      p.allow(/api\/satellite\/tile|502|api\/storms/);
      let mode = 'stale';
      await p.page.route('**/api/storms', route => {
        if (mode === 'down') return route.fulfill({ status: 502, contentType: 'application/json', headers: { 'Retry-After': '40' }, body: JSON.stringify({ error: { code: 'upstream_error', message: 'injected by the test: NHC HTTP 503', source: 'nhc', retry_after: 40 } }) });
        const body = Object.assign({}, fixture, { as_of: Date.now() / 1000 - (mode === 'stale' ? 2400 : 5) });
        if (mode === 'stale') Object.assign(body, { stale: true, error: { code: 'upstream_error', message: 'HTTP 503 Service Unavailable', source: 'nhc' },
          checked: fixture.checked.map(c => (c.basin === 'JTWC' ? Object.assign({}, c, { ok: false, error: { message: 'no answer within 12 s' } }) : c)) });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
      await p.goto('WX/CL'); await wxReady(p); await settle(p);
      const r = await p.page.evaluate(sel => { const m = document.querySelector(sel), b = m.querySelector('.mod-head .badge'), s = m.querySelector('.wx-strip .ot-state'); return { fresh: b.dataset.fresh, tip: b.getAttribute('data-tip'), asof: m.querySelector('.mod-asof').textContent, strip: s ? s.innerText.replace(/\s+/g, ' ') : '', list: m.querySelectorAll('.wx-sys').length, checked: m.querySelector('.wx-checked').textContent }; }, WX);
      check(r.fresh === 'STALE' && /HTTP 503/.test(r.tip) && /4\dm ago/.test(r.asof), 'a stale answer: header STALE with the reason and the age of the data: ' + r.fresh + ' · ' + r.asof);
      check(/STALE/.test(r.strip) && /HTTP 503/.test(r.strip) && r.list === 2, 'a strip says the feed is not answering; the last advisories stay listed: ' + r.strip);
      check(/NOT checked: other basins \(Esri relay: no answer within 12 s\)/.test(r.checked), 'a basin that could not be asked is named as not checked: ' + r.checked);
      await p.shotNow('geo_wx_stale_1920x1080');
      await p.close();
      /* nothing to show at all */
      const q = await ctx.open({ tag: 'down' });
      q.allow(/api\/satellite\/tile|502|api\/storms/);
      mode = 'down';
      await q.page.route('**/api/storms', route => (mode === 'down'
        ? route.fulfill({ status: 502, contentType: 'application/json', headers: { 'Retry-After': '40' }, body: JSON.stringify({ error: { code: 'upstream_error', message: 'injected by the test: NHC HTTP 503', source: 'nhc', retry_after: 40 } }) })
        : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.assign({}, fixture, { as_of: Date.now() / 1000 - 5 })) })));
      await q.goto('WX/CL');
      await q.waitFor(sel => document.querySelector(sel + ' .wx-strip .ot-state.k-error') && document.querySelectorAll(sel + ' .wx-regions ~ .wx-tblbox tbody tr').length > 0, { arg: WX, timeout: 45000, msg: 'feed down: an error strip, observations still load' });
      const e = await q.page.evaluate(sel => { const m = document.querySelector(sel), s = m.querySelector('.wx-strip .ot-state'); return { strip: s.innerText.replace(/\s+/g, ' '), fresh: m.querySelector('.mod-head .badge').dataset.fresh, cover: !!m.querySelector('.mod-body > .ot-state'), list: m.querySelectorAll('.wx-sys').length, checked: m.querySelector('.wx-checked').textContent }; }, WX);
      check(/STORM FEED UNAVAILABLE/.test(e.strip) && /NHC HTTP 503/.test(e.strip) && /Retrying in \d+s/.test(e.strip) && !e.cover && e.list === 0, 'feed down and nothing cached: an error strip with the reason and a countdown, no invented system, the rest of the page works: ' + e.strip);
      check(!/ADVISORY/.test(e.fresh || ''), 'the header badge does not claim advisory data it does not have: ' + e.fresh);
      await q.shotNow('geo_wx_feed_down_1920x1080');
      mode = 'ok';
      await q.page.locator(WX + ' .wx-strip .btn').click();
      await q.waitFor(sel => document.querySelectorAll(sel + ' .wx-sys').length === 2 && !document.querySelector(sel + ' .wx-strip .ot-state'), { arg: WX, timeout: 20000, msg: '"Retry now" brings the systems back once the feed answers' });
      await q.close();
    });

    await section('observations down, imagery list down, hostile text', async () => {
      const p = await ctx.open({ tag: 'obs-down' });
      p.allow(/api\/satellite|502|api\/wx\/obs/);
      await p.page.route('**/api/wx/obs**', route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'injected by the test: NDBC HTTP 500', source: 'ndbc' } }) }));
      await p.page.route('**/api/satellite/layers', route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ error: { code: 'upstream_error', message: 'injected by the test: GIBS HTTP 503', source: 'gibs' } }) }));
      const evil = JSON.parse(JSON.stringify(fixture));
      evil.storms[0].name = '<img src=x onerror="window.__pwned=1">'; evil.storms[0].title = 'Tropical Storm <b>bold</b>'; evil.storms[0].headline = ['<script>window.__pwned=2</script>'];
      await p.page.route('**/api/storms', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.assign(evil, { as_of: Date.now() / 1000 - 5 })) }));
      await p.goto('WX/CL');
      await p.waitFor(sel => document.querySelectorAll(sel + ' .wx-sys').length === 2 && /Imagery unavailable/.test(document.querySelector(sel + ' .wx-img').textContent), { arg: WX, timeout: 45000, msg: 'storms shown although observations and imagery fail' });
      await side(p, 'obs'); await settle(p);
      const r = await p.page.evaluate(sel => { const m = document.querySelector(sel), s = m.querySelector('.wx-tblbox .ot-state'); return { obs: s ? s.innerText.replace(/\s+/g, ' ') : '', img: m.querySelector('.wx-img').textContent, imgCls: m.querySelector('.wx-img').className, badge: m.querySelector('.wx-obs-meta .badge').dataset.fresh,
        name: m.querySelector('.wx-sys-name').textContent, imgs: m.querySelectorAll('.wx-side img, .wx-side script, .wx-side b b').length, pwned: window.__pwned || 0 }; }, WX);
      check(/Marine observations unavailable/.test(r.obs) && /NDBC HTTP 500/.test(r.obs) && !/MEASURED/.test(r.badge || ''), 'observations down: an error in that panel only, no MEASURED badge: ' + r.obs);
      check(/Imagery unavailable — injected by the test: GIBS HTTP 503/.test(r.img) && /bad/.test(r.imgCls), 'imagery list down: the line says so: ' + r.img);
      check(r.name === '<img src=x onerror="window.__pwned=1">' && r.imgs === 0 && r.pwned === 0, 'markup in a storm name is shown as text, nothing runs: ' + r.name);
      await side(p, 'hubs'); await settle(p);
      const hr = (await hubRows(p)).find(x => x.id === 'houston');
      check(hr && hr.cells[4] === 'none' && hr.cells[1] === '—', 'hubs without observations show "none" and dashes: ' + JSON.stringify(hr && hr.cells));
      const box = await cvBox(p), sp = (await hits(p)).find(q => q.type === 'storm');
      await p.mouse.hover({ x: box.x + sp.x, y: box.y + sp.y }); await sleep(300);
      check((await p.page.evaluate(() => document.querySelectorAll('.ot-tip img, .ot-tip script').length)) === 0 && /<b>bold<\/b>/.test(await tipText(p)), 'markup in the tooltip is text too');
      await p.shotNow('geo_wx_obs_down_1920x1080');
      await p.close();
    });

    await section('gateway stopped and restarted', async () => {
      const target = new URL(ctx.base);
      const proxy = ctx.tcpProxy(+target.port, target.hostname);
      await proxy.start();
      const p = await ctx.open({ tag: 'offline', base: proxy.base });
      p.allow(/ERR_CONNECTION|ERR_EMPTY_RESPONSE|ERR_SOCKET|WebSocket connection|Failed to load resource|net::|\/api\/|\/ws/);
      await p.goto('WX/CL'); await wxReady(p); await settle(p);
      const before = { sys: (await sysRows(p)).length, obs: (await obsRows(p)).length, hits: (await hits(p)).filter(q => q.type === 'storm').length };
      await proxy.stop();
      await p.waitFor(() => OT.state.gateway === 'offline', { timeout: 25000, msg: 'the page notices the gateway is gone' });
      await p.mouse.wheel(CV, -240, 2); await settle(p);
      const off = await p.page.evaluate(sel => ({ banner: !document.getElementById('offline').hidden, cover: !!document.querySelector(sel + ' .mod-body > .ot-state') }), WX);
      const during = { sys: (await sysRows(p)).length, obs: (await obsRows(p)).length, hits: (await hits(p)).filter(q => q.type === 'storm').length };
      check(off.banner && !off.cover && JSON.stringify(before) === JSON.stringify(during), 'offline: the banner is up, the last advisory and observations stay on the map and it still zooms: ' + JSON.stringify(during));
      await p.shotNow('geo_wx_offline_1920x1080');
      await proxy.start();
      await p.waitFor(() => OT.state.gateway === 'online', { timeout: 30000, msg: 'online again after the gateway came back' });
      await settle(p);
      check((await sysRows(p)).length === before.sys, 'after the restart the page shows the systems again');
      await p.close();
      await proxy.stop();
    });

    await section('1280x720: nothing is cut off', async () => {
      const p = await ctx.open({ tag: '1280', viewport: { width: 1280, height: 720 } });
      p.allow(/api\/satellite\/tile/);
      await p.page.route('**/api/storms', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.assign({}, fixture, { as_of: Date.now() / 1000 - 20 })) }));
      await p.goto('WX/CL'); await wxReady(p); await settle(p);
      await p.assertNoOverflow('WX at 1280x720');
      const r = await p.page.evaluate(sel => { const b = document.querySelector(sel + ' .mod-body'), out = [], cv = document.querySelector(sel + ' .wx-cv');
        if (b.scrollWidth > b.clientWidth + 1 || b.scrollHeight > b.clientHeight + 1) out.push('module body scrolls (' + b.scrollWidth + 'x' + b.scrollHeight + ' in ' + b.clientWidth + 'x' + b.clientHeight + ')');
        if (cv.clientWidth < 380 || cv.clientHeight < 300) out.push('the map is only ' + cv.clientWidth + 'x' + cv.clientHeight);
        b.querySelectorAll('.ot-tab, .btn, .wx-sys-wind, .wx-d-title').forEach(el => { if (el.offsetParent && el.scrollWidth > el.clientWidth + 2) out.push((el.className + ' "' + el.textContent.slice(0, 30) + '" is cut off').trim()); });
        const side = document.querySelector(sel + ' .wx-side').getBoundingClientRect(), body = b.getBoundingClientRect();
        if (side.right > body.right + 1 || side.bottom > body.bottom + 1) out.push('the side column leaves the module');
        return out; }, WX);
      check(!r.length, 'WX at 1280x720: ' + r.join('; '));
      await p.assertMinFontPx(12, WX);
      await p.shotNow('geo_wx_1280x720');
      await p.close();
    });
  },
};
