// Herdr owns the hardware cursor; Pi's private fake-cursor wrappers must not reach VT.
export function editorFrame(lines, tui) {
  const [cursorStart, cursorEnd] = tui.renderFakeCursor('\0').split('\0');
  let cursor;
  const rendered = lines.map((line, row) => {
    line = line.replaceAll(cursorStart, '').replaceAll(cursorEnd, '');
    const at = line.indexOf(tui.CURSOR_MARKER);
    if (at >= 0) cursor = [tui.visibleWidth(line.slice(0, at)), row];
    return line.replaceAll(tui.CURSOR_MARKER, '');
  });
  return { lines: rendered, cursor };
}
