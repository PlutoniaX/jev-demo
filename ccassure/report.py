"""Builds the static executive dashboard by embedding results into the HTML template."""

from __future__ import annotations

import json
from pathlib import Path

PLACEHOLDER = "/*__RESULTS__*/null"


def build(results: dict, template: Path, out: Path) -> None:
    html = template.read_text()
    if PLACEHOLDER not in html:
        raise ValueError(f"{template} is missing the {PLACEHOLDER} placeholder")
    payload = json.dumps(results, separators=(",", ":")).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    page = html.replace(PLACEHOLDER, payload)
    out.write_text('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n' + page + "\n</html>\n")
