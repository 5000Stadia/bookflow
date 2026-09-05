# Binary transfers

The registry's `TransferDescriptor(direction, prepare)` declares one external input or output body and its shared authorization-time preparation callback. JSON models carry business metadata only. Local paths, stream objects, and server storage paths are not attachment command inputs. [Attachment commands](cli/attachment.md) describe their typed metadata, roles, context, and errors.

## CLI files

After the README quick start, use an existing customer id from `bookflow customer query --company "Demo Plumbing Co" --json`. Set `customer_id` to that item's `id`, and put a real PDF at `receipt.pdf`:

```sh
bookflow attachment add customer "$customer_id" receipt.pdf --company "Demo Plumbing Co" --caption "Service receipt" --reason "File service receipt" --idempotency-key receipt-001 --json
```

The input file is opened on the calling machine, including when a local host executes the command. `original_filename` defaults to the PATH basename. `media_type` defaults to the MIME type inferred from that filename, falling back to `application/octet-stream`. Override these with `--original-filename` and `--media-type`; MIME inference does not inspect file content. JSON/Python require an explicit original filename and default the media type to `application/octet-stream`.

Set `attachment_id` to the returned `attachment.id`. Download to a new path:

```sh
bookflow attachment get "$attachment_id" --out downloaded-receipt.pdf --company "Demo Plumbing Co" --json
```

`--out` is required. Its parent directory must exist. The CLI writes a private temporary file in that directory and publishes it atomically only after digest, size, and command completion verification. It never overwrites an existing file, directory, or symlink; a destination race also fails. Failed transfers remove their private output. File publication requires filesystem support for no-replace hard links and synchronization. Stdout contains JSON metadata, never the binary body.

## Python streams

This example uses the initialized demo company and the first existing customer. Run from a directory containing `receipt.pdf`; `python-receipt.pdf` must not exist.

```python
from bookflow import connect

client = connect()
client.use_company("Demo Plumbing Co")
customer = client.customer.query(limit=1)["items"][0]["id"]
with open("receipt.pdf", "rb") as source:
    added = client.attachment.add(
        record_type="customer", record_id=customer,
        original_filename="receipt.pdf", media_type="application/pdf",
        caption="Service receipt", reason="File service receipt",
        input_stream=source,
    )
with open("python-receipt.pdf", "xb") as sink:
    metadata = client.attachment.get(
        attachment=added["attachment"]["id"], output_stream=sink,
    )
assert metadata["sha256"] == added["attachment"]["sha256"]
```

`client.run("attachment add", input, input_stream=source)` and `client.run("attachment get", input, output_stream=sink)` are equivalent. Supply exactly one stream of the declared direction. The caller owns and closes the stream. A Python sink can contain partial bytes after any failure; accept its contents only when the call returns successfully. The Python example's exclusive-create mode prevents overwrite but does not provide the CLI's atomic publication.

## HTTP raw bodies

Both directions use `POST /companies/{company_id}/transfers/{noun.verb}`. The ordinary `/commands/` JSON route is not the transfer interface. Authentication and write context use the usual headers. Browser clients use the host's session and request protections.

Encode the metadata object as UTF-8 JSON and then unpadded base64url in `X-Bookflow-Input`. The header is limited to 8,192 encoded bytes and 6,144 decoded bytes. Paths and base64 file content do not belong in that object. For example, with `company_id`, `customer_id`, `base_url`, and `token` set by your authenticated client:

```python
import base64
import json
from urllib.request import Request, urlopen

fields = {
    "record_type": "customer", "record_id": customer_id,
    "original_filename": "receipt.pdf", "media_type": "application/pdf",
    "caption": "Service receipt",
}
encoded = base64.urlsafe_b64encode(
    json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
).rstrip(b"=").decode("ascii")
with open("receipt.pdf", "rb") as source:
    request = Request(
        f"{base_url}/companies/{company_id}/transfers/attachment.add",
        data=iter(lambda: source.read(65_536), b""), method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Bookflow-Input": encoded,
            "X-Bookflow-Reason": "File service receipt",
            "Content-Type": "application/octet-stream",
        },
    )
    with urlopen(request, timeout=30) as response:
        added = json.load(response)
```

The request body is the raw file, without multipart parsing or base64 body encoding. The upload response is typed JSON metadata. `?dry_run=true` consumes and hashes the upload without persistent staging or business writes. Actual received bytes determine the limit even without a trustworthy Content-Length.

