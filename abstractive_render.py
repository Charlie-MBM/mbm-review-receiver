#!/usr/bin/env python3
"""Render an Abstractive summary JSON into a paste-ready clinical document.

The raw JSON is ~1.5 MB, almost all of it `section_summaries` (one entry per
source document - 472 for the first real pull). That bulk is ARCHIVE material.
The rolled-up longitudinal view lives in `meta_summary` and is only a few KB:
that is what a physician actually pastes into the chart.

Writes "<same folder>/<same stem>.md" next to the JSON. Local only - this
handles PHI and must never run anywhere but Charlie's machine.

  py abstractive_render.py                  # newest summary under ~/MBM_Abstractive_Out
  py abstractive_render.py <path.json>
  py abstractive_render.py --all            # every summary JSON found
"""
import glob
import json
import os
import sys
from pathlib import Path

OUT_ROOT = Path.home() / "MBM_Abstractive_Out"

# (key in meta_summary, heading) in clinical reading order. Keys absent from a
# given payload are skipped silently; Abstractive omits empty sections.
SECTIONS = [
    ("HPI", "History of Present Illness"),
    ("Past Clinical Events", "Past Clinical Events"),
    ("Surgical History", "Surgical History"),
    ("Pathology and Oncology History", "Pathology and Oncology History"),
    ("Family History", "Family History"),
    ("Social History", "Social History"),
    ("Allergies", "Allergies"),
    ("All Historical Medications", "Medications (historical)"),
    ("Labs", "Labs"),
    ("Images", "Imaging"),
    ("Vitals", "Vitals"),
    ("Medical Devices", "Medical Devices"),
    ("All Historical Providers", "Prior Providers"),
    ("All Historical Followups", "Outstanding Follow-ups"),
]


def emit(value):
    """Render one meta_summary value as markdown. str -> paragraph, list -> bullets."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
        if not items:
            return None
        return "\n".join(f"- {i}" for i in items)
    return str(value)


def render(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)

    pm = doc.get("patient_metadata") or {}
    ms = doc.get("meta_summary") or {}
    secs = doc.get("section_summaries") or []

    name = " ".join(x for x in [pm.get("first_name"), pm.get("last_name")] if x)
    lines = []
    lines.append(f"# Outside records summary - {name or 'patient'}")
    lines.append("")
    ident = [x for x in [
        f"DOB {pm['dob']}" if pm.get("dob") else None,
        f"Sex {pm['sex']}" if pm.get("sex") else None,
        pm.get("address"),
    ] if x]
    if ident:
        lines.append(" | ".join(ident))
        lines.append("")
    lines.append(f"Source: Abstractive Health via Carequality. "
                 f"Assembled from {len(secs)} source documents.")
    lines.append("")
    lines.append("> AI-generated summary of records from outside this practice. "
                 "Verify anything clinically material against the source documents "
                 "in the accompanying ZIP before acting on it.")
    lines.append("")
    lines.append("---")

    written = 0
    for key, heading in SECTIONS:
        body = emit(ms.get(key))
        if not body:
            continue
        written += 1
        lines.append("")
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body)

    # Surface any meta_summary key we did not map, so a schema change shows up
    # instead of silently dropping content.
    unmapped = [k for k in ms.keys() if k not in {s[0] for s in SECTIONS}]
    for key in sorted(unmapped):
        body = emit(ms.get(key))
        if not body:
            continue
        written += 1
        lines.append("")
        lines.append(f"## {key}")
        lines.append("")
        lines.append(body)

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"Full source documents: same folder, "
                 f"\"{Path(path).stem.replace('Abstractive summary', 'full docs')}.zip\"")
    if doc.get("conversation_id"):
        lines.append(f"Abstractive conversation: {doc['conversation_id']}")
    lines.append("")
    lines.append("Paste this into the patient's Hint chart the same day - "
                 "Hint is the legal record.")
    lines.append("")

    out = Path(path).with_suffix(".md")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out, written, len(secs)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all" in sys.argv:
        paths = sorted(glob.glob(str(OUT_ROOT / "*" / "*summary.json")))
    elif args:
        paths = args
    else:
        hits = sorted(glob.glob(str(OUT_ROOT / "*" / "*summary.json")),
                      key=os.path.getmtime, reverse=True)
        if not hits:
            sys.exit(f"No summary JSON found under {OUT_ROOT}")
        paths = hits[:1]

    for p in paths:
        try:
            out, written, nsec = render(p)
            print(f"OK  {out.name}  ({out.stat().st_size:,} bytes, "
                  f"{written} sections, from {nsec} source documents)")
            print(f"    {out}")
        except Exception as e:
            print(f"FAIL {Path(p).name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
