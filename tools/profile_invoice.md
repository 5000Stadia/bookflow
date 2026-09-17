# Invoice delay diagnostic

Run from the checkout whose code you want to measure, with its development environment.
This standalone tool installs temporary wrappers in its own process. Normal Bookflow
requests have no added instrumentation or dependency.

First prepare a **disposable copy** of the company data root. Use SQLite's backup API
for databases while a source host is running, or copy a stopped source. Remove copied
`host.json` and `root.lock`; neither belongs in an offline diagnostic copy. Never use
the original data root. The tool changes the current OS user's password **on the copy**
to log into its temporary ASGI host. It refuses known live/protected roots, symlinks,
host descriptors and locks, but `--disposable-copy` is your explicit ownership assertion.

```sh
PYTHONPATH=src:. .venv/bin/python tools/profile_invoice.py \
  --data-root /tmp/my-disposable-copy --disposable-copy \
  --company COMPANY_ID --invoice INVOICE_ID \
  --output /tmp/invoice-timing.json
```

Add `--lines` for line-level timings of the endpoint and permission observation/root
functions. That optional mode requires `line_profiler` in the diagnostic environment;
it is not a Bookflow runtime dependency. `--samples` controls 1–10 baseline requests
(default three). A stopped diagnostic host can leave an empty `root.lock`; remove it
from your owned copy before another diagnostic run after confirming that run stopped.

The JSON report contains:

- Uninstrumented request samples and median, with the first read distinguished from
  subsequent warmed reads. Startup and login are excluded.
- Separate stage counts/times for page construction, named command reads, permission
  loading/validation, template rendering and final response publication checks.
- Worker function hotspots with source filenames and line numbers, and optional
  per-line hotspots. No arguments, SQL values, passwords or response bodies are saved.

**Interpretation:** these are in-process ASGI timings, not LAN transfer or browser
painting. Instrumented runs include profiler overhead and are not normal latency.
Inclusive stage durations overlap; never add them together. “Exclusive” subtracts
only other instrumented children. Worker profiles exclude middleware/final publication;
the stage report covers publication separately. Unattributed time is not automatically
queue delay. Use the results to pick a narrow code investigation, then verify any fix
with another uninstrumented run under comparable conditions.
