"""The flow board: the declarative map behind the company home window.

Each tile declares three things, and all three are part of one reviewed availability contract:

* the action it advertises, in the user's words, and whether that action reads or writes;
* the command name or names that back it;
* the explicit browser destination a click lands on.

A tile is live only when the backing commands are registered and routed, a destination is
declared, a write-labelled tile is backed by a command that writes -- and a test navigates to
that destination and asserts a usable page. Registration is a discovery fact, not a promise:
a command can be registered and routed while its handler fails or while no browser page exists
for it, so the navigation witness in ``tests/test_home_window.py`` is the third condition and
adding a tile without one is how this contract is broken.

Resolution reads the registry only. It never executes a command. The figures, the attention
list and the recent activity above the tiles come from `overview`, which runs registered read
commands as the signed-in reader and shows what they return.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable
from urllib.parse import urlencode

from bookflow.core import registry

READ = "read"
WRITE = "write"


@dataclass(frozen=True)
class Action:
    """One advertised action: what it says it does, what backs it, and where it lands."""

    label: str
    kind: str  # READ or WRITE, matching what the label promises
    commands: tuple[str, ...] = ()
    destination: str | None = None  # path under /c/{company_id}, e.g. "/invoice/post"


@dataclass(frozen=True)
class Step:
    """One tile on a panel."""

    id: str
    title: str
    summary: str
    action: Action
    waits_on: str = ""  # what a planned step is waiting on, in a sentence a user can read
    aside: bool = False  # hangs beside the chain rather than sitting in the arrow sequence


@dataclass(frozen=True)
class Panel:
    id: str
    title: str
    summary: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class MenuGroup:
    """One entry in the persistent group menu, filtering the existing grouped-noun rendering."""

    label: str
    slug: str
    groups: tuple[str, ...] = ()  # whole navigation groups, by their existing names
    nouns: tuple[str, ...] = ()  # individual nouns, kept under whichever group they belong to


PANELS: tuple[Panel, ...] = (
    Panel(
        id="customers",
        title="Customers",
        summary="Quote the work, bill it, and bring the money in.",
        steps=(
            Step(
                id="customer-list",
                title="Customers",
                summary="Customers, their jobs, and the terms you bill them on.",
                action=Action("Open the customer list", READ, ("customer query",), "/customer"),
            ),
            Step(
                id="estimate",
                title="Estimate",
                summary="Quote work before you do it.",
                action=Action("Write an estimate", WRITE, ("estimate create",), "/estimate/create"),
            ),
            Step(
                id="time-activity",
                title="Time",
                summary="Log the hours somebody worked on a job, billable or not; billable hours wait here to be invoiced.",
                action=Action("Record time worked", WRITE, ("time-activity create",), "/time-activity/create"),
            ),
            Step(
                id="invoice",
                title="Invoice",
                summary="Bill a customer for work delivered.",
                action=Action("Create an invoice", WRITE, ("invoice post",), "/invoice/post"),
            ),
            Step(
                id="billing-groups",
                title="Billing groups",
                summary="Reusable sets of customers you bill the same thing to.",
                action=Action("Open the billing groups", READ, ("billing-group list",), "/billing-group"),
            ),
            Step(
                id="batch-invoice",
                title="Batch invoices",
                summary="Invoice a whole group at once; every invoice still resolves its own customer's terms, tax and prices.",
                action=Action("Invoice a billing group", WRITE, ("batch-invoice post",), "/batch-invoice/post"),
            ),
            Step(
                id="receive-payment",
                title="Receive payment",
                summary="Take money in and settle it against open invoices.",
                action=Action("Receive a customer payment", WRITE, ("payment receive",), "/receive-payments"),
            ),
            Step(
                id="deposit",
                title="Make deposit",
                summary="Group received payments into one bank deposit.",
                action=Action("Make a deposit", WRITE, ("deposit post",), "/deposit/post"),
            ),
            Step(
                id="sales-receipt",
                title="Sales receipt",
                summary="A sale that is paid at the moment of sale, with no invoice in between.",
                action=Action("Record a sales receipt", WRITE, ("sales-receipt post",), "/sales-receipt/post"),
                aside=True,
            ),
            Step(
                id="statement-charge",
                title="Statement charge",
                summary="Charge a customer's account directly for one thing, with no invoice; it ages and settles like one.",
                action=Action("Enter a statement charge", WRITE, ("statement-charge post",),
                              "/statement-charge/post"),
                aside=True,
            ),
            Step(
                id="statement-charges",
                title="Statement charges",
                summary="Every charge entered straight onto an account, newest first.",
                action=Action("Open the statement charges", READ, ("statement-charge query",),
                              "/statement-charge"),
                aside=True,
            ),
            Step(
                id="statement",
                title="Statement",
                summary="A customer's whole account over a period: what they owed, what changed, what is left.",
                action=Action("Open a customer statement", READ, ("report statement",), "/report/statement"),
                aside=True,
            ),
            Step(
                id="credit-memo",
                title="Credit memo",
                summary="Credit a customer for a return or an overcharge.",
                action=Action("Write a credit memo", WRITE, ("credit-memo post",),
                              "/credit-memo/post"),
                aside=True,
            ),
            Step(
                id="refund",
                title="Refund",
                summary="Pay a customer back what they are owed.",
                action=Action("Refund a customer", WRITE, ("customer-refund post",),
                              "/customer-refund/post"),
                aside=True,
            ),
        ),
    ),
    Panel(
        id="vendors",
        title="Vendors",
        summary="Order, receive, record what you owe, and pay it.",
        steps=(
            Step(
                id="vendor-list",
                title="Vendors",
                summary="The people and companies you buy from.",
                action=Action("Open the vendor list", READ, ("vendor query",), "/vendor"),
            ),
            Step(
                id="purchase-order",
                title="Purchase order",
                summary="Order goods or services from a vendor.",
                action=Action("Write a purchase order", WRITE, ("purchase-order post",),
                              "/purchase-order/post"),
            ),
            Step(
                id="receive-items",
                title="Receive items",
                summary="Record what arrived against the order.",
                action=Action("Receive items", WRITE, ("item-receipt post",),
                              "/item-receipt/post"),
            ),
            Step(
                id="bill",
                title="Enter bill",
                summary="Record what a vendor has charged you.",
                action=Action("Enter a bill", WRITE, ("bill post",), "/bill/post"),
            ),
            Step(
                id="pay-bills",
                title="Pay bills",
                summary="Settle open bills and take them off the payables list.",
                action=Action("Pay bills", WRITE, ("bill pay",), "/pay-bills"),
            ),
            Step(
                id="vendor-check",
                title="Pay a vendor",
                summary="Write a check straight out of a bank account, without entering a bill first.",
                action=Action("Write a check to a vendor", WRITE, ("check post",), "/check/post"),
                aside=True,
            ),
            Step(
                id="bill-payments",
                title="Bill payments",
                summary="Find a payment you made and read back which bills it settled.",
                action=Action("Open the bill payment list", READ, ("bill payment query",),
                              "/bill-payment"),
                aside=True,
            ),
            Step(
                id="vendor-credit",
                title="Vendor credit",
                summary="Record a credit a vendor owes you.",
                action=Action("Enter a vendor credit", WRITE, ("vendor-credit post",),
                              "/vendor-credit/post"),
                aside=True,
            ),
        ),
    ),
    Panel(
        id="banking",
        title="Banking",
        summary="Move money and keep the bank accounts true.",
        steps=(
            Step(
                id="registers",
                title="Account registers",
                summary="Open a balance-sheet account and type entries row by row in its register.",
                action=Action("Browse accounts to open a register", READ, ("account query",), "/account"),
            ),
            Step(
                id="journal",
                title="Journal entry",
                summary="Post a balanced entry straight to the ledger.",
                action=Action("Post a journal entry", WRITE, ("journal post",), "/journal/post"),
            ),
            Step(
                id="saved-deposits",
                title="Saved deposits",
                summary="Find deposits, compare current bank effects and open their composition.",
                action=Action("Saved deposits", READ, ("deposit query",), "/deposit"),
            ),
            Step(
                id="banking-deposit",
                title="Make deposit",
                summary="Take undeposited receipts into a bank account.",
                action=Action("Make a deposit", WRITE, ("deposit post",), "/deposit/post"),
            ),
            Step(
                id="write-check",
                title="Write check",
                summary="Pay someone straight out of a bank account.",
                action=Action("Write a check", WRITE, ("check post",), "/check/post"),
            ),
            Step(
                id="card-charge",
                title="Credit card charge",
                summary="Record a purchase put on a company credit card.",
                action=Action("Enter a credit card charge", WRITE, ("card-charge post",),
                              "/card-charge/post"),
            ),
            Step(
                id="transfer",
                title="Transfer funds",
                summary="Move money between two of your own accounts.",
                action=Action("Transfer funds", WRITE, ("transfer post",), "/transfer/post"),
            ),
            Step(
                id="reconcile",
                title="Reconcile",
                summary="Match the register against a bank statement and clear what matches.",
                action=Action("Reconcile an account", WRITE,
                              ("reconcile opening start", "reconcile start", "reconcile mark",
                               "reconcile finish", "reconcile candidates", "reconcile preview"),
                              "/reconcile-opening/start"),
            ),
        ),
    ),
    Panel(
        id="company",
        title="Company",
        summary="The lists, reports and history the rest of the board runs on.",
        steps=(
            Step(
                id="chart-of-accounts",
                title="Chart of accounts",
                summary="Every account the books post to.",
                action=Action("Open the chart of accounts", READ, ("account query",), "/account"),
            ),
            Step(
                id="items",
                title="Items",
                summary="What you sell, and the accounts each sale posts to.",
                action=Action("Open the item list", READ, ("item query",), "/item"),
            ),
            Step(
                id="reports",
                title="Reports",
                summary="Where the money came from and where it went: sales by customer, "
                        "item and rep, expenses by vendor. Customer statements, A/R aging, "
                        "open invoices, A/P aging, unpaid bills, trial balance, profit and "
                        "loss, balance sheet, statement of cash flows, income tax summary, "
                        "general ledger, transaction detail by account, missing checks, "
                        "reconciliation discrepancies, "
                        "inventory valuation and stock status, customer and vendor "
                        "balances, open purchase orders, purchases by vendor and item, "
                        "deposit detail, the transaction list by date and the 1099 summary.",
                action=Action(
                    "Choose a report to run",
                    READ,
                    ("report sales-by-customer", "report sales-by-item", "report sales-by-rep",
                     "report expenses-by-vendor",
                     "report statement", "report ar-aging", "report open-invoices",
                     "report ap-aging", "report unpaid-bills",
                     "report customer-balance-summary", "report customer-balance-detail",
                     "report vendor-balance-summary", "report vendor-balance-detail",
                     "report open-purchase-orders", "report purchases-by-vendor",
                     "report purchases-by-item",
                     "report deposit-detail", "report transaction-list-by-date",
                     "report vendor-1099-summary",
                     "report inventory-valuation", "report stock-status",
                     "report trial-balance", "report profit-and-loss", "report balance-sheet",
                     "report cash-flows", "report income-tax-summary",
                     "report general-ledger", "report transaction-detail",
                     "report missing-checks", "report reconciliation-discrepancy"),
                    "/_group/reports",
                ),
            ),
            Step(
                id="adjust-inventory",
                title="Adjust inventory",
                summary="Set opening stock, or correct what an item holds and what it is worth.",
                action=Action("Adjust inventory", WRITE, ("inventory adjust",), "/inventory/adjust"),
            ),
            Step(
                id="employees",
                title="Employees",
                summary="The people who do the work.",
                action=Action("Open the employee list", READ, ("employee query",), "/employee"),
            ),
            Step(
                id="custom-fields",
                title="Custom fields",
                summary="Extra fields you define and put on your own records.",
                action=Action("Open the custom field definitions", READ, ("custom-field list",), "/custom-field"),
            ),
            Step(
                id="memorized",
                title="Memorized transactions",
                summary="The entries you make again and again, what each one owes, and what is waiting.",
                action=Action("Open the memorized transaction list", READ, ("memorized list",), "/memorized"),
            ),
            Step(
                id="memorized-due",
                title="Enter memorized transactions",
                summary="Enter every memorized transaction whose date has arrived, each at its own date.",
                action=Action("Enter memorized transactions", WRITE, ("memorized process",), "/memorized/process"),
            ),
            Step(
                id="audit",
                title="Audit trail",
                summary="Who changed what, through which interface, and why.",
                action=Action("Open the audit trail", READ, ("audit list",), "/audit"),
            ),
        ),
    ),
)


MENU: tuple[MenuGroup, ...] = (
    MenuGroup("Customers", "customers", groups=("Customers and sales", "Customer work")),
    MenuGroup("Vendors", "vendors", groups=("Vendors and purchases",)),
    MenuGroup("Employees", "employees", groups=("Employees",)),
    MenuGroup("Items", "items", groups=("Items",)),
    MenuGroup("Banking", "banking", nouns=("account", "register", "journal", "rate")),
    MenuGroup("Accounting", "accounting", groups=("Accounting",)),
    MenuGroup("Reports", "reports", nouns=("report",)),
    MenuGroup("Company", "company", groups=("Company", "Hub")),
    MenuGroup("Settings", "settings", groups=("Settings",)),
    MenuGroup("Audit", "audit", groups=("Audit",)),
)

MENU_BY_SLUG: dict[str, MenuGroup] = {entry.slug: entry for entry in MENU}


# ---------------------------------------------------------------- section pages

@dataclass(frozen=True)
class Section:
    """What a menu section opens on: the records a person works with, one "+ New" menu of what can
    be created there, the lists that sit beside those records, and the everyday tasks.

    Every entry is an `Action`, resolved against the registry the way a tile is, so a section never
    links to a command that is missing, unrouted, switched off for this company or beyond this
    role. Generated command pages are not listed here: the finder and All commands reach them.
    """

    slug: str
    records: str | None = None  # the noun whose first records the page opens on
    records_label: str = ""  # the heading over those records
    new: tuple[Action, ...] = ()
    lists: tuple[Action, ...] = ()
    tasks: tuple[Action, ...] = ()
    groups: tuple[tuple[str, tuple[Action, ...]], ...] = ()  # a page of grouped links (Settings)


def _new(label: str, command: str, destination: str) -> Action:
    return Action(label, WRITE, (command,), destination)


def _list(label: str, command: str, destination: str) -> Action:
    return Action(label, READ, (command,), destination)


def _report(label: str, verb: str) -> Action:
    return Action(label, READ, (f"report {verb}",), f"/report/{verb}")


# Where the anchor keeps them: customer, vendor and item profile lists live under Settings rather
# than beside the records that use them.
SECTIONS: tuple[Section, ...] = (
    Section(
        "customers", records="customer", records_label="Customer list",
        new=(
            _new("Invoice", "invoice post", "/invoice/post"),
            _new("Estimate", "estimate create", "/estimate/create"),
            _new("Sales receipt", "sales-receipt post", "/sales-receipt/post"),
            _new("Payment", "payment receive", "/receive-payments"),
            _new("Credit memo", "credit-memo post", "/credit-memo/post"),
            _new("Refund", "customer-refund post", "/customer-refund/post"),
            _new("Statement charge", "statement-charge post", "/statement-charge/post"),
            _new("Time entry", "time-activity create", "/time-activity/create"),
            _new("Work order", "work-order create", "/work-order/create"),
            _new("Proposal", "proposal create", "/proposal/create"),
            _new("Customer", "customer create", "/customer/create"),
            _new("Billing group", "billing-group create", "/billing-group/create"),
        ),
        lists=(
            _list("Invoices", "invoice query", "/invoice"),
            _list("Estimates", "estimate query", "/estimate"),
            _list("Sales receipts", "sales-receipt query", "/sales-receipt"),
            _list("Payments", "payment query", "/payment"),
            _list("Credit memos", "credit-memo query", "/credit-memo"),
            _list("Refunds", "customer-refund query", "/customer-refund"),
            _list("Statement charges", "statement-charge query", "/statement-charge"),
            _list("Proposals", "proposal query", "/proposal"),
            _list("Work orders", "work-order query", "/work-order"),
            _list("Time entries", "time-activity query", "/time-activity"),
            _list("Billing groups", "billing-group list", "/billing-group"),
            _list("Invoice batches", "batch-invoice query", "/batch-invoice"),
        ),
        tasks=(
            Action("Receive payments", WRITE, ("payment receive",), "/receive-payments"),
            Action("Invoice a billing group", WRITE, ("batch-invoice post",), "/batch-invoice/post"),
            Action("Make a deposit", WRITE, ("deposit post",), "/deposit/post"),
            _report("Customer statement", "statement"),
            _report("A/R aging summary", "ar-aging"),
            _report("Open invoices", "open-invoices"),
            _report("Customer balance summary", "customer-balance-summary"),
            _report("Customer balance detail", "customer-balance-detail"),
        ),
    ),
    Section(
        "vendors", records="vendor", records_label="Vendor list",
        new=(
            _new("Bill", "bill post", "/bill/post"),
            _new("Check", "check post", "/check/post"),
            _new("Credit card charge", "card-charge post", "/card-charge/post"),
            _new("Credit card credit", "card-credit post", "/card-credit/post"),
            _new("Purchase order", "purchase-order post", "/purchase-order/post"),
            _new("Item receipt", "item-receipt post", "/item-receipt/post"),
            _new("Vendor credit", "vendor-credit post", "/vendor-credit/post"),
            _new("Vendor", "vendor create", "/vendor/create"),
        ),
        lists=(
            _list("Bills", "bill query", "/bill"),
            _list("Bill payments", "bill payment query", "/bill-payment"),
            _list("Checks", "check query", "/check"),
            _list("Credit card charges", "card-charge query", "/card-charge"),
            _list("Credit card credits", "card-credit query", "/card-credit"),
            _list("Purchase orders", "purchase-order query", "/purchase-order"),
            _list("Item receipts", "item-receipt query", "/item-receipt"),
            _list("Vendor credits", "vendor-credit query", "/vendor-credit"),
            _list("Sales tax payments", "sales-tax payment query", "/sales-tax-payment"),
        ),
        tasks=(
            Action("Pay bills", WRITE, ("bill pay",), "/pay-bills"),
            Action("Pay sales tax", WRITE, ("sales-tax pay",), "/sales-tax/pay"),
            Action("Sales tax owed", READ, ("sales-tax liability",), "/sales-tax/liability"),
            _report("A/P aging summary", "ap-aging"),
            _report("Unpaid bills", "unpaid-bills"),
            _report("Expenses by vendor", "expenses-by-vendor"),
            _report("Vendor balance summary", "vendor-balance-summary"),
            _report("Vendor balance detail", "vendor-balance-detail"),
            _report("Open purchase orders", "open-purchase-orders"),
            _report("Purchases by vendor summary", "purchases-by-vendor"),
            _report("Purchases by item summary", "purchases-by-item"),
            _report("1099 summary", "vendor-1099-summary"),
        ),
    ),
    Section(
        "employees", records="employee", records_label="Employee list",
        new=(
            _new("Employee", "employee create", "/employee/create"),
            _new("Other name", "other-name create", "/other-name/create"),
            _new("Time entry", "time-activity create", "/time-activity/create"),
        ),
        lists=(
            _list("Other names", "other-name list", "/other-name"),
            _list("Time entries", "time-activity query", "/time-activity"),
        ),
    ),
    Section(
        "items", records="item", records_label="Item list",
        new=(
            _new("Item", "item create", "/item/create"),
            _new("Inventory adjustment", "inventory adjust", "/inventory/adjust"),
        ),
        tasks=(
            Action("Adjust inventory", WRITE, ("inventory adjust",), "/inventory/adjust"),
            _report("Inventory valuation summary", "inventory-valuation"),
            _report("Inventory stock status by item", "stock-status"),
            _report("Sales by item", "sales-by-item"),
        ),
    ),
    Section(
        "banking",
        new=(
            _new("Check", "check post", "/check/post"),
            _new("Deposit", "deposit post", "/deposit/post"),
            _new("Transfer", "transfer post", "/transfer/post"),
            _new("Credit card charge", "card-charge post", "/card-charge/post"),
            _new("Credit card credit", "card-credit post", "/card-credit/post"),
            _new("Journal entry", "journal post", "/journal/post"),
        ),
        lists=(
            _list("Deposits", "deposit query", "/deposit"),
            _list("Checks", "check query", "/check"),
            _list("Transfers", "transfer query", "/transfer"),
            _list("Credit card charges", "card-charge query", "/card-charge"),
            _list("Credit card credits", "card-credit query", "/card-credit"),
            _list("Exchange rates", "rate query", "/rate"),
        ),
        tasks=(
            Action("Open an account register", READ, ("account list",), "/_registers"),
            Action("Reconcile an account", WRITE,
                   ("reconcile opening start", "reconcile start", "reconcile mark",
                    "reconcile finish", "reconcile candidates", "reconcile preview"),
                   "/reconcile-opening/start"),
            Action("Make a deposit", WRITE, ("deposit post",), "/deposit/post"),
            Action("Transfer funds", WRITE, ("transfer post",), "/transfer/post"),
            _report("Missing checks", "missing-checks"),
            _report("Reconciliation discrepancy", "reconciliation-discrepancy"),
            _report("Deposit detail", "deposit-detail"),
        ),
    ),
    Section(
        "accounting", records="account", records_label="Chart of accounts",
        new=(
            _new("Journal entry", "journal post", "/journal/post"),
            _new("Account", "account create", "/account/create"),
            _new("Memorized transaction", "memorized create", "/memorized/create"),
        ),
        lists=(
            _list("Journal entries", "journal query", "/journal"),
            _list("Memorized transactions", "memorized list", "/memorized"),
            _list("Memorized groups", "memorized-group list", "/memorized-group"),
            _list("Exchange rates", "rate query", "/rate"),
        ),
        tasks=(
            Action("Enter memorized transactions", WRITE, ("memorized process",), "/memorized/process"),
            _report("Trial balance", "trial-balance"),
            _report("General ledger", "general-ledger"),
            _report("Transaction list by date", "transaction-list-by-date"),
            _report("Profit and loss", "profit-and-loss"),
            _report("Balance sheet", "balance-sheet"),
        ),
    ),
    Section(
        "company",
        lists=(
            _list("Attachments", "attachment list", "/attachment"),
            _list("Notes", "note list", "/note"),
            _list("Directives", "directive list", "/directive"),
            _list("Memorized transactions", "memorized list", "/memorized"),
        ),
        tasks=(
            Action("Company information", READ, ("company show",), "/company/self"),
            Action("Preferences", WRITE, ("company update",), "/company/self/update"),
            Action("Users and permissions", READ, (), "/users"),
            Action("Audit trail", READ, ("audit list",), "/audit"),
            Action("Enter memorized transactions", WRITE, ("memorized process",), "/memorized/process"),
            Action("Rename the company", WRITE, ("company rename",), "/company/rename"),
            Action("Back up the company", WRITE, ("company backup",), "/company/backup"),
            Action("Restore a backup", WRITE, ("company restore",), "/company/restore"),
            Action("Settings and lists", READ, (), "/_group/settings"),
        ),
    ),
    Section(
        "settings",
        groups=(
            ("Customer and vendor profile lists", (
                _list("Sales reps", "sales-rep list", "/sales-rep"),
                _list("Customer types", "customer-type list", "/customer-type"),
                _list("Vendor types", "vendor-type list", "/vendor-type"),
                _list("Job types", "job-type list", "/job-type"),
                _list("Terms", "term list", "/term"),
                _list("Customer messages", "customer-message list", "/customer-message"),
                _list("Payment methods", "payment-method list", "/payment-method"),
                _list("Ship methods", "ship-method list", "/ship-method"),
            )),
            ("Items and prices", (
                _list("Price levels", "price-level list", "/price-level"),
                _list("Units of measure", "unit-of-measure list", "/unit-of-measure"),
                _list("Item categories", "item-category list", "/item-category"),
                _list("Sales tax codes", "sales-tax-code list", "/sales-tax-code"),
            )),
            ("Accounting", (
                _list("Chart of accounts", "account list", "/account"),
                _list("Classes", "class list", "/class"),
                _list("Custom fields", "custom-field list", "/custom-field"),
            )),
            ("Company setup", (
                Action("Company information", READ, ("company show",), "/company/self"),
                Action("Preferences", WRITE, ("company update",), "/company/self/update"),
                Action("Users and permissions", READ, (), "/users"),
                Action("Apply a setup profile", WRITE, ("profile apply",), "/profile/apply"),
                Action("Apply a chart of accounts", WRITE, ("chart apply",), "/chart/apply"),
                Action("Compact the company file", WRITE, ("company compact",), "/company/compact"),
            )),
            ("Tools", (
                Action("Audit trail", READ, ("audit list",), "/audit"),
                Action("Undo a change", WRITE, ("undo",), "/undo"),
                Action("All commands", READ, (), "/_all"),
            )),
        ),
    ),
)

SECTION_BY_SLUG: dict[str, Section] = {section.slug: section for section in SECTIONS}

# Which section a command noun belongs to, for the finder and for the menu's current section.
# The registry's navigation group gives the default; these nouns sit where the anchor keeps them.
_GROUP_SECTION = {
    "Customers and sales": "customers", "Customer work": "customers", "Vendors and purchases": "vendors",
    "Vendors": "vendors", "Employees": "employees", "Items": "items", "Accounting": "accounting",
    "Settings": "settings", "Audit": "audit", "Company": "company", "Hub": "company",
}
NOUN_SECTION = {
    **{noun: "settings" for noun in (
        "sales-rep", "customer-type", "vendor-type", "job-type", "term", "customer-message",
        "payment-method", "ship-method", "price-level", "unit-of-measure", "item-category",
        "sales-tax-code", "class", "custom-field", "profile", "chart")},
    **{noun: "banking" for noun in (
        "register", "deposit", "reconcile", "reconcile opening", "transfer", "rate")},
    **{noun: "vendors" for noun in ("purchase-order", "item-receipt", "sales-tax", "sales-tax payment")},
    **{noun: "customers" for noun in (
        "payment operation", "payment preview", "payment recovery", "payment selection", "payment settlement")},
    "report": "reports", "undo": "settings",
}


def section_of(noun: str, group: str) -> str:
    """The menu section slug a noun belongs to, given its registry navigation group."""
    return NOUN_SECTION.get(noun) or _GROUP_SECTION.get(group, "company")


def _live(action: Action, lookup, routed: set[str], permits) -> bool:
    """A section link is live when every backing command is registered, routed and offered here."""
    if action.destination is None:
        return False
    for name in action.commands:
        command = lookup(name)
        if command is None or name not in routed or not permits(command):
            return False
    return True


def resolve_section(company_id: str, section: Section, *, get=None, routed=None, permits=None) -> dict[str, Any]:
    """The live links of one section page, as dicts of label and href. Reads the registry only."""
    lookup = registry.get if get is None else get
    listing = registry.routed_commands if routed is None else routed
    allow = (lambda command: True) if permits is None else permits
    for action in (*section.new, *section.lists, *section.tasks, *(a for _, links in section.groups for a in links)):
        for name in action.commands:
            lookup(name)  # import before the routed listing, as `resolve` does
    routed_names = {command.name for command in listing()}

    def links(actions: tuple[Action, ...]) -> list[dict[str, str]]:
        return [dict(label=action.label, href=f"/c/{company_id}{action.destination}")
                for action in actions if _live(action, lookup, routed_names, allow)]

    return dict(
        slug=section.slug,
        new=links(section.new),
        lists=links(section.lists),
        tasks=links(section.tasks),
        groups=[(title, found) for title, actions in section.groups if (found := links(actions))],
    )

# The register chooser lists balance-sheet accounts in the order a bookkeeper reaches for them.
REGISTER_TYPES: tuple[tuple[str, str], ...] = (
    ("bank", "Bank accounts"),
    ("credit_card", "Credit cards"),
    ("accounts_receivable", "Accounts receivable"),
    ("accounts_payable", "Accounts payable"),
    ("other_current_asset", "Other current assets"),
    ("fixed_asset", "Fixed assets"),
    ("other_asset", "Other assets"),
    ("other_current_liability", "Other current liabilities"),
    ("long_term_liability", "Long-term liabilities"),
    ("equity", "Equity"),
)


def register_groups(accounts: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group `account list` rows that have a register (balance-sheet accounts) under their type."""
    labels = dict(REGISTER_TYPES)
    grouped: dict[str, list[dict]] = {}
    for account in accounts:
        if account.get("statement_family") == "balance_sheet":
            grouped.setdefault(account["type"], []).append(account)
    order = [kind for kind, _ in REGISTER_TYPES] + sorted(set(grouped) - set(labels))
    return [(labels.get(kind, kind.replace("_", " ").capitalize()), grouped[kind]) for kind in order if kind in grouped]


