#!/usr/bin/env python3
"""Extract, edit, and repackage a .3mf's embedded config -- the shared
technique behind several needs_fixture cases (a .3mf is just a zip; the
resolved print/printer/filament config lives at Metadata/project_settings.config
as JSON, per-object/per-plate metadata at Metadata/model_settings.config as XML).

Typical flow for building a fixture:
    1. Produce a base .3mf with the CLI itself (--export-3mf), using real
       shipped profiles, so everything except the one thing you're testing
       is realistic.
    2. Extract the member you need to edit:
         python scripts/patch_3mf.py extract base.3mf Metadata/project_settings.config out.json
    3. Edit out.json/out.xml by hand (or with jq/a script) for the specific
       broken/edge-case value you need.
    4. Write it back into a copy of the 3mf:
         python scripts/patch_3mf.py replace base.3mf Metadata/project_settings.config out.json fixture.3mf
    5. Sanity-check the result is still a valid 3mf the CLI can read:
         orca-slicer --datadir <dir> --outputdir <dir> --info fixture.3mf
"""
import argparse
import shutil
import zipfile
from pathlib import Path


def extract(args):
    with zipfile.ZipFile(args.threemf) as zf:
        data = zf.read(args.member)
    Path(args.out).write_bytes(data)
    print(f"wrote {args.out} ({len(data)} bytes) from {args.member}")


def replace(args):
    src = Path(args.threemf)
    dst = Path(args.out)
    new_content = Path(args.content_file).read_bytes()

    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        if args.member not in names:
            raise SystemExit(f"{args.member} not found in {src}. Members: {names}")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = new_content if item.filename == args.member else zin.read(item.filename)
                zout.writestr(item, data)
    print(f"wrote {dst} with {args.member} replaced ({len(new_content)} bytes)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser("extract", help="pull one member out of a .3mf")
    p_extract.add_argument("threemf")
    p_extract.add_argument("member", help="e.g. Metadata/project_settings.config")
    p_extract.add_argument("out")
    p_extract.set_defaults(func=extract)

    p_replace = sub.add_parser("replace", help="write a new .3mf with one member's content swapped in")
    p_replace.add_argument("threemf", help="source .3mf (unmodified)")
    p_replace.add_argument("member", help="e.g. Metadata/project_settings.config")
    p_replace.add_argument("content_file", help="file whose bytes replace that member")
    p_replace.add_argument("out", help="destination .3mf (created/overwritten)")
    p_replace.set_defaults(func=replace)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
