# Submission feedback and useful initial dates

Authorized2026-09-18, independently plan-reviewed. Mobile validation/refusal feedback belongs beside the submitting controls. Preserve detailed errors, recovery and drafts. Custom payment/pay-bill errors move with their existing handlers; a failed connection must not claim the write failed or retry automatically. Initial transaction effective dates default to company-local today; do not fill every date-shaped field. Initial reports use explicit sensible date families ending today. Missing values differ from explicit blanks; edits, submissions, source-derived conversions, drilldowns and saved attempts retain their dates. Apply defaults after existing initializers. No accounting changes.

Verify timezone rollover, fresh transactions and reports, retained blank/invalid attempts, mobile refusal adjacent to buttons, custom failure recovery, and existing date/preset workflows. Independent artifact review before deployment.

## Implemented defaults

- New transaction effective dates: company-local today, including nested deposit dates and custom payment/register workspaces.
- Period reports: January 1 through today; snapshot reports: today. The explicit command/field map lives in `adapters/workbench/date_defaults.py`.
- Company timezone absent/unavailable: UTC fallback. Date presets use the same company day.
- No default replacement on submitted forms, saved edits, explicit blank/invalid fields, source conversions, or query-bearing drilldowns.

## Feedback

Generated forms show a mobile error summary immediately before actions while keeping detailed recovery content. Payment and bill-payment workspaces and deletion confirmations retain their original error blocks beside actions. Credit apply/unapply selects the matching action area. Connection, timeout and non-swapped HTTP failures retain entries and describe the outcome as unconfirmed; they never automatically retry.
