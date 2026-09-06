# Installed MCP guide

Install `bookflow-core[mcp]` in the launcher and host environments. A host missing
the incremental parser rejects compatibility preflight with installation guidance
before business submission.
The optional SDK is pinned to2.1.1 and the incremental parser to3.5.1 in the initial
distribution. MCP wire framing belongs to that SDK. Both its legacy initialize
and modern discover connections expose the same three tools.

Start an isolated host and issue a bearer using the [operator bootstrap](agent-guide.md).
That bootstrap token is a human credential, not a newly created agent identity.
An assigned agent's token keeps its authenticated principal and authority epoch.
The launcher never accepts a bearer on its command line, reads browser cookies,
follows redirects, or infers credentials from a data root. It connects to an
existing HTTPS host or numeric HTTP loopback origin. It neither starts a second
bookkeeping host nor acquires the company's root lock.

Configure the MCP client's stdio command as `bookflow`, with arguments
`mcp --url HOST_ORIGIN --client-name plumbing-agent --input-dir INPUT_DIRECTORY --output-dir OUTPUT_DIRECTORY`.
Inject `BOOKFLOW_TOKEN` in its private process environment; do not paste a token
into an invocation or diagnostic log. Both directories must already exist and be
owned by the launching OS user, without group/world write permission. Configure
only the business inbox/outbox intended for this agent. File operations reject
symlinks, ancestor changes, nonregular/hard-linked inputs, paths outside these
directories and existing output destinations. No directory grants bookkeeping
authority. No directories are inferred from home, a company root, current working
directory or client-supplied MCP roots. Omit the directory options for inline JSON
only. File capabilities use POSIX descriptors: Linux is tested; macOS and WSL
remain unverified platforms and native Windows is not supported for these files.

Company selection is an explicit non-null tool `company`, then the calling
machine's `BOOKFLOW_COMPANY`, then its read-only OS-user configuration. Null is
omission; an explicit empty string is not a request for fallback. `--selection-root`
can select that calling-machine configuration root. A host's own default never
chooses a remote caller's company. Every MCP launcher has a fresh process session
ID. Labels, hostname and client version are informational provenance; they do not
grant permissions. The dedicated bridge records `interface=mcp`. Browser activity
currently records `interface=http`, `client_name=bookflow-workbench`.

`bookflow_list_commands` returns20 complete descriptors by default; use a noun
prefix and follow next_cursor. `bookflow_help` defaults to concise usage with the
complete input schema, context and errors. Select `view=input_schema`,
`view=output_schema` or `view=full` for the other complete views. No schema or full
help is truncated. A command without business fields still needs `input: {}`.
Reason is a short trigger, at most140 characters. Reads accept inactive null
optional context and false dry_run; active unsupported context is an error.
Preview does not save proposed IDs or posted status. Posting an invoice saves it
to the books and does not send it to the customer.

For "attach this receipt", supply the registered attachment metadata and
`transport.input_file` with the permitted business file path. The launcher opens,
reads, hashes, streams and verifies it in one call; the model supplies no digest,
base64 or chunks. Downloads use `transport.output_file`, or a generated name in
the configured outbox. Business metadata remains the structured result; verified
download location is also returned in text and `_meta.bookflow_delivery`.
`transport.input_json_file` replaces the business input object for large inputs.
`transport.result_file` publishes the complete JSON output/error artifact and
returns a tagged delivery receipt. Inspect that verified document through
`bookflow_run` with its operation_ref, action=inspect and a JSON Pointer; pages
have total and next_cursor. Editing or replacing the file invalidates inspection.

Every intent has one execution identity. prepare_only returns its reference
without executing. operation_ref and input_ref are exclusive aliases for that
same intent; execute again observes its original receipt/state. A new command
envelope is new work, even if its input is equal. Use the command's idempotency or
permanent business key when deliberately retrying one business effect. Status,
execute, release and inspect always require current authority. A lost, expired or
evicted submitted receipt is unknown, never proof of rollback. No automatic write
retry occurs. A lost one-time secret is not reconstructed: identify/revoke that
credential and deliberately replace it under current permissions.

