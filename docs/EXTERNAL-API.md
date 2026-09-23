# AnyList external tracking API

External clients can update an AnyList entry with a revocable OAuth device grant. This flow does not require a browser session or the account's general API key. The `tracking:write` scope is limited to status, episode progress, and ratings. Examples use the same-origin `/api/proxy` path; trusted direct backend clients can omit that prefix.

## Obtain a tracking credential

Start a device authorization request. The scope must be exactly `tracking:write`:

```http
POST /api/proxy/auth/device/code
Content-Type: application/json

{"client_name":"My script","scope":"tracking:write"}
```

Open the returned `verification_uri_complete` in a browser signed in to the target AnyList account and approve the named client. The user can revoke it later under **Connections → Connected Apps**. Approval codes expire after 15 minutes.

Poll the token endpoint using the returned device code:

```http
POST /api/proxy/auth/device/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=DEVICE_CODE
```

Poll no more often than the returned `interval`. `authorization_pending` means approval is still waiting; `slow_down` means increase the interval by five seconds. A successful response contains `access_token`, `refresh_token`, `expires_in`, and `scope`. Refresh access when it expires:

```http
POST /api/proxy/auth/device/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=REFRESH_TOKEN
```

Store both tokens as secrets. Refresh tokens rotate on use. Revoking the grant invalidates access immediately.

## Update a tracked title

Send an authenticated patch to the AnyList backend (or its same-origin `/api/proxy` path):

```http
PATCH /api/proxy/tracking/entry/{media_id}/external
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json

{"status":"watching","progress":12,"manual_score":8.5}
```

`media_id` is AnyList's internal catalogue ID, not a TMDB, TVDB, IMDb, or provider ID. The title must already exist in the AnyList catalogue. All fields are optional; send only fields being changed.

- `status`: `planning`, `watching`, `paused`, `dropped`, or `completed`.
- `progress`: non-negative cumulative released episode count for a series; movies use `0` or `1`.
- `manual_score`: `null` or `0` to clear the whole-title rating, or a score from `0.5` to `10` in half-point increments. Zero means unrated.
- `rating_mode`: `manual` or `average` for a series. `season_scores` sets or clears individual season ratings using season numbers as keys.

For a series, use `manual_score` for a whole-show score or `season_scores` for individual seasons. `rating_mode: "average"` opts into the calculated average of rated seasons. A progress rollback that would clear later watched episodes returns `409`; the external route does not silently confirm destructive rollback. A successful response returns the canonical tracked-entry representation and a `delivery_job_id`. Poll `GET /api/proxy/tracking/delivery/{delivery_job_id}` for delivery state. States include `queued`, `dispatching`, `attempted_unverified`, `failed`, and `no_external_changes`. `attempted_unverified` means AnyList ran provider fan-out; it does not guarantee every provider accepted the change. An edit with no provider-mirrored changes returns `no_external_changes`.

Jobs remain queryable after completion. A job can remain `queued` if the server stops before its background delivery begins; check the AnyList tracking UI or make a fresh entry update to create a new delivery attempt. Jobs are not blindly retried because a provider may have accepted a request before a process interruption.

The credential can call this external route and check its delivery job. It cannot authenticate legacy history, list, or rating endpoints, account/security endpoints, or the ordinary browser entry editor. Legacy device grants using scope `write` remain separate and are not valid for this external tracking route.

## Example

```sh
curl -X PATCH "$ANYLIST_URL/api/proxy/tracking/entry/123/external" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"watching","progress":12,"manual_score":8.5}'
```
