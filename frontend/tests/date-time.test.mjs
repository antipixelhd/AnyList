import assert from 'node:assert/strict';
import test from 'node:test';
import { calendarDate, dateInputPlaceholder, parseDateInput, formatDate, formatDateTime, formatTime, localToday, requestDateLocale, timestamp } from '../src/lib/date-time.ts';
import { DATE_REGIONS, browserDateRegion, dateRegion, deviceDateRegion } from '../src/lib/date-region.ts';

test('calendar dates follow German, US and British ordering with a complete year', () => {
  assert.equal(formatDate('2026-03-04', {locale:'de-DE'}), '04.03.2026');
  assert.equal(formatDate('2026-03-04', {locale:'en-US'}), '03/04/2026');
  assert.equal(formatDate('2026-03-04', {locale:'en-GB'}), '04/03/2026');
});

test('editable dates round-trip every configured region without changing the ISO calendar day', () => {
  for (const [locale] of DATE_REGIONS) {
    for (const iso of ['2022-12-19', '2024-02-29', '2026-03-04']) {
      const display = formatDate(iso, {locale});
      assert.equal(parseDateInput(display, {locale}), iso, `${locale}: ${display}`);
    }
  }
  assert.equal(dateInputPlaceholder({locale:'de-DE'}), 'DD.MM.YYYY');
  assert.equal(dateInputPlaceholder({locale:'en-US'}), 'MM/DD/YYYY');
  assert.equal(parseDateInput('19.12.2022', {locale:'de-DE'}), '2022-12-19');
  for (const value of ['31.02.2022','29.02.2023','19/12/2022','nonsense']) {
    assert.equal(parseDateInput(value, {locale:'de-DE'}), null);
  }
});

test('timeline month/day labels respect locale and omit the year without inventing missing days', () => {
  assert.equal(formatDateTime('2026-03-04','month-day',{locale:'de-DE'}),'4. März');
  assert.equal(formatDateTime('2026-03-04','month-day',{locale:'en-US'}),'March 4');
  assert.equal(formatDateTime('2026-03','month-day',{locale:'de-DE'}),'März');
  assert.equal(formatDateTime('2026','month-day',{locale:'de-DE'}),'');
  assert.equal(formatDateTime('2026-02-30','month-day',{locale:'de-DE'}),'');
});

test('calendar dates never shift with the viewer zone, including birthdays and leap days', () => {
  for (const timeZone of ['America/Los_Angeles','Pacific/Kiritimati','Europe/Berlin']) {
    assert.equal(formatDate('2024-02-29', {locale:'de-DE',timeZone}), '29.02.2024');
  }
  for (const value of ['2025-02-29','2026-04-31','2026-00-12','not a date','']) assert.equal(calendarDate(value),null);
  assert.equal(formatDate(null), '');
  assert.equal(formatDate('1965'), '1965');
  assert.equal(formatDate('2026-03', {locale:'de-DE'}), 'März 2026');
  assert.equal(formatDate('2026-03-04T00:00:00Z', {locale:'de-DE',timeZone:'America/Los_Angeles'}), '04.03.2026');
});

test('timestamps preserve explicit offsets and normalize naive API timestamps as UTC', () => {
  assert.equal(timestamp('2026-10-12T20:30:00')?.toISOString(), '2026-10-12T20:30:00.000Z');
  assert.equal(timestamp('2026-10-12 20:30:00.123')?.toISOString(), '2026-10-12T20:30:00.123Z');
  assert.equal(timestamp('2026-10-12T20:30:00+02:00')?.toISOString(), '2026-10-12T18:30:00.000Z');
  assert.equal(timestamp('2026-10-12T20:30:00-0700')?.toISOString(), '2026-10-13T03:30:00.000Z');
  for (const value of [null,'unknown','2026-02-30T12:00:00Z','2026-10-12',NaN]) assert.equal(timestamp(value),null);
});

test('clock preference follows locale, including explicit Unicode hour cycle preferences', () => {
  const value='2026-10-12T20:30:00Z';
  assert.equal(formatTime(value,{locale:'de-DE',timeZone:'UTC'}),'20:30');
  assert.match(formatTime(value,{locale:'en-US',timeZone:'UTC'}),/^8:30\sPM$/);
  assert.equal(formatTime(value,{locale:'en-US-u-hc-h23',timeZone:'UTC'}),'20:30');
  assert.match(formatTime(value,{locale:'de-DE-u-hc-h12',timeZone:'UTC'}),/^8:30\sPM$/);
});

