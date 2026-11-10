import test from 'node:test';
import assert from 'node:assert/strict';
import {gameListWindow,gameListPages,GameListGeometry} from '../src/lib/game-list-window.ts';

test('virtual windows are bounded independently of complete library size',()=>{
  const window=gameListWindow(-80000,900,100000,80);
  assert(window.end-window.start<70);
  assert(window.start<1000&&window.end>1000);
  assert.deepEqual(gameListPages(1001,1050,100000),[1000]);
});

test('natural row heights position subsequent rows without changing table dimensions',()=>{
  const geometry=new GameListGeometry(56);
  assert.equal(geometry.offset(2000),112000);
  assert.equal(geometry.measure(2,74),true);
  assert.equal(geometry.offset(2),112);
  assert.equal(geometry.offset(3),186);
  assert.equal(geometry.offset(2000),112018);
  assert.equal(geometry.measure(2,74),false);
  geometry.measure(2,56);
  assert.equal(geometry.offset(2000),112000);
});

test('measured mobile wrapping preserves stable windows and complete scroll extent',()=>{
  const geometry=new GameListGeometry(68);
  geometry.measure(1,86);geometry.measure(5,104);
  assert.equal(geometry.offset(6),462);
  assert.deepEqual(geometry.window(-geometry.offset(1900),800,2000),{start:1882,end:1959});
  assert.deepEqual(geometry.window(10000,800,2000),{start:0,end:0});
});
test('direct bottom jumps acquire destination pages without traversing the library',()=>{
  const window=gameListWindow(-159100,900,2000,80);
  assert.equal(window.end,2000);
  assert.deepEqual(gameListPages(window.start,window.end,2000),[1900]);
  assert.deepEqual(gameListPages(450,450,2000),[]);
  assert.deepEqual(gameListPages(399,451,2000),[300,400]);
});
test('groups outside the viewport produce no rows and mobile uses its actual geometry',()=>{
  assert.deepEqual(gameListWindow(10000,900,2000,80),{start:0,end:0});
  assert.deepEqual(gameListWindow(-200000,900,2000,80),{start:2000,end:2000});
  const mobile=gameListWindow(-7400,800,2000,74);
  assert(mobile.start<100&&mobile.end>100);
});
