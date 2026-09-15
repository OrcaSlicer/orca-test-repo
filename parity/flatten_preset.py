#!/usr/bin/env python3
"""Flatten a vendor profile's `inherits` chain into a single self-contained JSON.

The GUI resolves `inherits` through PresetBundle; the CLI's --load-settings /
--load-filaments read one file with ConfigBase::load_from_json and never walk
the chain, so a leaf vendor profile handed to the CLI silently falls back to
built-in defaults for every key its parents define. Flattening here is what
makes a CLI lane comparable to a GUI lane.

Usage:
  flatten_preset.py --profiles resources/profiles --vendor BBL \
      --kind process --name "0.20mm Standard @BBL X1C" --out flat.json
"""

import argparse
import json
import os
import sys

# Bookkeeping keys that describe the preset rather than the print; the leaf's
# values win and parent values must not leak in.
LEAF_ONLY = ("name", "from", "setting_id", "filament_id", "instantiation",
             "version", "is_custom", "url")


def find(profiles, vendors, kind, name):
    for v in vendors:
        p = os.path.join(profiles, v, kind, name + ".json")
        if os.path.isfile(p):
            return p
    return None


def flatten(profiles, vendors, kind, name):
    """Walk leaf -> root, then merge root -> leaf so children override."""
    chain, seen = [], set()
    while name:
        if name in seen:
            sys.exit("inherits cycle at %r" % name)
        seen.add(name)
        path = find(profiles, vendors, kind, name)
        if not path:
            sys.exit("cannot resolve %s preset %r" % (kind, name))
        d = json.load(open(path))
        chain.append(d)
        name = d.get("inherits", "")

    merged = {}
    for d in reversed(chain):
        for k, v in d.items():
            if k == "inherits" or (k in LEAF_ONLY and d is not chain[0]):
                continue
            merged[k] = v
    merged.pop("inherits", None)
    # a flattened preset stands alone; leave it marked as a system preset so
    # the CLI's `from` check accepts it
    merged.setdefault("from", "system")
    return merged, [d.get("name") for d in chain]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profiles", required=True)
    ap.add_argument("--vendor", action="append", required=True,
                    help="vendor dir(s) to search, in order")
    ap.add_argument("--kind", required=True, choices=("machine", "process", "filament"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    merged, chain = flatten(a.profiles, a.vendor, a.kind, a.name)
    with open(a.out, "w") as f:
        json.dump(merged, f, indent=1)
    print("%s: %d keys from chain %s" % (a.kind, len(merged), " <- ".join(chain)))


if __name__ == "__main__":
    main()
