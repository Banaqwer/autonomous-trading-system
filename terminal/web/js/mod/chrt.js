/* CHRT — Chart. Owner: S1 markets.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'CHRT', title: 'Chart', group: 'MARKETS', needs: 'candles',
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Chart',
      detail: 'This page is being rebuilt on real data. It will show real candles and volume. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
