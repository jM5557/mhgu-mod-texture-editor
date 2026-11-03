#!/usr/bin/env python3
"""mhgu_tex_tool.py : MHGU Mod Texture Editor. View, export, replace and repack the textures inside
MHGU (Switch) .arc files, for a whole mod (folder or release zip) at a time.

Made for the "MH4U Port" mod (Stygian Zinogre = nativeNX/arc/enemy/em057_01.arc), works on any MHGU arc.

Double-click (or `python mhgu_tex_tool.py [MOD.zip | MOD_FOLDER]`) opens the GUI: open a mod (a release zip such
as stygian_zinogre-x.y.z.zip, or a mod folder), browse every arc and texture, drop images on the preview to stage
replacements (Undo under the preview), then export the whole mod or a patch with only the modified arcs, as a
folder or a zip.  Command line:

  mod     MOD.zip|FOLDER [--list] [--export-pngs DIR] [--images DIR] [--out DEST(.zip)] [--patch]
                                               the GUI flow without the GUI

  list    ARC                                  list the textures in an arc
  export  ARC OUTDIR [--mips]                  every texture -> OUTDIR/<internal path>.png (+ textures.json)
  replace ARC PNG_DIR [-o OUT.arc]             encode every PNG in PNG_DIR (matched by name) into the arc
          [--no-keep-alpha] [--dry-run]        and repack it (without -o: writes ARC, keeps ARC.bak)
                                               [--patch-dest DEST [--patch-root ROOT]]
                                               write DEST/<arc path below ROOT> instead (a patch
                                               folder holding only the modified arcs)
  replace-one ARC TEXNAME PNG [-o OUT.arc]     replace a single texture (same --patch-* options)
  export-tree ROOT OUTDIR [--mips] [-v]        every .arc below ROOT (recursive) -> PNGs in
                                               OUTDIR/<arc path below ROOT, minus .arc>/...
  unpack  ARC OUTDIR                           raw entries -> OUTDIR/<path>.<typehash> + _order.txt
  pack    INDIR OUT.arc                        rebuild an arc from an unpack folder
  topng   FILE.tex OUT.png [mip]               decode one loose TEX file
  totex   IN.png OUT.tex --like ORIG.tex       encode a PNG with ORIG's format / mip count
          | --fmt bc1|bc3|bc4|bc5|rgba8
  scan    MOD_FOLDER                           list textures of every .arc below a folder

Formats: BC1 (0x13/0x14), BC3 (0x17), BC4 (0x19), BC5 (0x1F, normal maps), RGBA8 (0x07).
Container / swizzle logic follows tools/arcpack.py and tools/tex.py of
https://github.com/Scrimas/MH4U-port-to-MHGU (GPL-3.0-or-later); this tool is GPL-3.0-or-later too.
Requires Python 3.9+, numpy, Pillow (+ tkinterdnd2 for drag and drop).
"""
import argparse, json, os, shutil, struct, subprocess, sys, tempfile, zipfile, zlib

__version__ = '1.0.0'

import numpy as np

TEX_TYPE = 0x241F5DEB
ARC_GU_VERSION, ARC_GU_FLAGS, ARC_ALIGN = 0x11, 2, 0x8000

# =============================================================== ARC

def arc_read(data):
    """-> (version, [(path, type_hash, bytes)])"""
    magic, ver, n = struct.unpack_from('<4sHH', data, 0)
    if magic != b'ARC\0':
        raise ValueError('not an ARC file (magic %r)' % magic)
    if len(data) < 0xC + 0x50 * n:
        raise ValueError('truncated or damaged arc (%d entries declared, file is %d bytes)' % (n, len(data)))
    out = []
    for i in range(n):
        name, th, cs, ds, off = struct.unpack_from('<64s4I', data, 0xC + 0x50 * i)
        raw = data[off:off + cs]
        size = ds & 0x1FFFFFFF
        d = zlib.decompress(raw) if (cs != size or raw[:1] == b'\x78') else raw
        if len(d) != size:
            raise ValueError('entry %s: size %d, header says %d' % (name, len(d), size))
        out.append((name.split(b'\0')[0].decode('ascii'), th, d))
    return ver, out


def arc_build(entries, level=9):
    """[(path, type_hash, bytes)] -> MHGU arc (v0x11, all zlib, data aligned to 0x8000)."""
    n = len(entries)
    hdr = 0xC + 0x50 * n
    start = (hdr + ARC_ALIGN - 1) // ARC_ALIGN * ARC_ALIGN
    table, blobs, off = bytearray(), bytearray(), start
    for path, th, d in entries:
        name = path.encode('ascii')
        if len(name) >= 64:
            raise ValueError('path too long for ARC (63 max): ' + path)
        z = zlib.compress(d, level)
        table += struct.pack('<64s4I', name, th, len(z), len(d) | (ARC_GU_FLAGS << 29), off)
        blobs += z
        off += len(z)
    head = struct.pack('<4sHHI', b'ARC\0', ARC_GU_VERSION, n, 0) + table
    return bytes(head) + b'\0' * (start - len(head)) + bytes(blobs)


def load_arc(path):
    with open(path, 'rb') as f:
        return arc_read(f.read())


def long_path(p):
    """Windows refuses paths over 260 characters unless they carry the \\\\?\\ prefix (deep mod folders hit it)."""
    p = os.path.abspath(p)
    if os.name == 'nt' and len(p) > 240 and not p.startswith('\\\\?\\'):
        return '\\\\?\\' + p
    return p


def save_arc(path, entries, backup=True):
    data = arc_build(entries)
    ver, back = arc_read(data)        # verify before touching the disk
    if [(p, t, d) for p, t, d in back] != list(entries):
        raise RuntimeError('repacked arc does not read back identically; nothing written')
    path = long_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if backup and os.path.exists(path) and not os.path.exists(path + '.bak'):
        shutil.copy2(path, path + '.bak')
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return len(data)

# =============================================================== TEX header

FMT_NAME = {0x13: 'bc1', 0x14: 'bc1', 0x17: 'bc3', 0x19: 'bc4', 0x1F: 'bc5', 0x07: 'rgba8'}
NAME_FMT = {'bc1': 0x13, 'bc3': 0x17, 'bc4': 0x19, 'bc5': 0x1F, 'rgba8': 0x07}
BLOCK_BYTES = {'bc1': 8, 'bc4': 8, 'bc3': 16, 'bc5': 16}
FMT_HINT = {'bc1': 'BC1 (colour, 1-bit alpha)', 'bc3': 'BC3 (colour + alpha)', 'bc4': 'BC4 (greyscale)',
            'bc5': 'BC5 (normal map, R+G)', 'rgba8': 'RGBA8 (uncompressed)'}


def tex_parse(t):
    magic, t0, t1, faces, fmt, depth = struct.unpack_from('<4sIIBBH', t, 0)
    if magic != b'TEX\0':
        raise ValueError('not a TEX file')
    h = dict(version=t0 & 0xFF, unk1=(t0 >> 14) & 0x3FFF, type=t0 >> 28, tier0=t0,
             mips=t1 & 0x3F, width=(t1 >> 6) & 0x1FFF, height=t1 >> 19, faces=faces, fmt=fmt, depth=depth)
    h['fmt_name'] = FMT_NAME.get(fmt)
    h['supported'] = h['version'] == 0xA0 and h['fmt_name'] is not None and h['type'] != 6 and faces == 1
    if h['version'] == 0xA0:
        size = struct.unpack_from('<I', t, 16)[0]
        h['offsets'] = list(struct.unpack_from('<%dI' % h['mips'], t, 20))
        start = 20 + 4 * h['mips']
        h['data'] = t[start:start + size]
    return h


def tex_header(src_h, width, height, mips, offsets, size):
    """MHGU header that keeps every field of the original texture but the size / mip data."""
    t1 = mips | (width << 6) | (height << 19)
    return struct.pack('<4sIIBBH', b'TEX\0', src_h['tier0'], t1, src_h['faces'], src_h['fmt'], src_h['depth']) + \
        struct.pack('<I', size) + struct.pack('<%dI' % mips, *offsets)

# =============================================================== Tegra X1 block linear

def div_up(a, b): return (a + b - 1) // b


def pow2_up(v):
    p = 1
    while p < v: p <<= 1
    return p


def block_height_log2(h_blocks):
    return min(16, pow2_up(div_up(h_blocks, 8))).bit_length() - 1


def mip_block_height(base_log2, h_blocks):
    log2 = base_log2
    while log2 > 0 and h_blocks <= (8 << log2) // 2:
        log2 -= 1
    return 1 << log2


def bl_size(w, h, bpp, bh):
    return div_up(w * bpp, 64) * 64 * div_up(h, 8 * bh) * 8 * bh


def bl_offsets(w, h, bpp, bh):
    x = np.arange(w)[None, :] * bpp
    y = np.arange(h)[:, None]
    gobs_w = div_up(w * bpp, 64)
    gob = (y // (8 * bh)) * gobs_w * bh * 512 + (x // 64) * bh * 512 + ((y % (8 * bh)) // 8) * 512
    return gob + ((x % 64) // 32) * 256 + ((y % 8) // 2) * 64 + ((x % 32) // 16) * 32 + (y % 2) * 16 + (x % 16)


def gu_layout(width, height, mips, fmt):
    """[(w_elems, h_elems, bpp, block_height, offset, size)] per mip."""
    if fmt == 'rgba8':
        bpp, eh = 4, height
    else:
        bpp, eh = BLOCK_BYTES[fmt], div_up(height, 4)
    base = block_height_log2(eh)
    out, off = [], 0
    for m in range(mips):
        if fmt == 'rgba8':
            w, h = max(1, width >> m), max(1, height >> m)
        else:
            w, h = div_up(max(1, width >> m), 4), div_up(max(1, height >> m), 4)
        bh = mip_block_height(base, h)
        size = bl_size(w, h, bpp, bh)
        out.append((w, h, bpp, bh, off, size))
        off += size
    return out


def deswizzle(data, w, h, bpp, bh):
    buf = np.frombuffer(data, np.uint8)
    return buf[bl_offsets(w, h, bpp, bh)[..., None] + np.arange(bpp)]


def swizzle(elems, bpp, bh):
    h, w = elems.shape[:2]
    out = np.zeros(bl_size(w, h, bpp, bh), np.uint8)
    out[bl_offsets(w, h, bpp, bh)[..., None] + np.arange(bpp)] = elems
    return out.tobytes()

# =============================================================== BCn

def _rgb565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.uint16)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _unpack565(v):
    v = v.astype(np.int32)
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1)


