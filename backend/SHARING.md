# Sharing previews

Canonical title, username profile, actor, staff and studio pages render Open Graph
and large-image Twitter Card metadata in their initial HTML. Share the normal
page URL. `SERVER_URL` should be the public HTTPS origin in deployed instances.

The card image is `/api/proxy/sharing/image?path=<page path>`. It always reads as
an anonymous visitor, including when requested with a session or API key.
Logged-out navigation must be enabled, and a profile must be public. There is
no crawler-specific access bypass.

- Titles use the selected primary poster and blurred backdrop, top metadata,
  genres and the saved IMDb score. Missing scores are omitted; personal scores
  and synopses are excluded.
- Profiles use their selected background, avatar and name. Movie/series counts
  and combined watch days come from the current published statistics snapshot.
  Until a compatible snapshot exists, stats are omitted rather than invented.
- Contributors use their portrait/logo and the latest released known work's
  backdrop (or poster). Ages respect death dates. Work counts are deduplicated
  across the complete available credits, before pagination. Studio counts also
  incorporate complete cached discovery evidence when available. These reflect
  known metadata and the site's anonymous result filters, not a viewer's list.

Images are 1200 × 630 JPEGs. Rendering runs in worker threads, with two render
slots, at most eight pending identities, and shared concurrent image requests.
The five-minute process cache is capped at 64 images / 16 MiB. Access is checked
before consulting it. Origin responses use `no-store` so privacy changes are
checked again; messaging apps maintain their own caches independently.

Artwork downloads allow public HTTPS addresses only, pin validated IPs, verify
TLS against the original hostname and reject redirects, oversized downloads and
oversized decoded images. Missing artwork gets a plain background or initial.
No screenshot browser or external image-generation service is required.

The bundled Overpass font comes from
[Google Fonts](https://github.com/google/fonts/tree/main/ofl/overpass) under the
adjacent SIL Open Font License. Pillow is already a backend dependency.

Verification: `frontend` runs `npm test`, `npm run check`, and `npm run build`.
Backend sharing tests use `python -m unittest discover -s tests -p
'test_share_preview*.py'`; database tests require the existing explicit
`STATISTICS_TEST_DATABASE_URL` disposable PostgreSQL fixture. Validate a deployed
public URL in Discord/WhatsApp to check each client's cropping and external
cache behavior.
