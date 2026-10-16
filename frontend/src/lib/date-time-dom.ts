import { formatDateTime, isDateTimeStyle, timestamp, type DateTimeStyle } from './date-time.ts';

const selector = '[data-local-date-time],time[data-relative-time]';
export function localizeDateTimes(root: ParentNode) {
  const elements = [...root.querySelectorAll<HTMLElement>(selector)];
  if (root instanceof HTMLElement && root.matches(selector)) elements.unshift(root);
  for (const element of elements) {
    const raw = element.getAttribute('datetime') || element.dataset.dateValue || '';
    if (element.matches('time[data-relative-time]')) {
      const exact = formatDateTime(raw, 'datetime');
      if (element.title !== exact) element.title = exact;
      continue;
    }
    const requested = element.dataset.localDateTime || 'date';
    const style = isDateTimeStyle(requested) ? requested : 'date';
    const label = formatDateTime(raw, style) || element.dataset.dateFallback || '';
    const text = `${element.dataset.datePrefix || ''}${label}`;
    if (element.textContent !== text) element.textContent = text;
    if (style === 'time' || style === 'timestamp-date' || style === 'datetime') {
      const exact = formatDateTime(raw, 'datetime');
      if (element.title !== exact) element.title = exact;
    }
  }
}

/** Mark changed values explicitly, including nodes inserted by optimistic/live views. */
export function setLocalDateTime(element: HTMLElement, value: string | null | undefined, style: DateTimeStyle = 'date', fallback = '') {
  element.dataset.localDateTime = style;
  element.dataset.dateFallback = fallback;
  const normalized = style === 'time' || style === 'datetime' || style === 'timestamp-date' ? timestamp(value)?.toISOString() || '' : value || '';
  if (element instanceof HTMLTimeElement) element.dateTime = normalized;
  else element.dataset.dateValue = normalized;
  localizeDateTimes(element);
}

/** Observe only inserted subtrees and date attributes; never rescan on unrelated text updates. */
export function mountDateTimes(root: HTMLElement, signal: AbortSignal) {
  if (signal.aborted) return;
  localizeDateTimes(root);
  const observer = new MutationObserver(records => {
    const roots = new Set<Element>();
    for (const record of records) {
      if (record.type === 'attributes') roots.add(record.target as Element);
      else for (const node of record.addedNodes) if (node instanceof Element) roots.add(node);
    }
    for (const node of roots) {
      if (!node.isConnected) continue;
      let ancestor = node.parentElement;
      while (ancestor && !roots.has(ancestor)) ancestor = ancestor.parentElement;
      if (!ancestor) localizeDateTimes(node);
    }
  });
  observer.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['datetime', 'data-date-value', 'data-local-date-time'] });
  signal.addEventListener('abort', () => observer.disconnect(), { once: true });
}