Initial host intent capacity is8 total/2 per effective principal, shared across
tokens/companies. Unsubmitted work has30s idle/300s absolute deadlines. Prepared
inputs/guards have64MiB global/8MiB principal retention budgets; direct ordinary
execution does not require prepared retention. Completed recovery has128 entries
global/16 principal,32MiB global/4MiB principal memory, with at most1MiB serialized
receipt per entry and60s idle/300s absolute lifetime. A large result still delivers
in full; cache availability is separately reported. File mappings have32 entries,
16MiB memory and60s idle/300s absolute lifetime. Eviction never deletes caller
output files. Started writers finish under the host; delivery cancellation does
not undo them or free their resources while they run. EOF closes the launcher
without a final command document. JSON delivery currently has30s inactivity and
300s absolute timeout. Operators can set `BOOKFLOW_MCP_JSON_SECONDS` in both host
and launcher environments to an integer from30 through86400 seconds for a slower
complete JSON delivery. This does not extend preexecution/receipt lifetimes or
binary leases, and imposes no byte/row cap. The host reports its value at preflight
and in intent status; the two processes retain their own configured deadline.

## Literal disposable-company journey

Run the following Python block unchanged using the installed `[mcp]` environment.
Operator prerequisites are an isolated host, its bearer, a company explicitly
authorized for this trial, an existing harmless PDF receipt and private existing
input/output directories. Export `BOOKFLOW_URL`, `BOOKFLOW_TOKEN`,
`BOOKFLOW_COMPANY`, `BOOKFLOW_RECEIPT` (absolute receipt path) and
`BOOKFLOW_MCP_OUTDIR` (absolute private output directory). `BOOKFLOW_MCP_BINARY`
optionally identifies an installed `bookflow` executable. The program creates
disposable business records and prints only their identifiers. The launcher does
all byte mechanics. This is an executable documentation journey, not blind-agent
usability acceptance.

