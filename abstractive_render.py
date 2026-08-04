#!/usr/bin/env python3
"""Render an Abstractive summary JSON into an upload-ready clinical document.

The raw JSON is ~1.5 MB, nearly all of it `section_summaries` (one entry per
source document - 492 on the first real pull). That bulk is ARCHIVE material.
The rolled-up longitudinal view lives in `meta_summary` and is a few KB: that
is what a physician actually puts in the chart.

OUTPUT IS .txt, DELIBERATELY. OpenEvidence rejects .md - verified 2026-08-03,
its uploader states: "Only .pdf, .doc(x), .xls(x), images (.jpg, .png, .webp,
.heic), and plain text files are accepted." Plain text needs no dependencies
and uploads cleanly, so headers are CAPS rather than markdown.

Writes "<same folder>/<same stem>.txt". Local only - this handles PHI and must
never run anywhere but the practice machine.

  py abstractive_render.py                  # newest summary found
  py abstractive_render.py <path.json>
  py abstractive_render.py --all            # every summary JSON found
"""
import glob
import json
import os
import sys
from pathlib import Path

DEFAULT_OUT_ROOT = Path.home() / "MBM_Abstractive_Out"
ENV_PATH = Path(__file__).parent / ".env"

SECTIONS = [
    ("HPI", "HISTORY OF PRESENT ILLNESS"),
    ("Past Clinical Events", "PAST CLINICAL EVENTS"),
    ("Surgical History", "SURGICAL HISTORY"),
    ("Pathology and Oncology History", "PATHOLOGY AND ONCOLOGY HISTORY"),
    ("Family History", "FAMILY HISTORY"),
    ("Social History", "SOCIAL HISTORY"),
    ("Allergies", "ALLERGIES"),
    ("All Historical Medications", "MEDICATIONS (HISTORICAL)"),
    ("Labs", "LABS"),
    ("Images", "IMAGING"),
    ("Vitals", "VITALS"),
    ("Medical Devices", "MEDICAL DEVICES"),
    ("All Historical Providers", "PRIOR PROVIDERS"),
    ("All Historical Followups", "OUTSTANDING FOLLOW-UPS"),
]


def out_root():
    """Honour ABSTRACTIVE_OUT_DIR so --all finds files written to Drive."""
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ABSTRACTIVE_OUT_DIR="):
                v = line.partition("=")[2].strip().strip('"')
                if v:
                    return Path(v)
    except Exception:
        pass
    return DEFAULT_OUT_ROOT


def emit(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
        return "\n".join(f"- {i}" for i in items) if items else None
    return str(value)


def render(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)

    pm = doc.get("patient_metadata") or {}
    ms = doc.get("meta_summary") or {}
    secs = doc.get("section_summaries") or []

    name = " ".join(x for x in [pm.get("first_name"), pm.get("last_name")] if x)
    L = []
    L.append(f"OUTSIDE RECORDS SUMMARY - {name or 'patient'}")
    ident = [x for x in [
        f"DOB {pm['dob']}" if pm.get("dob") else None,
        f"Sex {pm['sex']}" if pm.get("sex") else None,
        pm.get("address"),
    ] if x]
    if ident:
        L.append(" | ".join(ident))
    L.append("")
    L.append(f"Source: Abstractive Health via Carequality. "
             f"Assembled from {len(secs)} source documents.")
    L.append("AI-generated summary of records from outside this practice. Verify "
             "anything clinically material against the source documents in the "
             "accompanying ZIP before acting on it.")
    L.append("=" * 70)

    written = 0
    seen = {s[0] for s in SECTIONS}
    ordered = SECTIONS + [(k, k.upper()) for k in sorted(ms) if k not in seen]
    for key, heading in ordered:
        body = emit(ms.get(key))
        if not body:
            continue
        written += 1
        L.append("")
        L.append(heading)
        L.append("-" * len(heading))
        L.append(body)

    L.append("")
    L.append("=" * 70)
    stem = Path(path).stem
    L.append(f"Full source documents: same folder, "
             f"\"{stem.replace('Abstractive summary', 'full docs')}.zip\"")
    if doc.get("conversation_id"):
        L.append(f"Abstractive conversation: {doc['conversation_id']}")
    L.append("")

    out = Path(path).with_suffix(".txt")
    out.write_text("\n".join(L), encoding="utf-8")
    return out, written, len(secs)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = out_root()
    if "--all" in sys.argv:
        paths = sorted(glob.glob(str(root / "*" / "*summary.json")))
    elif args:
        paths = args
    else:
        hits = sorted(glob.glob(str(root / "*" / "*summary.json")),
                      key=os.path.getmtime, reverse=True)
        if not hits:
            sys.exit(f"No summary JSON found under {root}")
        paths = hits[:1]

    if not paths:
        sys.exit(f"No summary JSON found under {root}")
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
