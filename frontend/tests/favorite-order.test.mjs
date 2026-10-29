import assert from 'node:assert/strict';
import test from 'node:test';
import {favoriteKey,favoriteOrderChanges,favoriteOrderSaved} from '../src/lib/favorite-order.ts';

test('mixed favorites keep overlapping game and Media IDs separate',()=>{
  assert.notEqual(favoriteKey('movie','1'),favoriteKey('game','1'));
  const saved=['media:1','media:2','game:1','game:2'];
  assert.deepEqual(favoriteOrderChanges(saved,saved),[]);
  assert.deepEqual(favoriteOrderChanges(['media:1','media:2','game:2','game:1'],saved),[
    {path:'/api/proxy/tracking/games/favorites/order',body:{work_ids:[2,1]}},
  ]);
  assert.deepEqual(favoriteOrderChanges(['media:2','media:1','game:1','game:2'],saved),[
    {path:'/api/proxy/tracking/favorites/order',body:{media_ids:[2,1]}},
  ]);
});

test('a partial save keeps the committed domain as the Cancel and retry baseline',()=>{
  const saved=['media:1','media:2','game:1','game:2'];
  const current=['media:2','media:1','game:2','game:1'];
  const [media,games]=favoriteOrderChanges(current,saved);
  const baseline=favoriteOrderSaved(saved,current,media.path);
  assert.deepEqual(baseline,['media:2','media:1','game:1','game:2']);
  assert.deepEqual(favoriteOrderChanges(current,baseline),[games]);
  assert.deepEqual(favoriteOrderSaved(baseline,current,games.path),current);
});
