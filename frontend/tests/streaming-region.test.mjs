import test from 'node:test';
import assert from 'node:assert/strict';
import { deviceStreamingCountry, streamingCountry, streamingRegionPreference, searchStreamingCountry, rememberDeviceRegion } from '../src/lib/streaming-region.ts';

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

test('search honors the account country before automatic device hints', () => {
  assert.equal(searchStreamingCountry('DE',streamingRegionPreference('auto.US'),'US'),'DE');
  assert.equal(searchStreamingCountry('DE',streamingRegionPreference('manual.AT'),'US'),'AT');
  assert.equal(searchStreamingCountry(null,streamingRegionPreference('auto.US'),'DE'),'US');
  assert.equal(searchStreamingCountry(null,undefined,'DE'),'DE');
});

test('ordinary navigation remembers a device hint but never overwrites a manual country', () => {
  const previousDocument=globalThis.document, previousLocation=globalThis.location;
  const writes=[];
  globalThis.document={set cookie(value){writes.push(value);}};
  globalThis.location={protocol:'https:'};
  try {
    assert.deepEqual(rememberDeviceRegion('','DE'),{country:'DE',manual:false});
    assert.match(writes[0],/^anylist_streaming_region=auto.DE;/);
    assert.ok(writes[0].endsWith('; Secure'));
    assert.deepEqual(rememberDeviceRegion('anylist_streaming_region=manual.JP','DE'),{country:'JP',manual:true});
    assert.deepEqual(rememberDeviceRegion('anylist_streaming_region=auto.DE','DE'),{country:'DE',manual:false});
    assert.equal(writes.length,1);
  } finally {
    globalThis.document=previousDocument;globalThis.location=previousLocation;
  }
});
