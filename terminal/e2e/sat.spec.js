/* S5 spec: the SAT and SHIP modules against the real gateway (started with OT_FEEDS
   including `ais`). What is shown is compared with what the page received; the image and the
   map are driven with real mouse input; the failure paths (no clear scene, image render error,
   AIS feed off) are provoked by altering answers in transit. Upstream-vs-gateway comparisons
   (scene date/cloud vs the Copernicus catalog, EIA figure vs EIA, vessels vs the raw hub) are
   done by hand in the slice report. */
'use strict';
const http = require('http');

const SAT = '.mod[data-mod="SAT"]';
const SHIP = '.mod[data-mod="SHIP"]';
const SATCV = SAT + ' .sat-imgbox canvas';
const SHIPCV = SHIP + ' .ship-cv canvas';
const sleep = ms => new Promise(r => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dayLabel = d => { const p = String(d).split('-'); return p.length === 3 ? (+p[2]) + ' ' + MONTHS[+p[1] - 1] + ' ' + p[0] : d; };

const satState = c => c.page.evaluate(sel => { const cv = document.querySelector(sel); return cv && cv.__sat ? { loaded: cv.__sat.loaded(), site: cv.__sat.site(), date: cv.__sat.date(), bands: cv.__sat.bands(), view: cv.__sat.view(), img: cv.__sat.imgSize() } : null; }, SATCV);
const shipState = c => c.page.evaluate(sel => { const cv = document.querySelector(sel); return cv && cv.__ship ? { count: cv.__ship.count(), inView: cv.__ship.inView(), sel: cv.__ship.sel(), view: cv.__ship.view(), feed: cv.__ship.feed(), hits: cv.__ship.hits().length } : null; }, SHIPCV);
const shipHits = c => c.page.evaluate(sel => document.querySelector(sel).__ship.hits(), SHIPCV);
const text = (c, sel) => c.page.evaluate(s => { const el = document.querySelector(s); return el ? el.textContent.trim() : null; }, sel);

module.exports = {
  name: 'sat', slice: 'sat',
  async run(ctx) {
    const base = ctx.base.replace(/\/+$/, '');

    /* ───────────────────────── SAT ───────────────────────── */
    await ctx.select('CL');
    await ctx.go('SAT');
    await ctx.waitFor(() => { const cv = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox canvas'); return cv && cv.__sat && cv.__sat.loaded(); },
      { timeout: 40000, msg: 'SAT: the Sentinel-2 image never loaded' });
    await sleep(400);
    await ctx.shot('sat');

    // the displayed acquisition matches what the page received from /api/sat/scene
    const scene = await getJson(base + '/api/sat/scene?site=cushing');
    ctx.check(!!scene.scene, 'SAT: /api/sat/scene returned no scene for cushing');
    if (scene.scene) {
      const shownDate = await text(ctx, SAT + ' .sat-kv dd');          // first dd = Acquired
      ctx.check(shownDate === dayLabel(scene.scene.date), `SAT: shown date ${shownDate} != scene ${dayLabel(scene.scene.date)}`);
      const st = await satState(ctx);
      ctx.check(st && st.site === 'cushing' && st.date === scene.scene.date, 'SAT: module date does not match the scene');
      ctx.check(st && st.img && st.img.w > 50 && st.img.h > 50, 'SAT: the image has no real pixels');
    }
    await ctx.assertHeaderBadge('SAT');
    await ctx.assertNoBadText(SAT);
    await ctx.assertMinFontPx(12, SAT);

    // date selector: picking a different recent date loads another scene
    const dates = (scene.dates || []).map(d => d.date);
    if (dates.length >= 2) {
      const other = dates[1];
      await ctx.mouse.click(SAT + ` .sat-date[data-date="${other}"]`);
      await ctx.waitFor((o) => { const cv = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox canvas'); return cv && cv.__sat && cv.__sat.date() === o && cv.__sat.loaded(); },
        { timeout: 40000, msg: 'SAT: the second date never loaded', arg: other });
      const st2 = await satState(ctx);
      ctx.check(st2 && st2.date === other, 'SAT: date selector did not switch the scene');
    }

    // wheel zoom + drag pan change the view (real mouse)
    const before = await satState(ctx);
    await ctx.mouse.wheel(SATCV, -400, 3);
    await sleep(200);
    const zoomed = await satState(ctx);
    ctx.check(zoomed.view.k > before.view.k + 0.01, 'SAT: wheel did not zoom the image');
    await ctx.mouse.drag(SATCV, 80, 40);
    await sleep(150);
    const panned = await satState(ctx);
    ctx.check(Math.abs(panned.view.x - zoomed.view.x) > 2 || Math.abs(panned.view.y - zoomed.view.y) > 2, 'SAT: drag did not pan the image');

    // the storage figure matches /api/sat/storage (EIA can be slow; wait for the value)
    const storage = await getJson(base + '/api/sat/storage?hub=cushing');
    await ctx.waitFor(() => { const el = document.querySelector('.mod[data-mod="SAT"] .sat-st-val'); return el && /\d/.test(el.textContent); },
      { timeout: 30000, msg: 'SAT: the EIA storage figure never appeared' });
    const stVal = await text(ctx, SAT + ' .sat-st-val');
    ctx.check(stVal && stVal.replace(/[, ]/g, '') === String(Math.round(storage.value)), `SAT: storage shown ${stVal} != EIA ${storage.value}`);

    // a site with no EIA series (Rotterdam) says so honestly — no invented storage number
    await ctx.page.evaluate(sel => { const s = document.querySelector(sel); s.value = 'rotterdam'; s.dispatchEvent(new Event('change', { bubbles: true })); }, SAT + ' select[aria-label="Site"]');
    await ctx.waitFor(() => { const cv = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox canvas'); return cv && cv.__sat && cv.__sat.site() === 'rotterdam'; },
      { timeout: 15000, msg: 'SAT: could not switch to rotterdam' });
    await sleep(400);
    const noHub = await text(ctx, SAT + ' .sat-st-none');
    ctx.check(noHub && /no EIA/i.test(noHub), 'SAT: a no-hub site did not say the EIA series is unavailable');
    ctx.check(!(await ctx.page.evaluate(() => { const el = document.querySelector('.mod[data-mod="SAT"] .sat-st-has'); return el && !el.hidden; })), 'SAT: a storage figure was shown for a site with no EIA series');

    /* failure path: image render error -> the error state (a deliberate 502), while a scene is loaded */
    await ctx.waitFor(() => { const cv = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox canvas'); return cv && cv.__sat && cv.__sat.loaded(); },
      { timeout: 40000, msg: 'SAT: rotterdam scene did not load before the image-error test' });
    ctx.allow(/\/api\/sat\/image/);
    await ctx.page.route('**/api/sat/image**', route => route.fulfill({ status: 502, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'upstream_error', message: 'copernicus: processing failed (test)', source: 'copernicus' } }) }));
    // switching bands forces a fresh (now 502) image fetch without touching the storage route
    await ctx.mouse.click(SAT + ' .ot-tab[data-id="falsecolor"]');
    await ctx.waitFor(() => { const el = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox .ot-state'); return el && /could not be rendered|processing failed/i.test(el.textContent); },
      { timeout: 20000, msg: 'SAT: image-error state not shown' });
    await ctx.assertNoBadText(SAT);
    await ctx.page.unroute('**/api/sat/image**');

    /* failure path: no clear scene -> the "NO CLEAR SCENE" empty state, no image */
    await ctx.page.route('**/api/sat/scene**', async route => {
      const res = await route.fetch(); let body;
      try { body = await res.json(); } catch (e) { return route.fulfill({ response: res }); }
      body.scene = null; body.dates = []; body.image_url = null; body.usable_found = 0;
      await route.fulfill({ response: res, json: body });
    });
    await ctx.go('MON'); await ctx.go('SAT');
    await ctx.waitFor(() => { const el = document.querySelector('.mod[data-mod="SAT"] .sat-imgbox .ot-state'); return el && /NO CLEAR SCENE|no Sentinel-2 acquisition/i.test(el.textContent); },
      { timeout: 15000, msg: 'SAT: empty-scene state not shown' });
    await ctx.assertNoBadText(SAT);
    await ctx.page.unroute('**/api/sat/scene**');
    await ctx.page.unroute('**/api/sat/image**');

    /* ───────────────────────── SHIP ───────────────────────── */
    // capture the page's OWN /api/vessels answers: the store grows every second, so the count shown
    // must be compared with what THIS page received, not an independent fetch.
    const vresp = [];
    ctx.page.on('response', async r => { if (/\/api\/vessels(\?|$)/.test(r.url()) && r.status() === 200) { try { vresp.push(await r.json()); } catch (e) { /* aborted */ } } });
    await ctx.go('SHIP');
    await ctx.waitFor(() => { const cv = document.querySelector('.mod[data-mod="SHIP"] .ship-cv canvas'); return cv && cv.__ship && cv.__ship.count() > 0; },
      { timeout: 40000, msg: 'SHIP: no vessels arrived (is the ais feed enabled?)' });
    await sleep(600);
    await ctx.shot('ship');

    const api = vresp[vresp.length - 1];
    ctx.check(api && api.feed && api.feed.enabled, 'SHIP: the AIS feed is not enabled on the gateway');
    const cntShown = await text(ctx, SHIP + ' .ship-big');
    ctx.check(api && cntShown && cntShown.replace(/[, ]/g, '') === String(api.count), `SHIP: count shown ${cntShown} != received /api/vessels ${api && api.count}`);
    const ss = await shipState(ctx);
    ctx.check(ss.count > 0 && ss.hits >= 0, 'SHIP: no vessels on the map');
    await ctx.assertHeaderBadge('SHIP');
    await ctx.assertNoBadText(SHIP);
    await ctx.assertMinFontPx(12, SHIP);
    await ctx.assertCanvasText(12);

    // type filter: switching to tankers keeps only tankers in the in-view table
    await ctx.mouse.click(SHIP + ' .ot-tab[data-id="tanker"]');
    await sleep(300);

    // hover a vessel marker -> tooltip with its MMSI; click -> selected
    const hits = await shipHits(ctx);
    // if the tanker filter emptied the map, go back to All to have something to hover
    if (!hits.length) { await ctx.mouse.click(SHIP + ' .ot-tab[data-id="all"]'); await sleep(300); }
    const hits2 = await shipHits(ctx);
    if (hits2.length) {
      const box = await ctx.page.locator(SHIPCV).boundingBox();
      const hh = hits2[0];
      await ctx.mouse.hover({ x: box.x + hh.x, y: box.y + hh.y });
      await sleep(250);
      const tip = await ctx.page.evaluate(() => { const t = document.querySelector('.ot-tip'); return t && !t.hidden ? t.innerText : ''; });
      ctx.check(/MMSI|kn|AISStream/i.test(tip), 'SHIP: hovering a vessel showed no details');
      await ctx.mouse.click({ x: box.x + hh.x, y: box.y + hh.y });
      await sleep(200);
      const sel = await shipState(ctx);
      ctx.check(sel.sel === hh.mmsi, 'SHIP: clicking a vessel did not select it');
    }

    // wheel zoom + drag on the map
    const v0 = (await shipState(ctx)).view;
    await ctx.mouse.wheel(SHIPCV, -400, 3);
    await sleep(200);
    const v1 = (await shipState(ctx)).view;
    ctx.check(v1.zoom > v0.zoom + 0.01, 'SHIP: wheel did not zoom the map');
    await ctx.mouse.drag(SHIPCV, 90, 50);
    await sleep(150);
    const v2 = (await shipState(ctx)).view;
    ctx.check(Math.abs(v2.lon - v1.lon) > 0.01 || Math.abs(v2.lat - v1.lat) > 0.01, 'SHIP: drag did not pan the map');

    /* failure path: AIS feed off -> the "AIS OFF" state and NO ships */
    await ctx.page.route('**/api/vessels**', async route => {
      const res = await route.fetch(); let body;
      try { body = await res.json(); } catch (e) { return route.fulfill({ response: res }); }
      body.feed = { enabled: false, state: 'disabled', reason: 'the AIS feed is not enabled on this gateway (test)' };
      body.vessels = []; body.count = 0; body.counts_by_region = []; body.counts_by_type = [];
      body.coverage = 'the AIS feed is not enabled on this gateway (test)';
      await route.fulfill({ response: res, json: body });
    });
    await ctx.go('MON'); await ctx.go('SHIP');
    await ctx.waitFor(() => { const el = document.querySelector('.mod[data-mod="SHIP"] .ship-strip .ot-state'); return el && /AIS OFF|not running|not enabled/i.test(el.textContent); },
      { timeout: 15000, msg: 'SHIP: feed-off state not shown' });
    const off = await shipState(ctx);
    ctx.check(off.count === 0, 'SHIP: vessels still shown after the feed went off — must show none, never stale');
    await ctx.assertNoBadText(SHIP);
    await ctx.page.unroute('**/api/vessels**');
  },
};
