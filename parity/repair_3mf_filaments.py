"""Normalise the per-filament vectors of a CLI-exported project.

`--export-3mf` writes filament_settings_id and filament_ids with one entry per
loaded filament but leaves filament_colour, filament_type, filament_map and
filament_diameter at length 1. Reloaded, the plate counts as single-filament
(filament_colour.size() is what the slicing path treats as authoritative) and
the prime tower is forced off; the same mismatch is the root cause catalogued
as BUG-50 / BL-34 in the vault.

This applies that note's Option C repair externally, so a usable multi-filament
fixture can be produced without patching and rebuilding the slicer:

  N       = max(len(filament_settings_id), len(filament_ids), highest object extruder)
  pad     short plain per-filament vectors from values.front() -- short means
          "never expanded", not "fewer filaments", which is why max is correct
  rebuild flush_volumes_vector (2N), flush_volumes_matrix (N^2 x nozzles),
          filament_map (N, round-robin across the printer's nozzles)

Vectors already at N, 2N or N x nozzles are left alone: those are the
filament-x-variant and per-nozzle dimension classes, not plain per-filament.
"""
import json, re, sys, zipfile
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
assign = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else []

with zipfile.ZipFile(src) as z:
    cfg = json.loads(z.read("Metadata/project_settings.config").decode())
    ms = z.read("Metadata/model_settings.config").decode()
    names = z.namelist()
    blobs = {n: z.read(n) for n in names}

nozzles = len(cfg.get("nozzle_diameter") or ["0.4"])
obj_max = max([int(v) for v in re.findall(r'key="extruder" value="(\d+)"', ms)] or [1])
N = max(len(cfg.get("filament_settings_id") or []), len(cfg.get("filament_ids") or []),
        obj_max, len(assign) or 1)

DERIVED = {"flush_volumes_vector", "flush_volumes_matrix", "filament_map"}
padded = []
for k, v in cfg.items():
    if not isinstance(v, list) or k in DERIVED or not v:
        continue
    if not (k.startswith("filament_") or k.startswith("nozzle_temperature")):
        continue
    if len(v) in (N, 2 * N, N * nozzles, N * N * nozzles):
        continue                     # variant / per-nozzle / derived class
    if len(v) < N:
        cfg[k] = v + [v[0]] * (N - len(v))
        padded.append(f"{k} {len(v)}->{N}")

cfg["filament_map"] = [str((i % nozzles) + 1) for i in range(N)]
fv = cfg.get("flush_volumes_vector") or ["140"]
cfg["flush_volumes_vector"] = (fv * (2 * N))[:2 * N]
cfg["flush_volumes_matrix"] = ["0" if (i // N) == (i % N) else "280"
                               for i in range(N * N * nozzles)]

if assign:
    seen = [0]
    def sub(m):
        i = seen[0]; seen[0] += 1
        return m.group(0).replace(f'value="{m.group(1)}"', f'value="{assign[i]}"') if i < len(assign) else m.group(0)
    ms = re.sub(r'<metadata key="extruder" value="(\d+)"/>', sub, ms)

with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
    for n in names:
        if n == "Metadata/project_settings.config":
            out.writestr(n, json.dumps(cfg, indent=1))
        elif n == "Metadata/model_settings.config":
            out.writestr(n, ms)
        else:
            out.writestr(n, blobs[n])
print(f"{dst.name}: N={N}, {nozzles} nozzle(s); padded {len(padded)} vectors")
for p in padded[:10]: print(f"   {p}")
print(f"   filament_map -> {cfg['filament_map']}")
print(f"   flush_volumes_matrix -> {len(cfg['flush_volumes_matrix'])} entries")
