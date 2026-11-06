// Static demo: the page lives on the day the demo was recorded. Time runs normally from the moment the visitor
// first opened the demo in this tab; a reload continues the same clock. Loaded before the workspace modules.
(function () {
  const Real = Date;
  const start = window.DEMO_START;
  let offset = Number(sessionStorage.getItem("demo.clock"));
  if (!Number.isFinite(offset) || !sessionStorage.getItem("demo.clock")) {
    offset = start - Real.now();
    sessionStorage.setItem("demo.clock", String(offset));
  }
  class DemoDate extends Real {
    constructor(...args) {
      if (args.length === 0) super(Real.now() + offset);
      else super(...args);
    }
    static now() { return Real.now() + offset; }
  }
  DemoDate.parse = Real.parse;
  DemoDate.UTC = Real.UTC;
  window.Date = DemoDate;
  window.DemoRealDate = Real;
})();
