import test from 'node:test';
import assert from 'node:assert/strict';
import {gameListTitlePending} from '../src/lib/game-list-title.ts';
test('Planning masks missing technical names while usable names and other statuses stay visible',()=>{
 assert.equal(gameListTitlePending({status:'planning',title:'Steam 123'}),true);
 assert.equal(gameListTitlePending({status:'planning',title:''}),true);
 assert.equal(gameListTitlePending({status:'planning',title:'SteamWorld Dig'}),false);
 assert.equal(gameListTitlePending({status:'playing',title:'Steam 123'}),false);
 assert.equal(gameListTitlePending({status:'planning',title:'Real title'}),false);
});
