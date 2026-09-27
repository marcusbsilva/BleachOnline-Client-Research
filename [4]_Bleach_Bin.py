#!/usr/bin/env python3
"""
Bleach Online BIN / BINPACKAGE <-> separated semantic JSON v6

Binary schemas were recovered from TApplication_uncompressed(1).swf:
- DB streams are LITTLE-ENDIAN.
- Standalone DB .bin files are zlib streams.
- Decompressed DB:
      uint32 resource_id
      uint32 record_count
      repeat:
          uint32 record_id
          class-specific fields
- .binPackage:
      uint32 package_id
      uint16 member_count
      repeat:
          uint32 compressed_member_size
          zlib-compressed DB stream

This converter deliberately refuses resources without an exact recovered schema.
It does not guess binary widths.

Commands:
  extract-tree Resources -s [5]_Bleach_BinSchema.json -o DB_EDIT
  validate DB_EDIT -s [5]_Bleach_BinSchema.json
  repack-tree Resources DB_EDIT -s [5]_Bleach_BinSchema.json -o Repacked
"""

import argparse, json, struct, zlib, hashlib
from pathlib import Path

def sha256(b): return hashlib.sha256(b).hexdigest()

def norm_id(v):
    if isinstance(v, str):
        return "0x%08X" % int(v, 0)
    return "0x%08X" % int(v)

class Reader:
    def __init__(self, b):
        self.b=b; self.p=0
    def take(self,n):
        if self.p+n>len(self.b): raise ValueError("Unexpected EOF")
        x=self.b[self.p:self.p+n]; self.p+=n; return x
    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack("<H",self.take(2))[0]
    def u32(self): return struct.unpack("<I",self.take(4))[0]
    def i32(self): return struct.unpack("<i",self.take(4))[0]
    def f32(self): return struct.unpack("<f",self.take(4))[0]
    def f64(self): return struct.unpack("<d",self.take(8))[0]
    def utf(self):
        n=self.u16()
        return self.take(n).decode("utf-8")

class Writer:
    def __init__(self): self.b=bytearray()
    def u8(self,v): self.b += struct.pack("B",int(v))
    def u16(self,v): self.b += struct.pack("<H",int(v))
    def u32(self,v): self.b += struct.pack("<I",int(v))
    def i32(self,v): self.b += struct.pack("<i",int(v))
    def f32(self,v): self.b += struct.pack("<f",float(v))
    def f64(self,v): self.b += struct.pack("<d",float(v))
    def utf(self,s):
        raw=str(s).encode("utf-8")
        if len(raw)>65535: raise ValueError("UTF string exceeds uint16 byte length")
        self.u16(len(raw)); self.b += raw
    def bytes(self): return bytes(self.b)

def zdec(data):
    o=zlib.decompressobj()
    raw=o.decompress(data)+o.flush()
    if not o.eof or o.unused_data:
        raise ValueError("Invalid or concatenated zlib stream")
    return raw

def read_value(r,c):
    if c=="uint32": return r.u32()
    if c=="int32": return r.i32()
    if c=="float32": return r.f32()
    if c=="float64": return r.f64()
    if c=="bool8": return bool(r.u8())
    if c=="utf": return r.utf()
    raise ValueError("Unsupported codec: "+c)

def write_value(w,c,v):
    if c=="uint32": w.u32(v)
    elif c=="int32": w.i32(v)
    elif c=="float32": w.f32(v)
    elif c=="float64": w.f64(v)
    elif c=="bool8": w.u8(1 if v else 0)
    elif c=="utf": w.utf(v)
    else: raise ValueError("Unsupported codec: "+c)

def load_schemas(path):
    d=json.loads(Path(path).read_text(encoding="utf-8"))
    return d["resources"]

