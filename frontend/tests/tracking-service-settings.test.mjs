import assert from "node:assert/strict";
import test from "node:test";
import { readTrackingServiceSettings } from "../src/lib/tracking-service-settings.ts";

function form(fields) {
  return { querySelector: selector => fields[selector] ?? null };
}

for (const [provider, name, credentials] of [
  ["trakt", "Trakt", ["client_id", "client_secret"]],
  ["simkl", "Simkl", ["client_id"]],
  ["mdblist", "MDBList", ["api_key"]],
  ["bingebase", "Bingebase", ["webhook_url", "api_key"]],
]) {
  test(`${name} saves only its own supported fields and retains unchecked preferences`, () => {
    const fields = Object.fromEntries(credentials.map(key => [`#${provider}_${key}`, { value: "test-value" }]));
    fields[`#${provider}_scrobble`] = { checked: false };
    fields[`#${provider}_push_watched`] = { checked: true };
    fields[`#${provider}_unknown`] = { checked: true };
    fields["#radarr_token"] = { value: "unrelated-secret" };
    fields[`[name="${provider}_auto_sync_interval"]`] = { value: "0.5" };
    const result = readTrackingServiceSettings(`save_${provider}_settings`, form(fields));
    assert.deepEqual(result, {
      name,
      body: {
        ...Object.fromEntries(credentials.map(key => [`${provider}_${key}`, "test-value"])),
        ...(provider === "bingebase" ? {} : { [`${provider}_auto_sync_interval`]: 0.5 }),
        [`${provider}_push_watched`]: true,
        [`${provider}_scrobble`]: false,
      },
    });
  });
}

test("blank credentials and scheduling selections clear settings without inventing checkbox values", () => {
  assert.deepEqual(readTrackingServiceSettings("save_trakt_settings", form({})), {
    name: "Trakt",
    body: { trakt_client_id: null, trakt_client_secret: null, trakt_auto_sync_interval: null },
  });
  const result = readTrackingServiceSettings("save_simkl_settings", form({
    '[name="simkl_auto_sync_interval"]': { value: "0" },
  }));
  assert.equal(result.body.simkl_auto_sync_interval, 0);
});

test("other page actions do not read or serialize settings", () => {
  const scope = { querySelector() { throw new Error("Unexpected form access"); } };
  for (const action of [null, "sync_trakt", "save_connection", "save_radarr_settings", "save_unknown_settings", "constructor"]) {
    assert.equal(readTrackingServiceSettings(action, scope), undefined);
  }
});
