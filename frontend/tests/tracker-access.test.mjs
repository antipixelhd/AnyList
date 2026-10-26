import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';

const source=readFileSync(new URL('../src/lib/tracker.ts',import.meta.url),'utf8')
  .replace('"./responsive-artwork"',JSON.stringify(new URL('../src/lib/responsive-artwork.ts',import.meta.url).href))
  .replace("'./date-time'",JSON.stringify(new URL('../src/lib/date-time.ts',import.meta.url).href))
  .replaceAll("'./tracking-statuses.ts'",JSON.stringify(new URL('../src/lib/tracking-statuses.ts',import.meta.url).href));
const {tracker,trackerFailure,TrackerRequestError}=await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);

test('real request failures retain HTTP access status independently of message text',async t=>{
  for(const status of [401,403,404,429,500,503]){
    const request=t.mock.method(globalThis,'fetch',async()=>new Response('Private upstream diagnostic',{status}));
    try{
      await assert.rejects(()=>tracker('games/users/private/statistics','fixture-token'),error=>{
        assert.ok(error instanceof TrackerRequestError);assert.equal(error.status,status);
        error.message='Display wording may change';
        assert.deepEqual(trackerFailure(error),{message:'Display wording may change',status:[401,403,404].includes(status)?status:503});
        return true;
      });
      assert.equal(request.mock.callCount(),1);
    }finally{request.mock.restore();}
  }
});

test('successful profile requests keep payload and token forwarding unchanged',async t=>{
  const value={profile:{owner:true},results:[{id:7}]};
  const request=t.mock.method(globalThis,'fetch',async()=>Response.json(value));
  assert.deepEqual(await tracker('games/users/me/list','fixture-token'),value);
  assert.equal(request.mock.calls[0].arguments[1].headers.Authorization,'Bearer fixture-token');
  assert.equal(request.mock.callCount(),1);
});

test('network failures become unavailable pages rather than successful empty profiles',async t=>{
  t.mock.method(globalThis,'fetch',async()=>{throw new TypeError('fetch failed');});
  try{await tracker('people/private');assert.fail('Request must fail');}
  catch(error){assert.deepEqual(trackerFailure(error),{message:'fetch failed',status:503});}
  assert.equal(trackerFailure(new Error('This profile is private.')).status,503);
  assert.equal(trackerFailure(null).status,503);
});
