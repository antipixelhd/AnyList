import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Script } from 'node:vm';
import test from 'node:test';

function* astroFiles(root) {
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) yield* astroFiles(path);
    else if (entry.name.endsWith('.astro')) yield path;
  }
}

test('unprocessed Astro inline scripts are valid browser JavaScript', () => {
  const root = fileURLToPath(new URL('../src', import.meta.url));
  let checked = 0;
  for (const path of astroFiles(root)) {
    const source = readFileSync(path, 'utf8');
    for (const match of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      if (!/\bis:inline\b|\bdefine:vars\s*=/.test(match[1])) continue;
      const line = source.slice(0, match.index).split('\n').length;
      // These blocks bypass Astro's TypeScript processing and execute verbatim.
      assert.doesNotThrow(() => new Script(match[2], { filename: `${path}:${line}` }));
      checked++;
    }
  }
  assert.ok(checked > 0, 'The check must find the application inline scripts');
});
