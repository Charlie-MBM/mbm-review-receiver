#!/usr/bin/env python3
"""Inventory an Abstractive source-document ZIP - PHI-safe.

Prints file COUNTS by extension, size distribution and total bytes. Never
prints a filename or any file content, so the output can be shared outside
the covered-entity boundary.

  py zip_inventory.py                 # newest ZIP under the output dir
  py zip_inventory.py "<path.zip>"
"""
import glob
import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
DEFAULT_ROOT = Path.home() / "MBM_Abstractive_Out"


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


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        path = args[0]
    else:
        hits = sorted(glob.glob(str(out_root() / "*" / "*full docs.zip")),
                      key=os.path.getmtime, reverse=True)
        if not hits:
            sys.exit(f"No document ZIP found under {out_root()}")
        path = hits[0]

    print(f"zip: {Path(path).name}  ({os.path.getsize(path):,} bytes on disk)")
    print("-" * 60)
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        ext = Counter()
        total = 0
        buckets = Counter()
        for i in infos:
            e = (Path(i.filename).suffix or "(none)").lower()
            ext[e] += 1
            total += i.file_size
            kb = i.file_size / 1024
            b = ("<10 KB" if kb < 10 else "10-100 KB" if kb < 100 else
                 "100 KB-1 MB" if kb < 1024 else ">1 MB")
            buckets[b] += 1
        depth = Counter(f.count("/") for f in (i.filename for i in infos))

    print(f"entries: {len(infos)}")
    print(f"uncompressed total: {total:,} bytes ({total/1024/1024:.1f} MB)")
    print(f"mean file size: {total/max(len(infos),1)/1024:.1f} KB")
    print("\nby extension:")
    for k, v in ext.most_common():
        print(f"  {k}: {v}")
    print("\nby size:")
    for k in ("<10 KB", "10-100 KB", "100 KB-1 MB", ">1 MB"):
        if buckets[k]:
            print(f"  {k}: {buckets[k]}")
    print("\nfolder depth (0 = flat):")
    for k, v in sorted(depth.items()):
        print(f"  {k}: {v}")
    print("\nCounts only - no filenames or contents printed.")


if __name__ == "__main__":
    main()
