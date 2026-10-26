import test from 'node:test';
import assert from 'node:assert/strict';
import { assertSupportedPiPackage, SUPPORTED_PI_VERSIONS, loadPi } from '../pi-package.mjs';

const name = '@earendil-works/pi-coding-agent';

test('local bridge allows only the two regression-tested Pi releases', () => {
  assert.deepEqual(SUPPORTED_PI_VERSIONS, ['1.3.0', '1.4.0']);
  assert.equal(Object.isFrozen(SUPPORTED_PI_VERSIONS), true);
  for (const version of SUPPORTED_PI_VERSIONS) {
    assert.doesNotThrow(() => assertSupportedPiPackage({ name, version }));
  }
  for (const version of ['1.2.0', '1.3.1', '1.4.1', '1.4.0-beta.1', '2.0.0', undefined]) {
    assert.throws(() => assertSupportedPiPackage({ name, version }), /supported local Pi installation/);
  }
  assert.throws(() => assertSupportedPiPackage({ name: 'unrelated-pi', version: '1.4.0' }), /unrelated-pi/);
  assert.throws(() => assertSupportedPiPackage(null), /unknown package/);
});

test('installed Pi resolves all bridge UI modules and original editor methods', async () => {
  const { sdk, tui, colors, keys, renderer, viewport, commands, clipboard } = await loadPi();
  for (const fn of [sdk.CustomEditor, tui.matchesKey, tui.truncateToWidth, colors.initTheme,
    colors.getEditorTheme, keys.KeybindingsManager.create, renderer.createInteractiveTui,
    viewport.createChatViewport, clipboard.readClipboardImage]) {
    assert.equal(typeof fn, 'function');
  }
  assert.ok(Array.isArray(commands.BUILTIN_SLASH_COMMANDS));
  colors.initTheme('dark', false);
  const bindings = keys.KeybindingsManager.create();
  tui.setKeybindings(bindings);
  const editor = new sdk.CustomEditor({ requestRender() {}, terminal: { rows: 40, columns: 100 } },
    colors.getEditorTheme(), bindings, { embedWorkingStatus: true });
  for (const method of ['getText', 'setText', 'handleInput', 'render', 'onAction', 'addToHistory',
    'insertTextAtCursor', 'setAutocompleteProvider', 'setPaddingX', 'setAutocompleteMaxVisible']) {
    assert.equal(typeof editor[method], 'function', method);
  }
});
