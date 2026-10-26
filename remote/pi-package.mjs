import { execFileSync } from 'node:child_process';
import { realpathSync, readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// This bridge uses internal UI modules as well as the public CustomEditor.
// Keep an explicit, regression-tested allowlist rather than accepting all 1.x.
export const SUPPORTED_PI_VERSIONS = Object.freeze(['1.3.0', '1.4.0']);

export function assertSupportedPiPackage(pkg) {
  if (pkg?.name !== '@earendil-works/pi-coding-agent' || !SUPPORTED_PI_VERSIONS.includes(pkg.version)) {
    throw new Error(`The local input bridge supports @earendil-works/pi-coding-agent ${SUPPORTED_PI_VERSIONS.join(' or ')}; detected ${pkg?.name ?? 'unknown package'} ${pkg?.version ?? 'unknown version'}. Set HERDR_PI_BIN to a supported local Pi installation.`);
  }
}

export async function loadPi() {
  const executable = process.env.HERDR_PI_BIN || execFileSync('which', ['pi'], { encoding: 'utf8' }).trim();
  let root = dirname(realpathSync(executable));
  while (!existsSync(join(root, 'package.json')) && dirname(root) !== root) root = dirname(root);
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  assertSupportedPiPackage(pkg);
  const require = createRequire(join(root, 'package.json'));
  const moduleAt = relative => import(pathToFileURL(join(root, 'dist', relative)).href);
  const [sdk, tui, colors, keys, renderer, viewport, commands, clipboard] = await Promise.all([
    moduleAt('index.js'), import(pathToFileURL(require.resolve('@earendil-works/pi-tui')).href),
    moduleAt('modes/interactive/theme/theme.js'), moduleAt('core/keybindings.js'),
    moduleAt('modes/interactive/tui-renderer.js'), moduleAt('modes/interactive/chat-viewport.js'),
    moduleAt('core/slash-commands.js'), moduleAt('utils/clipboard-image.js'),
  ]);
  return { sdk, tui, colors, keys, renderer, viewport, commands, clipboard };
}