@dataclass(frozen=True)
class ResolvedStep:
    step: Step
    live: bool
    href: str | None
    reason: str  # empty when live; otherwise what the tile is waiting on
    offered: bool = True  # False when the commands exist but this company or role does not offer them


@dataclass(frozen=True)
class ResolvedPanel:
    panel: Panel
    steps: tuple[ResolvedStep, ...]

    @property
    def chain(self) -> tuple[ResolvedStep, ...]:
        return tuple(item for item in self.steps if not item.step.aside)

    @property
    def asides(self) -> tuple[ResolvedStep, ...]:
        return tuple(item for item in self.steps if item.step.aside)


def _availability(step: Step, commands: dict[str, registry.Command | None], routed: set[str],
                  permits) -> tuple[str, bool]:
    """Return what the step waits on (empty when live) and whether it is offered here at all."""
    action = step.action
    if not action.commands:
        return step.waits_on or "a command that performs this action", True
    missing = [name for name in action.commands if commands.get(name) is None]
    if missing:
        return step.waits_on or "these commands, which are not registered yet: " + ", ".join(missing), True
    unrouted = [name for name in action.commands if name not in routed]
    if unrouted:
        return step.waits_on or ("a form an agent and a browser can both reach; these run only on "
                                 "the host's own machine: " + ", ".join(unrouted)), True
    if action.destination is None:
        return step.waits_on or "a browser page to land on; the commands exist but nothing shows them", True
    if action.kind == WRITE and not any(commands[name].is_write for name in action.commands):
        return step.waits_on or ("a command that writes; the commands behind this tile only read, "
                                 "so the tile would promise more than it does"), True
    withheld = [name for name in action.commands if not permits(commands[name])]
    if withheld:
        return ("this company's preferences or your role, which do not offer "
                + ", ".join(withheld)), False
    return "", True


