import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

test('static Icon references use names supported by the shared renderer', () => {
  const source = readFileSync(new URL('../src/components/Icon.astro', import.meta.url), 'utf8');
  const mapping = source.match(/const icons:[\s\S]*?= \{([\s\S]*?)\n\};/)[1];
  const names = new Set([...mapping.matchAll(/^\s*(?:'([^']+)'|([\w-]+)):/gm)].map(match => match[1] || match[2]));
  const root = new URL('../src/', import.meta.url);
  for (const file of readdirSync(root, { recursive: true }).filter(file => file.endsWith('.astro'))) {
    const content = readFileSync(new URL(file.replaceAll('\\', '/'), root), 'utf8');
    for (const match of content.matchAll(/<Icon\b[^>]*\bname=["']([^"']+)["']/g)) {
      assert.ok(names.has(match[1]), `${file}: unsupported icon ${match[1]}`);
    }
  }
});
