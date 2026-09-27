#!/usr/bin/env python3
"""
Bleach Online TexClient Complete Converter v3
==============================================

Source-grounded implementation based on:
  Foundation.Resources.Streamization.Textures.TResourceUnstreamizerTexture
  Foundation.Decoder.Pak.TPakDecoder

Outer TexClient stream (little-endian):
  uint32 identifier
  uint32 texture_data_length
  byte[texture_data_length] texture_data
  uint16 pak_count
  repeat pak_count:
      uint32 compressed_pak_length
      byte[compressed_pak_length] zlib(TPak)
  uint16 sequence_count
  [sequence definitions live in texture_data and are preserved verbatim]

TPak after zlib decompression (big-endian / default AS3 ByteArray endian):
  int8   type
  uint16 width
  uint16 height
  int8   quality
  int8   alpha_quality
  int8   alpha_filter
  uint16 sprite_count
  repeat sprite_count:
      int16 ignored_x
      int16 ignored_y
      int16 sprite_width
      int16 sprite_height
  uint32 base_image_length
  byte[base_image_length] base_image
  if alpha_quality != 0 and quality != alpha_quality:
      uint32 alpha_image_length
      byte[alpha_image_length] alpha_image

Extraction creates editable RGBA atlas PNGs. For one pak the legacy-compatible name is
<name>.png. For multiple paks it creates <name>.pak00.png, <name>.pak01.png, ...
and a <name>.json metadata file.

Repacking is template-based. It preserves texture_data, sequence data, TPak metadata,
unknown bytes, and package ordering, replacing only the encoded image streams.

Requires:
  py -m pip install pillow
"""

import argparse, io, json, struct, sys, zlib
from pathlib import Path
from PIL import Image

def be_i8(b,p): return struct.unpack_from(">b",b,p)[0],p+1
def be_u16(b,p): return struct.unpack_from(">H",b,p)[0],p+2
def be_i16(b,p): return struct.unpack_from(">h",b,p)[0],p+2
def be_u32(b,p): return struct.unpack_from(">I",b,p)[0],p+4

def load_image(blob):
    im=Image.open(io.BytesIO(blob)); im.load()
    return im.convert("RGBA")

def image_kind(blob):
    if blob.startswith(b"\xff\xd8\xff"): return "jpeg"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    return "unknown"

def encode_image(im, kind, quality=95, grayscale=False):
    out=io.BytesIO()
    if kind=="jpeg":
        x=im.convert("L" if grayscale else "RGB")
        x.save(out,"JPEG",quality=quality,subsampling=-1 if grayscale else 0,
               optimize=False,progressive=False)
    elif kind=="png":
        x=im.convert("L" if grayscale else "RGBA")
        x.save(out,"PNG",optimize=False)
    else:
        raise ValueError(f"unsupported embedded image kind: {kind}")
    return out.getvalue()

def parse_tpak(comp):
    raw=zlib.decompress(comp)
    p=0
    typ,p=be_i8(raw,p); width,p=be_u16(raw,p); height,p=be_u16(raw,p)
    quality,p=be_i8(raw,p); alpha_quality,p=be_i8(raw,p); alpha_filter,p=be_i8(raw,p)
    count,p=be_u16(raw,p)
    sizes=[]
    for _ in range(count):
        ix,p=be_i16(raw,p); iy,p=be_i16(raw,p); w,p=be_i16(raw,p); h,p=be_i16(raw,p)
        sizes.append({"ignored_x":ix,"ignored_y":iy,"width":w,"height":h})
    meta_end=p
    n,p=be_u32(raw,p)
    if p+n>len(raw): raise ValueError("TPak base image exceeds decompressed stream")
    base=raw[p:p+n]; p+=n
    alpha=None
    joint = alpha_quality != 0 and quality != alpha_quality
    if joint:
        n2,p=be_u32(raw,p)
        if p+n2>len(raw): raise ValueError("TPak alpha image exceeds decompressed stream")
        alpha=raw[p:p+n2]; p+=n2
    trailing=raw[p:]
    return {
        "raw":raw,"type":typ,"width":width,"height":height,
        "quality":quality,"alpha_quality":alpha_quality,"alpha_filter":alpha_filter,
        "sprite_count":count,"sizes":sizes,"meta_prefix":raw[:meta_end],
        "base":base,"base_kind":image_kind(base),
        "alpha":alpha,"alpha_kind":image_kind(alpha) if alpha else None,
        "joint_alpha":joint,"trailing":trailing
    }

