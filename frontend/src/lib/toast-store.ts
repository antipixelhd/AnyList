export type ToastKind = 'success' | 'error' | 'info' | 'warning';
export type ToastOptions = {
  message: string; kind?: ToastKind; key?: string; duration?: number;
  action?: {label: string; run: () => void | Promise<void>};
};
export type Toast = ToastOptions & {id: number; kind: ToastKind};
type Entry = {toast: Toast; remaining: number; started: number; timer?: ReturnType<typeof setTimeout>; pauses: Set<string>};
type Schedule = (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
type Cancel = (timer: ReturnType<typeof setTimeout>) => void;

/** Bounded, deduplicated feedback with pausable expiry and one-shot actions. */
export class ToastStore {
  private entries = new Map<number, Entry>();
  private subscribers = new Set<(toasts: Toast[]) => void>();
  private sequence = 0;
  private clock: typeof Date.now;
  private schedule: Schedule;
  private cancel: Cancel;
  constructor(clock = Date.now, schedule: Schedule = (callback,delay)=>setTimeout(callback,delay), cancel: Cancel = timer=>clearTimeout(timer)) {
    this.clock=clock; this.schedule=schedule; this.cancel=cancel;
  }
  get toasts() { return [...this.entries.values()].map(entry => entry.toast); }
  subscribe(listener: (toasts: Toast[]) => void) {
    this.subscribers.add(listener); listener(this.toasts);
    return () => { this.subscribers.delete(listener); };
  }
  private emit() { this.subscribers.forEach(listener => listener(this.toasts)); }
  private start(entry: Entry) {
    entry.started = this.clock();
    if (entry.remaining > 0 && !entry.pauses.size) {
      entry.timer = this.schedule(() => this.dismiss(entry.toast.id), entry.remaining);
    }
  }
  show(options: ToastOptions) {
    if (!options.message.trim()) return null;
    const kind = options.kind || 'info';
    const existing = [...this.entries.values()].find(entry => options.key
      ? entry.toast.key === options.key : entry.toast.message === options.message && entry.toast.kind === kind);
    if (existing?.timer) this.cancel(existing.timer);
    const id = existing?.toast.id ?? ++this.sequence;
    const entry: Entry = {toast:{...options, id, kind}, remaining:Math.max(0, options.duration ?? (kind === 'error' ? 8000 : 4500)),
      started:this.clock(), pauses:existing?.pauses || new Set()};
    this.entries.set(id, entry);
    while (this.entries.size > 4) this.dismiss(this.entries.keys().next().value!);
    this.start(entry); this.emit(); return id;
  }
  dismiss(id: number) {
    const entry = this.entries.get(id);
    if (!entry) return;
    if (entry.timer) this.cancel(entry.timer);
    this.entries.delete(id); this.emit();
  }
  pause(id: number, reason = 'pointer') {
    const entry = this.entries.get(id);
    if (!entry || entry.pauses.has(reason)) return;
    if (!entry.pauses.size && entry.timer) {
      this.cancel(entry.timer); entry.timer = undefined;
      entry.remaining = Math.max(1, entry.remaining - (this.clock() - entry.started));
    }
    entry.pauses.add(reason);
  }
  resume(id: number, reason = 'pointer') {
    const entry = this.entries.get(id);
    if (entry?.pauses.delete(reason) && !entry.pauses.size) this.start(entry);
  }
  async act(id: number) {
    const action = this.entries.get(id)?.toast.action;
    this.dismiss(id);
    await action?.run();
  }
  clear() { [...this.entries.keys()].forEach(id => this.dismiss(id)); }
}