def resolve(company_id: str, *, panels: tuple[Panel, ...] = PANELS, get=None, routed=None,
            permits=None) -> tuple[ResolvedPanel, ...]:
    """Resolve every tile against the registry. Reads only; runs no command.

    ``permits`` receives a resolved command and answers whether this company and this credential
    offer it, so a tile is never a link to a form the company has switched off or the role forbids.
    """
    lookup = registry.get if get is None else get
    listing = registry.routed_commands if routed is None else routed
    allow = (lambda command: True) if permits is None else permits
    names = {name for panel in panels for step in panel.steps for name in step.action.commands}
    # Resolve every declared name first: a lookup may import a command module, and the routed
    # listing must be taken after those imports so the two questions see one registry.
    commands = {name: lookup(name) for name in names}
    routed_names = {command.name for command in listing()}
    resolved = []
    for panel in panels:
        steps = []
        for step in panel.steps:
            reason, offered = _availability(step, commands, routed_names, allow)
            live = not reason
            href = f"/c/{company_id}{step.action.destination}" if live else None
            steps.append(ResolvedStep(step=step, live=live, href=href, reason=reason, offered=offered))
        resolved.append(ResolvedPanel(panel=panel, steps=tuple(steps)))
    return tuple(resolved)


def find(resolved: tuple[ResolvedPanel, ...], step_id: str) -> ResolvedStep | None:
    for panel in resolved:
        for item in panel.steps:
            if item.step.id == step_id:
                return item
    return None


