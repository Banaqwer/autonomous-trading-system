/* A page that shows prices the gateway never sent: core.js is altered in transit so that the rail paints
   last * (1 ± 1.5 %) for crypto / FX / indices and last * 1.004 for CL (the reviewer's mutation, which the old
   gate — "within 2 %" — let through). The price gate of shell.spec.js must reject it. */
'use strict';
const http = require('http');
const shell = require('../shell.spec.js');
const getJson = url => new Promise((res, rej) => { http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej); });
module.exports = {
  name: 'fabricated', slice: 'selftest',
  async before(ctx) {
    await ctx.page.route('**/js/core.js', async route => {
      const res = await route.fetch();
      const src = await res.text();
      const from = "setText(r.last, ok ? fmt.px(id, q.last) : DASH);";
      if (src.indexOf(from) < 0) throw new Error('fabricated.spec.js: the line to alter is no longer in core.js');
      await route.fulfill({ response: res, body: src.replace(from, "setText(r.last, ok ? fmt.px(id, q.last * (id === 'CL' ? 1.004 : 1 + (Math.random() - 0.5) * 0.03)) : DASH);") });
    });
  },
  async run(ctx) {
    const uni = await getJson(ctx.base + '/api/universe');
    const ids = [].concat(...uni.groups.map(g => g.ids));
    await ctx.go('MON');
    await shell.truth(ctx, ids, uni, 'rail');
    await ctx.shot('selftest_fabricated');
  },
};
