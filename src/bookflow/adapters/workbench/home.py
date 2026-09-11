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

Resolution reads the registry only. It never executes a command, so rendering the home window
runs no business command beyond the company lookup the page already needs.
"""

from __future__ import annotations

from dataclasses import dataclass

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
                id="invoice",
                title="Invoice",
                summary="Bill a customer for work delivered.",
                action=Action("Create an invoice", WRITE, ("invoice post",), "/invoice/post"),
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
                action=Action("Write a credit memo", WRITE),
                waits_on="credit memo commands",
                aside=True,
            ),
            Step(
                id="refund",
                title="Refund",
                summary="Pay a customer back what they are owed.",
                action=Action("Refund a customer", WRITE),
                waits_on="refund commands",
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
                action=Action("Write a purchase order", WRITE),
                waits_on="purchase order commands",
            ),
            Step(
                id="receive-items",
                title="Receive items",
                summary="Record what arrived against the order.",
                action=Action("Receive items", WRITE),
                waits_on="item receipt commands and inventory",
            ),
            Step(
                id="bill",
                title="Enter bill",
                summary="Record what a vendor has charged you.",
                action=Action("Enter a bill", WRITE),
                waits_on="bill commands and accounts payable",
            ),
            Step(
                id="pay-bills",
                title="Pay bills",
                summary="Settle open bills and take them off the payables list.",
                action=Action("Pay bills", WRITE),
                waits_on="bill payment commands",
            ),
            Step(
                id="vendor-check",
                title="Pay a vendor",
                summary="Write a check straight out of a bank account, without entering a bill first.",
                action=Action("Write a check to a vendor", WRITE, ("check post",), "/check/post"),
                aside=True,
            ),
            Step(
                id="vendor-credit",
                title="Vendor credit",
                summary="Record a credit a vendor owes you.",
                action=Action("Enter a vendor credit", WRITE),
                waits_on="vendor credit commands",
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
                action=Action("Reconcile an account", WRITE),
                waits_on="reconciliation commands and cleared-status tracking",
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
                summary="Customer statements, A/R aging, open invoices, A/P aging, unpaid bills, trial balance, profit and loss, balance sheet, general ledger.",
                action=Action(
                    "Choose a report to run",
                    READ,
                    ("report statement", "report ar-aging", "report open-invoices",
                     "report ap-aging", "report unpaid-bills",
                     "report trial-balance", "report profit-and-loss", "report balance-sheet",
                     "report general-ledger"),
                    "/_group/reports",
                ),
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
                id="audit",
                title="Audit trail",
                summary="Who changed what, through which interface, and why.",
                action=Action("Open the audit trail", READ, ("audit list",), "/audit"),
            ),
            Step(
                id="time-tracking",
                title="Time tracking",
                summary="Time against a job, carried through to what you bill and what you pay.",
                action=Action("Track time", WRITE),
                waits_on="a later roadmap decision: time tracking and payroll sit outside the "
                         "goal that is being built now",
                aside=True,
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


@dataclass(frozen=True)
class ResolvedStep:
    step: Step
    live: bool
    href: str | None
    reason: str  # empty when live; otherwise what the tile is waiting on
    offered: bool = True  # False when the commands exist but this company or role does not offer them

    @property
    def planned(self) -> bool:
        """A step the product has not built yet, as opposed to one this credential may not use."""
        return not self.live and self.offered


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

    @property
    def planned(self) -> tuple[ResolvedStep, ...]:
        return tuple(item for item in self.steps if item.planned)


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