def _check_map() -> None:
    """The map is data; keep it well formed at import so a typo cannot reach a page."""
    seen: set[str] = set()
    for panel in PANELS:
        for step in panel.steps:
            if step.id in seen:
                raise ValueError(f"duplicate flow step id {step.id!r}")
            seen.add(step.id)
            if step.action.kind not in (READ, WRITE):
                raise ValueError(f"{step.id}: action kind must be {READ!r} or {WRITE!r}")
            if step.action.destination is not None and not step.action.destination.startswith("/"):
                raise ValueError(f"{step.id}: destination must be a path under the company")
            if not step.action.commands and not step.waits_on:
                raise ValueError(f"{step.id}: a step with no command must say what it waits on")


_check_map()


# ---------------------------------------------------------------- the figures above the tiles

Ask = Callable[[str, dict[str, Any]], "dict[str, Any] | None"]
ATTENTION_ROWS = 5


def overview(company_id: str, today: str, ask: Ask) -> dict[str, Any]:
    """The Overview's figures, what needs attention and recent activity, from read commands.

    `ask(name, input)` runs a registered command as the signed-in reader and answers None when
    that reader may not run it or it fails, so a figure the reader cannot read is simply absent.
    Every figure is one report's own total, which covers the whole report whatever page of rows
    comes back, so each read asks for no more rows than the attention lists show. Nothing here
    adds amounts up: any interface can print the same figure by running the same report.
    """
    base = f"/c/{company_id}"
    report = lambda slug, **fields: f"{base}/report/{slug}?" + urlencode({f"f:{k}": v for k, v in fields.items()}, safe=":")
    month_start = today[:8] + "01"
    week = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    figures: list[dict[str, Any]] = []
    attention: dict[str, Any] = {}

    # Closing cash is the sum of the bank accounts on the balance sheet for date_to, on either basis.
    flows = ask("report cash-flows", {"date_from": month_start, "date_to": today, "limit": 1})
    if flows is not None:
        figures.append(dict(key="cash", label="Cash", value=flows["totals"]["closing_cash"],
                            note="In the bank today",
                            href=report("cash-flows", date_from=month_start, date_to=today)))

    aging = ask("report ar-aging", {"as_of": today, "limit": 1})
    if aging is not None:
        figures.append(dict(key="receivable", label="Owed to you", value=aging["totals"]["total"],
                            note="Open receivables", href=report("ar-aging", as_of=today)))

    overdue = ask("report open-invoices", {"as_of": today, "past_due_only": True, "limit": ATTENTION_ROWS})
    if overdue is not None:
        figures.append(dict(key="overdue", label="Overdue", value=overdue["totals"]["balance"],
                            note="Invoices past due" if overdue["rows"] else "Nothing past due",
                            href=report("open-invoices", as_of=today, past_due_only="true")))
        attention["overdue"] = [dict(row, href=f"{base}/{row['document_type'].replace('_', '-')}/{row['transaction_id']}")
                                for row in overdue["rows"]]
        attention["overdue_more"] = bool(overdue.get("next_cursor"))
        attention["overdue_href"] = figures[-1]["href"]

    # What the company owes is Accounts Payable: the A/P aging total, which is the balance
    # sheet's Accounts Payable and the vendor balance summary's total for the same day. It
    # counts items received but not yet billed and unapplied vendor credits, which a list of
    # unpaid bills does not.
    payable = ask("report ap-aging", {"as_of": today, "limit": 1})
    if payable is not None:
        figures.append(dict(key="payable", label="You owe", value=payable["totals"]["total"],
                            note="Accounts payable", href=report("ap-aging", as_of=today)))

    # Unpaid bills come oldest due date first, so the first rows are the overdue and the soonest
    # due; one row past what is shown says whether there are more due within the week.
    bills = ask("report unpaid-bills", {"as_of": today, "limit": ATTENTION_ROWS + 1})
    if bills is not None:
        soon = [dict(row, href=f"{base}/bill/{row['transaction_id']}") for row in bills["rows"] if row["due_date"] <= week]
        attention["bills"] = soon[:ATTENTION_ROWS]
        attention["bills_more"] = len(soon) > ATTENTION_ROWS
        attention["bills_href"] = report("unpaid-bills", as_of=today)

    month = ask("report profit-and-loss", {"date_from": month_start, "date_to": today, "limit": 1})
    if month is not None:
        figures.append(dict(key="income", label="Income this month", value=month["totals"]["income"],
                            note=month["totals"]["net_income"], note_label="Net income",
                            href=report("profit-and-loss", date_from=month_start, date_to=today)))

    events = ask("audit list", {"limit": ATTENTION_ROWS})
    activity = None if events is None else [dict(event, href=f"{base}/audit/{event['id']}") for event in events["items"]]
    return dict(figures=figures, attention=attention, activity=activity, today=today,
                uncategorized=_uncategorized(company_id, today, ask, report))


