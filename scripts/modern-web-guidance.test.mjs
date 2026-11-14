import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdir, mkdtemp, readFile, rm, utimes, writeFile} from 'node:fs/promises';
import {join, resolve, sep} from 'node:path';
import test from 'node:test';
import {dailyCli} from './modern-web-guidance.mjs';

const day = 24 * 60 * 60 * 1000;

async function fixture(t) {
  const root = resolve('tmp');
  await mkdir(root, {recursive: true});
  const cacheDir = await mkdtemp(join(root, 'guidance cache '));
  t.after(async () => {
    assert.ok(cacheDir.startsWith(root + sep));
    await rm(cacheDir, {recursive: true, force: true});
  });
  const cliPath = join(cacheDir, 'cli.mjs');
  await writeFile(cliPath, '// cached CLI fixture\n');
  return {cacheDir, cliPath};
}

test('reuses the CLI across invocations for 24 hours, then resolves the latest release', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  let clock = 1_000;
  let checks = 0;
  const options = {cacheDir, now: () => clock, refresh: async () => {
    checks++;
    return {cliPath, version: String(checks)};
  }};
  assert.equal(await dailyCli(options), cliPath);
  clock += day - 1;
  assert.equal(await dailyCli(options), cliPath);
  assert.equal(checks, 1);
  clock++;
  assert.equal(await dailyCli(options), cliPath);
  assert.equal(checks, 2);
  assert.equal(JSON.parse(await readFile(join(cacheDir, 'state.json'), 'utf8')).version, '2');
});

test('concurrent invocations share one update check', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  let checks = 0;
  const options = {cacheDir, refresh: async () => {
    checks++;
    await new Promise(done => setTimeout(done, 75));
    return {cliPath, version: '1'};
  }};
  assert.deepEqual(await Promise.all([dailyCli(options), dailyCli(options), dailyCli(options)]),
    [cliPath, cliPath, cliPath]);
  assert.equal(checks, 1);
});

test('failed daily refresh keeps the working CLI and does not retry on every invocation', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  await writeFile(join(cacheDir, 'state.json'), JSON.stringify({checkedAt: 0, cliPath}));
  let attempts = 0;
  const warnings = [];
  const options = {cacheDir, now: () => day, warn: message => warnings.push(message),
    refresh: async () => { attempts++; throw new Error('offline'); }};
  assert.equal(await dailyCli(options), cliPath);
  assert.equal(await dailyCli(options), cliPath);
  assert.equal(attempts, 1);
  assert.match(warnings[0], /using the cached CLI/);
});

test('failed first installation reports missing cache and respects the daily retry limit', async t => {
  const {cacheDir} = await fixture(t);
  let attempts = 0;
  const options = {cacheDir, warn: () => {}, refresh: async () => {
    attempts++;
    throw new Error('offline');
  }};
  await assert.rejects(dailyCli(options), /Guidance CLI unavailable/);
  await assert.rejects(dailyCli(options), /Guidance CLI unavailable/);
  assert.equal(attempts, 1);
});

test('corrupt state is replaced by a successful resolution', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  await writeFile(join(cacheDir, 'state.json'), '{broken');
  assert.equal(await dailyCli({cacheDir, refresh: async () => ({cliPath, version: '1'})}), cliPath);
});

test('a backward clock change triggers a fresh check', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  await writeFile(join(cacheDir, 'state.json'), JSON.stringify({checkedAt: day, cliPath}));
  let checks = 0;
  await dailyCli({cacheDir, now: () => 1, refresh: async () => {
    checks++;
    return {cliPath, version: '1'};
  }});
  assert.equal(checks, 1);
});

test('recovers a refresh lock left by a process that exited', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  const owner = spawnSync(process.execPath, ['-e', '']);
  assert.equal(owner.status, 0);
  await writeFile(join(cacheDir, 'refresh.lock'), String(owner.pid));
  assert.equal(await dailyCli({cacheDir, refresh: async () => ({cliPath, version: '1'})}), cliPath);
});

test('recovers an old lock left before its owner could be recorded', async t => {
  const {cacheDir, cliPath} = await fixture(t);
  const lockPath = join(cacheDir, 'refresh.lock');
  await writeFile(lockPath, '');
  const old = new Date(Date.now() - 120_000);
  await utimes(lockPath, old, old);
  assert.equal(await dailyCli({cacheDir, refresh: async () => ({cliPath, version: '1'})}), cliPath);
});
