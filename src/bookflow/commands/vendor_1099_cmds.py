"""`vendor 1099-opening`: what a 1099 vendor was paid in a year before the books began here (R179)."""
from __future__ import annotations

from bookflow.company import vendor_1099_openings as openings
from bookflow.core.registry import Applied, Plan, command


@command("vendor 1099-opening", scope="company", required_role="standard", capability="vendor",
         description=("Set what a 1099 vendor was paid in a year before the company's books began here, from January 1 "
                      "through `as_of`, as the old books' 1099 Summary shows it, so this year's 1099 summary is the whole "
                      "year's: `report vendor-1099-summary` adds it to the vendor's payments when its dates include `as_of`. "
                      "`cutover apply` sets it from the old books' 1099 Summary. Setting it again replaces it; 0.00 clears "
                      "it. The vendor must be marked eligible for a 1099."),
         input_model=openings.Vendor1099OpeningInput, output_model=openings.Vendor1099OpeningOutput,
         writes={"company"}, positional=["vendor"], accepts_idempotency_key=True,
         error_codes=["E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_AMOUNT_PRECISION"])
def vendor_1099_opening(inp, ctx, s) -> Plan:
    planned = openings.plan(s, ctx, inp)
    return Plan(preview=openings.output(planned, written=False), data={"input": inp})


@vendor_1099_opening.applier
def _apply(plan: Plan, ctx, s) -> Applied:
    planned = openings.plan(s, ctx, plan.data["input"])  # re-derived under the write transaction
    if not planned.changed:
        return Applied(openings.output(planned, written=False), [], "no change")
    out, touched = openings.apply(s, ctx, planned, "vendor 1099-opening")
    return Applied(out, touched, f"set {planned.vendor['name']}'s {out.year} 1099 payments before the move-in", audited=True)


VENDOR_1099_COMMANDS = [vendor_1099_opening]
