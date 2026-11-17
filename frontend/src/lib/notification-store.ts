import {startVisibleRefresh, type VisibleRefreshHandle} from './visible-refresh';

export type NotificationSnapshot = {
  results: any[];
  pending: number;
  outbound: any[];
};

export type NotificationListener = (snapshot: NotificationSnapshot) => void;

type SubscribeOptions = {signal?: AbortSignal; full?: boolean};
type RefreshOptions = {force?: boolean; signal?: AbortSignal};

const listeners = new Map<NotificationListener, boolean>();
let currentSnapshot: NotificationSnapshot | null = null;
let currentFull = false;
let fullSubscribers = 0;
const hydrated = new WeakSet<Element>();
let lifecycle: VisibleRefreshHandle | undefined;
let inFlight: Promise<NotificationSnapshot | null> | undefined;
let inFlightController: AbortController | undefined;
let inFlightFull = false;
let mutationTimer: ReturnType<typeof setTimeout> | undefined;
let mutationListenersAttached = false;
let identityKey: string | null = null;
let identityVersion = 0;

function activeIdentity() {
  return document.body?.dataset.cacheUser || 'anonymous';
}

function syncIdentity() {
  const next = activeIdentity();
  if (identityKey === null) identityKey = next;
  else if (identityKey !== next) {
    identityKey = next;
    identityVersion += 1;
    currentSnapshot = null;
    currentFull = false;
    // Do not let a request for the previous account occupy the new account's
    // initial refresh slot. Its response is still guarded by its identity.
    inFlightController?.abort();
    inFlight = undefined;
    inFlightController = undefined;
  }
  return {key: next, version: identityVersion};
}

function normalizeSnapshot(value: any): NotificationSnapshot {
  const pending = Number(value?.pending);
  return {
    results: Array.isArray(value?.results) ? value.results : [],
    pending: Number.isFinite(pending) && pending >= 0 ? pending : 0,
    outbound: Array.isArray(value?.outbound) ? value.outbound : [],
  };
}

function publish(snapshot: NotificationSnapshot, full = false) {
  currentSnapshot = snapshot;
  currentFull = full;
  for (const [listener, needsFull] of [...listeners]) if (full || !needsFull) listener(snapshot);
  document.dispatchEvent(new CustomEvent<NotificationSnapshot>('anylist:notifications-snapshot', {detail: snapshot}));
}

async function fetchSnapshot(signal: AbortSignal, requestIdentity: string, requestVersion: number, full: boolean): Promise<NotificationSnapshot | null> {
  const response = await fetch(full ? '/api/proxy/tracking/recent-events' : '/api/proxy/tracking/notifications', {cache: 'no-store', signal});
  if (!response.ok) throw new Error(`Notification refresh failed (${response.status}).`);
  if (signal.aborted || requestVersion !== identityVersion || activeIdentity() !== requestIdentity) return currentSnapshot;
  const snapshot = normalizeSnapshot(await response.json());
  if (!signal.aborted && requestVersion === identityVersion && activeIdentity() === requestIdentity) publish(snapshot, full);
  return snapshot;
}

/**
 * Fetch the complete notification snapshot. A forced refresh aborts an
 * in-flight request before starting, so a mutation that completed while the
 * first request was on the wire cannot be hidden by an older response.
 */
export async function refreshNotifications({force = false, signal}: RefreshOptions = {}): Promise<NotificationSnapshot | null> {
  const requestIdentity = syncIdentity();
  const full = fullSubscribers > 0;
  if (inFlight) {
    if (!force && (!full || inFlightFull)) return inFlight;
    // A mutation event invalidates the old response immediately. Starting the
    // next request without awaiting the old one prevents stale cards from
    // publishing after the write has completed.
    inFlightController?.abort();
    inFlight = undefined;
    inFlightController = undefined;
  }

  const requestController = new AbortController();
  const abortRequest = () => requestController.abort();
  signal?.addEventListener('abort', abortRequest, {once: true});
  let request: Promise<NotificationSnapshot | null>;
  request = fetchSnapshot(requestController.signal, requestIdentity.key, requestIdentity.version, full).catch(error => {
    // A failed refresh must leave the last known snapshot visible. Abort is
    // also intentionally quiet because Astro navigation owns that signal.
    if (requestController.signal.aborted) return currentSnapshot;
    console.debug('Notification refresh deferred.', error);
    return currentSnapshot;
  });
  const tracked = request.finally(() => {
    signal?.removeEventListener('abort', abortRequest);
    if (inFlight === tracked) inFlight = undefined;
    if (inFlightController === requestController) inFlightController = undefined;
  });
  inFlight = tracked;
  inFlightFull = full;
  inFlightController = requestController;
  return tracked;
}

