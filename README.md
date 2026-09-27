# Bleach Online Legacy Client Research Toolkit

> **Educational reverse-engineering and digital-preservation study of a
> legacy Adobe Flash game client.**

## Introduction

This repository documents a personal hobby and educational research
project focused on the legacy client files of **Bleach Online**, a
browser game built around Adobe Flash-era technologies and data formats.

The purpose of the project is not to recreate, redistribute, or
commercially exploit the original game. Its goal is to study how an
older Flash-based client organized, protected, compressed, serialized,
and rendered its local resources, and to build small research tools
capable of interpreting those structures.

Adobe Flash Player reached end-of-life at the end of 2020, and modern
browsers no longer provide native support for the runtime. As a
consequence, many browser games, interactive applications, animations,
and other pieces of web history became difficult to execute or inspect
using contemporary software.

This project explores an important preservation question:

**When the original runtime is obsolete, can the underlying data still
be understood and recovered?**

The results of this study show that, in many cases, the answer is
**yes**.

By examining legacy client code, binary structures, resource
identifiers, compression layers, image containers, map tiling rules, and
SWF behavior, it is possible to recover meaningful information from
formats that are no longer directly supported by the modern web. The
scripts in this repository are therefore best understood as **digital
archaeology and interoperability tools**: they transform opaque legacy
resources into formats that can be inspected with current software while
preserving enough structural information to reconstruct them.

This does not automatically restore the original online service, server
infrastructure, or complete gameplay experience. It does, however,
demonstrate that a significant portion of a discontinued Flash client's
assets and data structures can remain technically recoverable long after
the original delivery platform has disappeared.

------------------------------------------------------------------------

## Project Components

The toolkit is divided into four Python utilities and one schema
registry:

``` text
[1]_Bleach_CoreSwf.py
[2]_Bleach_TexClient.py
[3]_Bleach_Map.py
[4]_Bleach_Bin.py
[5]_Bleach_BinSchema.json
```

Each utility focuses on a different layer of the client resource
pipeline.

------------------------------------------------------------------------

## 1. Core SWF Analysis --- `[1]_Bleach_CoreSwf.py`

The Core SWF tool studies the protection and compression applied to the
client's `TApplication.swf`.

SWF files normally begin with a recognizable signature describing how
the file body is stored. In the analyzed client, the application SWF
uses a lightweight protection mechanism that alters selected byte
positions, preventing the file from immediately presenting itself as a
conventional compressed SWF.

The tool models the protection observed in the studied sample and can
reverse the transformation to recover a structurally valid SWF stream.
It also understands the distinction between:

-   **CWS** --- a SWF whose body is compressed with zlib;
-   **FWS** --- an uncompressed SWF representation.

Internally, the tool validates SWF headers, declared file lengths,
compression streams, signatures, and SHA-256 hashes. This makes it
useful not only for recovering the application SWF but also for
documenting exactly which transformation is being applied.

The reverse operation is also represented, allowing a valid SWF
representation to be transformed back into the protection layout
observed in the original client.

From a reverse-engineering perspective, this tool represents the first
layer of the study: recovering executable legacy code into a form that
can be inspected by contemporary SWF analysis and decompilation tools.

------------------------------------------------------------------------

## 2. TexClient Resource Conversion --- `[2]_Bleach_TexClient.py`

`TexClient` is a proprietary client-side texture container used by the
analyzed Bleach Online resources.

A `.TexClient` file is **not simply an image with a different
extension**. It is a structured binary resource containing
texture-related data, one or more compressed texture packages, metadata,
and information required by the original client.

The format observed during this study contains an outer little-endian
stream with resource information and one or more compressed texture
packages. Those packages are referred to by the client as **TPak**
structures.

After zlib decompression, a TPak contains information such as:

-   texture type;
-   width and height;
-   image quality parameters;
-   alpha-channel quality and filtering information;
-   sprite count;
-   sprite dimensions and positional metadata;
-   encoded base-image data;
-   optional separate alpha-image data.

The embedded raster data can use formats such as JPEG or PNG. Some
resources store color and alpha information separately, meaning that
simply extracting the visible base image is insufficient to reconstruct
the original appearance.

The TexClient tool parses these layers and reconstructs editable **RGBA
PNG atlases**. When multiple TPaks exist in the same resource, they are
preserved as separate editable images.

An important design goal is **template-based reconstruction**. Instead
of attempting to regenerate undocumented portions of the format from
assumptions, the tool preserves original texture data, sequence
information, package ordering, unknown bytes, and metadata whenever
possible, replacing only the image streams that were intentionally
edited.

This approach reduces unnecessary changes to legacy binary structures
and makes the converter useful for studying round-trip behavior.

------------------------------------------------------------------------

## 3. Map Reconstruction --- `[3]_Bleach_Map.py`

Large game maps are not stored as a single enormous image. The client
divides them into smaller texture tiles that can be loaded and rendered
independently.

