"""Two 20 mm cubes at fixed bed coordinates, for the by-object print sequence.

`print_sequence = by object` enforces extruder_clearance_radius (40 mm) between
objects, which the torture plate is far too wide to satisfy. Direct STL inputs
keep their file coordinates (unlike a 3mf round-trip, which re-centres every
object), so authoring the two cubes 80 mm apart is enough to place them.
"""
import struct, sys

def cube(cx, cy, s=20.0):
    h = s / 2.0
    p = [(cx-h,cy-h,0),(cx+h,cy-h,0),(cx+h,cy+h,0),(cx-h,cy+h,0),
         (cx-h,cy-h,s),(cx+h,cy-h,s),(cx+h,cy+h,s),(cx-h,cy+h,s)]
    q = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    T = []
    for a,b,c,d in q:
        T += [(p[a],p[b],p[c]), (p[a],p[c],p[d])]
    return T

out, cx = sys.argv[1], float(sys.argv[2])
T = cube(cx, 128.0)
with open(out, "wb") as f:
    f.write(b"parity cube".ljust(80, b"\0")); f.write(struct.pack("<I", len(T)))
    for a,b,c in T:
        f.write(struct.pack("<3f", 0, 0, 0))
        for v in (a,b,c): f.write(struct.pack("<3f", *v))
        f.write(b"\0\0")
print(f"{out}: cube at x={cx}")
