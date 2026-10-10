import assert from 'node:assert/strict';
import test from 'node:test';
import { metricTooltipData } from '../src/lib/stats-metric-tooltip.ts';

test('year tooltips include useful metrics without the rating count', () => {
  const row = {key:'2025',label:'2025',titles:12,minutes:150,mean_score:8.25,rated_titles:10};
  const data = metricTooltipData('release_years',row);
  assert.equal(data.title,'2025');
  assert.deepEqual(data.values.map(item => [item.label,item.value]),[
    ['Titles','12'],['Hours','2.5'],['Mean score','8.25'],
  ]);
});

test('zero values stay visible and unrated points are not represented as score zero', () => {
  const row = {key:'8',label:'8',titles:0,minutes:0,mean_score:null,rated_titles:0};
  const data = metricTooltipData('episode_counts',row);
  assert.equal(data.title,'8 episodes');
  assert.deepEqual(data.values.map(item => item.value),['0','0.0','Unrated']);
});

test('score tooltips retain the interval boundary and omit redundant score statistics', () => {
  const data = metricTooltipData('scores',{key:'8',label:'8',titles:3,minutes:120,mean_score:7.9,rated_titles:3});
  assert.equal(data.title,'Score >7.5–8');
  assert.deepEqual(data.values.map(item => item.metric),['titles','hours']);
});
