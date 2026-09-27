#!/usr/bin/env python3
"""
Bleach Online Map Composer / Splitter
=====================================

Combines extracted Bleach Online map PNG tiles into complete map images and
splits edited complete maps back into the original tile files.

Confirmed filename layout from the supplied Map samples:
    MMMMRRCC.png

    MAP  = variable-length hexadecimal map ID prefix
    XX    = 2-hex-digit X / column
    YY    = 2-hex-digit Y / row

The client constructs the resource ID as:
    MapID << 16 | X << 8 | Y

Examples:
    4E2A0000.png -> map 4E2A, column/X 0, row/Y 0
    4E2A0402.png -> map 4E2A, column/X 4, row/Y 2

The supplied samples use 300x300 tiles. The tool records actual tile sizes
in a manifest instead of hard-coding them.

Requires:
    py -m pip install pillow
"""
import argparse, json, re, sys
from pathlib import Path
from collections import defaultdict
from PIL import Image

# Last four hexadecimal digits encode X/column then Y/row. The map-ID prefix can
# be shorter than four digits (for example map 0x64 -> 64RRCC.png).
PATTERN = re.compile(r"^([0-9A-Fa-f]+)([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})\.png$", re.I)

def resolve_map_root(root):
    """Resolve the actual extracted map-tile directory.

    Accepted inputs:
      - PNG_Files
      - PNG_Files/Textures
      - PNG_Files/Textures/Map
    """
    root = Path(root)

    # Exact map directory supplied.
    if root.name.lower() == "map":
        return root

    # Normal Bleach Online extraction layouts.
    candidates = [
        root / "Textures" / "Map",
        root / "Map",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    # Last resort: locate a unique Textures/Map below the supplied root.
    found = [
        d for d in root.rglob("Map")
        if d.is_dir() and d.parent.name.lower() == "textures"
    ]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ValueError(
            "More than one Textures/Map directory was found. "
            "Pass the complete path to the desired Textures/Map directory."
        )

    raise ValueError(
        f"Map tile directory not found under {root}. "
        "Expected a path such as PNG_Files\\Textures\\Map."
    )

def discover(root):
    # IMPORTANT: only scan the real Textures/Map directory. Other texture
    # folders contain hexadecimal PNG names that can look like map tile names
    # (for example Textures/BackgroundIcon/00002710.png).
    map_root = resolve_map_root(root)
    groups=defaultdict(list)
    for f in map_root.rglob("*.png"):
        m=PATTERN.match(f.name)
        if not m: continue
        mapid=m.group(1).upper()
        # Client source:
        #   sourceID = MapID << 16 | X << 8 | Y
        # Therefore the first suffix byte is column/X and the second is row/Y.
        col=int(m.group(2),16)
        row=int(m.group(3),16)
        groups[mapid].append((row,col,f))
    return groups

def compose_group(mapid, tiles, outdir):
    rows=sorted({r for r,c,f in tiles}); cols=sorted({c for r,c,f in tiles})
    minr,maxr=min(rows),max(rows); minc,maxc=min(cols),max(cols)
    pos={(r,c):f for r,c,f in tiles}
    first=Image.open(tiles[0][2]).convert("RGBA")
    tw,th=first.size
    for r,c,f in tiles:
        with Image.open(f) as im:
            if im.size!=(tw,th):
                raise ValueError(f"{f}: tile size {im.size} differs from {(tw,th)}")
    expected=(maxr-minr+1)*(maxc-minc+1)
    missing=[(r,c) for r in range(minr,maxr+1) for c in range(minc,maxc+1) if (r,c) not in pos]
    canvas=Image.new("RGBA",((maxc-minc+1)*tw,(maxr-minr+1)*th),(0,0,0,0))
    entries=[]
    for r,c,f in sorted(tiles):
        with Image.open(f) as im:
            canvas.alpha_composite(im.convert("RGBA"),((c-minc)*tw,(r-minr)*th))
        entries.append({"row":r,"column":c,"filename":f.name,
                        "relative_source":f.as_posix()})
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    png=outdir/f"{mapid}.png"
    manifest=outdir/f"{mapid}.map.json"
    canvas.save(png,"PNG")
    doc={"format":"BleachOnline.MapComposition","version":1,"map_id":mapid,
         "tile_width":tw,"tile_height":th,"min_row":minr,"max_row":maxr,
         "min_column":minc,"max_column":maxc,"rows":maxr-minr+1,
         "columns":maxc-minc+1,"tile_count":len(tiles),"expected_tile_count":expected,
         "complete_grid":not missing,
         "missing":[{"row":r,"column":c} for r,c in missing],
         "composed_png":png.name,"width":canvas.width,"height":canvas.height,
         "tiles":entries}
    manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf-8")
    return doc

def compose(root,outdir,mapid=None):
    map_root = resolve_map_root(root)
    print(f"[MAP ROOT] {map_root.resolve()}")
    groups=discover(map_root)
    if mapid:
        mid=mapid.upper().replace("0X","")
        groups={mid:groups[mid]} if mid in groups else {}
    if not groups: raise ValueError("No matching map tiles were found.")
    ok=0
    for mid,tiles in sorted(groups.items()):
        d=compose_group(mid,tiles,outdir); ok+=1
        state="complete" if d["complete_grid"] else f"{len(d['missing'])} missing"
        print(f"[COMPOSE] {mid}: {d['rows']}x{d['columns']} tiles, "
              f"{d['width']}x{d['height']} px, {state}")
    print(f"\nComposed maps: {ok}\nOutput: {Path(outdir).resolve()}")

def split_one(manifest, composed, output):
    d=json.loads(Path(manifest).read_text(encoding="utf-8"))
    img=Image.open(composed).convert("RGBA")
    if img.size!=(d["width"],d["height"]):
        raise ValueError(f"Edited map is {img.size}, expected {(d['width'],d['height'])}")
    tw,th=d["tile_width"],d["tile_height"]; minr=d["min_row"]; minc=d["min_column"]
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    for t in d["tiles"]:
        r,c=t["row"],t["column"]
        x=(c-minc)*tw; y=(r-minr)*th
        tile=img.crop((x,y,x+tw,y+th))
        tile.save(output/t["filename"],"PNG")
    print(f"[SPLIT] {d['map_id']}: {len(d['tiles'])} tile(s) -> {output}")

def split_tree(composed_dir, output):
    composed_dir=Path(composed_dir); n=0
    for manifest in sorted(composed_dir.glob("*.map.json")):
        d=json.loads(manifest.read_text(encoding="utf-8"))
        png=composed_dir/d.get("composed_png",f"{d['map_id']}.png")
        if not png.exists():
            print(f"[SKIP] {d['map_id']}: {png.name} not found"); continue
        split_one(manifest,png,output); n+=1
    print(f"\nSplit maps: {n}\nOutput: {Path(output).resolve()}")

def info(root):
    map_root = resolve_map_root(root)
    print(f"[MAP ROOT] {map_root.resolve()}")
    groups=discover(map_root)
    for mid,tiles in sorted(groups.items()):
        rs=[x[0] for x in tiles]; cs=[x[1] for x in tiles]
        with Image.open(tiles[0][2]) as im: sz=im.size
        print(f"{mid}: {len(tiles)} tiles, rows {min(rs):02X}-{max(rs):02X}, "
              f"cols {min(cs):02X}-{max(cs):02X}, tile {sz[0]}x{sz[1]}")

def main():
    ap=argparse.ArgumentParser(description="Bleach Online map PNG composer/splitter")
    sp=ap.add_subparsers(dest="cmd",required=True)
    p=sp.add_parser("info"); p.add_argument("root",type=Path)
    p=sp.add_parser("compose"); p.add_argument("root",type=Path); p.add_argument("-o","--output",type=Path,default=Path("Map_Composed")); p.add_argument("--map")
    p=sp.add_parser("split"); p.add_argument("manifest",type=Path); p.add_argument("png",type=Path); p.add_argument("-o","--output",type=Path,default=Path("Map_Tiles"))
    p=sp.add_parser("split-tree"); p.add_argument("composed",type=Path); p.add_argument("-o","--output",type=Path,default=Path("Map_Tiles"))
    a=ap.parse_args()
    try:
        if a.cmd=="info": info(a.root)
        elif a.cmd=="compose": compose(a.root,a.output,a.map)
        elif a.cmd=="split": split_one(a.manifest,a.png,a.output)
        elif a.cmd=="split-tree": split_tree(a.composed,a.output)
    except Exception as e:
        print(f"[FAIL] {e}",file=sys.stderr); raise SystemExit(1)
if __name__=="__main__": main()
