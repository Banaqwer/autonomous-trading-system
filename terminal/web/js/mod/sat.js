/* SAT — Satellite. Owner: S5 sat+ship.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'SAT', title: 'Satellite', group: 'WORLD', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Satellite',
      detail: 'This page is being rebuilt on real data. It will show real dated Sentinel-2 scenes of storage sites. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
