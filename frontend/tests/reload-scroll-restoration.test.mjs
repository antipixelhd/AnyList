import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';

const component = readFileSync(new URL('../src/components/ReloadScrollRestoration.astro', import.meta.url), 'utf8');
const script = component.match(/<script is:inline>([\s\S]*?)<\/script>/)[1];
const contextFor = (history, entries) => {
  const saved = new Map();
  return { history, window: { addEventListener() {} }, document: { addEventListener() {} }, performance: { getEntriesByType: () => entries },
    location: { href: 'https://anylist.test/title/320' },
    sessionStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key) },
  };
};

test('a hard reload restores natively before deferred routing without changing the saved position', () => {
  const state = { index: 2, scrollX: 0, scrollY: 1800 };
  const history = { state, scrollRestoration: 'manual' };
  runInNewContext(script, contextFor(history, [{ type: 'reload' }]));
  assert.equal(history.scrollRestoration, 'auto');
  assert.equal(history.state, state);
  assert.equal(history.state.scrollY, 1800);
});

test('normal visits and back/forward navigation keep the router restoration mode', () => {
  for (const type of ['navigate', 'back_forward', undefined]) {
    const history = { scrollRestoration: 'manual' };
    runInNewContext(script, contextFor(history, type ? [{ type }] : []));
    assert.equal(history.scrollRestoration, 'manual');
  }
});

test('browsers without scroll restoration support are left alone', () => {
  const history = {};
  runInNewContext(script, contextFor(history, [{ type: 'reload' }]));
  assert.equal('scrollRestoration' in history, false);
});

test('all shared layouts initialize restoration in the head before routing', () => {
  for (const name of ['Tracker', 'TrackerSettings', 'Base']) {
    const layout = readFileSync(new URL(`../src/layouts/${name}.astro`, import.meta.url), 'utf8');
    const position = layout.indexOf('<ReloadScrollRestoration />');
    assert.ok(position > layout.indexOf('<head>'), name);
    assert.ok(position < layout.indexOf('</head>'), name);
    if (layout.includes('<ClientRouter')) assert.ok(position < layout.indexOf('<ClientRouter'), name);
  }
});

test('leaving during a scroll reconciles the next reload without changing other router state', () => {
  const listeners = new Map();
  const history = { scrollRestoration: 'manual', state: { index: 0, scrollX: 0, scrollY: 1800, custom: 'keep' },
    replaceState(state) { this.state = state; } };
  const context = contextFor(history, [{ type: 'reload' }]);
  context.window = { scrollX: 0, scrollY: 2000, addEventListener: (event, handler) => listeners.set(event, handler) };
  runInNewContext(script, context);
  listeners.get('pagehide')();
  // Chrome can already have copied this stale value before pagehide runs.
  assert.equal(history.state.scrollY, 1800);
  const reload = contextFor(history, [{ type: 'reload' }]);
  reload.sessionStorage = context.sessionStorage;
  runInNewContext(script, reload);
  assert.equal(history.state.scrollY, 2000);
  assert.equal(history.state.index, 0);
  assert.equal(history.state.custom, 'keep');
  assert.equal(reload.sessionStorage.getItem('anylist:reload-scroll'), null);
});

test('repeat initialization registers one final-position listener and ignores unrelated history', () => {
  const listeners = [];
  const history = { state: { otherRouter: true }, replaceState() { throw Error('Unrelated state changed'); } };
  const context = contextFor(history, []);
  context.window.addEventListener = (event, handler) => { if (event === 'pagehide') listeners.push(handler); };
  runInNewContext(script, context);
  runInNewContext(script, context);
  assert.equal(listeners.length, 1);
  assert.doesNotThrow(() => listeners[0]());
});

test('exit positions are scoped to the exact URL and history entry with valid coordinates', () => {
  for (const overrides of [{ url: 'https://anylist.test/title/999' }, { index: 1 }, { y: -1 }, { y: '2000' }, { x: null }]) {
    const history = { scrollRestoration: 'manual', state: { index: 0, scrollX: 0, scrollY: 1800 }, replaceState() { throw Error('Wrong position applied'); } };
    const context = contextFor(history, [{ type: 'reload' }]);
    context.sessionStorage.setItem('anylist:reload-scroll', JSON.stringify({ url: context.location.href, index: 0, x: 0, y: 2000, ...overrides }));
    assert.doesNotThrow(() => runInNewContext(script, context));
    assert.equal(history.state.scrollY, 1800);
    assert.equal(context.sessionStorage.getItem('anylist:reload-scroll'), null);
  }
});