def _uncategorized(company_id: str, today: str, ask: Ask, report) -> dict[str, Any] | None:
    """What waits in the Uncategorized (Ask My Accountant) accounts, or None when nothing does.

    Each account's count and amount are the read command's own figures. An income or expense
    account's register is its general ledger, so each account links there, from its oldest
    waiting entry to today; each listed entry opens its own document.
    """
    from bookflow.adapters.workbench.transaction_detail import document_link
    waiting = ask("account uncategorized", {"limit": ATTENTION_ROWS})
    if waiting is None or not waiting["count"]:
        return None
    labels = {account["account_id"]: account["label"] for account in waiting["accounts"]}
    accounts = [dict(account, href=report("general-ledger", date_from=account["oldest_date"],
                                          date_to=max(account["oldest_date"], today), account=account["account_id"]))
                for account in waiting["accounts"] if account["count"]]
    entries = [dict(entry, account_label=labels.get(entry["account_id"], ""), href=document_link(company_id, entry))
               for entry in waiting["entries"]]
    return dict(count=waiting["count"], accounts=accounts, entries=entries, more=waiting["more"])


# ---------------------------------------------------------------- the command finder

# A generated page whose title reads as machinery gets the words a person would search for.
FINDER_LABELS = {
    "customer link-vendor": "Link a customer to a vendor",
    "customer unlink-vendor": "Unlink a customer from a vendor",
}


