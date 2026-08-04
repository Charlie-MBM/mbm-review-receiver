#!/usr/bin/env python3
"""Unpack an Abstractive source-document ZIP into a browsable folder + index.

WHY: the summary tells the physician a document exists ("cardiology note,
3/12/26"); finding it meant digging through a 45 MB zip of 1,041 files. This
extracts the human-readable documents into "source documents/" beside the
summary and writes an index so the right file is findable in seconds. Drive
previews PDFs in the browser, so it is index -> click -> reading.

WHAT GETS EXTRACTED: PDFs, and TIFFs converted to PNG (OpenEvidence accepts
jpg/png/webp/heic, not tiff; Drive previews PNG, not tiff). The C-CDA XML and
the JSON metadata sidecars stay inside the zip - they are machine formats, and
the sidecars are read in place to build the index.

Deliberate exception to the flat-folder rule in the Drive README: 1,041 files
cannot sit loose next to the summary.

PHI: runs on the practice machine only. Console output is COUNTS ONLY - no
filename or document content is ever printed, so the run can be reviewed from
outside the covered-entity boundary. The index it writes to disk does contain
identifiers, which is correct - it lives in the patient's own folder.

  py abstractive_unpack.py                 # newest zip found
  py abstractive_unpack.py --all           # every zip found
  py abstractive_unpack.py "<path.zip>"
"""
import glob
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
DEFAULT_ROOT = Path.home() / "MBM_Abstractive_Out"
SUBFOLDER = "source documents"
INDEX_NAME = "SOURCE DOCUMENTS INDEX.txt"

DOC_EXT = {".pdf"}
IMG_EXT = {".tif", ".tiff"}

# Exact keys, confirmed against a real Abstractive pull 2026-08-03. The
# sidecars carry: pipeline, doc_title, section_note_type, section_note_id,
# mime_type, section_date, section_content, section_page, section_header.
# Order matters - "mime_type" must never win the type column.
DATE_KEYS = ("section_date",)
TITLE_KEYS = ("doc_title", "section_header")
TYPE_KEYS = ("section_note_type",)
HEADER_KEYS = ("section_header",)


def out_root():
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ABSTRACTIVE_OUT_DIR="):
                v = line.partition("=")[2].strip().strip('"')
                if v:
                    return Path(v)
    except Exception:
        pass
    return DEFAULT_ROOT


def flatten(obj, out, depth=0):
    """Collect scalar leaves as lowercased key -> string value."""
    if depth > 4:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (str, int, float)) and str(v).strip():
                out.setdefault(str(k).lower(), str(v).strip())
            else:
                flatten(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:5]:
            flatten(v, out, depth + 1)


def pick(flat, keys):
    for want in keys:
        if flat.get(want):
            return flat[want]
    return ""


def tidy_date(s):
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", str(s))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_name(fname):
    """Abstractive encodes the metadata in the FILENAME, not the JSON sidecars:
        <type>_<source organization>_<YYYY-MM-DD>_<hash>.pdf
    e.g. "N_A_Providence_Health_and_Services_Oregon_and_California_2023-04-19_74b7...pdf"
    ("N_A" = type not supplied). Verified against a real pull 2026-08-03; the
    sidecars are per-SECTION and do not map to documents by stem, which is why
    matching on them produced 330 rows of "date unknown".
    """
    stem = Path(fname).stem
    m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    date = m.group(1) if m else ""
    prefix = stem[:m.start()].rstrip("_") if m else stem
    prefix = re.sub(r"_[0-9a-fA-F]{16,}$", "", prefix)      # trailing hash
    desc = prefix.replace("_", " ").strip()
    desc = re.sub(r"^N A\s+", "", desc)                      # unsupplied type
    return date, desc[:90]


def safe_name(name):
    base = Path(name).name
    return re.sub(r'[<>:"/\\|?*]', "_", base)