test('real instants use local dates and daylight saving rather than a fixed UTC clock', () => {
  assert.equal(formatDateTime('2026-10-12T00:30:00Z','timestamp-date',{locale:'de-DE',timeZone:'America/Los_Angeles'}),'11.10.2026');
  const context={locale:'de-DE',timeZone:'Europe/Berlin'};
  assert.equal(formatTime('2026-03-29T00:30:00Z',context),'1:30');
  assert.equal(formatTime('2026-03-29T01:30:00Z',context),'3:30');
  assert.equal(formatTime('2026-10-25T00:30:00Z',context),'2:30');
  assert.equal(formatTime('2026-10-25T01:30:00Z',context),'2:30');
});

test('formatter uses the browser/runtime default and keeps contexts isolated', () => {
  const date=new Date('2026-03-04T00:00:00Z');
  const expected=new Intl.DateTimeFormat(undefined,{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'UTC'}).format(date);
  assert.equal(formatDate('2026-03-04'),expected);
  formatDate('2026-03-04',{locale:'en-US'});
  assert.equal(formatDate('2026-03-04',{locale:'de-DE'}),'04.03.2026');
  assert.equal(formatDate('2026-03-04'),expected);
});

test('SSR accepts supported request locales; local input dates keep the local calendar day', () => {
  assert.equal(requestDateLocale('de-DE,de;q=0.9,en;q=0.8'),'de-DE');
  assert.equal(requestDateLocale('*, not_valid, en-GB'),'en-GB');
  assert.equal(requestDateLocale(null),undefined);
  const now=new Date(2026,9,12,0,5);
  assert.equal(localToday(now),'2026-10-12');
  assert.equal(now.getHours(),0);
});

test('saved region overrides browser language during SSR and ignores invalid preferences', () => {
  assert.equal(requestDateLocale('en-US,en;q=0.9', 'de-DE'), 'de-DE');
  assert.equal(requestDateLocale('en-US', 'invalid'), 'en-US');
  assert.equal(requestDateLocale(null, 'de-DE'), 'de-DE');
  assert.equal(dateRegion(''), undefined);
  assert.equal(dateRegion('en-US<script>'), undefined);
});

test('browser region applies across dates, times and months and resetting restores the default', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const browser = {cookie:'other=value; anylist_date_region=de-DE'};
  Object.defineProperty(globalThis, 'document', {value:browser, configurable:true});
  try {
    assert.equal(browserDateRegion(), 'de-DE');
    assert.equal(formatDate('2014-07-30'), '30.07.2014');
    assert.equal(formatDateTime('2026-10', 'month'), 'Oktober 2026');
    assert.equal(formatTime('2026-10-13T18:30:00Z', {timeZone:'UTC'}), '18:30');
    assert.equal(formatDate('2014-07-30', {locale:'en-US'}), '07/30/2014');
    assert.equal(formatDate('2026-10-13', {timeZone:'America/Los_Angeles'}), '13.10.2026');
    browser.cookie = 'anylist_date_region=en-GB';
    assert.equal(formatDate('2014-07-30'), '30/07/2014');
    for (const cookie of ['', 'anylist_date_region=invalid', 'prefix_anylist_date_region=de-DE']) {
      browser.cookie = cookie;
      assert.equal(browserDateRegion(), undefined);
      assert.equal(formatDate('2014-07-30'), new Intl.DateTimeFormat(deviceDateRegion(),{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'UTC'}).format(new Date('2014-07-30T00:00:00Z')));
    }
  } finally {
    if (original) Object.defineProperty(globalThis, 'document', original);
    else delete globalThis.document;
  }
});

test('automatic numeric dates follow the device zone without changing month language or an explicit choice', () => {
  assert.equal(deviceDateRegion('Europe/Berlin'), 'de-DE');
  assert.equal(deviceDateRegion('America/Unknown'), undefined);
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const NativeDateTimeFormat = Intl.DateTimeFormat;
  const browser = {cookie: ''};
  Object.defineProperty(globalThis, 'document', {value:browser, configurable:true});
  Intl.DateTimeFormat = class extends NativeDateTimeFormat {
    resolvedOptions() { return {...super.resolvedOptions(), timeZone:'Europe/Berlin'}; }
  };
  try {
    assert.equal(formatDate('2026-10-17'), '17.10.2026');
    assert.equal(formatDateTime('2026-10-17T18:30:00Z', 'datetime', {timeZone:'UTC'}), '17.10.2026, 18:30');
    assert.equal(formatDateTime('2026-10', 'month'), new NativeDateTimeFormat(undefined,{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date('2026-10-01T00:00:00Z')));
    browser.cookie = 'anylist_date_region=en-GB';
    assert.equal(formatDate('2026-10-17'), '17/10/2026');
  } finally {
    Intl.DateTimeFormat = NativeDateTimeFormat;
    if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument);
    else delete globalThis.document;
  }
});
