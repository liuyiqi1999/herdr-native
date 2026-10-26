import test from 'node:test';
import assert from 'node:assert/strict';
import { stripVTControlCharacters } from 'node:util';
import { loadPi } from '../pi-package.mjs';
import { editorFrame } from '../editor-frame.mjs';

const { sdk, tui, colors, keys } = await loadPi();
colors.initTheme('dark', false);
const bindings = keys.KeybindingsManager.create();
tui.setKeybindings(bindings);

function editor(text) {
  const result = new sdk.CustomEditor(
    { terminal: { rows: 24, columns: 80 }, requestRender() {} },
    colors.getEditorTheme(), bindings,
  );
  result.focused = true;
  result.setText(text);
  return result;
}

for (const text of ['同步一下最新的md文件', '中文🙂e\u0301👨‍👩‍👧‍👦结束']) {
  test(`left/right preserve text and cursor columns: ${text}`, () => {
    const input = editor(text);
    const graphemes = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)].map(s => s.segment);
    const check = position => {
      const raw = input.render(80);
      const { lines, cursor } = editorFrame(raw, tui);
      assert.equal(stripVTControlCharacters(lines[1]).trimEnd(), text);
      assert.deepEqual(cursor, [tui.visibleWidth(graphemes.slice(0, position).join('')), 1]);
      assert.ok(lines.every(line => !line.includes('\x1b_pi:')));
      assert.equal(input.getText(), text);
    };
    check(graphemes.length);
    for (let i = graphemes.length - 1; i >= 0; i--) { input.handleInput('\x1b[D'); check(i); }
    for (let i = 1; i <= graphemes.length; i++) { input.handleInput('\x1b[C'); check(i); }
  });
}

test('wrapped multiline frames contain no private markers', () => {
  const input = editor('中文🙂中文🙂中文🙂\nsecond');
  for (let i = 0; i < 15; i++) {
    const { lines, cursor } = editorFrame(input.render(10), tui);
    assert.ok(lines.every(line => !line.includes('\x1b_pi:')));
    assert.ok(cursor[0] >= 0 && cursor[0] < 10);
    assert.ok(cursor[1] >= 0 && cursor[1] < lines.length);
    input.handleInput('\x1b[D');
  }
});
