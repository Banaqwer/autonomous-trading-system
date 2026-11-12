/* WX — Weather. Owner: S4 weather.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'WX', title: 'Weather', group: 'WORLD', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Weather',
      detail: 'This page is being rebuilt on real data. It will show real tropical cyclones, cloud imagery and marine observations. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
