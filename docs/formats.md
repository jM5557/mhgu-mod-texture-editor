# File formats

What the tool reads and writes. Layouts follow `tools/arcpack.py`, `tools/tex.py` and `notes/assets.md` of
[Scrimas/MH4U-port-to-MHGU](https://github.com/Scrimas/MH4U-port-to-MHGU), where the TEX round trip
(deswizzle → swizzle → header rebuild) is byte-identical over all 7928 textures of the 137 MHGU enemy arcs.

## ARC (MT Framework archive), MHGU version 0x11

```
'ARC\0'  u16 version (0x11)  u16 count  u32 0
count × 0x50-byte entries:
    char path[64]      backslashes, no extension, NUL-padded
    u32  type hash     e.g. 0x241F5DEB = rTexture
    u32  compressed size
    u32  decompressed size | flags << 29     (MHGU: flags = 2 on every entry)
    u32  offset
zero padding up to the next 0x8000 boundary, then the zlib streams, contiguous in table order
```
The tool rebuilds arcs in this exact layout, in the original entry order. MHGU's zlib streams are not
reproduced byte for byte by Python's zlib, but every entry decompresses to the same bytes; the game only
cares about the contents. Each rebuilt arc is read back and compared entry by entry before it is written.

## TEX (rTexture), MHGU version 0xA0

```
'TEX\0'
u32 tier0   version:8 (0xA0) | unk:6 | unk:14 | type:4        (type 6 = cube map, not supported)
u32 tier1   mips:6 | width:13 | height:13
u8  faces   u8 format   u16 depth
u32 data size
mips × u32  offsets (relative to the data start)
data        each mip in Tegra X1 block-linear layout
```

| format byte | name | used for |
|---|---|---|
| 0x13, 0x14 | BC1 | colour, 1-bit alpha |
| 0x17 | BC3 | colour + alpha (`_BM`, `_BM+A`) |
| 0x19 | BC4 | one channel |
| 0x1F | BC5 | normal maps (R, G) |
| 0x07 | RGBA8 | uncompressed |

Block linear: 64-byte × 8-row GOBs; block height = min(16, next power of two of ceil(height in blocks / 8))
GOBs, halved per mip while the mip is at most half a block high. `gu_layout()` computes every mip's offset
and size; they match the offsets stored in MHGU's own textures.

Replacing a texture keeps the original header word `tier0`, faces, format and depth, and rewrites the size,
mip count, offsets and data. The mip count stays the original's when the size is unchanged; a new size gets
a full chain when the original had one.

## Encoding

- BC1 colour: principal-axis endpoints, then two least-squares refinement passes (kept only when they lower
  the block error). Blocks with transparent pixels (alpha < 128) use BC1's 3-colour mode.
- BC3 alpha / BC4 / BC5 channels: min / max endpoints, 8-value mode.
- Mips: 2×2 box filter; normal maps are renormalised per mip.
- BC5 export: blue is rebuilt from R and G (z = √(1 − x² − y²)) so the PNG looks like a normal map; only R
  and G are encoded back.
- Alpha: when the new image has no alpha channel and the original texture has a non-opaque alpha, the
  original alpha (resized if needed) is kept.
