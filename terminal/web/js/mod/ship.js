/* SHIP — Vessels (AIS). Owner: S5 sat+ship.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'SHIP', title: 'Vessels (AIS)', group: 'WORLD', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Vessels (AIS)',
      detail: 'This page is being rebuilt on real data. It will show real AIS vessel positions. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
