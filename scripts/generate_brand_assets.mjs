import { readFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const sharp = createRequire(join(root, 'frontend', 'package.json'))('sharp');
const publicDir = join(root, 'frontend', 'public');
const source = await readFile(join(root, 'scripts', 'brand-mark-source.png'));
const background = '#101b2a';

async function squareIcon(name, size, occupancy) {
  const markSize = Math.round(size * occupancy);
  const mark = await sharp(source).resize(markSize, markSize).png().toBuffer();
  await sharp({create: {width: size, height: size, channels: 4, background}})
    .composite([{input: mark, gravity: 'centre'}])
    .png().toFile(join(publicDir, name));
}

await sharp(source).resize(256, 256).png().toFile(join(publicDir, 'brand-mark.png'));
await sharp(source).resize(96, 96).png().toFile(join(publicDir, 'favicon-96x96.png'));
await sharp(source).resize(512, 512).png().toFile(join(publicDir, 'scrob.png'));
await squareIcon('apple-touch-icon.png', 180, 0.78);
for (const size of [192, 512]) {
  await squareIcon(`web-app-manifest-${size}x${size}.png`, size, 0.78);
  await squareIcon(`web-app-maskable-${size}x${size}.png`, size, 0.62);
}

const previewDir = join(root, '.github');
await mkdir(previewDir, {recursive: true});
const previewMark = await sharp(source).resize(500, 500).png().toBuffer();
await sharp({create: {width: 1280, height: 640, channels: 4, background}})
  .composite([{input: previewMark, gravity: 'centre'}])
  .png().toFile(join(previewDir, 'social-preview.png'));

console.log('Generated AnyList browser, PWA, and GitHub artwork.');