The Map tool reconstructs these individual PNG tiles into complete map
images.

Analysis of the client code and resource naming convention showed that
map texture identifiers encode:

``` text
MapID << 16 | X << 8 | Y
```

This means the final bytes of a map tile resource identify its spatial
position:

-   the first coordinate byte represents **X / column**;
-   the second coordinate byte represents **Y / row**.

For example, a resource named conceptually as:

``` text
4E2A0402.png
```

represents map `4E2A`, column `04`, row `02`.

This distinction is important. Treating the suffix as row-then-column
transposes the reconstructed map. The implementation follows the
coordinate behavior recovered from the client rather than relying only
on filename appearance.

The tool groups tiles by map ID, verifies tile dimensions, determines
the occupied grid, identifies missing positions, and produces a complete
RGBA canvas. A JSON manifest records the relationship between the
composed image and every original tile.

The reverse process uses that manifest to split an edited full map back
into the original tile layout and filenames.

This component demonstrates how source-code analysis and asset analysis
can complement each other: the images alone reveal a pattern, while the
legacy client code explains the exact coordinate semantics behind that
pattern.

------------------------------------------------------------------------

## 4. BIN / BINPACKAGE Semantic Conversion --- `[4]_Bleach_Bin.py`

The `.bin` and `.binPackage` resources represent another layer of the
Bleach Online client: **serialized structured game data**.

Despite their generic extensions, these files are not arbitrary raw
binary blobs.

### `.bin`

In the database resources analyzed by this project, a standalone `.bin`
is stored as a zlib-compressed stream. After decompression, the data
follows a structured little-endian representation containing a resource
identifier, a record count, and class-specific records.

Conceptually:

``` text
compressed .bin
      |
      v
    zlib
      |
      v
resource identifier
record count
record 0
record 1
record 2
...
```

The meaning and binary width of each field depend on the resource class.
For that reason, the converter deliberately avoids guessing unknown
layouts.

Recovered schemas describe known field types such as integers,
floating-point values, booleans, and length-prefixed UTF strings. The
resulting data can then be represented semantically as JSON rather than
as an unreadable byte stream.

The study also identified a separate family of map `.bin` resources
containing map-related structural information. These require dedicated
interpretation rather than being treated as ordinary database tables.

### `.binPackage`

A `.binPackage` is a higher-level binary container.

The analyzed package structure contains a package identifier followed by
a member count and a sequence of compressed database members. Each
member is itself a zlib-compressed DB stream.

Conceptually:

``` text
.binPackage
|
+-- package identifier
+-- member count
|
+-- compressed member
|      +-- DB resource
|
+-- compressed member
|      +-- DB resource
|
+-- ...
```

Therefore, `.binPackage` should not be interpreted as one large database
table. It is a **container of multiple binary database resources**.

The converter separates package members, resolves their resource IDs
against known schemas, and exposes supported records as semantic JSON.
During reconstruction, the package hierarchy and binary serialization
rules are restored.

------------------------------------------------------------------------

## 5. Binary Schema Registry --- `[5]_Bleach_BinSchema.json`

The schema registry is intentionally kept separate from the binary
converter.

It describes resource IDs for which a sufficiently precise structure was
recovered from the client code and research samples.

This separation is important because binary parsing becomes unreliable
when field sizes or serialization rules are guessed. A single incorrect
assumption can shift every subsequent byte and produce data that looks
plausible while actually being corrupted.

The converter therefore follows a conservative principle:

> **If an exact schema has not been recovered, do not silently invent
> one.**

The registry acts as a documented bridge between resource identifiers
and their known semantic structures.

------------------------------------------------------------------------

## Why These Formats Matter for Digital Preservation

Legacy browser games frequently depended on more than a `.swf` file.

A complete client could involve:

-   SWF application code;
-   custom binary databases;
-   proprietary texture containers;
-   compressed package formats;
-   tiled maps;
-   resource manifests;
-   server-provided data;
-   runtime-specific serialization rules.

Preserving only screenshots or a single SWF can therefore preserve the
*appearance* of a game without preserving enough information to
understand how the client actually worked.

Studying formats such as `TexClient`, `.bin`, and `.binPackage` provides
another form of preservation: **structural preservation**.

Even when the original web runtime is unavailable, binary analysis can
reveal how images were encoded, how maps were assembled, how records
were serialized, how resources referenced each other, and how the
original software expected those resources to be loaded.

This project is an example of how reverse engineering can contribute to
the study of obsolete software formats and lost or endangered digital
media.

------------------------------------------------------------------------

## Technical Principles

The tools in this repository follow several principles intended to make
the research reproducible and conservative.

### Source-grounded interpretation

Where possible, binary behavior is derived from the analyzed client
implementation rather than inferred exclusively from file appearance.

### Exact binary parsing

Byte order, integer width, compression boundaries, string encoding, and
resource identifiers are treated as part of the format specification.

### Round-trip awareness

