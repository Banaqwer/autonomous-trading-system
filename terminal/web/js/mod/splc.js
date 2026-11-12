/* SPLC — Crude Supply Chain. Owner: S6 macro.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'SPLC', title: 'Crude Supply Chain', group: 'MACRO', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Crude Supply Chain',
      detail: 'This page is being rebuilt on real data. It will show real EIA weekly supply figures. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
