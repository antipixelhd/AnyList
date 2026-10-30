import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');

test('cold list destinations reuse actual filter controls with one progress indicator and no invented rows',()=>{
  const template=read('../src/components/ProfileListLoading.astro');
  assert.match(template,/<ProfileListFilters/);
  assert.match(template,/kind==='games'\?gameStatuses:statuses/);
  assert.match(template,/profile-list-loading-filters" inert/);
  assert.equal((template.match(/role="progressbar"/g)||[]).length,1);
  assert.doesNotMatch(template,/entry-row|skeleton|<img/);
  assert.match(template,/prefers-reduced-motion:reduce/);
  for(const route of ['[section].astro','games.astro','stats.astro','stats/games.astro'])assert.match(read('../src/pages/user/[username]/'+route),/<ProfileListLoading\/>/);
});
