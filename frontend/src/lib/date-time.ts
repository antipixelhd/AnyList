import { browserDateRegion, dateRegion, deviceDateRegion } from './date-region.ts';

/** Shared display/input conversion: API values and native picker values remain ISO. */
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

function dateFormatter(style: DateTimeStyle, context: DateTimeContext = {}) {
  const calendar = style === 'date' || style.startsWith('month');
  const options = { ...styles[style], calendar:'gregory', ...(calendar ? { timeZone: 'UTC' } : context.timeZone ? { timeZone: context.timeZone } : {}) };
  const textual = style.startsWith('month');
  const locale = context.locale ?? browserDateRegion() ?? (typeof document !== 'undefined' && !textual ? deviceDateRegion() : undefined);
  const key = JSON.stringify([locale ?? null, options]);
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, options);
    if (formatters.size >= 32) formatters.delete(formatters.keys().next().value!);
    formatters.set(key, formatter);
  }
  return formatter;
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
  return dateFormatter(style, context).format(date);
}

const cleanDateText = (value: string) => value.replace(/[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '');
const escapePattern = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function dateInputPlaceholder(context: DateTimeContext = {}): string {
  return cleanDateText(dateFormatter('date', context).formatToParts(new Date('2006-11-22T00:00:00Z'))
    .map(part => (({day:'DD',month:'MM',year:'YYYY'} as Record<string,string>)[part.type] ?? part.value)).join(''));
}

/** Parse the same localized ordering and separators that formatDate displays. */
export function parseDateInput(value: string, context: DateTimeContext = {}): string | null {
  if (calendarDate(value)) return value;
  const formatter = dateFormatter('date', context);
  const numbers = new Intl.NumberFormat(formatter.resolvedOptions().locale, {useGrouping:false});
  const digits = new Map(Array.from({length:10}, (_, digit) => [numbers.format(digit), String(digit)]));
  const normalized = [...cleanDateText(value).trim()].map(character => digits.get(character) ?? character).join('');
  const order: string[] = [];
  const pattern = formatter.formatToParts(new Date('2006-11-22T00:00:00Z')).map(part => {
    if (part.type === 'day' || part.type === 'month' || part.type === 'year') {
      order.push(part.type);
      return part.type === 'year' ? '(\\d{4})' : '(\\d{1,2})';
    }
    return cleanDateText(part.value).split(/\s+/).map(escapePattern).join('\\s*');
  }).join('');
  const match = normalized.match(new RegExp(`^${pattern}$`));
  if (!match) return null;
  const fields = Object.fromEntries(order.map((field, index) => [field, match[index + 1].padStart(field === 'year' ? 4 : 2, '0')]));
  const iso = `${fields.year}-${fields.month}-${fields.day}`;
  return calendarDate(iso) ? iso : null;
}

export const formatDate = (value: string | null | undefined, context?: DateTimeContext) => formatDateTime(value, 'date', context);
export const formatTime = (value: string | number | Date | null | undefined, context?: DateTimeContext) => formatDateTime(value, 'time', context);

/** UTC instants and local calendar "today" must not be confused when editing dates. */
export function localToday(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

/** SSR can know the preferred language, but only the browser knows its time zone. */
export function requestDateLocale(acceptLanguage: string | null, region?: string): string | undefined {
  const preference = dateRegion(region);
  if (preference) return preference;
  const locales = (acceptLanguage || '').split(',').map(part => part.split(';')[0].trim());
  for (const locale of locales) {
    try { if (locale && Intl.DateTimeFormat.supportedLocalesOf([locale]).length) return locale; } catch {}
  }
  return undefined;
}

export function clearDateTimeFormatters() { formatters.clear(); }
