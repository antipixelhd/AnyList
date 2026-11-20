import {execFile, spawn} from 'node:child_process';
import {access, mkdir, open, readFile, rename, stat, unlink, writeFile} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';

const execute = promisify(execFile);
const day = 24 * 60 * 60 * 1000;
const defaultCache = fileURLToPath(new URL('../.npm-cache/modern-web-guidance/', import.meta.url));
const pause = ms => new Promise(done => setTimeout(done, ms));

async function readJson(path) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT' || error instanceof SyntaxError) return null;
    throw error;
  }
}

async function exists(path) {
  if (!path) return false;
  try { await access(path); return true; }
  catch { return false; }
}

// Serialize refreshes across chats. A crashed process must not leave a permanent lock.
async function lock(cacheDir) {
  const path = join(cacheDir, 'refresh.lock');
  const deadline = Date.now() + 60_000;
  while (true) {
    try {
      const handle = await open(path, 'wx');
      await handle.writeFile(String(process.pid));
      return async () => { await handle.close(); await unlink(path); };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      try {
        const pid = Number(await readFile(path, 'utf8'));
        if (Number.isInteger(pid) && pid > 0) {
          try { process.kill(pid, 0); }
          catch (ownerError) {
            if (ownerError.code !== 'ESRCH') throw ownerError;
            await unlink(path);
            continue;
          }
        } else if (Date.now() - (await stat(path)).mtimeMs > 90_000) {
          await unlink(path);
          continue;
        }
      } catch (ownerError) {
        if (ownerError.code !== 'ENOENT') throw ownerError;
      }
      if (Date.now() >= deadline) throw new Error('Timed out waiting for the guidance update check.');
      await pause(50);
    }
  }
}

async function resolveLatestCli(cacheDir) {
  let npmCommand = 'npm';
  let npmPrefix = [];
  if (process.platform === 'win32') {
    const {stdout} = await execute('where.exe', ['npm.cmd'], {windowsHide: true});
    const npmCli = join(dirname(stdout.trim().split(/\r?\n/)[0]), 'node_modules/npm/bin/npm-cli.js');
    npmCommand = process.execPath;
    npmPrefix = [npmCli];
  }
  const npm = async args => (await execute(npmCommand, [...npmPrefix, ...args], {
    windowsHide: true, timeout: 25_000, maxBuffer: 2 * 1024 * 1024,
  })).stdout.trim();
  const options = ['--cache', join(cacheDir, 'npm'), '--prefer-online'];
  const version = await npm(['view', 'modern-web-guidance', 'dist-tags.latest', ...options]);
  if (!/^\d+\.\d+\.\d+(?:[-+][\w.+-]+)?$/.test(version)) {
    throw new Error('npm returned an invalid guidance version.');
  }
  // Exact versions keep a later refresh from replacing files used by an active CLI.
  const shim = (await npm(['exec', ...options, '--yes', '--ignore-scripts',
    `--package=modern-web-guidance@${version}`, '--call',
    process.platform === 'win32' ? 'where.exe modern-web-guidance' : 'command -v modern-web-guidance',
  ])).split(/\r?\n/)[0];
  const packageDir = join(dirname(dirname(shim)), 'modern-web-guidance');
  const metadata = await readJson(join(packageDir, 'package.json'));
  if (metadata?.name !== 'modern-web-guidance' || metadata.version !== version) {
    throw new Error('Resolved guidance package does not match the requested release.');
  }
  const cliPath = resolve(packageDir, metadata.bin['modern-web-guidance']);
  if (!await exists(cliPath)) throw new Error('The resolved guidance CLI is missing.');
  return {cliPath, version};
}

export async function dailyCli({cacheDir = defaultCache, now = Date.now,
  refresh = resolveLatestCli, warn = console.error} = {}) {
  await mkdir(cacheDir, {recursive: true});
  const statePath = join(cacheDir, 'state.json');
  const fresh = state => Number.isFinite(state?.checkedAt) &&
    now() >= state.checkedAt && now() - state.checkedAt < day;
  const usable = state => exists(state?.cliPath);
  let state = await readJson(statePath);
  if (fresh(state) && await usable(state)) return state.cliPath;
  const release = await lock(cacheDir);
  try {
    state = await readJson(statePath);
    if (!fresh(state)) {
      const checkedAt = now();
      try {
        state = {...await refresh(cacheDir), checkedAt};
      } catch (error) {
        state = {...state, checkedAt};
        warn(`Guidance update check failed; ${await usable(state) ? 'using the cached CLI' : 'no cached CLI is available'}. ${error.message}`);
      }
      const temporaryPath = join(cacheDir, `state-${process.pid}.json`);
      await writeFile(temporaryPath, JSON.stringify(state) + '\n');
      await rename(temporaryPath, statePath);
    }
    if (!await usable(state)) {
      throw new Error(`Guidance CLI unavailable. Restore network access and remove ${statePath} to retry before the next daily check.`);
    }
    return state.cliPath;
  } finally { await release(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const cliPath = await dailyCli();
    const child = spawn(process.execPath, [cliPath, ...process.argv.slice(2)], {
      stdio: 'inherit', windowsHide: true,
    });
    child.on('error', error => { console.error(error.message); process.exitCode = 1; });
    child.on('exit', code => { process.exitCode = code ?? 1; });
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
