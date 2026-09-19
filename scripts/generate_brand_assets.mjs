import sharp from '../frontend/node_modules/sharp/lib/index.js';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const publicDir = join(root, 'frontend', 'public');
const source = await readFile(join(publicDir, 'favicon.svg'));

for (const [name, size] of [
  ['favicon-96x96.png', 96],
  ['apple-touch-icon.png', 180],
  ['web-app-manifest-192x192.png', 192],
  ['web-app-manifest-512x512.png', 512],
  ['scrob.png', 512],
]) {
  await sharp(source).resize(size, size).png().toFile(join(publicDir, name));
}

console.log('Generated Media Tracker browser and PWA artwork.');
