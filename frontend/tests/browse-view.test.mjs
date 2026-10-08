import test from "node:test";
import assert from "node:assert/strict";
import {
  isCategoryView,
  browseRequestParams,
  sectionHref,
} from "../src/lib/browse-view.ts";

test("browse starts with categories, while searches, filters and explicit sorts use full results", () => {
  for (const query of ["", "type=series", "sort=all&region=DE", "min_votes=250", "min_votes=1000"]) {
    assert.equal(isCategoryView(new URLSearchParams(query)), true, query);
  }
  for (const query of [
    "q=Arrval",
    "genres=18",
    "start=2026-01-01",
    "end=2026-12-31",
    "status=airing",
    "provider=8",
    "sort=popular",
    "sort=trending",
  ]) {
    assert.equal(isCategoryView(new URLSearchParams(query)), false, query);
  }
});

test('vote thresholds survive full-result requests and section links', () => {
  assert.equal(browseRequestParams(new URLSearchParams('q=Arrival&min_votes=500')).get('min_votes'), '500');
  assert.equal(new URL(sectionHref('movie', 'US', {sort: 'popular', min_votes: '0'}), 'https://anylist.test').searchParams.get('min_votes'), '0');
});

test("all-category selection can search using the paginated API without mutating the view", () => {
  const values = new URLSearchParams("sort=all&q=Arrval&page=2&source=local");
  const request = browseRequestParams(values);
  assert.equal(request.get("sort"), "popular");
  assert.equal(request.get("q"), "Arrval");
  assert.equal(request.get("page"), "2");
  assert.equal(request.get("source"), "local");
  assert.equal(values.get("sort"), "all");
});

test("view-all links retain category dates and the selected media type and streaming region", () => {
  const href = sectionHref("series", "DE", {
    sort: "popular",
    start: "2026-10-01",
    end: "2026-12-31",
  });
  const values = new URL(href, "https://anylist.test").searchParams;
  assert.equal(values.get("type"), "series");
  assert.equal(values.get("region"), "DE");
  assert.equal(values.get("sort"), "popular");
  assert.equal(values.get("start"), "2026-10-01");
  assert.equal(values.get("end"), "2026-12-31");
  assert.equal(
    sectionHref("movie", "US", { sort: "score" }),
    "/browse?sort=score",
  );
});
