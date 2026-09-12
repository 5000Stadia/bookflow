"""The one way to turn a saved document into bytes a customer can receive.

`render` takes a `read` callable and a document identity. It takes no request, no
response and no session, so the web route that serves a print, a command that attaches a
copy to the record, and a future mail sender all call the same function and get the same
bytes for the same document. Adding a delivery route means calling this; it never means
laying a document out again.

    from bookflow.documents import render
    pdf = render(read, company_id, "invoice", {"document": invoice_id})
    pdf.filename, pdf.media_type, pdf.content

`read(command_name, input, company_id)` runs a registered command as whoever is asking,
which is what keeps permission and company isolation exactly where they already are: a
principal who may not read the invoice cannot print it either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from bookflow.documents.model import BUILDERS, Read, build

# The kinds are whatever has a builder. Written out a second time they agreed for as long as
# nobody added one, which is the only interval in which two copies of a set ever agree.
KINDS = tuple(BUILDERS)
MEDIA_TYPE = "application/pdf"
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Rendered:
    """Finished bytes and the name to save or attach them under."""

    kind: str
    filename: str
    media_type: str
    content: bytes
    title: str


def filename_for(kind: str, parts) -> str:
    """A filename a person can recognise in a downloads folder or an attachment list."""
    stem = "-".join(UNSAFE.sub("-", str(part)).strip("-") for part in parts if part)
    return f"{kind}-{stem}.pdf" if stem else f"{kind}.pdf"


def render(read: Read, company_id: str, kind: str, identity: Mapping[str, Any]) -> Rendered:
    """Produce one document as PDF bytes.

    `kind` is one of KINDS. `identity` is `{"document": <id>}` for an invoice, sales
    receipt or estimate, and `{"customer": <id>, "date_from": ..., "date_to": ...}` for a
    customer statement, which is identified by whose account it is and over what period.
    """
    from bookflow.documents.pdf import to_pdf

    document = build(read, company_id, kind, identity)
    if kind == "statement":
        parts = (document.parties[0].name if document.parties else None,
                 dict(document.facts).get("Statement date"))
    else:
        parts = (document.number,)
    return Rendered(kind=kind, filename=filename_for(kind, parts), media_type=MEDIA_TYPE,
                    content=to_pdf(document), title=document.name or document.title)
