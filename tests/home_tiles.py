"""Find an Overview tile by its step title, reading the label it shows from the board's own map."""
import json

from bookflow.adapters.workbench import home


def label(title):
    """A step is named by its title in tests; the tile shows the step's action label."""
    return next(step.action.label for panel in home.PANELS for step in panel.steps if step.title == title)


def tile(title):
    return ('[...document.querySelectorAll("a.flow-tile")].find(a => '
            f'a.querySelector(".flow-tile-title")?.textContent.trim() === {json.dumps(label(title))})')