def decode_db(raw,schemas):
    r=Reader(raw)
    rid=norm_id(r.u32())
    count=r.u32()
    if rid not in schemas:
        raise KeyError(f"No exact schema for {rid}")
    sc=schemas[rid]
    records=[]
    for _ in range(count):
        rec={}
        for f in sc["fields"]:
            rec[f["name"]]=read_value(r,f["codec"])
        records.append(rec)
    trailing=r.take(len(raw)-r.p)
    if trailing:
        raise ValueError(f"Unexpected trailing bytes in DB stream: {len(trailing)}")
    return {
        "format":"BleachOnline.SemanticDatabase",
        "version":7,
        "file_id":rid,
        "class":sc["class"],
        "count":count,
        "records":records,
        "_trailing_hex":""
    }

def encode_db(doc,schemas):
    rid=norm_id(doc["file_id"])
    if rid not in schemas: raise KeyError(f"No exact schema for {rid}")
    sc=schemas[rid]
    if doc.get("class") != sc["class"]:
        raise ValueError(f"class mismatch: JSON={doc.get('class')} schema={sc['class']}")
    records=doc["records"]
    w=Writer(); w.u32(int(rid,16)); w.u32(len(records))
    for rec in records:
        for f in sc["fields"]:
            if f["name"] not in rec:
                raise ValueError(f"Missing field {f['name']} in {sc['class']}")
            write_value(w,f["codec"],rec[f["name"]])
    if doc.get("_trailing_hex",""):
        raise ValueError("DB trailing bytes are not accepted by the recovered client parser")
    return w.bytes()

def parse_package(data):
    r=Reader(data); pid=r.u32(); count=r.u16(); members=[]
    for i in range(count):
        n=r.u32(); comp=r.take(n); raw=zdec(comp)
        members.append({"index":i,"compressed":comp,"raw":raw})
    trailing=r.take(len(data)-r.p)
    return pid,members,trailing

def safe_name(rid,cls):
    return f"{rid[2:]}_{cls}.json"


def map_read_utf_be(r):
    # Map streams are BIG-ENDIAN in the supplied client build.
    if r.p+2 > len(r.b): raise ValueError("Unexpected EOF reading map UTF length")
    n=struct.unpack(">H",r.take(2))[0]
    return r.take(n).decode("utf-8")

def decode_map(raw):
    # Confirmed from TResourceUnstreamizerMap.as.
    p=0
    def take(n):
        nonlocal p
        if p+n>len(raw): raise ValueError("Unexpected EOF in map stream")
        b=raw[p:p+n]; p+=n; return b
    def u8(): return take(1)[0]
    def u16(): return struct.unpack(">H",take(2))[0]
    def u32(): return struct.unpack(">I",take(4))[0]
    def utf():
        n=u16()
        return take(n).decode("utf-8")
    ident=u32()
    doc={
      "format":"BleachOnline.Map","version":7,
      "identifier":ident,"identifier_hex":f"0x{ident:08X}",
      "Name":utf(),"Mode":u8(),"Type":u8(),
      "Width":u32(),"Height":u32(),
      "CellWidth":u16(),"CellHeight":u16(),
      "PreRows":u8(),"PreColumns":u8(),
      "TileWidth":u16(),"TileHeight":u16(),
      "Rows":u16(),"Columns":u16(),
      "StartPointX":u32(),"StartPointY":u32(),
      "MapData":utf()
    }
    # The supplied map samples contain two trailing zero bytes after MapData.
    # TResourceUnstreamizerMap does not validate bytesAvailable, so preserve them.
    doc["_trailing_hex"]=raw[p:].hex()
    return doc