For `attachment.get`, encode `{"attachment": attachment_id}` in the same header and send an empty request body. The successful response body contains bytes, not a JSON metadata envelope. Response headers include the stored `Content-Type`, verified `Content-Length`, `X-Bookflow-SHA256`, `X-Bookflow-Output`, `Content-Disposition: attachment` with a sanitized ASCII fallback and RFC5987 UTF-8 filename, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`. `X-Bookflow-Output` contains the complete typed command output as unpadded base64url-encoded UTF-8 JSON, with the same limits of 6,144 decoded bytes and 8,192 encoded bytes as `X-Bookflow-Input`. Decode this header to obtain the same complete metadata returned by CLI, Python, and local forwarding; the response body remains raw file bytes. Verify received size and SHA-256 before accepting a download. Failures before response headers use the ordinary JSON error shape. A failure after output starts truncates the response; it does not append JSON to the file.

## Limits, authorization, and completion

Original filenames are UTF-8 basenames of at most 255 bytes, without path separators, control characters, or dot/dot-dot names. Media types are ASCII type/subtype tokens of at most 127 characters. Captions allow at most 2,048 UTF-8 bytes.

Uploads allow at most the company setting `attachment_max_bytes`: default 25,000,000, configurable from 1 through 100,000,000 via [company update](cli/company.md#company-update). The shared preparation validates company, target, role, reason/directive, input, and size setting before reading body bytes. Final execution repeats authorization and business checks. Downloads verify stored digest/size and recheck authorization before output chunks. Verification reads do not repair missing or corrupt bodies.

Transfers use chunks of at most 65,536 bytes, a 300-second absolute lifetime, and a 30-second transport inactivity bound. The host admits at most eight transfers overall and two per effective principal across companies and directions. Exhaustion returns `E_DB_BUSY` immediately. Arbitrary Python streams must cooperate with bounded I/O; a blocked caller-owned operation cannot be forcibly interrupted. Its lease remains held until its owner finishes cleanup.

I/O leases hold no database snapshots across transport waits. Ordinary writes can proceed while transfers wait. Folder moves, detach/reset, and compact exclude new leases and wait at most five seconds for existing readers/transfers; a busy transfer fails the filesystem operation. Accepted writer jobs retain their stage and lease through execution and cleanup, including after caller cancellation. Shutdown keeps the root lock until admitted resources finish cleanup.

Compatible CLI/Python clients forward bytes to the local host after peer/metadata validation and a ready response. The envelope is at most 8,192 bytes and declares transfer version 1 and direction. Body frames have a four-byte unsigned big-endian length from 1 through 65,536; zero marks body EOF. The final framed JSON result/error is at most 65,536 bytes. Download acceptance requires matching digest and size **and a successful final response**. Body EOF alone is insufficient. Partial frames, absent terminal/final frames, and timeouts are interrupted I/O.

A forwarded write never retries standalone after sending its envelope. An interrupted result may have committed; retry with the same idempotency key, metadata, and bytes. Verified SHA-256 and actual size participate in the retry hash. Upload dry runs hash without persistent staging, publication, collection recovery, metadata changes, or audit writes. A pending collection intent returns `E_DB_BUSY` during a dry run.

Read access permits list/get/activity; standard access permits add/link/unlink; compact requires admin access (also satisfied by owners). Normal agent reason/directive rules apply to writes. Excess bytes return `E_VALUE_RANGE`; malformed metadata/framing returns `E_VALIDATION`; interruption, corruption, or missing bodies return `E_IO`.

## Links, activity, and collection

Identical bytes share a digest/body and attachment row, retaining the first upload's filename, media type, and upload attribution. Repeated add/link to an already-active target association preserves its caption and attribution. Unlink requires the link's `expected_version`, even when already inactive, and retains link history. Linking after unlink creates a new occurrence. Unlinking the last association retains the body until collection.

[Attachment list](cli/attachment.md#attachment-list) returns active links, newest occurrence first, with a default limit of 50 and maximum of 200. Continue with `next_cursor` and the same target/company/permissions. [Activity](cli/activity.md) returns immutable audit, note, and file-link actions chronologically, including edits and unlinks, with actors. Filters include `since`, `until`, and `kinds`. Supply `kinds` as a list through Python or HTTP, or as a quoted JSON array at the CLI: `--kinds '["note","attachment"]'`. Continue with the same filters and page size; a fixed audit high-water bounds each traversal. `text_truncated` identifies excerpts whose event/entry ids locate the full audit snapshot.

[Company compact](cli/company.md#company-compact) collects at most 200 unlinked bodies per invocation, with bounded orphan discovery and `has_more` continuation. It retains attachment, link, and audit metadata and never collects actively linked bodies. A durable intent precedes removal; recovery finishes collection with the original audit/retry context. Failure preserves the intent and reports `E_IO`. Missing/collected bodies cannot be downloaded or linked; fresh verified uploads can restore collected bytes.

Compact dry-run projects metadata candidates and bytes with `dry_run: true` and no operation id; it does not recover, remove bytes, or scan all orphans. Its warning and `has_more` do not claim complete orphan enumeration. Repeat real compact invocations while `has_more` is true, using a new idempotency key for each new batch.
