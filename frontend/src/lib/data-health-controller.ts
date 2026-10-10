import { healthJobMessage, healthSummary, isHealthJobActive } from './data-health';
import type { DataHealth, HealthAction, HealthJob } from './data-health';

let dispose: (() => void) | undefined;

export function mountDataHealth() {
  dispose?.();
  const root = document.querySelector<HTMLElement>('#data-health');
  if (!root) return;
  const controller = new AbortController();
  const { signal } = controller;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let busy = false;
  let job: HealthJob | null = null;
  const recheck = root.querySelector<HTMLButtonElement>('#health-recheck')!;
  const showClear = root.querySelector<HTMLInputElement>('#health-show-clear')!;
  const error = root.querySelector<HTMLElement>('#health-error')!;
  const write = (id: string, value: string) => { root.querySelector<HTMLElement>(`#${id}`)!.textContent = value; };
  const fail = (message: string) => { error.textContent = message; error.hidden = false; };

  function controls() {
    root!.querySelectorAll<HTMLButtonElement>('[data-health-action]').forEach(button => {
      button.disabled = busy || isHealthJobActive(job);
    });
    recheck.disabled = busy;
  }
  function renderJob(value: HealthJob | null) {
    job = value;
    root!.querySelector<HTMLElement>('#health-job')!.hidden = !job;
    if (job) {
      write('health-job-message', healthJobMessage(job));
      const progress = root!.querySelector<HTMLProgressElement>('#health-job-progress')!;
      progress.max = job.total_steps || 1; progress.value = job.completed_steps;
      progress.hidden = !isHealthJobActive(job);
      write('health-job-results', Object.entries(job.results).map(([label, result]) =>
        `${label}: ${Object.entries(result).map(([key, count]) => `${key.replaceAll('_', ' ')} ${count.toLocaleString()}`).join(' · ')}`,
      ).join(' / '));
    }
    controls();
  }
  function filter() {
    root!.querySelectorAll<HTMLElement>('.health-check').forEach(check => {
      check.hidden = check.classList.contains('is-clear') && !showClear.checked;
    });
  }
  function render(data: DataHealth) {
    const open = new Set([...root!.querySelectorAll<HTMLDetailsElement>('details[open]')].map(row => row.dataset.check));
    const summary = healthSummary(data.checks);
    write('health-tracked', data.tracked_titles.toLocaleString());
    write('health-records', data.catalogue_records.toLocaleString());
    write('health-attention', String(summary.attention)); write('health-clear', String(summary.clear));
    write('health-time', `Checked ${new Date(data.checked_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
    const checks = root!.querySelector<HTMLElement>('#health-checks')!;
    checks.replaceChildren();
    // Keep Astro's scoped styling on dynamically built nodes.
    const scope = [...checks.attributes].find(attribute => attribute.name.startsWith('data-astro-cid-'))?.name;
    function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string) {
      const node = document.createElement(tag);
      if (scope) node.setAttribute(scope, '');
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }
    for (const check of data.checks) {
      const detail = element('details', undefined, `health-check${check.count ? '' : ' is-clear'}`);
      detail.dataset.check = check.code; detail.open = open.has(check.code);
      const heading = element('summary'); heading.append(element('span', check.label));
      heading.append(element('span', `${check.count.toLocaleString()} ${check.unit}`, `health-count${check.count > 0 && check.severity === 'warning' ? ' is-warning' : ''}`));
      detail.append(heading);
      const body = element('div', undefined, 'health-check-body'); body.append(element('p', check.description));
      const list = element('ul');
      for (const example of check.examples) {
        const item = element('li');
        // Only allow app-local title links from the diagnostics response.
        if (example.href && /^\/title\/\d+$/.test(example.href)) {
          const link = element('a', example.name); link.href = example.href; item.append(link);
        } else item.append(element('span', example.name));
        if (example.detail) item.append(element('small', example.detail));
        list.append(item);
      }
      if (check.examples.length) body.append(list);
      if (check.count > check.examples.length) body.append(element('p', `Showing ${check.examples.length} of ${check.count.toLocaleString()} ${check.unit}`, 'health-sample-note'));
      detail.append(body); checks.append(detail);
    }
    root!.querySelector<HTMLElement>('#health-all-clear')!.hidden = data.checks.some(check => check.count > 0);
    filter(); renderJob(data.job);
  }
  async function request<T>(path: string, action?: HealthAction): Promise<T> {
    const response = await fetch(`/api/proxy/admin/data-health${path}`, {
      signal, cache: 'no-store', method: action ? 'POST' : 'GET',
      headers: action ? { 'Content-Type': 'application/json' } : undefined,
      body: action ? JSON.stringify({ action }) : undefined,
    });
    if (!response.ok) throw new Error(response.status === 409 ? 'Another repair is already running. Recheck to see its progress.' : 'Data health request failed. Try again.');
    return response.json() as Promise<T>;
  }
  async function refresh() {
    busy = true; controls(); error.hidden = true;
    try { render(await request<DataHealth>('')); }
    catch (failure) { if (!signal.aborted) fail(failure instanceof Error ? failure.message : 'Health check failed.'); }
    finally { busy = false; if (!signal.aborted) controls(); }
  }
  function schedule() {
    clearTimeout(timer);
    if (signal.aborted || !isHealthJobActive(job)) return;
    timer = setTimeout(async () => {
      try {
        renderJob(await request<HealthJob | null>('/job'));
        error.hidden = true;
        if (!isHealthJobActive(job)) await refresh();
      } catch { if (!signal.aborted) fail('Progress could not be loaded. Recheck to reconnect; the repair continues on the server.'); }
      schedule();
    }, 2500);
  }
  recheck.addEventListener('click', async () => { await refresh(); schedule(); }, { signal });
  showClear.addEventListener('change', filter, { signal });
  root.querySelectorAll<HTMLButtonElement>('[data-health-action]').forEach(button => {
    button.addEventListener('click', async () => {
      if (busy || isHealthJobActive(job)) return;
      busy = true; controls(); error.hidden = true;
      try { renderJob(await request<HealthJob>('/repair', button.dataset.healthAction as HealthAction)); }
      catch (failure) { if (!signal.aborted) fail(failure instanceof Error ? failure.message : 'Repair could not be started.'); }
      finally { busy = false; if (!signal.aborted) { controls(); schedule(); } }
    }, { signal });
  });
  try { const initial = JSON.parse(root.dataset.initial || 'null') as DataHealth | null; if (initial) renderJob(initial.job); } catch { /* Recheck recovers malformed initial state. */ }
  schedule();
  dispose = () => { controller.abort(); clearTimeout(timer); };
  document.addEventListener('astro:before-swap', dispose, { once: true, signal });
}
