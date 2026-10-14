"""Write separate deterministic preparation and engineering QA artifacts.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained; reports contain project evidence only in caller-selected output.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def json_value(value: Any) -> Any:
    """Convert supported immutable preparation records to JSON values."""
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def stable_hash(value: Any) -> str:
    """Fingerprint canonical JSON, rejecting nonfinite numeric values."""
    payload = json.dumps(json_value(value), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    """Create one UTF-8 JSON artifact without overwriting an existing file."""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(json_value(value), stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Create a review table with stable columns and safe spreadsheet text."""
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            cells = {}
            for key, value in row.items():
                cell = json.dumps(json_value(value), ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list, tuple)) else value
                # Numeric values stay numeric. Protect text when opened by a
                # spreadsheet; exact values remain available in JSON artifacts.
                if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@")):
                    cell = "'" + cell
                cells[key] = cell
            writer.writerow(cells)


def write_review_html(path: Path, prepared: Mapping[str, Any], membership: Sequence[Mapping[str, Any]], *,
                      candidate_findings: Sequence[Mapping[str, Any]] = ()) -> None:
    """Create a portable engineering review with links to both SHMM diffs."""
    sections = []
    for title, records in (("Membership and holds", membership),
                           ("Prepared junctions", prepared.get("nodes", [])),
                           ("Prepared pipes", prepared.get("pipes", [])),
                           ("Preparation findings", prepared.get("findings", [])),
                           ("Written-model engineering findings", candidate_findings)):
        rows = [json_value(row) for row in records]
        columns = list(dict.fromkeys(k for row in rows for k in row))
        head = "".join("<th>" + html.escape(str(k)) + "</th>" for k in columns)
        body = "".join("<tr>" + "".join("<td>" + html.escape(
            json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list))
            else str(row.get(k, ""))) + "</td>" for k in columns) + "</tr>" for row in rows)
        sections.append("<h2>" + title + "</h2><div class='scroll'><table><thead><tr>"
                        + head + "</tr></thead><tbody>" + body + "</tbody></table></div>")
    content = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Utility model engineering review</title><style>
body{font-family:system-ui;margin:2rem;color:#18212b}table{border-collapse:collapse;font-size:.85rem}
td,th{border:1px solid #ccd5df;padding:.4rem;text-align:left;vertical-align:top}th{background:#edf2f7}
.scroll{overflow:auto;max-height:36rem}input{padding:.5rem;width:30rem;max-width:90%}nav a{margin-right:1rem}
</style><h1>Utility model engineering review</h1>
<p>Prepared values, engineering findings and membership are separate from model-file integrity.
Simulation and calibration are not established by this report.</p>
<nav><a href="preview_diff.html">SHMM preview diff</a><a href="written_model_diff.html">SHMM written-model diff</a>
<a href="topology_components.csv">Network components</a><a href="topology_connector_candidates.csv">Held/excluded connector candidates</a>
<a href="candidate_qa.csv">Written-model QA CSV</a><a href="profile_verification.json">Profile readback</a>
<a href="prepared.json">Prepared values and evidence</a><a href="membership.csv">Membership CSV</a></nav>
<p><label>Filter rows <input id="filter" type="search"></label></p>""" + "".join(sections) + """
<script>document.getElementById('filter').addEventListener('input',function(){
let q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(
r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></html>"""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
