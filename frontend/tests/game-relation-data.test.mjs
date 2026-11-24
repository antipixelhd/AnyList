import test from 'node:test';
import assert from 'node:assert/strict';
import {gameRelationCards} from '../src/lib/game-relation-data.ts';

function game(id,title,relation,extra={}) {
  return {id,title,relation,type:'game',url:`/games/${id}`,...extra};
}

test('game relations keep one row per game with the more specific relationship',()=>{
  const remaster=game(2,'Return to Arkham','remaster');
  const sequel=game(3,'Arkham City','sequel');
  assert.deepEqual(gameRelationCards([game(2,'Return to Arkham','version'),sequel,remaster,
    game(3,'Arkham City','series_member')]),[
    {work:remaster,gameIds:[2]}, {work:sequel,gameIds:[3]},
  ]);
});

test('same-title ports share a card without merging a separately identified remake',()=>{
  const port=game(4,'Arkham Asylum','port');
  const betterPort=game(5,' Arkham  Asylum ','port',{year:'2012',poster:'/cover.jpg'});
  const remake=game(6,'Arkham Asylum','remake');
  assert.deepEqual(gameRelationCards([port,betterPort,remake]),[
    {work:betterPort,gameIds:[4,5]}, {work:remake,gameIds:[6]},
  ]);
});
