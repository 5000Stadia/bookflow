# Serving Bookflow over HTTPS behind a proxy

Groundwork for R190 (camera scanning): browsers open the camera only on a secure page (https, or
http://localhost on the same computer). Bookflow does not terminate TLS itself. A TLS proxy (`colony serve`,
`tailscale serve`, Caddy, nginx) serves `https://host:port/` and forwards to the host on `127.0.0.1:PORT`;
Bookflow is told that address as configuration. The how-to for operators is in docs/agent-install.md,
"Serving over HTTPS".

## The setting

- `--public-origin https://host[:port]` on `serve`, `setup` and `owner-link`; otherwise `[host] public_origin`
  in the data root's `config.toml`. The flag wins. It is normalized (lower-case host, default port dropped,
  no path, query, fragment or credentials) by `bookflow.core.public_origin`.
- A running host records it in `host.json` beside `bind`, for tools that discover the host.
- Nothing derives the origin from a request: not `Host`, not `X-Forwarded-Proto`/`-Host` (the proxy is not
  known to send them, and a client can forge them on a direct connection).

## What it changes

| Site | Without | With a public origin |
|---|---|---|
| `serve` bind | loopback; any address with `--allow-network` | `127.0.0.1` only, else `E_NETWORK_NOT_ALLOWED` |
| `serve` output `url`, the "ready at" line | `http://127.0.0.1:PORT/` | the origin (local address still printed for the health check) |
| `setup` owner link, `owner-link` url | local address | origin |
| `.mcp.json` `--url` that `setup` writes | local address | origin (the MCP launcher already accepts https) |
| `setup`, `owner-link`, `serve --background` health checks | local address | still the local address |
| Session cookie, last-company cookie | `Secure` off on loopback | `Secure` on for an https origin (an explicit `--no-secure-cookies` still wins) |
| `request.url` in the app (Starlette's trailing-slash redirect) | from the request | scheme and Host rewritten to the origin by `PublicOriginMiddleware` |
| Unsafe requests (POST etc.) | no Origin check | an `Origin` (or, lacking one, `Referer`) naming anything but the origin or the host's own loopback address is refused with `E_ORIGIN_REFUSED` (403) before any handler runs; a request with neither header (agents, the MCP launcher) passes |

Unchanged: the workbench's own redirects and `HX-Redirect` targets are relative paths; `SameSite=Lax` and the
`X-Bookflow-Workbench` header rule work as before through the proxy (the header is an ordinary request header;
the cookie is first-party on the origin). The LAN mode (`--bind 0.0.0.0:PORT --allow-network`, no origin)
keeps working and gets no Origin check, as before.

## Sharing a hostname with other apps (ports apart)

`colony serve` puts every project on one hostname, each on its own port, with its board on 443. Browsers do
not isolate cookies by port, and SameSite counts all those ports as one site, so Bookflow's protection
cannot rest on cookies alone:

- **Cookies.** `bookflow_session` is HttpOnly, SameSite=Lax and Secure under an https origin;
  `bookflow_company` (the last company opened, no identity in it) is HttpOnly too. Both carry the `bookflow_`
  prefix and no Domain attribute (host-only). Another app on the same host can still see or overwrite them
  in principle, which is why the next two rules matter.
- **Exact origin.** The Origin/Referer check compares scheme, host and port exactly with `--public-origin`
  (plus the host's own loopback address). The same host on another port, or on the default port, is refused
  (tested in tests/test_public_origin.py).
- **The workbench header.** Every cookie-authenticated write still needs `X-Bookflow-Workbench: 1`, which a
  cross-origin page cannot send without a CORS preflight that Bookflow never grants.
- **No HSTS.** Bookflow never sends `Strict-Transport-Security`: it would apply to the whole hostname and
  every other app on it. Whoever runs the proxy decides that.

## The camera's secure-context check

`GET /secure-context` answers `{"public_origin", "https"}` with no credential. `static/secure-context.js`
defines `bookflowSecureContext()`, which combines that with `window.isSecureContext` and resolves to
`{secure, https, public_origin, camera: "live" | "photo", message}`. R190 increment 2 loads it in the Scan
field: `live` opens the camera stream; `photo` offers `<input type=file capture>` and shows `message`, which
either points at the https address (when one is configured but the page was opened over plain http) or asks
the administrator to serve Bookflow over https (for example with `colony serve`).

## Tailscale identity headers

A Tailscale proxy adds `Tailscale-User-Login`, `-Name` and `-Profile-Pic` and strips them from outside
traffic. Bookflow does not use them: `PublicOriginMiddleware` drops them from every request, so no handler can
read or log them. They are trustworthy only when every path to the host goes through that proxy, which
Bookflow cannot check (a local process can reach `127.0.0.1` and send them).

A later "sign in with your Tailscale identity" is worth having for a small business on a tailnet: a phone at
the till would open the workbench already signed in, with no password typed on a shared device. It would need
(1) an explicit opt-in naming the proxy as trusted, (2) a mapping from tailnet login to a Bookflow person
(never auto-creating one), and (3) a way to tell proxied requests from local ones (a separate loopback port or
socket that only the proxy uses). Not built; noted for R191 (each person's Home) or later.

## Known limits behind a proxy

- Every proxied client is `127.0.0.1` to the host, so the setup-link page's loopback guard admits it: under a
  public origin the one-time secret (and the tailnet's own access control) is what protects the link, which is
  why the links now carry the origin and are meant to be opened through it.
- Login throttling is per source address, so all proxied clients share one throttle.
