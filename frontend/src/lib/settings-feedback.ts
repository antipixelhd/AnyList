let timeout: ReturnType<typeof setTimeout> | undefined;
let message: HTMLElement | null = null;
let listening = false;

export function escapeHtml(value: unknown): string {
  return value == null ? '' : String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

function positionMessage() {
  if (!message || message.classList.contains('hidden')) return;
  const bottom = document.querySelector('nav')?.getBoundingClientRect().bottom ?? 0;
  message.style.top = `max(16px, calc(env(safe-area-inset-top, 0px) + 8px), ${bottom + 12}px)`;
}

export function showMessage(text: string, type: 'success' | 'error' = 'success'): void {
  if (!listening) {
    window.addEventListener('scroll', positionMessage, { passive: true });
    document.addEventListener('astro:before-swap', () => {
      clearTimeout(timeout);
      timeout = undefined;
      message = null;
    });
    listening = true;
  }
  message = document.getElementById('js-message');
  if (!message) return;
  message.textContent = text;
  message.className = `fixed right-4 z-[200] max-w-sm p-4 rounded-lg shadow-2xl border ${type === 'success'
    ? 'bg-zinc-900 border-green-500/50 text-green-400'
    : 'bg-zinc-900 border-red-500/50 text-red-400'}`;
  positionMessage();
  clearTimeout(timeout);
  timeout = setTimeout(() => message?.classList.add('hidden'), 4000);
}
