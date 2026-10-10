import assert from 'node:assert/strict';
import test from 'node:test';
import { metricTooltipData } from '../src/lib/stats-metric-tooltip.ts';

test('each X-axis tooltip includes every metric from the same underlying row', () => {
  const row = {key:'2025',label:'2025',titles:12,minutes:150,mean_score:8.25,rated_titles:10};
  const data = metricTooltipData('release_years',row);
  assert.equal(data.title,'Release year 2025');
  assert.deepEqual(data.values.map(item => [item.label,item.value]),[
    ['Titles watched','12'],['Hours watched','2.50'],['Mean score','8.25 / 10'],['Rated titles','10'],
  ]);
});

test('zero values stay visible and unrated points are not represented as score zero', () => {
  const row = {key:'8',label:'8',titles:0,minutes:0,mean_score:null,rated_titles:0};
  const data = metricTooltipData('scores',row);
  assert.equal(data.title,'Score (7.5, 8]');
  assert.deepEqual(data.values.map(item => item.value),['0','0.00','Unrated','0']);
});
