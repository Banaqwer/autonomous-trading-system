/* HEAT — Depth Heatmap. Owner: S2 flow.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'HEAT', title: 'Depth Heatmap', group: 'FLOW', needs: 'book',
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Depth Heatmap',
      detail: 'This page is being rebuilt on real data. It will show real depth over time. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
