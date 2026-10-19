import test from 'node:test';
import assert from 'node:assert/strict';
import {legalTextParts} from '../src/lib/legal-text.ts';

test('private disclosures can link provider policies while preserving surrounding text', () => {
  assert.deepEqual(legalTextParts('See [Policy](https://example.test/privacy).\nContact us.'), [
    {text:'See '},{text:'Policy',href:'https://example.test/privacy'},{text:'.\nContact us.'},
  ]);
});

test('HTML, unsafe schemes and credential-bearing URLs remain uninterpreted text', () => {
  for (const text of ['<script>alert(1)</script>', '[Bad](javascript:alert)', '[Bad](https://user:password@example.test)', '[Bad](https://[invalid)']) {
    assert.deepEqual(legalTextParts(text), [{text}]);
  }
});