function scheduleMutationRefresh() {
  if (mutationTimer !== undefined) return;
  mutationTimer = setTimeout(() => {
    mutationTimer = undefined;
    void refreshNotifications({force: true});
  }, 0);
}

function attachMutationListeners() {
  if (mutationListenersAttached) return;
  mutationListenersAttached = true;
  document.addEventListener('anylist:data-changed', scheduleMutationRefresh);
  document.addEventListener('media-tracker:notifications-changed', scheduleMutationRefresh);
}

function detachMutationListeners() {
  if (!mutationListenersAttached) return;
  mutationListenersAttached = false;
  document.removeEventListener('anylist:data-changed', scheduleMutationRefresh);
  document.removeEventListener('media-tracker:notifications-changed', scheduleMutationRefresh);
  if (mutationTimer !== undefined) clearTimeout(mutationTimer);
  mutationTimer = undefined;
}

function ensureLifecycle() {
  if (lifecycle) return;
  attachMutationListeners();
  const activeLifecycle = startVisibleRefresh({
    intervalMs: 60_000,
    immediate: !currentSnapshot || (fullSubscribers > 0 && !currentFull),
    wakeIntervalMs: 15_000,
    refresh: async signal => { await refreshNotifications({signal}); },
    onError: error => console.debug('Notification refresh deferred.', error),
  });
  lifecycle = activeLifecycle;
  // The shared lifecycle stops itself during Astro swaps. Reset the store's
  // handle as well so the next page can start a fresh visible refresh.
  document.addEventListener('astro:before-swap', () => {
    if (lifecycle === activeLifecycle) {
      lifecycle = undefined;
      currentSnapshot = null;
      inFlightController?.abort();
      inFlight = undefined;
      inFlightController = undefined;
      detachMutationListeners();
    }
  }, {once: true});
}

function stopLifecycleIfUnused() {
  if (listeners.size || !lifecycle) return;
  lifecycle.stop();
  lifecycle = undefined;
  currentSnapshot = null;
  currentFull = false;
  inFlightController?.abort();
  inFlight = undefined;
  inFlightController = undefined;
  detachMutationListeners();
}

/** Subscribe a notification surface to the shared snapshot stream. */
export function subscribeNotifications(listener: NotificationListener, {signal, full = false}: SubscribeOptions = {}): () => void {
  syncIdentity();
  const seed = document.querySelector('#viewer-notifications');
  if (seed && !hydrated.has(seed)) {
    hydrated.add(seed);
    try {
      const data = JSON.parse(seed.textContent || 'null');
      if (String(data?.user) === activeIdentity() && Array.isArray(data?.snapshot?.results)) {
        currentSnapshot = normalizeSnapshot(data.snapshot);
        currentFull = false;
      }
    } catch { /* A failed seed falls back to the normal authenticated refresh. */ }
  }
  listeners.set(listener, full);
  if (full) fullSubscribers += 1;
  if (currentSnapshot && (!full || currentFull)) listener(currentSnapshot);
  const hadLifecycle = !!lifecycle;
  ensureLifecycle();
  if (hadLifecycle && full && !currentFull) void refreshNotifications();

  let active = true;
  const unsubscribe = () => {
    if (!active) return;
    active = false;
    listeners.delete(listener);
    if (full) fullSubscribers -= 1;
    stopLifecycleIfUnused();
  };
  if (signal) {
    if (signal.aborted) unsubscribe();
    else signal.addEventListener('abort', unsubscribe, {once: true});
  }
  return unsubscribe;
}