<!-- bookflow-example: executable -->
```python
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    receipt = Path(os.environ["BOOKFLOW_RECEIPT"])
    outbox = Path(os.environ["BOOKFLOW_MCP_OUTDIR"])
    company = os.environ["BOOKFLOW_COMPANY"]
    suffix = uuid4().hex[:10]
    params = StdioServerParameters(
        command=os.environ.get("BOOKFLOW_MCP_BINARY", "bookflow"),
        args=["mcp", "--url", os.environ["BOOKFLOW_URL"],
              "--client-name", "installed-mcp-guide", "--input-dir", str(receipt.parent),
              "--output-dir", str(outbox)],
        env={"BOOKFLOW_TOKEN": os.environ["BOOKFLOW_TOKEN"], "BOOKFLOW_COMPANY": company},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.discover()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "bookflow_list_commands", "bookflow_help", "bookflow_run"}

            async def tool(name, arguments, error=None):
                reply = await session.call_tool(name, arguments)
                value = reply.structured_content
                if bool(reply.is_error) != bool(error):
                    raise RuntimeError("Unexpected tool outcome; inspect the confidential client result.")
                if error:
                    assert value["code"] == error
                return value

            async def run(name, data, **context):
                return await tool("bookflow_run", {"command": name, "input": data, **context})

            catalog = await tool("bookflow_list_commands", {"prefix": "invoice"})
            assert "invoice post" in {row["name"] for row in catalog["commands"]}
            for name in ("journal post", "invoice post", "invoice update", "attachment add", "attachment get"):
                usage = await tool("bookflow_help", {"command": name})
                full = await tool("bookflow_help", {"command": name, "view": "full"})
                assert usage["input_schema"] == full["input_schema"]
                assert full["output_schema"]["type"] == "object"

            companies = await run("company list", {})
            assert company in {row["id"] for row in companies["items"]}
            accounts = await run("account list", {})
            assert accounts["count"] > 0
            bank = await run("account create", {"name": "Trial bank " + suffix, "type": "bank"}, reason="Set up disposable trial")
            income = await run("account create", {"name": "Trial labor " + suffix, "type": "income"}, reason="Set up disposable trial")
            customer = await run("customer create", {"name": "Trial customer " + suffix}, reason="Set up disposable trial")
            codes = await run("sales-tax-code list", {})
            exempt = next(row["id"] for row in codes["items"] if not row["taxable"])
            item = await run("item create", {"name": "Trial service " + suffix, "type": "service",
                "description": "Disposable trial labor", "sales_enabled": True,
                "income_account_id": income["id"], "price": "12.34", "sales_tax_code_id": exempt},
                reason="Set up disposable trial")
            day = datetime.now(timezone.utc).date().isoformat()
            data = {"date": day, "customer": customer["id"], "lines": [{"item": item["id"], "quantity": ".5"}]}
            preview = await run("invoice post", data, dry_run=True, reason="Preview trial invoice")
            data["expected_facts_fingerprint"] = preview["facts_fingerprint"]
            key = "guide-invoice-" + suffix
            invoice = await run("invoice post", data, reason="Save trial invoice", idempotency_key=key)
            replay = await run("invoice post", data, reason="Save trial invoice", idempotency_key=key)
            assert replay["id"] == invoice["id"]
            change = {"invoice": invoice["id"], "expected_version": invoice["version"], "memo": "Continued from shared invoice"}
            preview = await run("invoice update", change, dry_run=True, reason="Preview correction")
            change["expected_facts_fingerprint"] = preview["facts_fingerprint"]
            corrected = await run("invoice update", change, reason="Save correction")
            await tool("bookflow_run", {"command": "invoice update", "input": change,
                "reason": "Witness stale version"}, error="E_VERSION_CONFLICT")
            current = await run("invoice show", {"invoice": invoice["id"]})
            assert current["version"] == corrected["version"]

            directive = await run("directive add", {"text": "Record the disposable trial's balanced bank entry."}, reason="Set trial instruction")
            code = directive["directive"]["code"]
            journal_input = {"date": day, "lines": [
                {"account": bank["id"], "side": "debit", "amount": "12.34"},
                {"account": income["id"], "side": "credit", "amount": "12.34"}]}
            await run("journal post", journal_input, directive=code, dry_run=True)
            journal = await run("journal post", journal_input, directive=code, idempotency_key="guide-journal-" + suffix)
            assert journal["total_minor_units"] == 1234

            metadata = {"record_type": "customer", "record_id": customer["id"],
                        "original_filename": receipt.name, "media_type": "application/pdf", "caption": "Trial receipt"}
            await run("attachment add", metadata, transport={"input_file": str(receipt)}, dry_run=True, reason="Preview receipt")
            added = await run("attachment add", metadata, transport={"input_file": str(receipt)}, reason="Save receipt")
            downloaded = await run("attachment get", {"attachment": added["attachment"]["id"]},
                transport={"output_file": str(outbox / (suffix + ".pdf"))})
            assert downloaded["sha256"] == added["attachment"]["sha256"]
            delivered = await run("attachment list", {"record_type": "customer", "record_id": customer["id"]},
                transport={"result_file": str(outbox / (suffix + ".json"))})
            inspected = await tool("bookflow_run", {"operation_ref": delivered["operation_ref"], "action": "inspect", "pointer": "/count"})
            assert inspected["value"] == 1

            prepared = await run("company update", {"fax": "Disposable MCP guide"},
                reason="Witness one execution", transport={"prepare_only": True})
            ref = prepared["operation_ref"]
            saved = await tool("bookflow_run", {"operation_ref": ref, "action": "execute"})
            recovered = await tool("bookflow_run", {"input_ref": ref, "action": "execute"})
            assert recovered == saved
            await tool("bookflow_run", {"operation_ref": ref, "action": "release"})
            print(json.dumps({"company": company, "invoice": invoice["id"], "journal": journal["id"],
                              "directive": code, "attachment": added["attachment"]["id"]}))


anyio.run(main)
```

The same company and record identifiers can be opened in the human workbench.
Use invoice show/history before continuing a human correction; keep stable line
IDs and the latest expected_version, preview explicitly and retain the returned
facts fingerprint. A conflict is not permission to overwrite the other writer.
The program's SDK context exit sends EOF; it does not stop the separately owned
host or delete the company's history or caller-owned output files. Use the
operator-bootstrap cleanup for that disposable host after completing the trial.
