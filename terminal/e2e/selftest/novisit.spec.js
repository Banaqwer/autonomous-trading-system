/* A spec that checks nothing and looks at nothing. */
module.exports = { name: 'novisit', slice: 'selftest', async run(ctx) { await ctx.page.evaluate(() => 1); } };
