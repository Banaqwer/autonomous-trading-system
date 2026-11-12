/* VOLS — Volatility. Owner: S3 options.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'VOLS', title: 'Volatility', group: 'OPTIONS', needs: 'options',
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Volatility',
      detail: 'This page is being rebuilt on real data. It will show real implied volatility smile, term structure and surface. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
