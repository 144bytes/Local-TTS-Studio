"use strict";
/* Minimal script editor: undo/redo on top of a plain textarea. No command syntax. */
window.Editor = (function () {
  let ta, onChange = () => {};
  const undo = [], redo = [];
  let last = 0;

  function push(force) {
    const now = Date.now();
    if (!force && now - last < 500) return;
    last = now;
    if (!undo.length || undo[undo.length - 1] !== ta.value) {
      undo.push(ta.value);
      if (undo.length > 200) undo.shift();
      redo.length = 0;
    }
  }
  function doUndo() {
    if (!undo.length) return;
    const cur = ta.value;
    let prev = undo.pop();
    if (prev === cur && undo.length) prev = undo.pop();
    redo.push(cur); ta.value = prev; onChange();
  }
  function doRedo() {
    if (!redo.length) return;
    undo.push(ta.value); ta.value = redo.pop(); onChange();
  }
  return {
    init(opts) {
      ta = opts.textarea; onChange = opts.onChange || onChange;
      ta.addEventListener("input", () => { push(false); onChange(); });
      ta.addEventListener("keydown", (e) => {
        const k = e.key.toLowerCase();
        if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); doUndo(); }
        else if ((e.ctrlKey || e.metaKey) && (k === "y" || (e.shiftKey && k === "z"))) { e.preventDefault(); doRedo(); }
      });
      push(true);
    },
    undo: doUndo, redo: doRedo,
    setValue(v, silent) { ta.value = v || ""; undo.length = 0; redo.length = 0; push(true); if (!silent) onChange(); },
    getValue: () => ta.value,
  };
})();