test('blocked session storage does not interfere with loading or leaving a page', () => {
  const listeners = [];
  const context = contextFor({ state: { index: 0 }, scrollRestoration: 'manual' }, [{ type: 'reload' }]);
  context.window.addEventListener = (event, listener) => { if (event === 'pagehide') listeners.push(listener); };
  context.sessionStorage = { getItem() { throw Error('Blocked'); }, setItem() { throw Error('Blocked'); } };
  assert.doesNotThrow(() => runInNewContext(script, context));
  assert.equal(context.history.scrollRestoration, 'auto');
  assert.doesNotThrow(() => listeners[0]());
});

test('the outgoing document allows native reload restoration while Astro owns route traversal', () => {
  const documentEvents = new Map(), windowEvents = new Map();
  const history = { state: null, scrollRestoration: 'auto' };
  const context = contextFor(history, [{ type: 'navigate' }]);
  context.document.addEventListener = (event, handler) => documentEvents.set(event, handler);
  context.window.addEventListener = (event, handler, options) => windowEvents.set(event, { handler, options });
  runInNewContext(script, context);
  // Astro's first initialization puts the outgoing document in manual mode.
  history.state = { index: 0, scrollX: 0, scrollY: 0 };
  history.scrollRestoration = 'manual';
  documentEvents.get('astro:page-load')();
  assert.equal(history.scrollRestoration, 'auto', 'the very first reload must already be native');
  documentEvents.get('astro:before-preparation')();
  assert.equal(history.scrollRestoration, 'manual');
  documentEvents.get('astro:page-load')();
  assert.equal(history.scrollRestoration, 'auto');
  assert.equal(windowEvents.get('popstate').options.capture, true);
  windowEvents.get('popstate').handler();
  assert.equal(history.scrollRestoration, 'manual', 'disable native traversal before Astro swaps the DOM');
  documentEvents.get('astro:page-load')();
  assert.equal(history.scrollRestoration, 'auto');
  history.scrollRestoration = 'manual';
  windowEvents.get('pageshow').handler();
  assert.equal(history.scrollRestoration, 'auto', 'full-document layouts and BFCache returns also allow reload restoration');
});

test('list landing does not override hard reload or back/forward positions', () => {
  const page = readFileSync(new URL('../src/pages/user/[username]/[section].astro', import.meta.url), 'utf8');
  const landing = page.match(/<script is:inline data-astro-rerun>([\s\S]*?)<\/script>/)[1];
  for (const [type, state, shouldAlign] of [['navigate', null, true], ['reload', null, false], ['back_forward', null, false], ['navigate', { index: 1 }, false]]) {
    const calls = [];
    runInNewContext(landing, { performance: { getEntriesByType: () => [{ type }] }, history: { state },
      document: { querySelector: () => ({ getBoundingClientRect: () => ({ top: 330 }) }) },
      window: { scrollY: 0, scrollTo: (...args) => calls.push(args) } });
    assert.equal(calls.length, Number(shouldAlign), `${type}, state: ${JSON.stringify(state)}`);
  }
});

