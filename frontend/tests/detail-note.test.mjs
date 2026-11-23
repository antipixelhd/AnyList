import assert from 'node:assert/strict';
import test from 'node:test';
import {mountDetailNote} from '../src/lib/detail-note.ts';

function mount(write) {
  const controller = new AbortController();
  const view = new EventTarget();
  const ownerDocument = Object.assign(new EventTarget(), {defaultView:view});
  const input = Object.assign(new EventTarget(), {value:'Original',ownerDocument});
  const status = {textContent:''};
  const root = {querySelector: selector => selector === '#detail-note' ? input : status};
  const sync = mountDetailNote(root,write,controller.signal);
  const edit = value => {
    input.value = value;
    input.dispatchEvent(new Event('input'));
  };
  return {input,status,edit,sync,view,controller};
}

test('detail notes coalesce typing and keep a newer draft through store updates',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const writes = [];
  const note = mount(value => {
    const pending = Promise.withResolvers();
    writes.push({value,...pending});
    return pending.promise;
  });
  note.edit('First');
  t.mock.timers.tick(300);
  note.edit('First edit');
  t.mock.timers.tick(499);
  assert.equal(writes.length,0);
  t.mock.timers.tick(1);
  assert.equal(writes[0].value,'First edit');

  note.edit('Latest edit');
  note.sync('First edit');
  writes[0].resolve('First edit');
  await writes[0].promise;
  assert.equal(note.input.value,'Latest edit');
  assert.equal(note.status.textContent,'');
  note.controller.abort();
  assert.equal(writes[1].value,'Latest edit');
  writes[1].resolve('Latest edit');
  await writes[1].promise;
});

test('detail notes retain a failed draft and flush it on reconnect',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const attempts = [];
  const note = mount(async value => {
    attempts.push(value);
    if (attempts.length === 1) throw new Error('Offline');
    return value;
  });
  note.edit('Try again');
  t.mock.timers.tick(500);
  await Promise.resolve();
  assert.equal(note.status.textContent,'Note not saved');
  note.sync('Original');
  assert.equal(note.input.value,'Try again');
  note.view.dispatchEvent(new Event('online'));
  await Promise.resolve();
  assert.deepEqual(attempts,['Try again','Try again']);
  assert.equal(note.status.textContent,'Saved');
  note.controller.abort();
});
