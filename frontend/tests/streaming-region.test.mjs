import test from 'node:test';
import assert from 'node:assert/strict';
import { deviceStreamingCountry, streamingCountry, streamingRegionPreference } from '../src/lib/streaming-region.ts';

test('streaming country uses the actual country behind the device time zone', () => {
  assert.equal(deviceStreamingCountry('Europe/Berlin', ['en-US']), 'DE');
  assert.equal(deviceStreamingCountry('Europe/Vienna', ['de-DE']), 'AT');
  assert.equal(deviceStreamingCountry('Europe/Zurich', ['de-DE']), 'CH');
  assert.equal(deviceStreamingCountry('America/Unknown', ['fr-CA']), 'CA');
  assert.equal(deviceStreamingCountry('America/Unknown', ['de']), undefined);
});

test('streaming preference distinguishes a manual country from an automatic hint', () => {
  assert.deepEqual(streamingRegionPreference('auto.DE'), {country: 'DE', manual: false});
  assert.deepEqual(streamingRegionPreference('manual.US'), {country: 'US', manual: true});
  assert.equal(streamingRegionPreference('manual.123'), undefined);
  assert.equal(streamingCountry('DE'), 'DE');
  assert.equal(streamingCountry('Germany'), undefined);
});
