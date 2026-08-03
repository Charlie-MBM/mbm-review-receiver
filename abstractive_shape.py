#!/usr/bin/env python3
"""Print the STRUCTURE of an Abstractive summary JSON - never its contents.

PHI-safe by construction: emits key names, value types, container lengths and
string LENGTHS only. No string, number or date value is ever printed, so the
output can be shared outside the covered-entity boundary (e.g. pasted into
Cowork) to design a renderer against.

  py abstractive_shape.py                 # newest summary under ~/MBM_Abstractive_Out
  py abstractive_shape.py <path.json>
"""
import glob
import json
import os
import sys
from pathlib import Path

MAX_DEPTH = 4
LIST_SAMPLE = 1  # describe only the first element of any list


def describe(node, depth=0, label="(root)"):
    pad = "  " * depth
    if isinstance(node, dict):
        print(f"{pad}{label}: dict[{len(node)}]")
        if depth >= MAX_DEPTH:
            print(f"{pad}  ... keys: {sorted(node.keys())}")
            return
        for k in sorted(node.keys()):
            describe(node[k], depth + 1, k)
    elif isinstance(node, list):
        print(f"{pad}{label}: list[{len(node)}]")
        if depth >= MAX_DEPTH or not node:
            return
        describe(node[0], depth + 1, "[0]")
    elif isinstance(node, str):
        print(f"{pad}{label}: str(len={len(node)})")
    elif isinstance(node, bool) or node is None:
        print(f"{pad}{label}: {type(node).__name__}")
    else:
        print(f"{pad}{label}: {type(node).__name__}")


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        pat = str(Path.home() / "MBM_Abstractive_Out" / "*" / "*summary.json")
        hits = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if not hits:
            sys.exit(f"No summary JSON found under {pat}")
        path = hits[0]
    print(f"file: {Path(path).name}  ({os.path.getsize(path):,} bytes)")
    print("-" * 60)
    with open(path, encoding="utf-8") as fh:
        describe(json.load(fh))
    print("-" * 60)
    print("Structure only - no values printed.")


if __name__ == "__main__":
    main()
