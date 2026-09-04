"""Generate parity/fixtures/vase.stl -- one solid tapered cone, alone on the
plate, for the spiral-vase baseline.

Spiral mode needs a single object with one wall and no top, so it cannot share
a plate with the torture part. The taper (30 mm at the base to 20 mm at the
top) is what makes the XY-smoothing and the start/finish flow-ratio options
observable: a straight cylinder's footprint never moves between layers.
"""
import math, struct, sys

N, H, R0, R1 = 64, 40.0, 15.0, 10.0
# second arg shifts the cone in X: the multi-filament plate needs it clear of
# the torture part, the spiral-vase plate wants it centred and alone
CX, CY = 128.0 + (float(sys.argv[2]) if len(sys.argv) > 2 else 0.0), 128.0
T = []

def tri(a, b, c): T.append((a, b, c))

ring = lambda z, r: [(CX + r*math.cos(2*math.pi*i/N), CY + r*math.sin(2*math.pi*i/N), z)
                     for i in range(N)]
bot, top = ring(0.0, R0), ring(H, R1)
cb, ct = (CX, CY, 0.0), (CX, CY, H)
for i in range(N):
    j = (i + 1) % N
    tri(cb, bot[j], bot[i])                       # base fan (normal -Z)
    tri(ct, top[i], top[j])                       # cap fan  (normal +Z)
    tri(bot[i], bot[j], top[j])                   # side
    tri(bot[i], top[j], top[i])

out = sys.argv[1]
with open(out, "wb") as f:
    f.write(b"OrcaSlicer parity vase".ljust(80, b"\0"))
    f.write(struct.pack("<I", len(T)))
    for a, b, c in T:
        ux, uy, uz = (b[i]-a[i] for i in range(3))
        vx, vy, vz = (c[i]-a[i] for i in range(3))
        nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
        L = math.sqrt(nx*nx+ny*ny+nz*nz) or 1.0
        f.write(struct.pack("<3f", nx/L, ny/L, nz/L))
        for v in (a, b, c): f.write(struct.pack("<3f", *v))
        f.write(b"\0\0")
print(f"{out}: {len(T)} triangles")
