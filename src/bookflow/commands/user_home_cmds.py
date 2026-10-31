"""`user home set`, `show` and `clear` (R191, design/point-of-sale.md §8).

A person's Home is the workbench page they land on when they sign in and when they press Home. Setting one's own is
open to every signed-in actor; setting, reading or clearing another person's needs the capability `user.manage`,
asked through `hub.capabilities.require_hub`, the one check a person and an agent both pass through. A page is
accepted only if it is a workbench page the person may open now, by their effective capabilities in that company.
Starting location is not permission: what they may do is still decided at the command boundary.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import LazyModule
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, command

homes = LazyModule("bookflow.hub.user_homes")

AUTHORIZATION = ("any signed-in person or agent for their own Home; another person's needs user.manage (an installation "
                 "administrator, or an agent its principal granted user.manage)")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Who(_Strict):
    user: str | None = Field(None, max_length=64, description="Username or id of another person; needs user.manage. Omit for your own.")


class HomeSetInput(_Who):
    page: str = Field(max_length=420, description=(
        "The workbench page, as the part of its address after /c/<company>/ (such as `report/profit-and-loss`, "
        "`pick-list` or `_group/items`), or its whole path `/c/<company>/...`; empty for the company's Overview."))
    company: str | None = Field(None, max_length=200, description=(
        "Company the page is in, by name or id. Taken from the page's own path when it is given as /c/<company>/...; "
        "otherwise needed unless the person sees exactly one company."))
    expected_version: int | None = Field(None, ge=1, description="The Home's current `version` from `user home show`; a stale one refuses.")


class HomeClearInput(_Who):
    expected_version: int | None = Field(None, ge=1, description="The Home's current `version` from `user home show`; a stale one refuses.")


class HomeShowInput(_Who):
    pass


class HomeView(BaseModel):
    company_id: str
    company_name: str | None = Field(None, description="Null when the company is no longer here.")
    page: str = Field(description="The page under /c/<company>/; empty for the Overview.")
    url: str = Field(description="Where the Home button and signing in land: /c/<company>/<page>.")
    available: bool = Field(description="Whether the person may open it now; when false they land on the Overview.")
    unavailable_reason: str | None = None
    version: int


class HomeOutput(WriteOutput):
    user_id: str
    username: str
    home: HomeView | None = Field(None, description="Null when no Home is set: signing in lands as it always has.")
    changed: bool = False
    message: str


def _target(s, selector: str | None) -> dict[str, Any]:
    from bookflow.commands.host_cmds import _find_user
    if selector is None:
        row = _find_user(s, s.actor.id)
    else:
        row = _find_user(s, selector)
    if row is None or row["kind"] == "system":
        raise BookflowError("E_USER_NOT_FOUND", details={"user": selector})
    return row


def authorize(inp, ctx: Context, s) -> dict[str, Any]:
    """Self always; another person only with user.manage, for a person and an agent alike. The capability is asked
    before the name is looked up, so whether someone exists is answered only to those who may manage them."""
    from bookflow.commands.host_cmds import _is_self
    if inp.user is not None and not _is_self(s, inp.user):
        from bookflow.hub.capabilities import require_hub
        require_hub(s, "user.manage", getattr(s, "_permission_command", None) or "user home set")
    person = _target(s, inp.user)
    if person["id"] != s.actor.id:
        if person["kind"] != "human" and not isinstance(inp, HomeShowInput):  # reading an agent's own Home is fine
            raise BookflowError("E_VALIDATION", details={"fields": [
                {"field": "user", "problem": "a Home belongs to a person; an agent has no workbench to land in"}]})
    return person


def _view(s, user_id: str, home: dict[str, Any] | None) -> HomeView | None:
    if home is None:
        return None
    available, why, company = homes.availability(s, user_id, home)
    url = f"/c/{home['company_id']}/" + (home["path"] if home["path"] else "")
    return HomeView(company_id=home["company_id"], company_name=company["display_name"] if company else None,
                    page=home["path"], url=url, available=available, unavailable_reason=why, version=home["version"])


def _check_version(home, expected: int | None) -> None:
    if expected is not None and (home is None or home["version"] != expected):
        raise BookflowError("E_VERSION_CONFLICT", details={"expected_version": expected,
                                                           "current_version": home["version"] if home else None})


def _company_and_path(s, inp: HomeSetInput) -> tuple[dict[str, Any], str]:
    from bookflow.core.dispatch import resolve_company
    from bookflow.hub import access
    page, selector = inp.page.strip(), inp.company
    if page.startswith("/c/"):
        rest = page[3:]
        named, _, page = rest.partition("/")
        if selector is not None and named and resolve_company(s, selector, "option")["id"] != \
                resolve_company(s, named, "option")["id"]:
            raise BookflowError("E_VALIDATION", details={"fields": [
                {"field": "company", "problem": "the page's path names a different company"}]})
        selector = selector or named or None
    if selector is None:
        import sqlalchemy as sa
        from bookflow.hub import schema as h
        visible = s.hub.conn.execute(sa.select(h.companies.c.id).where(access.visible_company_filter(s))).all()
        if len(visible) != 1:
            raise BookflowError("E_VALIDATION", details={"fields": [
                {"field": "company", "problem": "give the company the page is in"}]})
        selector = visible[0][0]
    return resolve_company(s, selector, "option"), page


user_home_set = command("user home set", scope="hub", capability="user",
                        description=("Choose the workbench page a person lands on when they sign in and when they press "
                                     "Home, such as the POS, the pick list or a report. Only a page the person may open "
                                     "is accepted. A Home decides where they start, never what they may do."),
                        input_model=HomeSetInput, output_model=HomeOutput, writes={"hub"},
                        error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT",
                                     "E_COMPANY_NOT_FOUND", "E_COMPANY_AMBIGUOUS"],
                        authorization=AUTHORIZATION)
@user_home_set
def plan_home_set(inp: HomeSetInput, ctx: Context, s) -> Plan:
    from bookflow.adapters.workbench import home as Home
    person = authorize(inp, ctx, s)
    company, raw_path = _company_and_path(s, inp)
    path = Home.normalize_page(raw_path)
    if path is None:
        raise homes.refuse_page(raw_path, "that is not a workbench page address")
    current = homes.read(s, person["id"])
    _check_version(current, inp.expected_version)
    candidate = dict(user_id=person["id"], company_id=company["id"], path=path,
                     version=(current["version"] + 1) if current else 1)
    available, why, _ = homes.availability(s, person["id"], candidate)
    if not available:
        raise homes.refuse_page(path, why)
    changed = current is None or (current["company_id"], current["path"]) != (company["id"], path)
    where = f"{company['display_name']}: " + (path or "the Overview")
    message = (f"{person['username']}'s Home is {where}. Signing in and the Home button land there."
               if changed else f"{person['username']}'s Home was already {where}.")
    out = HomeOutput(user_id=person["id"], username=person["username"], changed=changed, message=message,
                     home=_view(s, person["id"], candidate if changed else current))
    return Plan(out, data={"person": person, "current": current, "company_id": company["id"], "path": path,
                           "changed": changed})


plan_home_set.authorize_input = authorize


@user_home_set.applier
def apply_home_set(plan: Plan, ctx: Context, s) -> Applied:
    from bookflow.core.session import now_iso
    from bookflow.hub import schema as h
    from bookflow.commands.host_cmds import VIA
    d = plan.data
    if not d["changed"]:
        return Applied(plan.preview, [], "Home unchanged.")
    person, current = d["person"], d["current"]
    at = now_iso()
    after = dict(user_id=person["id"], company_id=d["company_id"], path=d["path"],
                 version=(current["version"] + 1) if current else 1, set_at=at, set_by=s.actor.id, set_via=VIA(ctx))
    if current is None:
        s.hub.conn.execute(h.user_homes.insert().values(**after))
    else:
        s.hub.conn.execute(h.user_homes.update().where(h.user_homes.c.user_id == person["id"]).values(**after))
    touched = [Touched("user_home", person["id"], "update" if current else "create",
                       current["version"] if current else None, after["version"], after, before=current)]
    out = plan.preview.model_copy(update={"home": _view(s, person["id"], after)})
    return Applied(out, touched, f"set the Home of {person['username']}")


user_home_show = command("user home show", scope="hub", capability="user",
                         description=("A person's Home: the page signing in and the Home button land on, and whether they "
                                      "may still open it (when not, they land on the Overview)."),
                         input_model=HomeShowInput, output_model=HomeOutput,
                         error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION"],
                         authorization=AUTHORIZATION)
@user_home_show
def plan_home_show(inp: HomeShowInput, ctx: Context, s) -> Plan:
    person = authorize(inp, ctx, s)
    view = _view(s, person["id"], homes.read(s, person["id"]))
    if view is None:
        message = f"{person['username']} has no Home set."
    elif view.available:
        message = f"{person['username']}'s Home is {view.url}."
    else:
        message = f"{person['username']}'s Home ({view.url}) is not available ({view.unavailable_reason}); they land on the Overview."
    return Plan(HomeOutput(user_id=person["id"], username=person["username"], home=view, message=message))


plan_home_show.authorize_input = authorize


user_home_clear = command("user home clear", scope="hub", capability="user",
                          description="Forget a person's Home: signing in lands as it did before a Home was chosen.",
                          input_model=HomeClearInput, output_model=HomeOutput, writes={"hub"},
                          error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                          authorization=AUTHORIZATION)
@user_home_clear
def plan_home_clear(inp: HomeClearInput, ctx: Context, s) -> Plan:
    person = authorize(inp, ctx, s)
    current = homes.read(s, person["id"])
    _check_version(current, inp.expected_version)
    message = (f"{person['username']}'s Home is cleared." if current else f"{person['username']} had no Home set.")
    return Plan(HomeOutput(user_id=person["id"], username=person["username"], home=None, changed=current is not None,
                           message=message), data={"person": person, "current": current})


plan_home_clear.authorize_input = authorize


@user_home_clear.applier
def apply_home_clear(plan: Plan, ctx: Context, s) -> Applied:
    from bookflow.hub import schema as h
    person, current = plan.data["person"], plan.data["current"]
    if current is None:
        return Applied(plan.preview, [], "No Home to clear.")
    s.hub.conn.execute(h.user_homes.delete().where(h.user_homes.c.user_id == person["id"]))
    return Applied(plan.preview, [Touched("user_home", person["id"], "delete", current["version"], None, None,
                                          before=current)], f"cleared the Home of {person['username']}")