test('routed non-list profile tabs land at the top and preserve horizontal tab position', () => {
  const runtime = readFileSync(new URL('../src/components/NavigationRuntime.astro', import.meta.url), 'utf8');
  const before = runtime.slice(runtime.indexOf("    document.addEventListener('astro:before-swap'"), runtime.indexOf("    document.addEventListener('astro:after-swap'"));
  const after = runtime.slice(runtime.indexOf("    document.addEventListener('astro:after-swap'"), runtime.indexOf("    document.addEventListener('astro:page-load'"));
  const handlers = new Map();
  const source = { dataset: { profileBase: '/user/alex' }, getBoundingClientRect: () => ({ top: 105 }), scrollLeft: 46 };
  const incoming = { dataset: { profileBase: '/user/alex' }, getBoundingClientRect: () => ({ top: 330 }), scrollLeft: 0 };
  const content = {};
  const main = { querySelector: () => null };
  const scrolls = [], reveals = [];
  let detail = null;
  const context = {
    pageEntrance: new AbortController().signal, profileTabPosition: undefined, detailLayoutTop: undefined,
    clearPreview: undefined, clearNavigationFeedback: undefined, prepareDetailResolution() {}, currentUser: () => 'alex',
    state: { alignListLanding: true },
    document: {
      body: { hasAttribute: () => false },
      addEventListener: (name, callback) => handlers.set(name, callback),
      querySelector: selector => new Map([
        ['.profile-tabs', incoming], ['#content > [data-profile-tab-content]', content], ['#content, #settings-content', main],
        ['.media-detail [data-detail-panels]', detail],
      ]).get(selector) ?? null,
    },
    window: { scrollY: 0, scrollTo: (...coordinates) => scrolls.push(coordinates) },
    settleNavigationContent: (element, kind, _signal, offset) => reveals.push([element, kind, offset]),
  };
  runInNewContext(stripTypeScriptTypes(before + after), context);
  const beforeSwap = (event) => handlers.get('astro:before-swap')({...event, signal:{aborted:true}});
  beforeSwap({sourceElement: {closest: () => source}, newDocument: {querySelector: () => incoming}, to: new URL('https://anylist.test/user/alex/favorites')});
  handlers.get('astro:after-swap')();
  assert.deepEqual(scrolls, [[0, 0]]);
  assert.equal(incoming.scrollLeft, 46);
  assert.deepEqual(reveals, [[content, 'tab', undefined]]);

  beforeSwap({sourceElement: {closest: () => source}, newDocument: {querySelector: () => incoming}, to: new URL('https://anylist.test/user/alex')});
  handlers.get('astro:after-swap')();
  assert.equal(scrolls.length, 1, 'a history traversal keeps the router position');
  assert.equal(reveals.length, 1, 'a history traversal does not replay the fade');

  context.pageEntrance = new AbortController().signal;
  beforeSwap({newDocument: {}, to: new URL('https://anylist.test/browse')});
  handlers.get('astro:after-swap')();
  assert.deepEqual(reveals[1], [main, 'page', 0], 'a main page settles without moving the whole surface');

  detail = { querySelector: () => null };
  context.pageEntrance = new AbortController().signal;
  beforeSwap({newDocument: {}, to: new URL('https://anylist.test/title/1')});
  handlers.get('astro:after-swap')();
  assert.deepEqual(reveals[2], [detail, 'page', undefined], 'detail entrances settle the panels while the sidebar and tabs stay fixed');
});

test('router scrollend saves finish before idle restoration without overriding a pending traversal', () => {
  const documentEvents = new Map(), windowEvents = new Map(), settledTasks = [];
  const state = { index: 4, scrollX: 0, scrollY: 900 };
  const history = { state, scrollRestoration: 'manual' };
  const context = contextFor(history, [{ type: 'navigate' }]);
  context.setTimeout = callback => settledTasks.push(callback);
  context.document.addEventListener = (event, handler) => documentEvents.set(event, handler);
  context.window.addEventListener = (event, handler) => windowEvents.set(event, handler);
  runInNewContext(script, context);
  documentEvents.get('astro:page-load')();
  windowEvents.get('scrollend')();
  // Astro may register its scrollend listener after the inline initializer.
  history.scrollRestoration = 'manual'; state.scrollY = 1100;
  settledTasks.shift()();
  assert.equal(history.scrollRestoration, 'auto');
  assert.equal(history.state, state); assert.equal(history.state.scrollY, 1100);
  windowEvents.get('scrollend')();
  documentEvents.get('astro:before-preparation')();
  settledTasks.shift()();
  assert.equal(history.scrollRestoration, 'manual');
  windowEvents.get('scrollend')(); settledTasks.shift()();
  assert.equal(history.scrollRestoration, 'manual');
  documentEvents.get('astro:page-load')();
  assert.equal(history.scrollRestoration, 'auto');
  windowEvents.get('scrollend')(); windowEvents.get('popstate')(); settledTasks.shift()();
  assert.equal(history.scrollRestoration, 'manual');
});

test('shared layouts preload the same primary typefaces without route-specific exceptions', () => {
  const preloads = readFileSync(new URL('../src/components/PrimaryFontPreloads.astro', import.meta.url), 'utf8');
  assert.match(preloads, /\[400, 500, 600, 700\]/);
  assert.match(preloads, /overpass-latin\.woff2/);
  for (const name of ['Tracker', 'TrackerSettings', 'Base']) {
    const layout = readFileSync(new URL(`../src/layouts/${name}.astro`, import.meta.url), 'utf8');
    const head = layout.slice(layout.indexOf('<head>'), layout.indexOf('</head>'));
    assert.match(head, /<PrimaryFontPreloads \/>/, name);
    assert.doesNotMatch(layout, /preloadBodyFonts/);
  }
});