def atlas_from_tpak(t):
    src=load_image(t["base"])
    widths=[max(1,x["width"]) for x in t["sizes"]]
    heights=[max(1,x["height"]) for x in t["sizes"]]
    atlas_w=sum(widths) if widths else src.width
    atlas_h=max(heights) if heights else src.height
    # TPakDecoder copies each sprite from x=running total, y=0.
    rgba=Image.new("RGBA",(atlas_w,atlas_h),(0,0,0,0))
    alpha_src=load_image(t["alpha"]) if t["joint_alpha"] and t["alpha"] else None
    x=0
    for w,h in zip(widths,heights):
        rgb_piece=src.crop((x,0,x+w,h)).convert("RGBA")
        if t["alpha_quality"] != 0:
            if t["joint_alpha"] and alpha_src is not None:
                mask=alpha_src.crop((x,0,x+w,h)).convert("RGB").getchannel("R")
            else:
                # Original client reads alpha from RED in the lower half.
                mask=src.crop((x,src.height//2,x+w,src.height//2+h)).convert("RGB").getchannel("R")
            rgb_piece.putalpha(mask)
        rgba.alpha_composite(rgb_piece,(x,0))
        x += w
    return rgba

def parse_tex(path):
    data=Path(path).read_bytes()
    if len(data)<10: raise ValueError("TexClient too small")
    p=0
    rid,texlen=struct.unpack_from("<II",data,p); p+=8
    if p+texlen+2>len(data): raise ValueError("texture_data exceeds TexClient")
    texture_data=data[p:p+texlen]; p+=texlen
    pak_count=struct.unpack_from("<H",data,p)[0]; p+=2
    paks=[]
    for i in range(pak_count):
        if p+4>len(data): raise ValueError(f"unexpected EOF before pak {i} size")
        n=struct.unpack_from("<I",data,p)[0]; p+=4
        if p+n>len(data): raise ValueError(f"unexpected EOF in pak {i}")
        comp=data[p:p+n]; p+=n
        t=parse_tpak(comp); t["compressed"]=comp; t["index"]=i
        paks.append(t)
    if p+2>len(data): raise ValueError("unexpected EOF before sequence_count")
    seq_count=struct.unpack_from("<H",data,p)[0]; p+=2
    trailing=data[p:]
    return {
        "data":data,"identifier":rid,"texture_data":texture_data,
        "pak_count":pak_count,"paks":paks,"sequence_count":seq_count,
        "outer_trailing":trailing
    }

def png_paths(outbase,pak_count):
    outbase=Path(outbase)
    if pak_count==1:
        return [outbase.with_suffix(".png")]
    return [outbase.parent/(outbase.name+f".pak{i:02d}.png") for i in range(pak_count)]

def extract_one(src,outbase):
    t=parse_tex(src)
    paths=png_paths(outbase,t["pak_count"])
    meta={
      "format":"BleachOnline.TexClientWorkspace","version":3,
      "source":str(src),"identifier":t["identifier"],
      "identifier_hex":f"0x{t['identifier']:08X}",
      "texture_data_length":len(t["texture_data"]),
      "pak_count":t["pak_count"],"sequence_count":t["sequence_count"],
      "outer_trailing_hex":t["outer_trailing"].hex(),"paks":[]
    }
    for pak,path in zip(t["paks"],paths):
        atlas=atlas_from_tpak(pak)
        path.parent.mkdir(parents=True,exist_ok=True); atlas.save(path,"PNG")
        meta["paks"].append({
          "index":pak["index"],"png":path.name,"type":pak["type"],
          "width":pak["width"],"height":pak["height"],
          "quality":pak["quality"],"alpha_quality":pak["alpha_quality"],
          "alpha_filter":pak["alpha_filter"],"sprite_count":pak["sprite_count"],
          "sizes":pak["sizes"],"base_kind":pak["base_kind"],
          "alpha_kind":pak["alpha_kind"],"joint_alpha":pak["joint_alpha"],
          "atlas_width":atlas.width,"atlas_height":atlas.height,
          "tpak_trailing_hex":pak["trailing"].hex()
        })
    mpath=Path(outbase).with_suffix(".json")
    mpath.parent.mkdir(parents=True,exist_ok=True)
    mpath.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    return meta,paths

def rebuild_tpak(t, edited, quality_override=None):
    widths=[max(1,x["width"]) for x in t["sizes"]]
    heights=[max(1,x["height"]) for x in t["sizes"]]
    aw=sum(widths) if widths else edited.width
    ah=max(heights) if heights else edited.height
    if edited.size!=(aw,ah):
        raise ValueError(f"dimension mismatch: PNG={edited.size}, expected={(aw,ah)}")
    q=quality_override if quality_override is not None else max(1,min(100,t["quality"] or 95))
    # Rebuild the source surface exactly in the model used by TPakDecoder.
    if t["alpha_quality"] != 0 and not t["joint_alpha"]:
        surface=Image.new("RGBA",(aw,ah*2),(0,0,0,255))
        surface.paste(edited.convert("RGB"),(0,0))
        a=edited.getchannel("A")
        lower=Image.merge("RGB",(a,a,a))
        surface.paste(lower,(0,ah))
        base_blob=encode_image(surface,t["base_kind"],q,False)
        alpha_blob=None
    else:
        base_blob=encode_image(edited.convert("RGB"),t["base_kind"],q,False)
        alpha_blob=None
        if t["joint_alpha"]:
            aq=max(1,min(100,t["alpha_quality"] or q))
            alpha_blob=encode_image(edited.getchannel("A"),t["alpha_kind"],aq,True)

    raw=bytearray(t["meta_prefix"])
    raw += struct.pack(">I",len(base_blob)) + base_blob
    if t["joint_alpha"]:
        raw += struct.pack(">I",len(alpha_blob)) + alpha_blob
    raw += t["trailing"]
    return bytes(raw)

def repack_one(template,pngbase,out_tex,level=9,quality=None):
    t=parse_tex(template)
    paths=png_paths(pngbase,t["pak_count"])
    missing=[str(x) for x in paths if not x.exists()]
    if missing: raise FileNotFoundError("missing editable PNG(s): "+", ".join(missing))
    comps=[]
    for pak,path in zip(t["paks"],paths):
        edited=Image.open(path).convert("RGBA")
        raw=rebuild_tpak(pak,edited,quality)
        comp=zlib.compress(raw,level)
        # Structural self-check.
        chk=parse_tpak(comp)
        if chk["sprite_count"]!=pak["sprite_count"]:
            raise RuntimeError(f"TPak {pak['index']} validation failed")
        comps.append(comp)
    out=bytearray()
    out += struct.pack("<I",t["identifier"])
    out += struct.pack("<I",len(t["texture_data"])) + t["texture_data"]
    out += struct.pack("<H",len(comps))
    for comp in comps:
        out += struct.pack("<I",len(comp)) + comp
    out += struct.pack("<H",t["sequence_count"])
    out += t["outer_trailing"]
    Path(out_tex).parent.mkdir(parents=True,exist_ok=True)
    Path(out_tex).write_bytes(out)
    # Full outer validation.
    check=parse_tex(out_tex)
    if check["pak_count"]!=t["pak_count"] or check["identifier"]!=t["identifier"]:
        raise RuntimeError("TexClient round-trip validation failed")
    return {"identifier":t["identifier"],"pak_count":t["pak_count"],"validated":True,
            "output":str(out_tex),"pngs":[str(x) for x in paths]}

def cmd_extract(a):
    out=Path(a.output) if a.output else Path(a.input).with_suffix("")
    meta,paths=extract_one(a.input,out)
    print(f"[OK] {a.input} -> {len(paths)} PNG(s), {meta['pak_count']} pak(s)")

def cmd_extract_tree(a):
    root=Path(a.root); out=Path(a.output)
    fs=sorted(x for x in root.rglob("*") if x.is_file() and x.suffix.lower()==".texclient")
    ok=fail=0; report=[]
    print(f"[+] Found {len(fs):,} TexClient file(s)")
    for i,f in enumerate(fs,1):
        rel=f.relative_to(root); base=out/rel.parent/f.stem
        try:
            meta,paths=extract_one(f,base)
            report.append({"relative":rel.as_posix(),"status":"ok","pak_count":meta["pak_count"],
                           "pngs":[str(x) for x in paths]})
            ok+=1; print(f"[{i}/{len(fs)}] [OK] {rel} -> {meta['pak_count']} pak(s)")
        except Exception as e:
            fail+=1; report.append({"relative":rel.as_posix(),"status":"failed","error":str(e)})
            print(f"[{i}/{len(fs)}] [FAIL] {rel}: {e}")
    out.mkdir(parents=True,exist_ok=True)
    (out/"extract_tree_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"\nExtracted={ok:,} Failed={fail:,} Output={out.resolve()}")

def cmd_repack(a):
    r=repack_one(a.template,a.pngbase,a.output,a.level,a.quality)
    print(f"[OK] {a.output} ({r['pak_count']} pak(s), validated=yes)")

def cmd_repack_tree(a):
    templates=Path(a.templates); pngroot=Path(a.pngroot); out=Path(a.output)
    fs=sorted(x for x in templates.rglob("*") if x.is_file() and x.suffix.lower()==".texclient")
    done=skip=fail=0; report=[]
    print(f"[+] Found {len(fs):,} template TexClient file(s)")
    for i,f in enumerate(fs,1):
        rel=f.relative_to(templates); base=pngroot/rel.parent/f.stem; dst=out/rel
        try:
            t=parse_tex(f); paths=png_paths(base,t["pak_count"])
            if not all(x.exists() for x in paths):
                skip+=1; print(f"[{i}/{len(fs)}] [SKIP] {rel} - PNG(s) not found")
                report.append({"relative":rel.as_posix(),"status":"skipped_missing_png",
                               "expected_pngs":[str(x) for x in paths]}); continue
            r=repack_one(f,base,dst,a.level,a.quality)
            done+=1; report.append({"relative":rel.as_posix(),"status":"ok",**r})
            print(f"[{i}/{len(fs)}] [OK] {rel} ({r['pak_count']} pak(s))")
        except Exception as e:
            fail+=1; report.append({"relative":rel.as_posix(),"status":"failed","error":str(e)})
            print(f"[{i}/{len(fs)}] [FAIL] {rel}: {e}")
    out.mkdir(parents=True,exist_ok=True)
    (out/"repack_tree_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("\n========== SUMMARY ==========")
    print(f"Repacked : {done:,}\nSkipped  : {skip:,}\nFailed   : {fail:,}\nOutput   : {out.resolve()}")

def cmd_info(a):
    t=parse_tex(a.input)
    print(f"File           : {a.input}")
    print(f"Identifier     : 0x{t['identifier']:08X}")
    print(f"Texture data   : {len(t['texture_data']):,} bytes")
    print(f"Pak count      : {t['pak_count']}")
    print(f"Sequence count : {t['sequence_count']}")
    for p in t["paks"]:
        aw=sum(max(1,x["width"]) for x in p["sizes"])
        ah=max([max(1,x["height"]) for x in p["sizes"]] or [0])
        print(f"  Pak {p['index']:02d}: sprites={p['sprite_count']} atlas={aw}x{ah} "
              f"base={p['base_kind']} alpha={p['alpha_kind'] or 'embedded/none'} "
              f"Q={p['quality']} AQ={p['alpha_quality']}")

def main():
    ap=argparse.ArgumentParser(description="Bleach Online TexClient complete converter v3")
    sp=ap.add_subparsers(dest="cmd",required=True)
    x=sp.add_parser("info"); x.add_argument("input",type=Path); x.set_defaults(func=cmd_info)
    x=sp.add_parser("extract"); x.add_argument("input",type=Path); x.add_argument("-o","--output",type=Path); x.set_defaults(func=cmd_extract)
    x=sp.add_parser("extract-tree"); x.add_argument("root",type=Path); x.add_argument("-o","--output",type=Path,default=Path("PNG")); x.set_defaults(func=cmd_extract_tree)
    x=sp.add_parser("repack"); x.add_argument("template",type=Path); x.add_argument("pngbase",type=Path); x.add_argument("-o","--output",type=Path,required=True); x.add_argument("--level",type=int,choices=range(10),default=6); x.add_argument("-q","--quality",type=int,choices=range(1,101)); x.set_defaults(func=cmd_repack)
    x=sp.add_parser("repack-tree"); x.add_argument("templates",type=Path); x.add_argument("pngroot",type=Path); x.add_argument("-o","--output",type=Path,default=Path("Repacked_PNG")); x.add_argument("--level",type=int,choices=range(10),default=9); x.add_argument("-q","--quality",type=int,choices=range(1,101)); x.set_defaults(func=cmd_repack_tree)
    a=ap.parse_args()
    try: a.func(a)
    except Exception as e:
        print(f"[FAIL] {e}",file=sys.stderr); raise SystemExit(1)

if __name__=="__main__":
    main()
