/* The page stores a price that differs from what the gateway sent (0.1 % off, applied where quotes are merged):
   display and store agree with each other, so only the comparison with the gateway's own answers catches it. */
'use strict';
const http = require('http');
const shell = require('../shell.spec.js');
const getJson = url => new Promise((res, rej) => { http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej); });
module.exports = {
  name: 'heldwrong', slice: 'selftest',
  async before(ctx) {
    await ctx.page.route('**/js/core.js', async route => {
      const res = await route.fetch();
      const src = await res.text();
      const from = "    q[k] = src[k];";
      if (src.indexOf(from) < 0) throw new Error('heldwrong.spec.js: the line to alter is no longer in core.js');
      await route.fulfill({ response: res, body: src.replace(from, "    q[k] = (k === 'last' && typeof src[k] === 'number') ? src[k] * 1.001 : src[k];") });
    });
  },
  async run(ctx) {
    const uni = await getJson(ctx.base + '/api/universe');
    const ids = [].concat(...uni.groups.map(g => g.ids));
    await ctx.go('MON');
    await shell.truth(ctx, ids, uni, 'rail');
    await ctx.shot('selftest_heldwrong');
  },
};
