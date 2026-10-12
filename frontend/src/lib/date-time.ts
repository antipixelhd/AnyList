/** Display only: API values and native date/datetime inputs remain ISO. */
export type DateTimeStyle = 'date' | 'timestamp-date' | 'time' | 'datetime' | 'month' | 'month-short' | 'month-name' | 'month-name-short' | 'month-day';
export type DateTimeContext = { locale?: string | string[]; timeZone?: string };
const formatters = new Map<string, Intl.DateTimeFormat>();
const styles: Record<DateTimeStyle, Intl.DateTimeFormatOptions> = {
  date: { day: '2-digit', month: '2-digit', year: 'numeric' },
  'timestamp-date': { day: '2-digit', month: '2-digit', year: 'numeric' },
  time: { hour: 'numeric', minute: '2-digit' },
  datetime: { day: '2-digit', month: '2-digit', year: 'numeric', hour: 'numeric', minute: '2-digit' },
  month: { month: 'long', year: 'numeric' },
  'month-short': { month: 'short', year: '2-digit' },
  'month-name': { month: 'long' },
  'month-name-short': { month: 'short' },
  'month-day': { month: 'long', day: 'numeric' },
};
export const isDateTimeStyle = (value: string): value is DateTimeStyle => Object.hasOwn(styles, value);

/** Calendar dates have no zone. Validate rather than rolling February 30 into March. */
export function calendarDate(value: string | null | undefined): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
}

/** The API's timezone-less timestamps are UTC; explicit offsets must be preserved. */
export function timestamp(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === '') return null;
  let date: Date;
  if (typeof value === 'string') {
    if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value) || !calendarDate(value.slice(0, 10))) return null;
    const iso = value.replace(' ', 'T');
    date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
  } else date = new Date(value instanceof Date ? value.getTime() : value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function formatDateTime(value: string | number | Date | null | undefined, style: DateTimeStyle = 'date', context: DateTimeContext = {}): string {
  const calendar = style === 'date' || style.startsWith('month');
  if (style === 'date' && typeof value === 'string' && /^\d{4}$/.test(value)) return value;
  if (style === 'date' && typeof value === 'string' && /^\d{4}-\d{2}$/.test(value)) return formatDateTime(value, 'month', context);
  const source = typeof value === 'string' ? value : '';
  const raw = calendar && source.length > 10 && timestamp(source) ? source.slice(0,10) : source;
  if (style === 'month-day' && /^\d{4}-\d{2}$/.test(raw)) return formatDateTime(raw, 'month-name', context);
  const date = calendar ? calendarDate(style.startsWith('month') && /^\d{4}-\d{2}$/.test(raw) ? `${raw}-01` : raw) : timestamp(value);
  if (!date) return '';
  // UTC here preserves a calendar day; real instants use the viewer's zone.
  const options = { ...styles[style], ...(calendar ? { timeZone: 'UTC' } : context.timeZone ? { timeZone: context.timeZone } : {}) };
  const key = JSON.stringify([context.locale ?? null, options]);
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(context.locale, options);
    if (formatters.size >= 32) formatters.delete(formatters.keys().next().value!);
    formatters.set(key, formatter);
  }
  return formatter.format(date);
}

export const formatDate = (value: string | null | undefined, context?: DateTimeContext) => formatDateTime(value, 'date', context);
export const formatTime = (value: string | number | Date | null | undefined, context?: DateTimeContext) => formatDateTime(value, 'time', context);

/** UTC instants and local calendar "today" must not be confused when editing dates. */
export function localToday(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

/** SSR can know the preferred language, but only the browser knows its time zone. */
export function requestDateLocale(acceptLanguage: string | null): string | undefined {
  const locales = (acceptLanguage || '').split(',').map(part => part.split(';')[0].trim());
  for (const locale of locales) {
    try { if (locale && Intl.DateTimeFormat.supportedLocalesOf([locale]).length) return locale; } catch {}
  }
  return undefined;
}

export function clearDateTimeFormatters() { formatters.clear(); }