def encode_map(doc):
    b=bytearray()
    def u8(v): b.extend(struct.pack(">B",int(v)))
    def u16(v): b.extend(struct.pack(">H",int(v)))
    def u32(v): b.extend(struct.pack(">I",int(v)))
    def utf(v):
        x=str(v).encode("utf-8")
        if len(x)>65535: raise ValueError("Map UTF exceeds uint16 byte length")
        u16(len(x)); b.extend(x)
    ident=doc.get("identifier")
    if isinstance(ident,str): ident=int(ident,0)
    u32(ident); utf(doc["Name"]); u8(doc["Mode"]); u8(doc["Type"])
    u32(doc["Width"]); u32(doc["Height"])
    u16(doc["CellWidth"]); u16(doc["CellHeight"])
    u8(doc["PreRows"]); u8(doc["PreColumns"])
    u16(doc["TileWidth"]); u16(doc["TileHeight"])
    u16(doc["Rows"]); u16(doc["Columns"])
    u32(doc["StartPointX"]); u32(doc["StartPointY"]); utf(doc["MapData"])
    b.extend(bytes.fromhex(doc.get("_trailing_hex","")))
    return bytes(b)


def extract_tree(root,out,schemas):
    root=Path(root).resolve(); out=Path(out).resolve()
    stand=out/"standalone"; packs=out/"packages"; mapsdir=out/"maps"; rawdir=out/"unsupported_raw"
    stand.mkdir(parents=True,exist_ok=True); packs.mkdir(parents=True,exist_ok=True); mapsdir.mkdir(parents=True,exist_ok=True)
    rawdir.mkdir(parents=True,exist_ok=True)
    ws={"format":"BleachOnline.SemanticWorkspace","version":7,"root":str(root),
        "standalone":[],"packages":[],"maps":[],"unsupported":[]}

    # Standalone .bin, excluding files physically under .binPackage extraction dirs if any.
    for f in sorted(root.rglob("*.bin")):
        rel=f.relative_to(root)
        if rel.parts and rel.parts[0].lower()=="map":
            continue
        try:
            comp=f.read_bytes(); raw=zdec(comp); rid=norm_id(struct.unpack_from("<I",raw,0)[0])
            doc=decode_db(raw,schemas)
            dst=stand/rel.parent/safe_name(rid,doc["class"])
            dst.parent.mkdir(parents=True,exist_ok=True)
            doc["_source_relative"]=rel.as_posix()
            doc["_sha256_decompressed"]=sha256(raw)
            dst.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf-8")
            ws["standalone"].append({"source":rel.as_posix(),"json":dst.relative_to(out).as_posix()})
            print(f"[BIN] {rel} -> {dst.relative_to(out)} ({doc['class']}, {doc['count']} records)")
        except Exception as e:
            dst=rawdir/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(f.read_bytes())
            ws["unsupported"].append({"source":rel.as_posix(),"reason":str(e),"raw":dst.relative_to(out).as_posix()})
            print(f"[SKIP] {rel}: {e}")

    # Map/*.bin uses a different, big-endian binary structure.
    for f in sorted(root.rglob("*.bin")):
        rel=f.relative_to(root)
        if not (rel.parts and rel.parts[0].lower()=="map"):
            continue
        try:
            comp=f.read_bytes(); raw=zdec(comp); doc=decode_map(raw)
            dst=mapsdir/rel.with_suffix(".map.json")
            dst.parent.mkdir(parents=True,exist_ok=True)
            doc["_source_relative"]=rel.as_posix()
            doc["_sha256_decompressed"]=sha256(raw)
            dst.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf-8")
            ws["maps"].append({"source":rel.as_posix(),"json":dst.relative_to(out).as_posix()})
            print(f"[MAP] {rel} -> {dst.relative_to(out)} ({doc['Name']})")
        except Exception as e:
            dst=rawdir/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(f.read_bytes())
            ws["unsupported"].append({"source":rel.as_posix(),"reason":"MAP: "+str(e),"raw":dst.relative_to(out).as_posix()})
            print(f"[MAP-FAIL] {rel}: {e}")

    for f in sorted(root.rglob("*.binPackage")):
        rel=f.relative_to(root)
        try:
            pid,members,trailing=parse_package(f.read_bytes())
            pdir=packs/rel
            pdir.mkdir(parents=True,exist_ok=True)
            pm={"format":"BleachOnline.PackageWorkspace","version":7,
                "source_relative":rel.as_posix(),"package_id":pid,
                "package_id_hex":f"0x{pid:08X}","member_count":len(members),
                "trailing_hex":trailing.hex(),"members":[]}
            for m in members:
                raw=m["raw"]
                rid=norm_id(struct.unpack_from("<I",raw,0)[0]) if len(raw)>=4 else "unknown"
                try:
                    doc=decode_db(raw,schemas)
                    fn=safe_name(rid,doc["class"])
                    # duplicate-safe, though resource IDs should normally be unique
                    if (pdir/fn).exists(): fn=f"{m['index']:04d}_{fn}"
                    doc["_package_member_index"]=m["index"]
                    doc["_sha256_decompressed"]=sha256(raw)
                    (pdir/fn).write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf-8")
                    pm["members"].append({"index":m["index"],"kind":"semantic","file":fn,
                                          "file_id":rid,"class":doc["class"]})
                except Exception as e:
                    fn=f"{m['index']:04d}_{rid.replace('0x','')}.raw.bin"
                    (pdir/fn).write_bytes(raw)
                    pm["members"].append({"index":m["index"],"kind":"raw","file":fn,
                                          "file_id":rid,"reason":str(e)})
                    print(f"  [UNSUPPORTED MEMBER] index={m['index']} id={rid} raw={len(raw)} bytes reason={e}")
            (pdir/"manifest.json").write_text(json.dumps(pm,ensure_ascii=False,indent=2),encoding="utf-8")
            ws["packages"].append({"source":rel.as_posix(),"directory":pdir.relative_to(out).as_posix()})
            sem=sum(x["kind"]=="semantic" for x in pm["members"])
            print(f"[PACKAGE] {rel}: {sem}/{len(members)} semantic members")
        except Exception as e:
            print(f"[FAIL] {rel}: {e}")

    (out/"workspace.json").write_text(json.dumps(ws,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"\nWorkspace: {out}")
    print(f"Standalone semantic: {len(ws['standalone'])}")
    print(f"Packages: {len(ws['packages'])}")
    print(f"Maps: {len(ws['maps'])}")
    print(f"Unsupported standalone: {len(ws['unsupported'])}")

def validate_workspace(root,schemas):
    root=Path(root); ws=json.loads((root/"workspace.json").read_text(encoding="utf-8"))
    bad=0; checked=0
    files=[]
    for x in ws.get("standalone",[]): files.append(root/x["json"])
    for p in ws.get("packages",[]):
        pdir=root/p["directory"]
        pm=json.loads((pdir/"manifest.json").read_text(encoding="utf-8"))
        files += [pdir/m["file"] for m in pm["members"] if m["kind"]=="semantic"]
    for x in ws.get("maps",[]):
        p=root/x["json"]; checked+=1
        try:
            d=json.loads(p.read_text(encoding="utf-8"))
            raw=encode_map(d); again=decode_map(raw)
            if again["identifier"] != (int(d["identifier"],0) if isinstance(d["identifier"],str) else d["identifier"]):
                raise ValueError("map identifier mismatch")
        except Exception as e:
            bad+=1; print(f"[FAIL] {p}: {e}")

    for p in files:
        checked+=1
        try:
            d=json.loads(p.read_text(encoding="utf-8"))
            raw=encode_db(d,schemas)
            again=decode_db(raw,schemas)
            if again["count"] != len(d["records"]): raise ValueError("record count mismatch")
        except Exception as e:
            bad+=1; print(f"[FAIL] {p}: {e}")
    print(f"Validated: {checked}; invalid: {bad}")
    return bad

def repack_tree(templates,workspace,out,schemas,level):
    templates=Path(templates).resolve(); workspace=Path(workspace).resolve(); out=Path(out).resolve()
    ws=json.loads((workspace/"workspace.json").read_text(encoding="utf-8"))
    ok=fail=0

    # Copy/rebuild standalone files represented by workspace.
    for x in ws.get("standalone",[]):
        try:
            d=json.loads((workspace/x["json"]).read_text(encoding="utf-8"))
            raw=encode_db(d,schemas); comp=zlib.compress(raw,level)
            if zdec(comp)!=raw: raise ValueError("zlib self-check failed")
            dst=out/x["source"]; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(comp)
            ok+=1; print(f"[BIN] {x['source']}")
        except Exception as e:
            fail+=1; print(f"[FAIL] {x['source']}: {e}")

    # Rebuild Map/*.bin with the exact big-endian structure used by TResourceUnstreamizerMap.
    for x in ws.get("maps",[]):
        try:
            d=json.loads((workspace/x["json"]).read_text(encoding="utf-8"))
            raw=encode_map(d); comp=zlib.compress(raw,level)
            if decode_map(zdec(comp))["identifier"] != decode_map(raw)["identifier"]:
                raise ValueError("map round-trip failed")
            dst=out/x["source"]; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(comp)
            ok+=1; print(f"[MAP] {x['source']}")
        except Exception as e:
            fail+=1; print(f"[MAP-FAIL] {x['source']}: {e}")

    # Preserve unsupported standalone from original templates.
    for x in ws.get("unsupported",[]):
        src=templates/x["source"]; dst=out/x["source"]
        if src.exists():
            dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(src.read_bytes())

    # Rebuild packages, using raw workspace members for unsupported schemas.
    for p in ws.get("packages",[]):
        try:
            pdir=workspace/p["directory"]
            pm=json.loads((pdir/"manifest.json").read_text(encoding="utf-8"))
            w=Writer(); w.u32(pm["package_id"]); w.u16(len(pm["members"]))
            for m in sorted(pm["members"],key=lambda q:q["index"]):
                fp=pdir/m["file"]
                if m["kind"]=="semantic":
                    d=json.loads(fp.read_text(encoding="utf-8")); raw=encode_db(d,schemas)
                else:
                    raw=fp.read_bytes()
                comp=zlib.compress(raw,level); w.u32(len(comp)); w.b += comp
            w.b += bytes.fromhex(pm.get("trailing_hex",""))
            rebuilt=w.bytes()
            # full structural verification
            pid,members,trailing=parse_package(rebuilt)
            if pid!=pm["package_id"] or len(members)!=len(pm["members"]):
                raise ValueError("package verification failed")
            dst=out/p["source"]; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(rebuilt)
            ok+=1; print(f"[PACKAGE] {p['source']} ({len(members)} members)")
        except Exception as e:
            fail+=1; print(f"[FAIL] {p['source']}: {e}")

    print(f"\nRebuilt: {ok}; failed: {fail}; output: {out}")

def main():
    ap=argparse.ArgumentParser(description="Bleach Online semantic BIN/BINPACKAGE/MAP converter v7")
    sp=ap.add_subparsers(dest="cmd",required=True)
    x=sp.add_parser("extract-tree"); x.add_argument("root",type=Path); x.add_argument("-s","--schemas",type=Path,required=True); x.add_argument("-o","--output",type=Path,default=Path("DB_EDIT"))
    x=sp.add_parser("validate"); x.add_argument("workspace",type=Path); x.add_argument("-s","--schemas",type=Path,required=True)
    x=sp.add_parser("repack-tree"); x.add_argument("templates",type=Path); x.add_argument("workspace",type=Path); x.add_argument("-s","--schemas",type=Path,required=True); x.add_argument("-o","--output",type=Path,default=Path("Repacked")); x.add_argument("--level",type=int,choices=range(10),default=6)
    a=ap.parse_args(); schemas=load_schemas(a.schemas)
    if a.cmd=="extract-tree": extract_tree(a.root,a.output,schemas)
    elif a.cmd=="validate": raise SystemExit(1 if validate_workspace(a.workspace,schemas) else 0)
    elif a.cmd=="repack-tree": repack_tree(a.templates,a.workspace,a.output,schemas,a.level)

if __name__=="__main__": main()
