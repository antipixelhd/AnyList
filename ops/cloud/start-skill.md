Start AnyList from the repository root with `bash ops/cloud/start.sh`. Check that
`http://127.0.0.1:7331/health` succeeds and `http://127.0.0.1:7330/login` loads.
Expose frontend port 7330 using the Cloud preview UI. If it provides an HTTPS
origin, set `CLOUD_SERVER_URL` to that origin and rerun startup.

Use `bash ops/cloud/check.sh` for full validation. After checkout/dependency
changes run setup if manifests changed, then restart with start.sh. Preserve the
development DB; tests have their own disposable database. Never use a VPS or
production DATABASE_URL for Cloud tests. Push commits to the chosen beta or
development branch for GitHub to deploy its VPS preview.

Never print provider secrets, add them to Git, persist their raw values in the
published image, or copy account passwords/OAuth tokens from another environment.
`.env.providers` and `.cloud-runtime` are private ignored runtime files.
