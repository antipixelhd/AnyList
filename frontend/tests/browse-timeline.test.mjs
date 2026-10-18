import test from 'node:test';
import assert from 'node:assert/strict';
import {timelineGroups,releaseGroup} from '../src/lib/browse-timeline-data.ts';
const monthName = (value) => new Intl.DateTimeFormat(undefined,{month:'long',timeZone:'UTC'}).format(new Date(`${value}-01T00:00:00Z`));
import {browseLayout,browseRequestParams,isCategoryView} from '../src/lib/browse-view.ts';

test('timeline requests retain every filter and use date ordering without mutating the grid sort',()=>{
  const values=new URLSearchParams('view=timeline&media_type=series&sort=score&genres=18&provider=8&region=DE&min_score=0&q=story&page=3&source=remote');
  const request=browseRequestParams(values,'2026-10-11');
  assert.equal(isCategoryView(values),false);assert.equal(request.get('sort'),'newest');assert.equal(request.get('end'),'2026-10-11');
  for(const key of ['media_type','genres','provider','region','min_score','q','page','source'])assert.equal(request.get(key),values.get(key));
  assert.equal(request.has('view'),false);assert.equal(values.get('sort'),'score');
  assert.equal(browseLayout(new URLSearchParams('sort=newest')),'grid');
  for(const query of ['view=timeline&status=upcoming','view=timeline&start=2027-01-01'])assert.equal(browseRequestParams(new URLSearchParams(query),'2026-10-11').has('end'),false);
  assert.equal(browseRequestParams(new URLSearchParams('view=timeline&end=2030-12-31'),'2026-10-11').get('end'),'2030-12-31');
});

test('release groups use real month boundaries and never invent dates for partial metadata',()=>{
  assert.equal(releaseGroup({title:'Leap day',release_date:'2024-02-29'}).label,monthName('2024-02'));
  assert.equal(releaseGroup({title:'Invalid day',release_date:'2025-02-29'}).label,'Month not listed');
  assert.equal(releaseGroup({title:'Partial',year:'2020'}).key,'2020-00');
  assert.equal(releaseGroup({title:'Missing'}).label,'Release date not listed');
});

test('multiple pages share month and year groups, ordered newest first with undated titles retained',()=>{
  const items=[{title:'Earlier',release_date:'2025-12-31'},{title:'October',release_date:'2026-10-01'},
    {title:'Later',release_date:'2026-10-10'},{title:'September',release_date:'2026-09-30'},
    {title:'Missing'},{title:'Partial',year:'2026'}];
  const groups=timelineGroups(items);
  assert.deepEqual(groups.map(year=>year.year),['2026','2025','']);
  assert.deepEqual(groups[0].months.map(month=>month.label),[monthName('2026-10'),monthName('2026-09'),'Month not listed']);
  assert.deepEqual(groups[0].months[0].items.map(item=>item.title),['Later','October']);
  assert.equal(groups.flatMap(year=>year.months.flatMap(month=>month.items)).length,items.length);
  assert.equal(items[0].title,'Earlier');
});


test('upcoming timeline shows nearest releases first and keeps equal-date provider order', () => {
  const request = browseRequestParams(new URLSearchParams('view=timeline&sort=upcoming&region=DE'), '2026-10-18');
  assert.equal(request.get('sort'), 'upcoming');
  assert.equal(request.has('end'), false);
  const entries = [{title:'Z',release_date:'2026-11-02'}, {title:'A',release_date:'2026-11-02'}, {title:'Next year',release_date:'2027-01-01'}];
  assert.deepEqual(timelineGroups(entries, true).flatMap(y => y.months.flatMap(m => m.items)).map(i=>i.title), ['Z','A','Next year']);
  assert.deepEqual(timelineGroups([...entries, {title:'Later page',release_date:'2026-11-02'}], true)[0].months[0].items.map(i=>i.title), ['Z','A','Later page']);
});
