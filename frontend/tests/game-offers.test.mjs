import test from 'node:test';
import assert from 'node:assert/strict';
import {deviceStoreCountry,rememberDeviceCountry} from '../src/lib/store-country.ts';
import {selectedGameOffer,offerLink,offerRegionMatches} from '../src/lib/game-offer-data.ts';
import {moneyLabel} from '../src/lib/media-detail-data.ts';

test('store-country hint uses the existing device-country rules, independently of date regions and browse overrides',()=>{
  assert.equal(deviceStoreCountry('Europe/Vienna',['de-DE']),'AT');
  assert.equal(deviceStoreCountry('Europe/Zurich',['en-US']),'CH');
  assert.equal(deviceStoreCountry('Unknown/Zone',['fr-CA']),'CA');
  assert.equal(deviceStoreCountry('Unknown/Zone',['en']),'US');
  const previousDocument=globalThis.document,previousLocation=globalThis.location,writes=[];
  globalThis.document={set cookie(value){writes.push(value);}};globalThis.location={protocol:'https:'};
  try {
    assert.equal(rememberDeviceCountry('anylist_date_region=en-US; anylist_streaming_region=manual.JP','AT'),'AT');
    assert.match(writes[0],/^anylist_device_country=AT;/);assert.ok(writes[0].endsWith('; Secure'));
    rememberDeviceCountry('anylist_device_country=AT','AT');assert.equal(writes.length,1);
  } finally {globalThis.document=previousDocument;globalThis.location=previousLocation;}
});

test('regional offer selection refuses another country or app and never shows unresolved SSR fallback',()=>{
  const row={steam_app_id:'123',country:'DE',current:{currency:'EUR',amountInt:799}};
  const data={country:'DE',country_source:'account',steam_app_id:'123',offers:[{...row,country:'US'}, {...row,steam_app_id:'999'},row]};
  assert.equal(selectedGameOffer(data),row);
  assert.equal(selectedGameOffer({...data,country_source:'fallback'},true),null);
  assert.equal(selectedGameOffer({...data,steam_app_id:null}),null);
  assert.equal(selectedGameOffer({...data,offers:[{...row,current:null}]}),null);
  assert.equal(moneyLabel(selectedGameOffer({...data,offers:[{...row,current:{currency:'USD',amountInt:0}}]}).current),'$0.00');
});

test('offer links use HTTPS without credentials',()=>{
  assert.equal(offerLink('javascript:alert(1)'),null);assert.equal(offerLink('https://user:pass@example.test'),null);
  assert.equal(offerLink('https://store.steampowered.com/app/123/'),'https://store.steampowered.com/app/123/');
});

test('warm SSR offers stay visible through initialization while an obsolete device region stays hidden',()=>{
  assert.equal(offerRegionMatches('account','DE','US'),true);
  assert.equal(offerRegionMatches('device','DE','DE'),true);
  assert.equal(offerRegionMatches('device','US','DE'),false);
  assert.equal(offerRegionMatches('fallback','US','US'),false);
});
