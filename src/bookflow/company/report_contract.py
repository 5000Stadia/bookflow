"""Report selector declarations; references may include retired master records."""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class ReportReference(ReferenceDefinition):
    include_inactive: bool = True


@dataclass(frozen=True)
class ReportFormDefinition:
    # A class is the one report filter whose value never arrives from a drill-down link, so
    # it is the one a reader has to find by name. Declaring it here is what turns the filter
    # control into the same searchable picker `account` already has.
    references: tuple[ReferenceDefinition, ...] = (
        ReportReference('account', 'account'), ReportReference('class_id', 'class'))


FORM = ReportFormDefinition()
