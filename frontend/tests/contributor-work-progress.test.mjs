import test from 'node:test';
import assert from 'node:assert/strict';
import {workProgress} from '../src/lib/contributor-work-progress.ts';
test('unknown total never invents a denominator',()=>{
 assert.deepEqual(workProgress({pending:true},245),{current:245,total:null,pending:true});
 assert.deepEqual(workProgress({works_current:245,works_total:200,works_pending:true},0),{current:245,total:null,pending:true});
});
test('silent validation is independent of work progress',()=>{
 assert.deepEqual(workProgress({works_current:257,works_total:257,works_pending:false,pending:true},24),{current:257,total:257,pending:false});
});
