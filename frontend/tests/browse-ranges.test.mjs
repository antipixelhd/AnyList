import test from 'node:test';
import assert from 'node:assert/strict';
import { releaseYearBounds, releaseYearDates } from '../src/lib/browse-ranges.ts';

test('year sliders show selected dates without changing precise category boundaries', () => {
  assert.deepEqual(releaseYearBounds('2026-10-01', '2026-12-31', 1900, 2029), {lower: 2026, upper: 2026});
  assert.deepEqual(releaseYearBounds('', '', 1900, 2029), {lower: 1900, upper: 2029});
  assert.deepEqual(releaseYearBounds('1888-01-01', '2035-12-31', 1900, 2029), {lower: 1900, upper: 2029});
});

test('moving the year handles selects whole inclusive years, with open ends at the limits', () => {
  assert.deepEqual(releaseYearDates(2000, 2020, 1900, 2029), {start: '2000-01-01', end: '2020-12-31'});
  assert.deepEqual(releaseYearDates(1900, 2029, 1900, 2029), {start: '', end: ''});
  assert.deepEqual(releaseYearDates(2024, 2024, 1900, 2029), {start: '2024-01-01', end: '2024-12-31'});
});
