"""Generate parity/torture.stl -- one plate of small primitives that between
them exercise the geometry-gated slicer features the 20 mm cube cannot:
overhangs at several angles, a flat bridge, vertical holes (incl. a
counterbore that has to be bridged), thin walls, sub-nozzle pins, a stepped
tower (internal solid infill), a sloped top surface and a closed internal
cavity. Emitted as a triangle soup of individually closed solids -- the
slicer unions them, which is exactly what a multi-part plate looks like.
"""
import math, struct, sys

T = []  # triangles: (v0, v1, v2), CCW seen from outside

def tri(a, b, c): T.append((a, b, c))

def quad(a, b, c, d):  # a-b-c-d CCW seen from outside
    tri(a, b, c); tri(a, c, d)

def box(x0, y0, z0, x1, y1, z1):
    p = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
         (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    quad(p[0],p[3],p[2],p[1])          # bottom (normal -Z)
    quad(p[4],p[5],p[6],p[7])          # top
    quad(p[0],p[1],p[5],p[4])          # -Y
    quad(p[1],p[2],p[6],p[5])          # +X
    quad(p[2],p[3],p[7],p[6])          # +Y
    quad(p[3],p[0],p[4],p[7])          # -X

def wedge(x0, y0, z0, dx, dy, h, overhang_deg):
    """Triangular prism: vertical back face at x0, front face leaning OUT over
    the plate by `overhang_deg` from vertical (so 50 deg needs support on an
    unmodified profile, 20 deg does not)."""
    top_dx = dx + h * math.tan(math.radians(overhang_deg))
    a0,a1 = (x0,y0,z0),(x0+dx,y0,z0)
    b0,b1 = (x0,y0+dy,z0),(x0+dx,y0+dy,z0)
    c0,c1 = (x0,y0,z0+h),(x0+top_dx,y0,z0+h)
    d0,d1 = (x0,y0+dy,z0+h),(x0+top_dx,y0+dy,z0+h)
    quad(a0,b0,b1,a1)                  # bottom
    quad(c0,c1,d1,d0)                  # top
    quad(a0,a1,c1,c0)                  # -Y side (triangle-ish quad)
    quad(b1,b0,d0,d1)                  # +Y side
    quad(a1,b1,d1,c1)                  # slanted overhanging face
    quad(b0,a0,c0,d0)                  # vertical back

def _ray_rect(cx, cy, ang, x0, y0, x1, y1):
    dx, dy = math.cos(ang), math.sin(ang)
    ts = []
    if abs(dx) > 1e-12: ts += [ (x1-cx)/dx, (x0-cx)/dx ]
    if abs(dy) > 1e-12: ts += [ (y1-cy)/dy, (y0-cy)/dy ]
    t = min(t for t in ts if t > 1e-9)
    return (cx+dx*t, cy+dy*t)

def slab_with_hole(x0, y0, z0, x1, y1, z1, r, n=48):
    """Rectangular slab with one centred vertical cylindrical hole. Both faces
    are triangulated as a strip between the circle and the rectangle sampled
    along the same rays, so the loops always correspond 1:1."""
    cx, cy = (x0+x1)/2, (y0+y1)/2
    ang = [2*math.pi*i/n for i in range(n)]
    circ = [(cx+r*math.cos(a), cy+r*math.sin(a)) for a in ang]
    rect = [_ray_rect(cx, cy, a, x0, y0, x1, y1) for a in ang]
    for i in range(n):
        j = (i+1) % n
        ci, cj, ri, rj = circ[i], circ[j], rect[i], rect[j]
        quad((*ci,z1),(*cj,z1),(*rj,z1),(*ri,z1))            # top
        quad((*ci,z0),(*ri,z0),(*rj,z0),(*cj,z0))            # bottom
        quad((*ci,z0),(*cj,z0),(*cj,z1),(*ci,z1))            # hole wall (inward)
        quad((*ri,z0),(*ri,z1),(*rj,z1),(*rj,z0))            # outer wall
    # the outer wall above is a fan of the sampled rect loop: it is closed and
    # watertight, only denser than four faces.

def cavity_box(x0, y0, z0, s, t):
    """Solid cube of side s with a closed cube void of side s-2t inside."""
    box(x0, y0, z0, x0+s, y0+s, z0+s)
    i0, i1 = x0+t, x0+s-t
    j0, j1 = y0+t, y0+s-t
    k0, k1 = z0+t, z0+s-t
    p = [(i0,j0,k0),(i1,j0,k0),(i1,j1,k0),(i0,j1,k0),
         (i0,j0,k1),(i1,j0,k1),(i1,j1,k1),(i0,j1,k1)]
    quad(p[0],p[1],p[2],p[3]); quad(p[4],p[7],p[6],p[5])     # inward normals
    quad(p[0],p[4],p[5],p[1]); quad(p[1],p[5],p[6],p[2])
    quad(p[2],p[6],p[7],p[3]); quad(p[3],p[7],p[4],p[0])

CX, CY = 128.0, 128.0        # X1C bed centre
def at(x, y): return CX + x, CY + y

# --- 1. bridge arch: 24 mm flat bridge, and the slab overhangs both legs ----
lx, ly = at(-38, 18)
box(lx, ly, 0, lx+8, ly+18, 14)
box(lx+32, ly, 0, lx+40, ly+18, 14)
box(lx-3, ly, 14, lx+43, ly+18, 17)          # spanning slab + 3 mm side overhang

# --- 2. overhang wedges at 20 / 35 / 50 / 65 deg from vertical --------------
for i, a in enumerate((20, 35, 50, 65)):
    wx, wy = at(-38 + i*13, -2)
    wedge(wx, wy, 0, 5, 12, 14, a)

# --- 3. holes: 8 mm, 2 mm, and an 8->4 counterbore that must be bridged -----
hx, hy = at(6, 16)
slab_with_hole(hx, hy, 0, hx+20, hy+20, 6, 4.0)
hx, hy = at(30, 16)
slab_with_hole(hx, hy, 0, hx+14, hy+14, 6, 1.0)
hx, hy = at(6, -8)
slab_with_hole(hx, hy, 0,  hx+18, hy+18, 5,  4.0)
slab_with_hole(hx, hy, 5,  hx+18, hy+18, 10, 2.0)

# --- 4. thin walls (0.3 / 0.6 / 1.2 mm) and sub-nozzle pins -----------------
for i, t in enumerate((0.3, 0.6, 1.2)):
    tx, ty = at(-38 + i*4, -20)
    box(tx, ty, 0, tx+t, ty+14, 12)
for i, s in enumerate((0.8, 1.5)):
    px, py = at(-22 + i*4, -20)
    box(px, py, 0, px+s, py+s, 10)

# --- 5. stepped tower: sparse infill, then internal solid over the step -----
sx, sy = at(-14, -20)
box(sx, sy, 0, sx+18, sy+18, 10)
box(sx+4, sy+4, 10, sx+14, sy+14, 20)

# --- 6. sloped top surface (ridge) -- top-surface / ironing features --------
rx, ry = at(30, -8)
wedge(rx, ry, 0, 2, 16, 12, 40)              # leaning ridge: sloped top + overhang

# --- 7. closed internal cavity: internal bridges, internal solid ------------
cavity_box(*at(8, -30), 0.0, 16, 3)

# --- 8. one part whose height is not a multiple of any common layer height --
qx, qy = at(-38, -34)
box(qx, qy, 0, qx+12, qy+12, 10.13)

out = sys.argv[1]
with open(out, "wb") as f:
    f.write(b"OrcaSlicer parity torture part".ljust(80, b"\0"))
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
