import assert from 'node:assert/strict';
import test from 'node:test';
import { rankedStaff, personTitleLabel } from '../src/lib/stats-staff-data.ts';
import { personAge, personDate } from '../src/lib/person-page-data.ts';

test('staff ranks by selected metric and only uses creative prominence to break ties', () => {
  const rows = Array.from({length:40}, (_, i) => ({key:String(i), label:`Person ${i}`, titles:1, mean_score:i/4, minutes:40-i, prominence:i===39?0:8}));
  assert.equal(rankedStaff(rows,'count').length,30);
  assert.equal(rankedStaff(rows,'count')[0].key,'39');
  assert.equal(rankedStaff(rows,'time')[0].key,'0');
  assert.equal(rankedStaff(rows,'mean_score')[0].key,'39');
  assert.equal(rows[0].key,'0');
});
test('people tooltips retain crew jobs and actor characters', () => {
  assert.equal(personTitleLabel({title:'Film', roles:['Director','Writer']}),'Film – Director / Writer');
  assert.equal(personTitleLabel({title:'Film', character:'Alice'}),'Film – Alice');
  assert.equal(personTitleLabel({title:'Film', roles:[]}),'Film');
});
test('person ages respect birthdays, deaths, and invalid or future dates', () => {
  const today = new Date('2026-10-10T12:00:00Z');
  assert.equal(personAge('1970-10-11',null,today),55);
  assert.equal(personAge('1970-10-10',null,today),56);
  assert.equal(personAge('1970-10-10','2020-10-09',today),49);
  for (const birth of [null,'1970-02-30','2030-01-01','unknown']) assert.equal(personAge(birth,null,today),null);
  assert.equal(personAge('1970-01-01','2027-01-01',today),null);
  assert.equal(personAge('1970-01-01','invalid',today),null);
  assert.equal(personDate('1970-02-30'),null);
  assert.equal(personDate('1970-01-01'),'January 1, 1970');
});