Extraction alone is not sufficient to understand a format. Where
practical, the tools also study reconstruction so that assumptions can
be tested against the original structure.

### Preservation of unknown data

Undocumented bytes should not automatically be discarded. Template-based
reconstruction is used where appropriate so that unknown or unrelated
structures can remain intact.

### Explicit failure instead of silent guessing

Unsupported structures should be reported as unsupported rather than
decoded using speculative field definitions.

### Separation between original assets and research code

The Python tools and format documentation are the work of this research
project. Original game files, artwork, characters, trademarks, source
material, and other proprietary assets are not part of the project's
software license.

------------------------------------------------------------------------

## Educational and Research Scope

This repository is presented as a **personal hobby, educational
exercise, interoperability experiment, and digital-preservation study**.

Topics explored include:

-   binary file-format analysis;
-   legacy ActionScript/SWF behavior;
-   zlib compression;
-   binary serialization;
-   endianness;
-   raster image encoding;
-   alpha-channel reconstruction;
-   tiled map rendering;
-   resource identifiers;
-   schema recovery;
-   deterministic and template-based repacking;
-   reverse engineering of discontinued software formats.

The repository should be understood as technical documentation and
tooling for studying software that is no longer naturally compatible
with the modern browser ecosystem.

------------------------------------------------------------------------

## Ethics of Reverse Engineering

Reverse engineering can serve legitimate purposes including education,
interoperability, security research, archival work, historical study,
and preservation. It can also be misused.

Anyone using this project is responsible for ensuring that their
research complies with the laws, licenses, terms, and permissions
applicable in their jurisdiction and to the material they possess.

This project should **not** be used to:

-   obtain unauthorized access to servers, accounts, or private systems;
-   bypass authentication or access controls;
-   steal credentials or private information;
-   impersonate an official service;
-   distribute proprietary game assets without authorization;
-   claim ownership of third-party intellectual property;
-   interfere with active services or other users;
-   facilitate cheating, fraud, or commercial exploitation of material
    without the necessary rights.

When researching legacy software, prefer working with files you are
legally entitled to possess, keep original samples unchanged, document
transformations, and distinguish clearly between independently written
research tools and proprietary material recovered from the software
being studied.

The existence of a technical method for extracting or reconstructing a
resource does **not** imply permission to redistribute that resource.

------------------------------------------------------------------------

## Intellectual Property Disclaimer

**Bleach**, its characters, artwork, names, logos, visual designs, and
related intellectual property belong to their respective original
authors, publishers, licensors, and rights holders.

The original **Bleach Online** game client, proprietary file formats as
implemented by that software, original binaries, artwork, textures,
maps, source assets, and other game resources remain the property of
their respective developers, publishers, licensors, and copyright
holders.

This repository is **not affiliated with, endorsed by, sponsored by, or
officially connected to** the creators or rights holders of Bleach or
Bleach Online.

No ownership of the original game's intellectual property is claimed.

The license applied to this repository covers **only the independently
written scripts and original documentation provided by this project**,
unless explicitly stated otherwise. It does not grant rights to
third-party game files, images, characters, trademarks, or other
copyrighted material.

------------------------------------------------------------------------

## License

### MIT License

The independently written Python scripts and original project
documentation are intended to be released under the **MIT License**.

MIT was selected because it is a simple permissive software license that
allows study, modification, redistribution, and reuse while requiring
preservation of the copyright and license notice.

The MIT License applies only to material authored specifically for this
repository. It does **not** relicense any Bleach Online resource, SWF,
texture, map, binary database, artwork, character, trademark, or other
third-party material.

A repository using this license should include the complete license text
in a separate `LICENSE` file.

------------------------------------------------------------------------

## Preservation Perspective

Flash was once a major platform for browser games and interactive web
content. Its disappearance illustrates a broader preservation problem:
software can survive as bytes while the environment required to
understand those bytes disappears.

Recovering a legacy application is therefore not always a matter of
simply "opening an old file." It may require reconstructing the
assumptions once provided by the runtime itself: compression,
serialization, rendering, resource addressing, and application-specific
container formats.

The Bleach Online client provides a useful case study because its
resources span several of these layers.

The central result of this project is not that every discontinued online
game can be restored completely. Rather, it demonstrates something
narrower and technically important:

> **Obsolete delivery technology does not necessarily mean that the
> underlying digital media is irrecoverable.**

With careful analysis, documentation, and purpose-built tooling, legacy
resources can often be translated into modern, inspectable
representations. That makes reverse engineering not only a debugging
technique, but also a potentially valuable tool for understanding and
preserving parts of the web's software history.

------------------------------------------------------------------------

## Final Note

This repository is a research notebook expressed through code.

Its value is in documenting observations, testing hypotheses against
real binary structures, and making legacy data understandable without
pretending that undocumented behavior is known with certainty.

If a format remains partially unknown, that uncertainty should remain
visible. Digital preservation benefits from recoverable data, but it
also benefits from accurate documentation of what has --- and has not
--- been established.