def finder_index(company_id: str, grouped, *, permits, heading, plural, selector) -> list[dict[str, Any]]:
    """Every task and command page this reader may open, by name, for the header's finder.

    ``grouped`` is the company's (group, [(noun, verbs)]) rows already filtered to what this company
    and role offer, the same rows All commands renders; ``heading(noun, verb)`` and ``plural(noun)``
    are the workbench's page titles and ``selector(command, noun)`` says whether a show needs a record.
    Tasks come first, in the words of the Overview and the section pages; every generated command
    page follows under its section. A record-level action (edit, void, link…) is reached from the
    record's own page, so it is not listed on its own. Reads the registry only.
    """
    base = f"/c/{company_id}"
    labels = {entry.slug: entry.label for entry in MENU}
    entries: dict[str, dict[str, Any]] = {}

    def add(label: str, href: str, section: str, kind: str, *keywords: str) -> None:
        entry = entries.get(href)
        if entry is None:
            entries[href] = dict(label=label, href=href, section=section, kind=kind, keywords=list(keywords))
        else:
            entry["keywords"] += [word for word in (label, *keywords) if word not in entry["keywords"]]

    add("Overview", base + "/", "Menu", "section")
    for entry in MENU:
        add(entry.label, f"{base}/_group/{entry.slug}", "Menu", "section")
    for panel in resolve(company_id, permits=permits):
        for item in panel.steps:
            if item.live:
                add(item.step.action.label, item.href, panel.panel.title, "task", item.step.title, *item.step.action.commands)
    for section in SECTIONS:
        found = resolve_section(company_id, section, permits=permits)
        where = labels.get(section.slug, "")
        for link in found["new"]:
            add("New " + link["label"][0].lower() + link["label"][1:], link["href"], where, "task")
        for link in (*found["tasks"], *found["lists"], *(item for _, items in found["groups"] for item in items)):
            add(link["label"], link["href"], where, "task")
    for group, nouns in grouped:
        for noun, verbs in nouns:
            where = labels.get(section_of(noun, group), group)
            noun_base = f"{base}/{noun.replace(' ', '-')}"
            query = registry.get(f"{noun} query")
            if any(verb.verb == "list" for verb in verbs) or (query is not None and not query.local_only and permits(query)):
                add(plural(noun), noun_base, where, "page", f"{noun} list")
            for verb in verbs:
                if not verb.verb:
                    add(heading(noun, ""), noun_base, where, "page", verb.name)
                elif verb.verb == "list":
                    continue
                elif verb.verb == "show" and not selector(verb, noun):
                    add(heading(noun, "show"), noun_base + "/self", where, "page", verb.name)
                elif verb.verb == "show":
                    # The show form picks one record and opens it.
                    title = heading(noun, "show").lower()
                    add(("Open an " if title[:1] in "aeiou" and not title.startswith("uni") else "Open a ") + title, f"{noun_base}/show", where, "page", verb.name)
                elif not verb.version_source:
                    add(FINDER_LABELS.get(verb.name) or heading(noun, verb.verb), f"{noun_base}/{verb.verb}", where, "page", verb.name)
    add("All commands", base + "/_all", labels.get("settings", ""), "task", "advanced tools")
    return list(entries.values())
