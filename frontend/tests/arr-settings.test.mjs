import assert from "node:assert/strict";
import test from "node:test";
import {
  arrSettings,
  fetchArrProfiles,
  renderArrProfiles,
} from "../src/lib/arr-settings.ts";

function form(provider, fields = {}, tags = []) {
  return {
    querySelector(selector) {
      return fields[selector.replace(`#${provider}_`, "")] ?? null;
    },
    querySelectorAll(selector) {
      assert.equal(
        selector,
        `#${provider}_tags_list input[name="${provider}_tags"]`,
      );
      return tags;
    },
  };
}

for (const provider of ["radarr", "sonarr"]) {
  test(`${provider} serializes selected settings without including unchecked tags`, () => {
    const scope = form(
      provider,
      {
        url: { value: "http://local-service:8989" },
        token: { value: "test-secret" },
        root_folder: { value: "/media" },
        quality_profile: { value: "12" },
        customize_on_add: { checked: true },
        season_folder: { checked: false },
      },
      [
        { type: "hidden", value: "3" },
        { type: "checkbox", value: "7", checked: true },
        { type: "checkbox", value: "9", checked: false },
      ],
    );
    assert.deepEqual(arrSettings(provider, scope), {
      [`${provider}_url`]: "http://local-service:8989",
      [`${provider}_token`]: "test-secret",
      [`${provider}_root_folder`]: "/media",
      [`${provider}_quality_profile`]: 12,
      [`${provider}_tags`]: [3, 7],
      [`${provider}_customize_on_add`]: true,
      ...(provider === "sonarr" ? { sonarr_season_folder: false } : {}),
    });
  });

  test(`${provider} tests credentials before requesting profiles`, async () => {
    const calls = [];
    const credentials = { url: "http://local-service", token: "test-token" };
    const profiles = { root_folders: [], quality_profiles: [], tags: [] };
    const result = await fetchArrProfiles(
      provider,
      credentials,
      async (path, body) => {
        calls.push({ path, body });
        return Response.json(
          path.endsWith("/profiles") ? profiles : { status: "ok" },
        );
      },
    );
    assert.deepEqual(result, profiles);
    assert.deepEqual(calls, [
      { path: `/api/proxy/auth/test-${provider}`, body: credentials },
      { path: `/api/proxy/auth/${provider}/profiles`, body: credentials },
    ]);
  });
}

test("blank selections retain null settings and Sonarr defaults", () => {
  assert.deepEqual(arrSettings("sonarr", form("sonarr")), {
    sonarr_url: null,
    sonarr_token: null,
    sonarr_root_folder: null,
    sonarr_quality_profile: null,
    sonarr_tags: [],
    sonarr_customize_on_add: false,
    sonarr_season_folder: true,
  });
});

test("failed credential test does not request profiles", async () => {
  const calls = [];
  await assert.rejects(
    fetchArrProfiles("radarr", {}, async (path) => {
      calls.push(path);
      return Response.json({ detail: "Invalid API key" }, { status: 401 });
    }),
    /Invalid API key/,
  );
  assert.deepEqual(calls, ["/api/proxy/auth/test-radarr"]);
});

test("failed profile response is rejected even after credentials pass", async () => {
  await assert.rejects(
    fetchArrProfiles("sonarr", {}, async (path) => {
      return path.endsWith("/profiles")
        ? new Response("Service unavailable", { status: 503 })
        : Response.json({ status: "ok" });
    }),
    /Failed to load Sonarr profiles \(HTTP 503\)/,
  );
});

test("profile refresh preserves form selections and treats remote labels as text", (t) => {
  const createElement = (tag) => ({
    tag,
    children: [],
    classList: { toggle() {} },
    addEventListener() {},
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
  });
  const originalDocument = Object.getOwnPropertyDescriptor(
    globalThis,
    "document",
  );
  globalThis.document = { createElement };
  t.after(() => {
    if (originalDocument)
      Object.defineProperty(globalThis, "document", originalDocument);
    else delete globalThis.document;
  });
  const folder = { ...createElement("select"), value: "/shows" };
  const profile = { ...createElement("select"), value: "8" };
  const container = createElement("div");
  const scope = form(
    "sonarr",
    { root_folder: folder, quality_profile: profile, tags_list: container },
    [{ type: "hidden", value: "5" }],
  );
  const remoteLabel = "<img src=x onerror=alert(1)>";
  renderArrProfiles(
    "sonarr",
    {
      root_folders: [{ path: "/shows", freeSpace: 1024 ** 3 }],
      quality_profiles: [{ id: 8, name: remoteLabel }],
      tags: [
        { id: 5, label: remoteLabel },
        { id: 6, label: "Other" },
      ],
    },
    scope,
  );
  assert.equal(folder.children[1].selected, true);
  assert.equal(profile.children[1].selected, true);
  assert.equal(profile.children[1].textContent, remoteLabel);
  assert.equal(container.children[0].children[0].checked, true);
  assert.equal(container.children[1].children[0].checked, false);
  assert.equal(container.children[0].children[1].textContent, remoteLabel);
});