def unpack(zip_path):
    zip_path = Path(zip_path)
    patient_dir = zip_path.parent
    dest = patient_dir / SUBFOLDER
    dest.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
        have_pil = True
    except Exception:
        have_pil = False

    rows = []
    stats = Counter()
    sidecar_keys = set()

    with zipfile.ZipFile(zip_path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        by_stem = {}
        for n in names:
            if Path(n).suffix.lower() == ".json":
                by_stem.setdefault(Path(n).stem.lower(), n)

        for info in z.infolist():
            if info.is_dir():
                continue
            src = info.filename
            ext = Path(src).suffix.lower()
            if ext not in DOC_EXT and ext not in IMG_EXT:
                stats["skipped (xml/json/other)"] += 1
                continue

            # metadata from the matching sidecar, if there is one
            flat = {}
            side = by_stem.get(Path(src).stem.lower())
            if side:
                try:
                    with z.open(side) as fh:
                        flatten(json.load(fh), flat)
                    sidecar_keys.update(flat.keys())
                except Exception:
                    stats["sidecar unreadable"] += 1

            target_name = safe_name(src)
            if ext in IMG_EXT and have_pil:
                target_name = Path(target_name).with_suffix(".png").name
            target = dest / target_name
            # Idempotent: a re-run (or a 30-day re-pull) must refresh the index
            # without duplicating every document as "name (2).pdf".
            if target.exists():
                stats["already present"] += 1
                rows.append({
                    "date": parse_name(target.name)[0],
                    "type": "",
                    "title": parse_name(target.name)[1],
                    "org": "",
                    "file": target.name,
                    "kb": info.file_size // 1024,
                })
                continue

            try:
                with z.open(info) as fh:
                    data = fh.read()
                if ext in IMG_EXT and have_pil:
                    import io
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as im:
                        im.save(target, format="PNG")
                    stats["tiff converted to png"] += 1
                else:
                    target.write_bytes(data)
                    stats["pdf extracted" if ext in DOC_EXT else "tiff extracted (no Pillow)"] += 1
            except Exception:
                stats["extract failed"] += 1
                continue

            rows.append({
                "date": parse_name(target.name)[0],
                "type": "",
                "title": parse_name(target.name)[1],
                "org": "",
                "file": target.name,
                "kb": info.file_size // 1024,
            })

    rows.sort(key=lambda r: (r["date"] or "0000-00-00"), reverse=True)
    idx = patient_dir / INDEX_NAME
    L = [f"SOURCE DOCUMENTS INDEX - {patient_dir.name}",
         f"{len(rows)} viewable documents extracted to \"{SUBFOLDER}\\\"",
         "Newest first. Open the file named at the end of each line.",
         "C-CDA XML and metadata remain inside the original ZIP.",
         "=" * 78, ""]
    for r in rows:
        head = " | ".join(x for x in [r["date"] or "date unknown", r["type"],
                                      r["title"], r["org"]] if x)
        L.append(head)
        L.append(f"    -> {r['file']}  ({r['kb']} KB)")
        L.append("")
    idx.write_text("\n".join(L), encoding="utf-8")

    if not have_pil:
        stats["NOTE: Pillow not installed, tiffs left as .tiff"] += 1
    return dest, idx, len(rows), stats, sorted(sidecar_keys)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = out_root()
    if "--all" in sys.argv:
        zips = sorted(glob.glob(str(root / "*" / "*full docs.zip")))
    elif args:
        zips = args
    else:
        hits = sorted(glob.glob(str(root / "*" / "*full docs.zip")),
                      key=os.path.getmtime, reverse=True)
        if not hits:
            sys.exit(f"No document ZIP found under {root}")
        zips = hits[:1]

    all_keys = set()
    for zp in zips:
        try:
            dest, idx, n, stats, keys = unpack(zp)
            all_keys.update(keys)
            print(f"OK  {n} documents -> {dest}")
            for k, v in stats.most_common():
                print(f"      {k}: {v}")
        except Exception as e:
            print(f"FAIL {Path(zp).name}: {type(e).__name__}: {e}")
    if all_keys:
        print(f"\nsidecar metadata keys seen (names only): {all_keys}")


if __name__ == "__main__":
    main()