def bc1_decode(blk, punchthrough=True):
    c0 = blk[:, 0].astype(np.uint16) | blk[:, 1].astype(np.uint16) << 8
    c1 = blk[:, 2].astype(np.uint16) | blk[:, 3].astype(np.uint16) << 8
    p0, p1 = _unpack565(c0), _unpack565(c1)
    four = (c0 > c1) | (not punchthrough)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)
    alpha = np.stack([np.full(len(blk), 255)] * 3 + [np.where(four, 255, 0)], 1)
    bits = blk[:, 4:8].copy().view('<u4')[:, 0]
    out = np.zeros((len(blk), 4, 4, 4), np.uint8)
    rows = np.arange(len(blk))
    for i in range(16):
        k = ((bits >> (2 * i)) & 3).astype(np.int64)
        out[:, i // 4, i % 4, :3] = pal[rows, k]
        out[:, i // 4, i % 4, 3] = alpha[rows, k]
    return out


def bc4_decode(blk):
    a0, a1 = blk[:, 0].astype(np.int32), blk[:, 1].astype(np.int32)
    gt = a0 > a1
    pal = np.zeros((len(blk), 8), np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    for i in range(1, 7):
        pal[:, i + 1] = np.where(gt, ((7 - i) * a0 + i * a1) // 7, 0)
    for i in range(1, 5):
        pal[:, i + 1] = np.where(gt, pal[:, i + 1], ((5 - i) * a0 + i * a1) // 5)
    pal[:, 6] = np.where(gt, pal[:, 6], 0)
    pal[:, 7] = np.where(gt, pal[:, 7], 255)
    bits = np.zeros(len(blk), np.uint64)
    for k in range(6):
        bits |= blk[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    out = np.zeros((len(blk), 4, 4), np.uint8)
    rows = np.arange(len(blk))
    for i in range(16):
        k = ((bits >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int64)
        out[:, i // 4, i % 4] = pal[rows, k]
    return out


def _bc1_pal4(c0, c1):
    p0, p1 = _unpack565(c0), _unpack565(c1)
    return np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)


def _bc1_fit(p, c0, c1):
    """indices + per-block squared error for 4-colour palette (c0 > c1 forced by caller)."""
    pal = _bc1_pal4(c0, c1)
    d = ((p[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(-1)
    return idx, np.take_along_axis(d, idx[..., None], -1)[..., 0].sum(1)


def _order(c0, c1):
    swap = c0 < c1
    return np.where(swap, c1, c0), np.where(swap, c0, c1), swap


def bc1_encode(px, use_alpha=False, refine=2):
    """px: (n,4,4,4) uint8 -> (n,8). Principal-axis endpoints + least-squares refinement.
    use_alpha: blocks with any alpha < 128 use 3-colour mode with transparent index 3."""
    n = len(px)
    p = px[..., :3].reshape(n, 16, 3).astype(np.float64)
    mean = p.mean(1, keepdims=True)
    cen = p - mean
    cov = np.einsum('nki,nkj->nij', cen, cen)
    axis = np.tile(np.array([0.577, 0.577, 0.577]), (n, 1))
    for _ in range(8):
        axis = np.einsum('nij,nj->ni', cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-9)
    proj = np.einsum('nki,ni->nk', cen, axis)
    hi = mean[:, 0] + axis * proj.max(1, keepdims=True)
    lo = mean[:, 0] + axis * proj.min(1, keepdims=True)
    c0, c1, _ = _order(_rgb565(hi), _rgb565(lo))
    pi = p.astype(np.int32)
    idx, err = _bc1_fit(pi, c0, c1)
    # least squares: pixel ~ a*w + b*(1-w), w = 1, 0, 2/3, 1/3 for index 0..3
    wt = np.array([1.0, 0.0, 2 / 3, 1 / 3])
    for _ in range(refine):
        w = wt[idx]                                     # (n,16)
        A, B, C = (w * w).sum(1), (w * (1 - w)).sum(1), ((1 - w) ** 2).sum(1)
        Xa = (w[..., None] * p).sum(1); Xb = ((1 - w)[..., None] * p).sum(1)
        det = A * C - B * B
        ok = np.abs(det) > 1e-6
        dd = np.where(ok, det, 1)[:, None]
        a = (C[:, None] * Xa - B[:, None] * Xb) / dd
        b = (A[:, None] * Xb - B[:, None] * Xa) / dd
        n0, n1, _ = _order(_rgb565(a), _rgb565(b))
        nidx, nerr = _bc1_fit(pi, n0, n1)
        better = ok & (nerr < err)
        c0, c1 = np.where(better, n0, c0), np.where(better, n1, c1)
        idx = np.where(better[:, None], nidx, idx)
        err = np.where(better, nerr, err)
    idx = idx.astype(np.uint32)
    flat = c0 == c1
    idx[flat] = 0
    if use_alpha:
        tr = px[..., 3].reshape(n, 16) < 128
        blk = tr.any(1)
        if blk.any():
            # 3-colour mode: c0 <= c1, palette p0, p1, (p0+p1)/2, transparent
            t0, t1 = np.minimum(c0[blk], c1[blk]), np.maximum(c0[blk], c1[blk])
            q0, q1 = _unpack565(t0), _unpack565(t1)
            pal = np.stack([q0, q1, (q0 + q1) // 2], 1)
            d = ((pi[blk][:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
            ti = d.argmin(-1).astype(np.uint32)
            ti[tr[blk]] = 3
            c0 = c0.copy(); c1 = c1.copy()
            c0[blk], c1[blk] = t0, t1
            idx[blk] = ti
    bits = np.zeros(n, np.uint32)
    for i in range(16):
        bits |= idx[:, i] << np.uint32(2 * i)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 0xFF, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 0xFF, c1 >> 8
    out[:, 4:8] = bits.view(np.uint8).reshape(n, 4)
    return out


def bc4_encode(ch):
    """ch: (n,4,4) uint8 -> (n,8), 8-value mode with min/max endpoints."""
    n = len(ch)
    v = ch.reshape(n, 16).astype(np.int32)
    a0, a1 = v.max(1), v.min(1)
    pal = np.stack([a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)], 1)
    idx = np.abs(v[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    idx[a0 == a1] = 0
    bits = np.zeros(n, np.uint64)
    for i in range(16):
        bits |= idx[:, i] << np.uint64(3 * i)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for k in range(6):
        out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)
    return out


def to_blocks(img):
    h, w = img.shape[:2]
    H, W = div_up(h, 4) * 4, div_up(w, 4) * 4
    if (H, W) != (h, w):
        img = np.pad(img, ((0, H - h), (0, W - w), (0, 0)), mode='edge')
    return img.reshape(H // 4, 4, W // 4, 4, -1).transpose(0, 2, 1, 3, 4).reshape(-1, 4, 4, img.shape[2])


def from_blocks(b, hb, wb):
    return b.reshape(hb, wb, 4, 4, b.shape[-1]).transpose(0, 2, 1, 3, 4).reshape(hb * 4, wb * 4, b.shape[-1])


def bc_encode(img, fmt, bc1_alpha=False):
    b = to_blocks(img)
    if fmt == 'bc1': return bc1_encode(b, use_alpha=bc1_alpha)
    if fmt == 'bc3': return np.concatenate([bc4_encode(b[..., 3]), bc1_encode(b)], 1)
    if fmt == 'bc4': return bc4_encode(b[..., 0])
    if fmt == 'bc5': return np.concatenate([bc4_encode(b[..., 0]), bc4_encode(b[..., 1])], 1)
    raise ValueError(fmt)


def bc_decode(blk, fmt):
    if fmt == 'bc1': return bc1_decode(blk)
    if fmt == 'bc3':
        c = bc1_decode(blk[:, 8:], punchthrough=False)
        c[..., 3] = bc4_decode(blk[:, :8])
        return c
    if fmt == 'bc4':
        g = bc4_decode(blk)
        return np.stack([g, g, g, np.full_like(g, 255)], -1)
    if fmt == 'bc5':
        r, g = bc4_decode(blk[:, :8]), bc4_decode(blk[:, 8:])
        # reconstruct blue as the normal's Z so the PNG looks like a normal map; it is ignored on re-encode
        x, y = r / 127.5 - 1, g / 127.5 - 1
        z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
        b = np.clip(np.rint((z + 1) * 127.5), 0, 255).astype(np.uint8)
        return np.stack([r, g, b, np.full_like(r, 255)], -1)
    raise ValueError(fmt)

# =============================================================== decode / encode TEX

def tex_decode(t, mip=0):
    """TEX bytes -> (h, w, 4) uint8 RGBA."""
    h = tex_parse(t)
    if not h['supported']:
        raise ValueError('unsupported texture (version %#x, format %#x, type %d)' % (h['version'], h['fmt'], h['type']))
    fmt = h['fmt_name']
    lay = gu_layout(h['width'], h['height'], h['mips'], fmt)
    w, hh, bpp, bh, off, size = lay[mip]
    el = deswizzle(h['data'][off:off + size], w, hh, bpp, bh)
    W, H = max(1, h['width'] >> mip), max(1, h['height'] >> mip)
    if fmt == 'rgba8':
        return np.ascontiguousarray(el[:H, :W])
    return np.ascontiguousarray(from_blocks(bc_decode(el.reshape(-1, bpp), fmt), hh, w)[:H, :W])


def _half(a):
    """box-filter halving (odd sizes drop the last row / column, mip size = floor(n / 2))."""
    h, w = a.shape[:2]
    if h > 1:
        e = h - h % 2
        a = (a[0:e:2] + a[1:e:2]) / 2
    if w > 1:
        e = w - w % 2
        a = (a[:, 0:e:2] + a[:, 1:e:2]) / 2
    return a


def mip_chain(img, mips, normal=False):
    out = [img]
    cur = img.astype(np.float32)
    for _ in range(1, mips):
        cur = _half(cur)
        m = cur
        if normal:      # renormalise the averaged normals (R, G channels)
            x, y = m[..., 0] / 127.5 - 1, m[..., 1] / 127.5 - 1
            z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
            ln = np.maximum(np.sqrt(x * x + y * y + z * z), 1e-6)
            m = m.copy(); m[..., 0] = (x / ln + 1) * 127.5; m[..., 1] = (y / ln + 1) * 127.5
        out.append(np.clip(np.rint(m), 0, 255).astype(np.uint8))
    return out


def full_mips(w, h):
    return max(w, h).bit_length()


def tex_encode(img, like_h, mips=None):
    """RGBA image -> MHGU TEX bytes with the format and header fields of `like_h` (a tex_parse() dict)."""
    fmt = like_h['fmt_name']
    H, W = img.shape[:2]
    if W > 8191 or H > 8191:
        raise ValueError('texture too large (max 8191)')
    if mips is None:
        if (W, H) == (like_h['width'], like_h['height']):
            mips = like_h['mips']
        else:   # keep "full chain" / "single mip" style of the original
            mips = full_mips(W, H) if like_h['mips'] >= full_mips(like_h['width'], like_h['height']) \
                else max(1, min(like_h['mips'], full_mips(W, H)))
    lay = gu_layout(W, H, mips, fmt)
    bc1_alpha = fmt == 'bc1' and bool((img[..., 3] < 128).any())
    data = bytearray()
    for (w, h, bpp, bh, off, size), m in zip(lay, mip_chain(img, mips, normal=fmt == 'bc5')):
        el = m.reshape(h, w, 4) if fmt == 'rgba8' else bc_encode(m, fmt, bc1_alpha).reshape(h, w, bpp)
        data += swizzle(el, bpp, bh)
    return tex_header(like_h, W, H, mips, [l[4] for l in lay], len(data)) + bytes(data)


def generic_header(fmt):
    """a header dict for a brand-new texture (fields as MHGU's enemy textures)."""
    return dict(tier0=0xA0 | (2 << 14) | (2 << 28), faces=1, fmt=NAME_FMT[fmt], depth=1, fmt_name=fmt,
                width=0, height=0, mips=0)

# =============================================================== PNG helpers

def load_png(path):
    from PIL import Image
    im = Image.open(path)
    has_alpha = im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info)
    return np.array(im.convert('RGBA')), has_alpha


def save_png(img, path):
    from PIL import Image
    path = long_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(np.ascontiguousarray(img), 'RGBA').save(path)


def resize(img, w, h):
    from PIL import Image
    return np.array(Image.fromarray(img, 'RGBA').resize((w, h), Image.LANCZOS))


def prepare_image(img, has_alpha, orig_tex, keep_alpha=True, log=print):
    """Apply the rules that keep a replacement sane:
    - BC3/RGBA8/BC1 and the PNG has no alpha channel: take the alpha from the original (cut-outs such as the fur)."""
    h = tex_parse(orig_tex)
    fmt = h['fmt_name']
    if keep_alpha and not has_alpha and fmt in ('bc3', 'rgba8', 'bc1'):
        a = tex_decode(orig_tex)[..., 3]
        if not (a == 255).all():
            if a.shape != img.shape[:2]:
                from PIL import Image
                a = np.array(Image.fromarray(a, 'L').resize((img.shape[1], img.shape[0]), Image.LANCZOS))
            img = img.copy(); img[..., 3] = a
            log('    PNG has no alpha: kept the original texture\'s alpha')
    if (img.shape[1], img.shape[0]) != (h['width'], h['height']):
        ow, oh = h['width'], h['height']
        log('    size %dx%d differs from the original %dx%d (allowed; UVs are size-independent)'
            % (img.shape[1], img.shape[0], ow, oh))
        if abs(img.shape[1] / img.shape[0] - ow / oh) > 1e-3:
            log('    WARNING: aspect ratio differs from the original; the texture will look stretched in game')
    return img

# =============================================================== arc-level operations

def texture_entries(entries):
    """-> [(index, path, header)] for every TEX entry."""
    out = []
    for i, (p, t, d) in enumerate(entries):
        if t == TEX_TYPE:
            try:
                out.append((i, p, tex_parse(d)))
            except Exception as e:
                out.append((i, p, dict(error=str(e), supported=False)))
    return out


def describe(h):
    if 'error' in h: return 'unreadable: ' + h['error']
    fmt = h['fmt_name'] or 'fmt %#x' % h['fmt']
    s = '%-5s %4dx%-4d mips %2d' % (fmt, h['width'], h['height'], h['mips'])
    if not h['supported']: s += '  (unsupported: export/replace not available)'
    return s


def png_rel(path):
    return path.replace('\\', '/') + '.png'


def cmd_list(a):
    ver, ents = load_arc(a.arc)
    tx = texture_entries(ents)
    print('%s: ARC v%#x, %d entries, %d textures' % (a.arc, ver, len(ents), len(tx)))
    for i, p, h in tx:
        print('  %-60s %s' % (p, describe(h)))


def export_all(arc_path, outdir, all_mips=False, log=print, ents=None):
    if ents is None:
        ver, ents = load_arc(arc_path)
    info = []
    for i, p, h in texture_entries(ents):
        if not h.get('supported'):
            log('  skip %s (%s)' % (p, describe(h))); continue
        dst = os.path.join(outdir, png_rel(p))
        save_png(tex_decode(ents[i][2]), dst)
        if all_mips:
            for m in range(1, h['mips']):
                save_png(tex_decode(ents[i][2], m), dst[:-4] + '.mip%d.png' % m)
        info.append(dict(path=p, png=png_rel(p), format=h['fmt_name'], fmt_byte=h['fmt'],
                         width=h['width'], height=h['height'], mips=h['mips']))
        log('  %s -> %s' % (p, png_rel(p)))
    os.makedirs(long_path(outdir), exist_ok=True)
    with open(long_path(os.path.join(outdir, 'textures.json')), 'w') as f:
        json.dump(dict(arc=os.path.abspath(arc_path), textures=info), f, indent=1)
    log('%d textures exported to %s' % (len(info), outdir))
    return info


def cmd_export(a):
    export_all(a.arc, a.outdir, a.mips)


def find_arcs(root):
    out = []
    for dp, dns, fs in os.walk(root):
        dns.sort()
        out += [os.path.join(dp, f) for f in sorted(fs) if f.lower().endswith('.arc')]
    return out


def export_tree(root, outdir, all_mips=False, log=print, verbose=False):
    """Every .arc below root -> outdir/<arc path relative to root, minus .arc>/<internal path>.png.
    Arcs without textures are skipped; one bad arc does not stop the run. Writes outdir/_arcs.json."""
    root, outdir = os.path.abspath(root), os.path.abspath(outdir)
    if os.path.commonpath([root, outdir]) == root and outdir != root:
        log('note: the output folder is inside the source folder; it is skipped while scanning')
    arcs = [a for a in find_arcs(root) if not os.path.abspath(a).startswith(outdir + os.sep)]
    log('%d .arc files below %s' % (len(arcs), root))
    index, total, failed = [], 0, []
    for n, arc in enumerate(arcs, 1):
        rel = os.path.relpath(arc, root)
        try:
            ver, ents = load_arc(arc)
            usable = [x for x in texture_entries(ents) if x[2].get('supported')]
            if not usable:
                if verbose: log('[%d/%d] %s: no textures' % (n, len(arcs), rel))
                continue
            dst = os.path.join(outdir, os.path.splitext(rel)[0])
            info = export_all(arc, dst, all_mips, log=log if verbose else (lambda m: None), ents=ents)
            index.append(dict(arc=rel.replace(os.sep, '/'), folder=os.path.splitext(rel)[0].replace(os.sep, '/'),
                              textures=len(info)))
            total += len(info)
            log('[%d/%d] %s: %d textures' % (n, len(arcs), rel, len(info)))
        except Exception as e:
            failed.append(rel)
            log('[%d/%d] %s: FAILED (%s)' % (n, len(arcs), rel, e))
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, '_arcs.json'), 'w') as f:
        json.dump(dict(source_root=root, arcs=index, failed=failed), f, indent=1)
    log('done: %d textures from %d arcs -> %s%s' % (total, len(index), outdir,
                                                     ', %d arcs failed' % len(failed) if failed else ''))
    return index


def cmd_export_tree(a):
    export_tree(a.root, a.outdir, a.mips, verbose=a.verbose)

# ---------------------------------------------------------------- patches: only the modified arcs, in their folders

SETTINGS = os.path.join(os.path.expanduser('~'), '.mhgu_tex_tool.json')


def load_settings():
    try:
        with open(SETTINGS) as f: return json.load(f)
    except Exception:
        return {}


def save_settings(**kw):
    s = load_settings(); s.update(kw)
    try:
        with open(SETTINGS, 'w') as f: json.dump(s, f, indent=1)
    except OSError:
        pass


def is_inside(path, root):
    if not path or not root:
        return False
    try:
        path, root = os.path.abspath(path), os.path.abspath(root)
        return os.path.commonpath([path, root]) == root and path != root
    except ValueError:          # other drive
        return False


def guess_patch_root(arc_path):
    """the folder above the mod folder, so a patch holds '<mod>/romfs/...': the parent of the folder that contains
    'romfs' (e.g. ...\\contents\\0100770008dd8000 for ...\\MH4U Port\\romfs\\nativeNX\\arc\\enemy\\x.arc)."""
    p = os.path.dirname(os.path.abspath(arc_path))
    cur = p
    while True:
        if os.path.basename(cur).lower() == 'romfs':
            return os.path.dirname(os.path.dirname(cur))
        nxt = os.path.dirname(cur)
        if nxt == cur: return p
        cur = nxt


def patch_target(arc_path, root, dest):
    """dest + (arc path relative to root)."""
    if not root or not dest:
        raise ValueError('choose both folders')
    if not is_inside(arc_path, root):
        raise ValueError('the arc is not inside the chosen root folder')
    return os.path.join(os.path.abspath(dest), os.path.relpath(os.path.abspath(arc_path), os.path.abspath(root)))


def match_pngs(png_dir, tex_list):
    """PNG files below png_dir -> {entry index: png path}. Match on the relative path first
    (enemy/em057/.../em057_01_01_BM.png as written by export), then on the file name alone."""
    by_rel = {png_rel(p).lower(): i for i, p, h in tex_list}
    by_name = {}
    for i, p, h in tex_list:
        by_name.setdefault(p.split('\\')[-1].lower(), []).append(i)
    found, problems = {}, []
    for dp, _, fs in os.walk(png_dir):
        for f in fs:
            if not f.lower().endswith('.png') or '.mip' in f.lower():
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, png_dir).replace(os.sep, '/').lower()
            if rel in by_rel:
                found[by_rel[rel]] = full; continue
            cand = by_name.get(f[:-4].lower(), [])
            if len(cand) == 1:
                if cand[0] in found and found[cand[0]] != full:
                    problems.append('two PNGs for the same texture: %s and %s' % (found[cand[0]], full))
                found.setdefault(cand[0], full)
            elif len(cand) > 1:
                problems.append('%s matches several textures (put it in the exported folder layout): %s'
                                % (f, ', '.join(tex_list_path(tex_list, c) for c in cand)))
            else:
                problems.append('no texture named %s in the arc (ignored)' % f[:-4])
    return found, problems


def tex_list_path(tex_list, idx):
    return next(p for i, p, h in tex_list if i == idx)


def replace_textures(entries, mapping, keep_alpha=True, log=print):
    """mapping {entry index: png path} -> new entries list."""
    ents = list(entries)
    for i, png in sorted(mapping.items()):
        p, t, d = ents[i]
        h = tex_parse(d)
        if not h['supported']:
            log('  skip %s: %s' % (p, describe(h))); continue
        log('  %s  <-  %s' % (p, os.path.basename(png)))
        img, has_a = load_png(png)
        img = prepare_image(img, has_a, d, keep_alpha, log)
        new = tex_encode(img, h)
        tex_decode(new)        # sanity: it parses and decodes
        ents[i] = (p, t, new)
        nh = tex_parse(new)
        log('    -> %s' % describe(nh))
    return ents


def cmd_replace(a):
    ver, ents = load_arc(a.arc)
    tx = texture_entries(ents)
    mapping, problems = match_pngs(a.png_dir, tx)
    for pr in problems: print('  note:', pr)
    if not mapping:
        sys.exit('no PNG in %s matches a texture of %s' % (a.png_dir, a.arc))
    new = replace_textures(ents, mapping, not a.no_keep_alpha)
    if a.dry_run:
        print('dry run: %d textures would be replaced' % len(mapping)); return
    out = output_path(a)
    same = os.path.abspath(out) == os.path.abspath(a.arc)
    size = save_arc(out, new, backup=same)
    print('%d textures replaced -> %s (%d bytes)%s' % (len(mapping), out, size,
                                                       '  [backup: %s.bak]' % a.arc if same else ''))


def output_path(a):
    """-o OUT, or --patch-dest DEST (+ --patch-root ROOT, default: guessed from the arc's path), or the arc itself."""
    if a.patch_dest:
        if a.output: sys.exit('use either -o or --patch-dest')
        root = a.patch_root or guess_patch_root(a.arc)
        try:
            return patch_target(a.arc, root, a.patch_dest)
        except ValueError as e:
            sys.exit('%s (root %s)' % (e, root))
    return a.output or a.arc


def find_texture(tx, name):
    n = name.replace('/', '\\').lower()
    hits = [i for i, p, h in tx if p.lower() == n or p.split('\\')[-1].lower() == n]
    if len(hits) != 1:
        sys.exit('%s: %s' % (name, 'not found' if not hits else 'ambiguous, give the full internal path'))
    return hits[0]


def cmd_replace_one(a):
    ver, ents = load_arc(a.arc)
    i = find_texture(texture_entries(ents), a.texname)
    new = replace_textures(ents, {i: a.png}, not a.no_keep_alpha)
    out = output_path(a)
    save_arc(out, new, backup=os.path.abspath(out) == os.path.abspath(a.arc))
    print('written', out)


def cmd_unpack(a):
    ver, ents = load_arc(a.arc)
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, '_order.txt'), 'w') as order:
        for p, t, d in ents:
            rel = p.replace('\\', '/') + '.%08x' % t
            dst = os.path.join(a.outdir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f: f.write(d)
            order.write(rel + '\n')
    print('ARC v%#x, %d entries -> %s' % (ver, len(ents), a.outdir))


def cmd_pack(a):
    op = os.path.join(a.indir, '_order.txt')
    if os.path.exists(op):
        files = [l.strip() for l in open(op) if l.strip()]
    else:
        files = sorted(os.path.relpath(os.path.join(dp, f), a.indir).replace(os.sep, '/')
                       for dp, _, fs in os.walk(a.indir) for f in fs if f != '_order.txt')
    ents = []
    for rel in files:
        base, th = rel.rsplit('.', 1)
        with open(os.path.join(a.indir, rel), 'rb') as f:
            ents.append((base.replace('/', '\\'), int(th, 16), f.read()))
    save_arc(a.output, ents, backup=True)
    print('%d entries -> %s' % (len(ents), a.output))


def cmd_topng(a):
    with open(a.tex, 'rb') as f: t = f.read()
    save_png(tex_decode(t, a.mip), a.png)
    print(describe(tex_parse(t)), '->', a.png)


def cmd_totex(a):
    img, has_a = load_png(a.png)
    if a.like:
        with open(a.like, 'rb') as f: orig = f.read()
        h = tex_parse(orig)
        img = prepare_image(img, has_a, orig, not a.no_keep_alpha)
    else:
        h = generic_header(a.fmt or 'bc3')
        h.update(width=img.shape[1], height=img.shape[0], mips=full_mips(img.shape[1], img.shape[0]))
    t = tex_encode(img, h, a.mips)
    with open(a.tex, 'wb') as f: f.write(t)
    print(describe(tex_parse(t)), '->', a.tex)


def cmd_scan(a):
    for dp, _, fs in os.walk(a.folder):
        for f in sorted(fs):
            if f.lower().endswith('.arc'):
                p = os.path.join(dp, f)
                try:
                    ver, ents = load_arc(p)
                except Exception as e:
                    print('%s: %s' % (p, e)); continue
                tx = texture_entries(ents)
                if tx:
                    print('%s  (%d textures)' % (os.path.relpath(p, a.folder), len(tx)))
                    for i, pp, h in tx:
                        print('    %-58s %s' % (pp, describe(h)))

# =============================================================== mod session (logic behind the GUI)

IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tif', '.tiff', '.webp', '.dds')


class ArcFile:
    """One .arc of a mod: its entry table is read up front, entry data only on demand."""

    def __init__(self, fs_path, logical):
        self.fs_path, self.logical = fs_path, logical
        with open(long_path(fs_path), 'rb') as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b'ARC\0':
                raise ValueError('not an ARC file')
            self.version, n = struct.unpack_from('<HH', head, 4)
            table = f.read(0x50 * n)
        if len(table) < 0x50 * n:
            raise ValueError('truncated or damaged arc')
        self.entries = []
        for i in range(n):
            name, th, cs, ds, off = struct.unpack_from('<64s4I', table, 0x50 * i)
            self.entries.append((name.split(b'\0')[0].decode('ascii', 'replace'), th, cs, ds & 0x1FFFFFFF, off))
        self.tex = [i for i, e in enumerate(self.entries) if e[1] == TEX_TYPE]
        self._headers = {}

    def raw(self, i):
        name, th, cs, ds, off = self.entries[i]
        with open(long_path(self.fs_path), 'rb') as f:
            f.seek(off)
            return f.read(cs)

    def read(self, i):
        name, th, cs, ds, off = self.entries[i]
        raw = self.raw(i)
        return zlib.decompress(raw) if (cs != ds or raw[:1] == b'\x78') else raw

    def header(self, i):
        """TEX header without decoding the texture (only the first bytes are inflated)."""
        if i not in self._headers:
            name, th, cs, ds, off = self.entries[i]
            try:
                raw = self.raw(i)
                t = zlib.decompressobj().decompress(raw, 0x200) if (cs != ds or raw[:1] == b'\x78') else raw[:0x200]
                self._headers[i] = tex_parse(t)
            except Exception as e:
                self._headers[i] = dict(error=str(e), supported=False)
        return self._headers[i]

    @property
    def stem(self):
        return self.logical[:-4] if self.logical.lower().endswith('.arc') else self.logical


class ModSession:
    """A mod opened from a folder or a zip. Paths are 'logical': for a zip, its member paths; for a folder, the
    folder's own name + the path below it. Every export writes files at their logical paths."""

    def __init__(self, src, log=print):
        self.src = os.path.abspath(src)
        self.tmp = None
        self.log = log
        if os.path.isdir(self.src):
            self.kind, self.root = 'folder', self.src
            self.prefix = os.path.basename(self.src.rstrip('\\/')) or 'mod'
            self.members = None
        elif zipfile.is_zipfile(self.src):
            self.kind, self.prefix = 'zip', ''
            self.tmp = self.root = tempfile.mkdtemp(prefix='mhgu_mod_')
            with zipfile.ZipFile(self.src) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                log('unpacking %s (%d files)...' % (os.path.basename(self.src), len(infos)))
                z.extractall(self.root)        # extractall drops absolute / '..' parts of member names
            self.members = [i.filename for i in infos]
        else:
            raise ValueError('choose a mod folder or a .zip file')
        self.arcs, self.bad = [], []
        for p in find_arcs(self.root):
            try:
                self.arcs.append(ArcFile(p, self.logical(p)))
            except Exception as e:
                self.bad.append((self.logical(p), str(e)))
        self.staged = {}       # (arc index, entry index) -> {'src', 'tex', 'notes'}

    # ---------------------------------------------------------------- paths
    def logical(self, fs_path):
        rel = os.path.relpath(fs_path, self.root).replace(os.sep, '/')
        return self.prefix + '/' + rel if self.prefix else rel

    def files(self):
        """[(logical path, file on disk)] of the whole mod, in the source's order."""
        if self.kind == 'zip':
            out = []
            for m in self.members:
                fs = os.path.join(self.root, *[p for p in m.replace('\\', '/').split('/') if p not in ('', '.', '..')])
                if os.path.isfile(fs):
                    out.append((self.logical(fs), fs))
            return out
        out = []
        for dp, dns, fs in os.walk(self.root):
            dns.sort()
            out += [(self.logical(os.path.join(dp, f)), os.path.join(dp, f)) for f in sorted(fs)]
        return out

    @property
    def tex_arcs(self):
        return [k for k, a in enumerate(self.arcs) if a.tex]

    # ---------------------------------------------------------------- staging
    def stage(self, k, i, image_path, keep_alpha=True):
        """encode image_path for texture (k, i); returns the notes (alpha kept, size / aspect warnings)."""
        orig = self.arcs[k].read(i)
        h = tex_parse(orig)
        if not h['supported']:
            raise ValueError('this texture type cannot be replaced (%s)' % describe(h))
        img, has_a = load_png(image_path)
        notes = []
        img = prepare_image(img, has_a, orig, keep_alpha, log=lambda m: notes.append(m.strip()))
        new = tex_encode(img, h)
        tex_decode(new)
        self.staged[(k, i)] = dict(src=image_path, tex=new, notes=notes)
        return notes

    def unstage(self, k, i):
        return self.staged.pop((k, i), None) is not None

    def staged_in(self, k):
        return sorted(i for kk, i in self.staged if kk == k)

    def modified_arcs(self):
        return sorted({k for k, i in self.staged})

    def build_arc(self, k):
        ver, ents = load_arc(self.arcs[k].fs_path)
        for i in self.staged_in(k):
            p, t, d = ents[i]
            ents[i] = (p, t, self.staged[(k, i)]['tex'])
        data = arc_build(ents)
        if arc_read(data)[1] != ents:
            raise RuntimeError('%s: repacked arc does not read back identically' % self.arcs[k].logical)
        return data

    def match_images(self, folder):
        """images below folder -> ({(k, i): path}, [notes]). Matches, in order: the export layout
        (<arc path minus .arc>/<texture path>.png, from any starting folder), then a texture name that is unique
        in the whole mod."""
        full, names = {}, {}
        for k in self.tex_arcs:
            a = self.arcs[k]
            for i in a.tex:
                p = a.entries[i][0]
                full[(a.stem + '/' + p.replace('\\', '/')).lower()] = (k, i)
                names.setdefault(p.split('\\')[-1].lower(), []).append((k, i))
        found, notes = {}, []
        for dp, _, fs in os.walk(folder):
            for f in sorted(fs):
                stem, ext = os.path.splitext(f)
                if ext.lower() not in IMAGE_EXT or '.mip' in stem.lower():
                    continue
                path = os.path.join(dp, f)
                rel = os.path.splitext(os.path.relpath(path, folder))[0].replace(os.sep, '/').lower()
                hit = full.get(rel) or next((v for key, v in full.items() if key.endswith('/' + rel)), None)
                if hit is None:
                    cand = names.get(stem.lower(), [])
                    if len(cand) == 1:
                        hit = cand[0]
                    elif cand:
                        notes.append('%s: %d textures have this name; use the exported folder layout' % (f, len(cand)))
                        continue
                    else:
                        notes.append('%s: no texture with this name' % f)
                        continue
                if hit in found:
                    notes.append('%s and %s both match %s; kept the first'
                                  % (os.path.basename(found[hit]), f, self.arcs[hit[0]].entries[hit[1]][0]))
                    continue
                found[hit] = path
        return found, notes

    # ---------------------------------------------------------------- export
    def export_pngs(self, outdir, log=print):
        n = 0
        for k in self.tex_arcs:
            a = self.arcs[k]
            info = export_all(a.fs_path, os.path.join(outdir, *a.stem.split('/')), log=lambda m: None)
            n += len(info)
            log('  %s: %d textures' % (a.logical, len(info)))
        log('%d original textures exported to %s' % (n, outdir))
        return n

    def check_dest(self, dest):
        d = os.path.abspath(dest)
        if self.kind == 'folder' and (d == self.root or is_inside(d, self.root)):
            raise ValueError('the destination is inside the mod being edited; choose another place')
        if self.kind == 'folder' and is_inside(self.root, d) and \
                os.path.abspath(os.path.join(d, self.prefix)) == self.root:
            raise ValueError('this would write over the original mod folder; choose another place')
        if self.kind == 'zip' and os.path.abspath(dest) == self.src:
            raise ValueError('choose another file than the zip being edited')

    def export(self, dest, as_zip, patch, log=print):
        """write the whole mod (patch=False) or only the modified arcs (patch=True) to a folder or a zip."""
        mods = {self.arcs[k].logical: k for k in self.modified_arcs()}
        if patch and not mods:
            raise ValueError('nothing is staged')
        self.check_dest(dest)
        files = [(lg, fs) for lg, fs in self.files() if not patch or lg in mods]
        n_mod = 0
        if as_zip:
            os.makedirs(os.path.dirname(os.path.abspath(dest)) or '.', exist_ok=True)
            tmp = dest + '.part'
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for lg, fs in files:
                    if lg in mods:
                        log('  repacking %s' % lg)
                        z.writestr(zipfile.ZipInfo(lg, date_time=_now_zip()), self.build_arc(mods[lg]),
                                   compress_type=zipfile.ZIP_DEFLATED)
                        n_mod += 1
                    else:
                        z.write(long_path(fs), lg)
            os.replace(tmp, dest)
        else:
            for lg, fs in files:
                out = long_path(os.path.join(dest, *lg.split('/')))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                if lg in mods:
                    log('  repacking %s' % lg)
                    data = self.build_arc(mods[lg])
                    with open(out + '.part', 'wb') as f:
                        f.write(data)
                    os.replace(out + '.part', out)
                    n_mod += 1
                else:
                    shutil.copy2(long_path(fs), out)
        log('%s written: %s (%d files, %d repacked arcs, %d textures replaced)'
            % ('patch' if patch else 'mod', dest, len(files), n_mod,
               sum(len(self.staged_in(k)) for k in mods.values())))
        return len(files), n_mod

    def close(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None


def _now_zip():
    import time
    return time.localtime()[:6]


def cmd_mod(a):
    """CLI version of the GUI flow: open a mod, stage a folder of images, export a mod or a patch."""
    s = ModSession(a.mod)
    try:
        print('%s: %d arcs, %d with textures, %d textures%s' % (
            a.mod, len(s.arcs), len(s.tex_arcs), sum(len(s.arcs[k].tex) for k in s.tex_arcs),
            ', %d unreadable' % len(s.bad) if s.bad else ''))
        if a.list:
            for k in s.tex_arcs:
                print('  %s (%d textures)' % (s.arcs[k].logical, len(s.arcs[k].tex)))
        if a.export_pngs:
            s.export_pngs(a.export_pngs)
        if a.images:
            found, notes = s.match_images(a.images)
            for n in notes: print('  note:', n)
            for (k, i), p in sorted(found.items()):
                print('  %s : %s  <-  %s' % (s.arcs[k].logical, s.arcs[k].entries[i][0], os.path.basename(p)))
                for n in s.stage(k, i, p, not a.no_keep_alpha): print('     ', n)
        if a.out:
            if not s.staged and a.patch:
                sys.exit('nothing to patch: no image matched a texture')
            s.export(a.out, a.out.lower().endswith('.zip'), a.patch)
    finally:
        s.close()

# =============================================================== GUI

def gui(initial=None):
    import queue, threading, tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import Image, ImageTk
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root, dnd = TkinterDnD.Tk(className='MhguTextureTool'), True
    except Exception:
        root, dnd = tk.Tk(className='MhguTextureTool'), False
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
    if os.path.isfile(icon):
        try: root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon)))
        except Exception: pass

    root.title('MHGU Mod Texture Editor %s' % __version__)
    root.geometry('1280x800')
    scale = os.environ.get('MHGU_SCALE') or os.environ.get('GDK_SCALE') or os.environ.get('QT_SCALE_FACTOR')
    if scale and sys.platform.startswith('linux'):      # HiDPI: Tk does not follow the desktop's scale factor
        try:
            root.tk.call('tk', 'scaling', 96 / 72 * float(scale))
            root.geometry('%dx%d' % (1280 * float(scale), 800 * float(scale)))
        except (ValueError, tk.TclError):
            pass

    # ------------------------------------------------------------ file dialogs
    # Linux: the desktop's own picker (kdialog on KDE, zenity elsewhere) when installed, as Tk's built-in one is
    # basic; MHGU_NATIVE_DIALOGS=0 turns it off. Every dialog starts in the last folder used.
    native = None
    if sys.platform.startswith('linux') and os.environ.get('MHGU_NATIVE_DIALOGS', '1') != '0':
        kde = 'kde' in os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        native = next((c for c in (('kdialog', 'zenity') if kde else ('zenity', 'kdialog')) if shutil.which(c)), None)
    ryu = os.path.expanduser('~/.config/Ryujinx/mods/contents/0100770008dd8000')
    dlg = dict(open=False)

    def start_dir(kind=None):
        d = load_settings().get('last_dir')
        if d and os.path.isdir(d): return d
        if kind == 'mod' and os.path.isdir(ryu): return ryu
        return os.path.expanduser('~')

    def remember(p):
        if p: save_settings(last_dir=p if os.path.isdir(p) else os.path.dirname(p))
        return p

    def run_native(args):
        out = {}

        def t():
            try:
                r = subprocess.run(args, capture_output=True, text=True)
                out['p'] = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ''
            except Exception as e:
                out['e'] = e
        th = threading.Thread(target=t, daemon=True); th.start()
        done = tk.BooleanVar(value=False)

        def poll():
            if th.is_alive(): root.after(60, poll)
            else: done.set(True)
        poll(); root.wait_variable(done)          # the window keeps redrawing while the picker is open
        if 'e' in out: raise out['e']
        return out['p']

    def pick(mode, title, patterns=(), initialfile='', defext='', kind=None, parent=None):
        """mode: 'open' | 'dir' | 'save'; patterns: [(label, '*.a *.b')]"""
        if dlg['open']: return ''
        dlg['open'] = True
        try:
            d0 = start_dir(kind)
            p = ''
            if native:
                try:
                    if native == 'kdialog':
                        flt = '\n'.join('%s (%s)' % (l, g) for l, g in patterns)
                        args = {'open': ['--getopenfilename', d0] + ([flt] if flt else []),
                                'dir': ['--getexistingdirectory', d0],
                                'save': ['--getsavefilename', os.path.join(d0, initialfile)] + ([flt] if flt else [])}[mode]
                        p = run_native(['kdialog', '--title', title] + args)
                    else:
                        args = ['zenity', '--file-selection', '--title=' + title]
                        args += ['--directory'] if mode == 'dir' else ['--save'] if mode == 'save' else []
                        args.append('--filename=' + (os.path.join(d0, initialfile) if mode == 'save'
                                                     else d0.rstrip('/') + '/'))
                        args += ['--file-filter=%s | %s' % (l, g) for l, g in patterns]
                        p = run_native(args)
                except Exception as e:
                    log('native file dialog failed (%s); using the built-in one' % e)
                    p = None
            if p is None or not native:
                kw = dict(title=title, initialdir=d0, parent=parent or root)
                ft = [(l, g) for l, g in patterns]
                if mode == 'dir':
                    p = filedialog.askdirectory(**kw)
                elif mode == 'open':
                    p = filedialog.askopenfilename(filetypes=ft, **kw)
                else:
                    p = filedialog.asksaveasfilename(filetypes=ft, initialfile=initialfile, defaultextension=defext, **kw)
            p = p or ''
            if p and mode == 'save' and defext and not p.lower().endswith(defext): p += defext
            return remember(p)
        finally:
            dlg['open'] = False
    root.minsize(900, 600)
    st = dict(s=None, sel=None, photo=None, view='new', cache={}, preview_token=0)
    jobs = queue.Queue()
    pending = [0]

    # ------------------------------------------------------------ layout
    bar = ttk.Frame(root, padding=(6, 6, 6, 2)); bar.pack(fill='x')
    bar2 = ttk.Frame(root, padding=(6, 0, 6, 4)); bar2.pack(fill='x')
    status = tk.StringVar(value='Open a mod (folder or .zip)' + (', or drop it on this window' if dnd else ''))
    body = ttk.PanedWindow(root, orient='horizontal'); body.pack(fill='both', expand=True, padx=6)
    left = ttk.Frame(body); right = ttk.Frame(body, padding=(8, 0, 0, 0))
    body.add(left, weight=3); body.add(right, weight=2)
    tree = ttk.Treeview(left, columns=('fmt', 'size', 'mips', 'state'), selectmode='browse')
    for c, w, t in (('#0', 420, 'Arc / texture'), ('fmt', 60, 'Format'), ('size', 90, 'Size'),
                    ('mips', 45, 'Mips'), ('state', 150, 'Status')):
        tree.heading(c, text=t, anchor='w'); tree.column(c, width=w, stretch=(c == '#0'))
    tree.tag_configure('staged', foreground='#b35900', font=('TkDefaultFont', 9, 'bold'))
    tree.tag_configure('dim', foreground='#888')
    sb = ttk.Scrollbar(left, command=tree.yview); tree.configure(yscrollcommand=sb.set)
    tree.pack(side='left', fill='both', expand=True); sb.pack(side='left', fill='y')

    title = ttk.Label(right, text='', font=('TkDefaultFont', 10, 'bold'), wraplength=480, justify='left')
    title.pack(fill='x')
    drop = tk.Label(right, text='', bg='#d9d9d9', relief='groove', bd=2, fg='#555',
                    font=('TkDefaultFont', 11), wraplength=380)
    drop.pack(fill='both', expand=True, pady=4)
    info = ttk.Label(right, text='', wraplength=480, justify='left'); info.pack(fill='x')
    stag = tk.Label(right, text='', fg='#b35900', wraplength=480, justify='left', anchor='w'); stag.pack(fill='x')
    btns = ttk.Frame(right); btns.pack(fill='x', pady=(4, 0))
    b_rep = ttk.Button(btns, text='Replace…'); b_undo = ttk.Button(btns, text='Undo')
    b_png = ttk.Button(btns, text='Save original as PNG…')
    for b in (b_rep, b_undo, b_png): b.pack(side='left', padx=(0, 4))
    opts = ttk.Frame(right); opts.pack(fill='x', pady=(4, 0))
    view = tk.StringVar(value='new'); bg = tk.StringVar(value='checker')
    r_new = ttk.Radiobutton(opts, text='Show replacement', variable=view, value='new', command=lambda: show())
    r_old = ttk.Radiobutton(opts, text='Show original', variable=view, value='old', command=lambda: show())
    r_new.pack(side='left'); r_old.pack(side='left', padx=(0, 12))
    for t, v in (('Checker', 'checker'), ('RGB', 'rgb'), ('Alpha', 'alpha')):
        ttk.Radiobutton(opts, text=t, variable=bg, value=v, command=lambda: show()).pack(side='left')
    keep_alpha = tk.BooleanVar(value=True)
    ttk.Checkbutton(right, text='Keep the original alpha (cut-outs) when the new image has none',
                    variable=keep_alpha).pack(anchor='w', pady=(4, 0))

    logw = tk.Text(root, height=7, state='disabled', wrap='none'); logw.pack(fill='x', padx=6, pady=(4, 0))
    ttk.Label(root, textvariable=status, anchor='w', padding=(6, 2)).pack(fill='x')

    def log(msg):
        def f():
            logw.configure(state='normal'); logw.insert('end', msg + '\n'); logw.see('end')
            logw.configure(state='disabled')
        root.after(0, f)

    # ------------------------------------------------------------ worker (encodes / exports run one at a time)
    def worker():
        while True:
            fn, done, label = jobs.get()
            root.after(0, lambda l=label: status.set(l + ('  (%d more queued)' % (pending[0] - 1) if pending[0] > 1 else '')))
            try:
                r = fn(); err = None
            except Exception as e:
                r, err = None, e
            def fin(r=r, err=err, done=done):
                pending[0] -= 1
                if err is not None:
                    log('ERROR: %s' % err); messagebox.showerror('Error', str(err))
                elif done:
                    done(r)
                status.set('Ready' if not pending[0] else status.get())
            root.after(0, fin)
    threading.Thread(target=worker, daemon=True).start()

    def submit(fn, done=None, label='Working…'):
        pending[0] += 1
        jobs.put((fn, done, label))

    # ------------------------------------------------------------ tree
    def arc_label(k):
        s = st['s']; a = s.arcs[k]
        n = len(s.staged_in(k))
        return '%d texture%s' % (len(a.tex), '' if len(a.tex) == 1 else 's') + (', %d staged' % n if n else '')

    def fill_tree():
        tree.delete(*tree.get_children())
        s = st['s']
        if not s: return
        nested = {}
        for k in s.tex_arcs:
            parts = s.arcs[k].logical.split('/')
            node = nested
            for p in parts[:-1]:
                node = node.setdefault(p + '/', {})
            node[parts[-1]] = k

        def insert(parent, node, prefix=''):
            for name in sorted(node, key=lambda n: (not n.endswith('/'), n.lower())):
                v = node[name]
                if isinstance(v, dict):
                    label, sub = name, v
                    while len(sub) == 1 and isinstance(next(iter(sub.values())), dict):   # collapse a/b/c/ chains
                        only = next(iter(sub)); label += only; sub = sub[only]
                    iid = tree.insert(parent, 'end', iid='d:' + prefix + label, text=label.rstrip('/'),
                                      open=True)
                    insert(iid, sub, prefix + label)
                else:
                    iid = tree.insert(parent, 'end', iid='a:%d' % v, text=name,
                                      values=('', '', '', arc_label(v)), open=False)
                    tree.insert(iid, 'end', iid='a:%d:ph' % v, text='…')
                    refresh_arc(v)
        insert('', nested)

    def fill_arc(k):
        if tree.exists('a:%d:ph' % k):
            tree.delete('a:%d:ph' % k)
            a = st['s'].arcs[k]
            for i in a.tex:
                h = a.header(i)
                tree.insert('a:%d' % k, 'end', iid='t:%d:%d' % (k, i), text=a.entries[i][0].split('\\')[-1],
                            values=(h.get('fmt_name') or '?', '%dx%d' % (h.get('width', 0), h.get('height', 0)),
                                    h.get('mips', ''), '' if h.get('supported') else 'not supported'),
                            tags=() if h.get('supported') else ('dim',))
        refresh_arc(k)

    def refresh_arc(k):
        s = st['s']
        staged = set(s.staged_in(k))
        tree.item('a:%d' % k, values=('', '', '', arc_label(k)), tags=('staged',) if staged else ())
        for i in s.arcs[k].tex:
            iid = 't:%d:%d' % (k, i)
            if tree.exists(iid):
                vals = list(tree.item(iid, 'values'))
                vals[3] = 'STAGED' if i in staged else ('' if s.arcs[k].header(i).get('supported') else 'not supported')
                tree.item(iid, values=vals, tags=('staged',) if i in staged else
                          (() if s.arcs[k].header(i).get('supported') else ('dim',)))
        n = len(s.staged)
        root.title('MHGU Mod Texture Editor - %s%s' % (os.path.basename(s.src), ' (%d staged)' % n if n else ''))

    def on_open(e=None):
        iid = tree.focus()
        if iid.startswith('a:') and iid.count(':') == 1: fill_arc(int(iid[2:]))

    def selected_tex():
        iid = tree.focus()
        if iid.startswith('t:'):
            _, k, i = iid.split(':'); return int(k), int(i)
        return None

    # ------------------------------------------------------------ preview
    def decoded(k, i, which):
        s = st['s']
        key = (id(s), k, i, which, id(s.staged.get((k, i))) if which == 'new' else 0)
        c = st['cache']
        if key not in c:
            data = s.staged[(k, i)]['tex'] if which == 'new' else s.arcs[k].read(i)
            if len(c) > 12: c.pop(next(iter(c)))
            c[key] = tex_decode(data)
        return c[key]

    def render(img, mode, w, h):
        if mode == 'alpha':
            im = Image.fromarray(img[..., 3], 'L').convert('RGB')
        elif mode == 'rgb':
            im = Image.fromarray(img[..., :3], 'RGB')
        else:
            im = Image.fromarray(img, 'RGBA')
            yy, xx = np.mgrid[:img.shape[0], :img.shape[1]]
            ch = np.where(((yy // 16 + xx // 16) % 2)[..., None] == 0, 205, 150).astype(np.uint8)
            base = Image.fromarray(np.repeat(ch, 3, -1), 'RGB'); base.paste(im, (0, 0), im); im = base
        im.thumbnail((w, h), Image.LANCZOS if max(im.size) > max(w, h) else Image.NEAREST)
        return im

    def set_buttons(k=None, i=None):
        s = st['s']
        is_tex = k is not None
        ok = is_tex and s.arcs[k].header(i).get('supported')
        staged = is_tex and (k, i) in s.staged
        b_rep.state(['!disabled'] if ok else ['disabled'])
        b_png.state(['!disabled'] if ok else ['disabled'])
        b_undo.state(['!disabled'] if staged else ['disabled'])
        for r in (r_new, r_old): r.state(['!disabled'] if staged else ['disabled'])

    def show(*_):
        s = st['s']
        if not s:
            title.config(text=''); info.config(text=''); stag.config(text='')
            drop.config(image='', text=('Drop a mod folder or .zip here\n\nor use Open mod folder… / Open mod zip…'
                                        if dnd else 'Open a mod folder or zip'))
            set_buttons(); return
        iid = tree.focus()
        if not iid.startswith('t:'):
            set_buttons(); stag.config(text='')
            if iid.startswith('a:'):
                k = int(iid.split(':')[1]); a = s.arcs[k]
                title.config(text=a.logical)
                info.config(text='ARC v%#x, %d entries, %d textures, %d staged'
                            % (a.version, len(a.entries), len(a.tex), len(s.staged_in(k))))
            else:
                title.config(text=s.src)
                info.config(text='%d arcs with textures (%d textures), %d staged replacements'
                            % (len(s.tex_arcs), sum(len(s.arcs[k].tex) for k in s.tex_arcs), len(s.staged)))
            drop.config(image='', text='Select a texture to preview it,\nthen drop an image here to replace it.')
            return
        k, i = selected_tex(); a = s.arcs[k]; h = a.header(i)
        title.config(text=a.entries[i][0])
        info.config(text='%s\n%s  %s' % (a.logical, describe(h), FMT_HINT.get(h.get('fmt_name'), '')))
        set_buttons(k, i)
        stg = s.staged.get((k, i))
        if stg:
            stag.config(text='STAGED  ←  %s%s' % (stg['src'], ''.join('\n• ' + n for n in stg['notes'])))
        else:
            stag.config(text='Original' + ('  —  drop an image here to replace it' if dnd else ''))
        if not h.get('supported'):
            drop.config(image='', text='(this texture type cannot be previewed or replaced)'); return
        which = 'new' if stg and view.get() == 'new' else 'old'
        st['preview_token'] += 1; token = st['preview_token']
        drop.config(image='', text='Loading…')
        mode, w, hh = bg.get(), max(120, drop.winfo_width() - 12), max(120, drop.winfo_height() - 12)

        def load():
            img = decoded(k, i, which)
            im = render(img, mode, w, hh)
            def put():
                if token != st['preview_token']: return
                st['photo'] = ImageTk.PhotoImage(im)
                drop.config(image=st['photo'], text='')
            root.after(0, put)
        threading.Thread(target=lambda: _safe(load), daemon=True).start()

    def _safe(fn):
        try: fn()
        except Exception as e: root.after(0, lambda: drop.config(image='', text='Preview failed: %s' % e))

    # ------------------------------------------------------------ mod open / close
    def confirm_discard():
        s = st['s']
        return not (s and s.staged) or messagebox.askyesno(
            'Unsaved changes', '%d staged replacement(s) have not been exported. Discard them?' % len(s.staged))

    def open_mod(path=None, folder=False):
        if not confirm_discard(): return
        if not path:
            path = pick('dir', 'Open mod folder', kind='mod') if folder else pick(
                'open', 'Open mod zip', [('Zip archive', '*.zip *.ZIP'), ('All files', '*')], kind='mod')
            if not path: return
        old = st['s']

        def job():
            return ModSession(path, log=log)

        def done(s):
            if old: old.close()
            st['s'] = s; st['cache'].clear()
            fill_tree(); show()
            log('opened %s (%s): %d arcs, %d with textures, %d textures%s' % (
                path, s.kind, len(s.arcs), len(s.tex_arcs), sum(len(s.arcs[k].tex) for k in s.tex_arcs),
                ', %d unreadable: %s' % (len(s.bad), ', '.join(b[0] for b in s.bad[:5])) if s.bad else ''))
            if not s.tex_arcs:
                messagebox.showinfo('Open', 'No .arc with textures was found in this mod.')
        submit(job, done, 'Opening %s…' % os.path.basename(path))

    # ------------------------------------------------------------ staging
    def stage(k, i, path):
        s = st['s']; name = s.arcs[k].entries[i][0]

        ka = keep_alpha.get()

        def job():
            return s.stage(k, i, path, ka)

        def done(notes):
            log('staged %s : %s  <-  %s%s' % (s.arcs[k].logical, name, os.path.basename(path),
                                               ''.join('\n    ' + n for n in notes)))
            refresh_arc(k)
            if selected_tex() == (k, i): view.set('new'); show()
        submit(job, done, 'Encoding %s…' % name.split('\\')[-1])

    def replace_dialog():
        t = selected_tex()
        if not t: return
        p = pick('open', 'Replacement image', [('Images', ' '.join('*' + e for e in IMAGE_EXT + tuple(x.upper() for x in IMAGE_EXT))), ('All files', '*')])
        if p: stage(*t, p)

    def undo():
        t = selected_tex()
        if t and st['s'].unstage(*t):
            log('undone: %s' % st['s'].arcs[t[0]].entries[t[1]][0])
            refresh_arc(t[0]); show()

    def undo_all():
        s = st['s']
        if not s or not s.staged: return
        if not messagebox.askyesno('Undo all', 'Undo all %d staged replacements?' % len(s.staged)): return
        ks = s.modified_arcs(); s.staged.clear()
        for k in ks: refresh_arc(k)
        show(); log('all staged replacements undone')

    def save_png_dialog():
        t = selected_tex()
        if not t: return
        name = st['s'].arcs[t[0]].entries[t[1]][0].split('\\')[-1]
        p = pick('save', 'Save original as PNG', [('PNG', '*.png')], initialfile=name + '.png', defext='.png')
        if p:
            submit(lambda: save_png(decoded(*t, 'old'), p), lambda r: log('saved %s' % p), 'Saving PNG…')

    def stage_folder():
        s = st['s']
        if not s: return
        d = pick('dir', 'Folder of replacement images (exported layout or texture names)')
        if not d: return
        found, notes = s.match_images(d)
        for n in notes: log('  note: ' + n)
        if not found:
            messagebox.showinfo('Stage folder', 'No image in that folder matches a texture of this mod.'); return
        lines = ['%s  <-  %s' % (s.arcs[k].entries[i][0].split('\\')[-1], os.path.basename(p))
                 for (k, i), p in sorted(found.items())]
        if not messagebox.askyesno('Stage folder', 'Stage %d replacement(s)?\n\n%s%s' % (
                len(found), '\n'.join(lines[:25]), '\n…' if len(lines) > 25 else '')): return
        for (k, i), p in sorted(found.items()):
            stage(k, i, p)

    def export_originals():
        s = st['s']
        if not s: return
        d = pick('dir', 'Export every original texture as PNG into…')
        if d: submit(lambda: s.export_pngs(d, log), None, 'Exporting PNGs…')

    # ------------------------------------------------------------ export form
    def export_dialog(patch):
        s = st['s']
        if not s: return
        if patch and not s.staged:
            messagebox.showinfo('Export patch', 'Nothing is staged yet.'); return
        if pending[0]:
            messagebox.showinfo('Busy', 'Wait for the current encodes to finish.'); return
        d = tk.Toplevel(root); d.title('Export patch' if patch else 'Export modified mod')
        d.transient(root); d.resizable(True, False)
        f = ttk.Frame(d, padding=12); f.pack(fill='both', expand=True)
        mods = s.modified_arcs()
        ttk.Label(f, text=('Only the %d modified arc(s), at their paths inside the mod:' if patch else
                           'The whole mod, with the %d modified arc(s) repacked:') % len(mods),
                  font=('TkDefaultFont', 10, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        lst = '\n'.join('  %s  (%d texture%s)' % (s.arcs[k].logical, len(s.staged_in(k)),
                                                  's' if len(s.staged_in(k)) > 1 else '') for k in mods[:8])
        ttk.Label(f, text=(lst + ('\n  …' if len(mods) > 8 else '')) or '  (none yet)', justify='left').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(2, 8))
        fmt = tk.StringVar(value='zip' if s.kind == 'zip' else 'folder')
        ttk.Label(f, text='Save as:').grid(row=2, column=0, sticky='w')
        rf = ttk.Frame(f); rf.grid(row=2, column=1, columnspan=2, sticky='w')
        ttk.Radiobutton(rf, text='Folder', variable=fmt, value='folder', command=lambda: upd()).pack(side='left')
        ttk.Radiobutton(rf, text='Zip file', variable=fmt, value='zip', command=lambda: upd()).pack(side='left', padx=8)
        ttk.Label(f, text='Destination:').grid(row=3, column=0, sticky='w', pady=(6, 0))
        dest = tk.StringVar()
        ttk.Entry(f, textvariable=dest, width=70).grid(row=3, column=1, sticky='ew', pady=(6, 0))
        f.columnconfigure(1, weight=1)
        base = os.path.splitext(os.path.basename(s.src))[0] + ('-patch' if patch else '-modified')

        def browse():
            if fmt.get() == 'zip':
                p = pick('save', 'Save as zip', [('Zip archive', '*.zip')], initialfile=base + '.zip',
                         defext='.zip', parent=d)
            else:
                p = pick('dir', 'Folder to write into', parent=d)
            if p: dest.set(p)
        ttk.Button(f, text='Browse…', command=browse).grid(row=3, column=2, padx=(4, 0), pady=(6, 0))
        pv = tk.StringVar()
        ttk.Label(f, textvariable=pv, wraplength=640, justify='left', foreground='#333').grid(
            row=4, column=0, columnspan=3, sticky='w', pady=10)
        bf = ttk.Frame(f); bf.grid(row=5, column=0, columnspan=3, sticky='e')
        okb = ttk.Button(bf, text='Export'); okb.pack(side='left', padx=4)
        ttk.Button(bf, text='Cancel', command=d.destroy).pack(side='left')
        sample = s.arcs[mods[0]].logical if mods else (s.files()[0][0] if s.files() else '')

        def upd(*_):
            p = dest.get().strip(); ok = False
            if fmt.get() == 'zip' and p and os.path.isdir(p):
                p = os.path.join(p, base + '.zip'); dest.set(p); return
            if fmt.get() == 'folder' and p.lower().endswith('.zip'):
                dest.set(os.path.dirname(p)); return
            if not p:
                msg = 'Choose where to save.'
            else:
                try:
                    s.check_dest(p); ok = True
                    if fmt.get() == 'zip':
                        msg = 'Writes the zip %s\nwith paths like: %s' % (p, sample)
                        if os.path.exists(p): msg += '\n\nThis zip exists and will be replaced.'
                    else:
                        msg = 'Writes into %s\ne.g. %s' % (p, os.path.join(p, *sample.split('/')))
                        top = sample.split('/')[0]
                        if os.path.exists(os.path.join(p, top)):
                            msg += '\n\n%s already exists there: files with the same paths will be replaced.' % top
                except ValueError as e:
                    msg = str(e)
            pv.set(msg); okb.state(['!disabled'] if ok else ['disabled'])
        dest.trace_add('write', upd); upd()

        def go():
            p, as_zip = dest.get().strip(), fmt.get() == 'zip'
            d.destroy()
            submit(lambda: s.export(p, as_zip, patch, log),
                   lambda r: messagebox.showinfo('Export', '%s written:\n%s\n\n%d file(s), %d repacked arc(s).'
                                                 % ('Patch' if patch else 'Mod', p, r[0], r[1])),
                   'Exporting…')
        okb.config(command=go)
        d.bind('<Escape>', lambda e: d.destroy())
        d.grab_set()

    # ------------------------------------------------------------ toolbar, bindings, drag and drop
    for parent, txt, fn in (
            (bar, 'Open mod folder…', lambda: open_mod(folder=True)), (bar, 'Open mod zip…', lambda: open_mod()),
            (bar, 'Export original PNGs…', export_originals), (bar, 'Stage images from folder…', stage_folder),
            (bar, 'Undo all', undo_all),
            (bar2, 'Export modified mod…', lambda: export_dialog(False)),
            (bar2, 'Export patch (modified arcs only)…', lambda: export_dialog(True))):
        ttk.Button(parent, text=txt, command=fn).pack(side='left', padx=2)
    if not dnd:
        ttk.Label(bar2, text='  (drag and drop off: run "pip install tkinterdnd2" to enable it)',
                  foreground='#a00').pack(side='left')
    b_rep.config(command=replace_dialog); b_undo.config(command=undo); b_png.config(command=save_png_dialog)
    tree.bind('<<TreeviewSelect>>', show)
    tree.bind('<<TreeviewOpen>>', on_open)
    tree.bind('<Double-1>', lambda e: replace_dialog() if selected_tex() else None)
    tree.bind('<Delete>', lambda e: undo())
    root.bind('<Control-z>', lambda e: undo())
    drop.bind('<Configure>', lambda e: root.after_idle(show) if selected_tex() else None)

    def dropped(paths, target_iid=None):
        paths = [p for p in paths if p]
        if not paths: return
        p = paths[0]
        if os.path.isdir(p) or p.lower().endswith('.zip'):
            if os.path.isdir(p) and st['s'] and messagebox.askyesno(
                    'Dropped folder', 'Stage the images in this folder?\n\n(No = open it as a mod)'):
                found, notes = st['s'].match_images(p)
                for n in notes: log('  note: ' + n)
                for (k, i), path in sorted(found.items()): stage(k, i, path)
                if not found: messagebox.showinfo('Stage', 'No image there matches a texture.')
                return
            open_mod(p); return
        if os.path.splitext(p)[1].lower() not in IMAGE_EXT:
            messagebox.showinfo('Drop', 'Drop an image (PNG, JPG, TGA, …), a mod folder or a .zip.'); return
        t = None
        if target_iid and target_iid.startswith('t:'):
            _, k, i = target_iid.split(':'); t = (int(k), int(i))
            tree.selection_set(target_iid); tree.focus(target_iid)
        t = t or selected_tex()
        if not st['s'] or not t:
            messagebox.showinfo('Drop', 'Select the texture to replace first (or drop onto its row).'); return
        if not st['s'].arcs[t[0]].header(t[1]).get('supported'):
            messagebox.showinfo('Drop', 'This texture type cannot be replaced.'); return
        if len(paths) > 1: log('note: %d files dropped, used the first one' % len(paths))
        stage(*t, p)

    if dnd:
        def on_drop(e, widget):
            files = list(root.tk.splitlist(e.data))
            iid = None
            if widget is tree:
                iid = tree.identify_row(e.y_root - tree.winfo_rooty()) or None
            dropped(files, iid)
            return e.action
        for w in (drop, tree, root):
            w.drop_target_register(DND_FILES)
            w.dnd_bind('<<Drop>>', lambda e, w=w: on_drop(e, w))
        drop.dnd_bind('<<DropEnter>>', lambda e: (drop.config(bg='#cfe3ff'), e.action)[1])
        drop.dnd_bind('<<DropLeave>>', lambda e: (drop.config(bg='#d9d9d9'), e.action)[1])
        _old_dropped = dropped

        def dropped(paths, target_iid=None):      # reset the highlight after a drop
            drop.config(bg='#d9d9d9'); _old_dropped(paths, target_iid)

    def on_close():
        if not confirm_discard(): return
        if st['s']: st['s'].close()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)
    show()
    if initial: root.after(150, lambda: open_mod(initial))
    root._mhgu = dict(state=st, open_mod=open_mod, stage=stage, export_dialog=export_dialog, tree=tree,
                      fill_arc=fill_arc, show=show, pending=pending, dropped=lambda *a: dropped(*a), undo=undo)
    root.mainloop()

# =============================================================== main

def patch_args(p):
    p.add_argument('--patch-dest', metavar='DEST', help='write the arc to DEST + its path relative to the patch root '
                   '(the original arc is not touched)')
    p.add_argument('--patch-root', metavar='ROOT', help='where the recreated path starts (default: the folder above '
                   'the mod folder that holds romfs)')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or (len(argv) == 1 and (os.path.isdir(argv[0]) or argv[0].lower().endswith('.zip'))):
        return gui(argv[0] if argv else None)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('gui'); p.add_argument('arc', nargs='?', help='mod folder or zip to open')
    p = sp.add_parser('mod', help='open a mod folder / zip, stage images, export a mod or a patch')
    p.add_argument('mod'); p.add_argument('--list', action='store_true', help='list the arcs with textures')
    p.add_argument('--export-pngs', metavar='DIR', help='every original texture as PNG (layout: arc path/texture)')
    p.add_argument('--images', metavar='DIR', help='stage the images in DIR (export layout or unique texture names)')
    p.add_argument('--out', metavar='DEST', help='write the result: a folder, or a .zip file')
    p.add_argument('--patch', action='store_true', help='write only the modified arcs')
    p.add_argument('--no-keep-alpha', action='store_true'); p.set_defaults(fn=cmd_mod)
    p = sp.add_parser('list'); p.add_argument('arc'); p.set_defaults(fn=cmd_list)
    p = sp.add_parser('export'); p.add_argument('arc'); p.add_argument('outdir')
    p.add_argument('--mips', action='store_true', help='also write every mip level'); p.set_defaults(fn=cmd_export)
    p = sp.add_parser('replace'); p.add_argument('arc'); p.add_argument('png_dir'); p.add_argument('-o', '--output')
    p.add_argument('--no-keep-alpha', action='store_true'); p.add_argument('--dry-run', action='store_true')
    patch_args(p); p.set_defaults(fn=cmd_replace)
    p = sp.add_parser('replace-one'); p.add_argument('arc'); p.add_argument('texname'); p.add_argument('png')
    p.add_argument('-o', '--output'); p.add_argument('--no-keep-alpha', action='store_true')
    patch_args(p); p.set_defaults(fn=cmd_replace_one)
    p = sp.add_parser('export-tree'); p.add_argument('root'); p.add_argument('outdir')
    p.add_argument('--mips', action='store_true'); p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(fn=cmd_export_tree)
    p = sp.add_parser('unpack'); p.add_argument('arc'); p.add_argument('outdir'); p.set_defaults(fn=cmd_unpack)
    p = sp.add_parser('pack'); p.add_argument('indir'); p.add_argument('output'); p.set_defaults(fn=cmd_pack)
    p = sp.add_parser('topng'); p.add_argument('tex'); p.add_argument('png')
    p.add_argument('mip', nargs='?', type=int, default=0); p.set_defaults(fn=cmd_topng)
    p = sp.add_parser('totex'); p.add_argument('png'); p.add_argument('tex'); p.add_argument('--like')
    p.add_argument('--fmt', choices=sorted(NAME_FMT)); p.add_argument('--mips', type=int)
    p.add_argument('--no-keep-alpha', action='store_true'); p.set_defaults(fn=cmd_totex)
    p = sp.add_parser('scan'); p.add_argument('folder'); p.set_defaults(fn=cmd_scan)
    a = ap.parse_args(argv)
    if a.cmd == 'gui':
        return gui(a.arc)
    a.fn(a)


if __name__ == '__main__':
    main()
