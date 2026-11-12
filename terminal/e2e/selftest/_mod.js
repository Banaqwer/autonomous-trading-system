/* Shared by the self-test specs: a small module registered in every page of the spec through the runner's
   `init` hook. `defect` switches on exactly one thing a real module could get wrong. */
'use strict';
function install(defect) {
  document.addEventListener('DOMContentLoaded', () => {
    let ctx, cv, note, bar = null;
    window.OT.register({ id: 'ZSELF', title: 'Self Test', group: 'TOOLS', needs: 'candles',
      mount(root, c) {
        ctx = c;
        note = OT.ui.h('div', { class: 'note', style: 'padding:12px' }, 'loading…');
        cv = OT.ui.h('canvas', defect === 'unsized' ? {} : { class: 'ot-canvas' });
        root.append(note, OT.ui.h('div', defect === 'unsized' ? {} : { style: 'height:120px' }, cv));
      },
      show(id) {
        bar = null;
        const ok = ctx.guard();
        ctx.poll(async () => {
          const d = await OT.api('/api/candles', { id, tf: '1d', limit: 5 });
          if (!ok()) return;
          if (defect === 'pollbug') d.totals.gex.toFixed(2);                    // TypeError inside a poll callback
          bar = d.candles[d.candles.length - 1];
          if (defect !== 'nobadge') ctx.header.setPayload(d);
          note.replaceChildren('1d bar ' + OT.fmt.date(bar.t) + ' close ', OT.ui.h('span', { class: 'val' }, OT.fmt.px(id, bar.c)));
          ctx.dirty();
        }, 300000);
      },
      frame() {
        const f = OT.canvas.fit(cv), g = f.ctx;
        g.fillStyle = OT.theme.bg; g.fillRect(0, 0, f.w, f.h);
        if (!bar) return;
        g.font = defect === 'canvas-small' ? '9px monospace' : OT.canvas.font(13);
        g.fillStyle = defect === 'canvas-faint' ? OT.theme.line : OT.theme.text2;
        g.fillText('high ' + OT.fmt.px(ctx.sel, defect === 'canvas-nan' ? NaN * bar.h : bar.h).replace('—', defect === 'canvas-nan' ? String(NaN) : '—'), 8, 30);
        if (defect === 'unsized') ctx.dirty();                                // a module that redraws every frame
      } });
  });
}
function spec(name, defect) {
  return {
    name, slice: 'selftest',
    init: new Function('(' + install.toString() + ')(' + JSON.stringify(defect) + ')'),
    async run(ctx) {
      await ctx.select('BTC'); await ctx.go('ZSELF');
      await ctx.waitFor(() => /close/.test(document.querySelector('.mod[data-mod="ZSELF"] .note').textContent), { timeout: 8000, msg: 'ZSELF rendered' });
      if (defect !== 'noshot') await ctx.shot('selftest_' + name);
    },
  };
}
module.exports = { spec };
