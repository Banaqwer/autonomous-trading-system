/* TNT — Paper Blotter. Owner: S1 markets.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'TNT', title: 'Paper Blotter', group: 'TOOLS', needs: null,
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Paper Blotter',
      detail: 'This page is being rebuilt on real data. It will show a local paper-trading blotter marked to real quotes. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
