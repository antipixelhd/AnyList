import assert from 'node:assert/strict';
import test from 'node:test';
import {POST} from '../src/pages/api/csp-report.ts';

test('CSP reports retain only a known directive, never page data or samples',async t=>{
  const logs=[];t.mock.method(console,'info',(...args)=>logs.push(args));
  const request=new Request('https://example.test/api/csp-report',{method:'POST',body:JSON.stringify({'csp-report':{'effective-directive':'script-src-elem','document-uri':'https://example.test/?token=private','script-sample':'private'}})});
  const response=await POST({request});
  assert.equal(response.status,204);
  assert.deepEqual(logs,[['CSP report-only','script-src-elem']]);
});

test('CSP reporting accepts Reporting API batches and bounds oversized/malformed bodies',async t=>{
  t.mock.method(console,'info',()=>{});
  const send=body=>POST({request:new Request('https://example.test/api/csp-report',{method:'POST',body})});
  assert.equal((await send(JSON.stringify([{body:{effectiveDirective:'script-src-attr'}}]))).status,204);
  assert.equal((await send('{')).status,400);
  assert.equal((await send('x'.repeat(16385))).status,413);
});
