/* NEWS — News. Owner: S6 macro.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'NEWS', title: 'News', group: 'MACRO', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'News',
      detail: 'This page is being rebuilt on real data. It will show real headlines. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
