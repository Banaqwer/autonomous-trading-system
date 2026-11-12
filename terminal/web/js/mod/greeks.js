/* GREEKS — Greeks. Owner: S3 options.
   STUB written by the shell (FF): it only registers the module so every tab works.
   It shows no data at all. The slice owner replaces this whole file (spec §6). */
(function () {
'use strict';
OT.register({
  id: 'GREEKS', title: 'Greeks', group: 'OPTIONS', needs: 'options',
  mount(root) {
    OT.ui.state(root, {
      kind: 'info', label: 'BEING REBUILT',
      title: 'Greeks',
      detail: 'This page is being rebuilt on real data. It will show real greeks by strike. Nothing is displayed here until it does.',
    });
  },
  show() {},
  hide() {},
});
})();
