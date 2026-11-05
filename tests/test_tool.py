"""Tests on generated data (no game files): python -m unittest discover -s tests -v

The GOLDEN hashes were produced by tools/tex.py of Scrimas/MH4U-port-to-MHGU (whose layout is verified
byte for byte against every MHGU monster texture) and checked equal to this tool's output when they were
recorded: they pin the Switch block-linear layout and the TEX header.
"""
import hashlib, os, shutil, sys, tempfile, unittest, zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import mhgu_tex_tool as M  # noqa: E402


def pattern(w, h):
    yy, xx = np.mgrid[:h, :w]
    return np.stack([(xx * 7 + yy * 3) % 256, (xx * xx + yy) % 256, (yy * 5) % 256, ((xx ^ yy) * 9) % 256],
                    -1).astype(np.uint8)


def smooth(w, h, alpha=None):
    yy, xx = np.mgrid[:h, :w]
    img = np.stack([xx * 255 // max(1, w - 1), yy * 255 // max(1, h - 1), (xx + yy) * 127 // max(1, w + h - 2),
                    np.full_like(xx, 255)], -1).astype(np.uint8)
    if alpha is not None:
        img[..., 3] = alpha
    return img


def psnr(a, b):
    mse = ((a.astype(float) - b.astype(float)) ** 2).mean()
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def header(fmt, w, h, mips=None):
    hd = M.generic_header(fmt)
    hd.update(width=w, height=h, mips=mips or M.full_mips(w, h))
    return hd


def make_tex(fmt, w, h, img=None, mips=None):
    return M.tex_encode(smooth(w, h) if img is None else img, header(fmt, w, h, mips))


GOLDEN_RGBA8 = {(64, 48): '3ecfabd5663ba9e65caca285f33516757d83eea655969189d1cf0aeae0c9acf4',
                (300, 90): '9ad85aec910a17967aa5cad9f9c3ae85cf96abb05f284bae5fc31dbfc8dd4a94'}
GOLDEN_SWIZZLE = {('bc1', 1024, 512): '97d5ed8bc5bba27aeeca328797160d9bc727113c86afbfa5fd6d3da2f84e4153',
                  ('bc3', 256, 256): '630e8837f90b8f4f7a550bbf351a13a1b6cce3b516c6303b25b8f7594db875c6',
                  ('bc5', 2048, 512): 'ccda5422c2dac0cb31291a10c5c226a1c93951833dafa90d19a979bb3d2f35d7',
                  ('bc4', 96, 40): 'f863a2142554992536b585c8ea4665bd086d13f47a3a8af43731182b90ddd79c',
                  ('bc3', 300, 90): '441bd08b6b2430cebbcee27110b2f678f7baa57f147503de5467779aa395e921'}
GOLDEN_LAST_OFFSETS = {('bc1', 1024, 512): [350720, 351232, 351744], ('bc3', 256, 256): [88064, 88576, 89088],
                       ('bc5', 2048, 512): [1400832, 1401344, 1401856], ('bc4', 96, 40): [5120, 5632, 6144],
                       ('bc3', 300, 90): [54784, 55296, 55808]}


class TestLayout(unittest.TestCase):
    def test_rgba8_matches_reference(self):
        for (w, h), digest in GOLDEN_RGBA8.items():
            t = M.tex_encode(pattern(w, h), header('rgba8', w, h, 1))
            self.assertEqual(hashlib.sha256(t).hexdigest(), digest, (w, h))

    def test_block_linear_matches_reference(self):
        for (fmt, w, h), digest in GOLDEN_SWIZZLE.items():
            lay = M.gu_layout(w, h, M.full_mips(w, h), fmt)
            self.assertEqual([l[4] for l in lay][-3:], GOLDEN_LAST_OFFSETS[(fmt, w, h)])
            bw, bhh, bpp, bh, off, size = lay[0]
            el = (np.arange(bw * bhh * bpp) % 251).astype(np.uint8).reshape(bhh, bw, bpp)
            self.assertEqual(hashlib.sha256(M.swizzle(el, bpp, bh)).hexdigest(), digest, fmt)

    def test_swizzle_inverse(self):
        for fmt, w, h in [('bc1', 128, 64), ('bc3', 300, 90), ('rgba8', 17, 9), ('bc5', 4, 4)]:
            for bw, bhh, bpp, bh, off, size in M.gu_layout(w, h, M.full_mips(w, h), fmt):
                el = np.random.default_rng(1).integers(0, 256, (bhh, bw, bpp), dtype=np.uint8)
                data = M.swizzle(el, bpp, bh)
                self.assertEqual(len(data), size)
                self.assertTrue((M.deswizzle(data, bw, bhh, bpp, bh) == el).all())


class TestCodec(unittest.TestCase):
    def test_formats_round_trip(self):
        for fmt, channels, min_db in [('bc1', 3, 34), ('bc3', 4, 34), ('bc4', 1, 40), ('bc5', 2, 40),
                                      ('rgba8', 4, 98)]:
            img = smooth(256, 128)
            out = M.tex_decode(make_tex(fmt, 256, 128, img))
            self.assertEqual(out.shape, (128, 256, 4))
            self.assertGreater(psnr(out[..., :channels], img[..., :channels]), min_db, fmt)

    def test_every_mip_decodes(self):
        t = make_tex('bc3', 300, 90)
        h = M.tex_parse(t)
        self.assertEqual(h['mips'], M.full_mips(300, 90))
        for m in range(h['mips']):
            self.assertEqual(M.tex_decode(t, m).shape[:2], (max(1, 90 >> m), max(1, 300 >> m)))

    def test_bc1_transparency(self):
        a = np.full((64, 64), 255, np.uint8); a[:32, :32] = 0
        out = M.tex_decode(make_tex('bc1', 64, 64, smooth(64, 64, a)))
        self.assertTrue((out[:32, :32, 3] == 0).all())
        self.assertTrue((out[32:, 32:, 3] == 255).all())

    def test_header_fields_kept(self):
        orig = make_tex('bc3', 128, 64)
        h = M.tex_parse(orig)
        new = M.tex_encode(smooth(256, 128), h)
        n = M.tex_parse(new)
        self.assertEqual((n['tier0'], n['fmt'], n['faces'], n['depth']), (h['tier0'], h['fmt'], h['faces'], h['depth']))
        self.assertEqual((n['width'], n['height'], n['mips']), (256, 128, M.full_mips(256, 128)))

    def test_keep_alpha_when_image_has_none(self):
        a = np.full((64, 64), 255, np.uint8); a[:, :16] = 0
        orig = make_tex('bc3', 64, 64, smooth(64, 64, a))
        img = smooth(64, 64)
        out = M.prepare_image(img, False, orig, keep_alpha=True, log=lambda m: None)
        self.assertTrue((out[:, :16, 3] < 20).all())
        out = M.prepare_image(img, False, orig, keep_alpha=False, log=lambda m: None)
        self.assertTrue((out[..., 3] == 255).all())


class TestArc(unittest.TestCase):
    def test_build_read(self):
        ents = [('a\\b\\model', 0x58A15856, b'x' * 1000), ('a\\b\\tex', M.TEX_TYPE, make_tex('bc1', 32, 32))]
        data = M.arc_build(ents)
        ver, back = M.arc_read(data)
        self.assertEqual(ver, 0x11)
        self.assertEqual(back, ents)
        first = int.from_bytes(data[0xC + 0x4C:0xC + 0x50], 'little')
        self.assertEqual(first % 0x8000, 0)

    def test_damaged(self):
        with self.assertRaises(ValueError):
            M.arc_read(b'ARC\0garbage')
        with self.assertRaises(ValueError):
            M.arc_read(b'NOPE' + b'\0' * 20)


class TestModSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.arc_path = 'MH4U Port/romfs/nativeNX/arc/enemy/em057_01.arc'
        self.helm_path = 'MH4U Port/romfs/nativeNX/arc/player/com/m/169/helm.arc'
        body = [('enemy\\em057\\em057_01\\mod\\em057_01', 0x58A15856, b'MOD' * 100),
                ('enemy\\em057\\em057_01\\mod\\em057_01_01_BM', M.TEX_TYPE, make_tex('bc3', 128, 64)),
                ('enemy\\em057\\em057_01\\mod\\em057_01_01_NM', M.TEX_TYPE, make_tex('bc5', 256, 64))]
        helm = [('player\\com\\m\\169\\helm_BM', M.TEX_TYPE, make_tex('bc1', 64, 64))]
        self.zip = os.path.join(self.dir, 'mod.zip')
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr(self.arc_path, M.arc_build(body))
            z.writestr(self.helm_path, M.arc_build(helm))
            z.writestr('MH4U Port/romfs/nativeNX/arc/sound/snd.arc', M.arc_build([('s', 0x1234, b'abc')]))
            z.writestr('MH4U Port/exefs/subsdk1', b'code')
        self.png = os.path.join(self.dir, 'new.png')
        M.save_png(smooth(128, 64)[..., [2, 1, 0, 3]], self.png)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def session(self):
        s = M.ModSession(self.zip, log=lambda m: None)
        self.addCleanup(s.close)
        return s

    def stage_body(self, s):
        k = next(k for k in s.tex_arcs if s.arcs[k].logical == self.arc_path)
        return k, s.stage(k, s.arcs[k].tex[0], self.png)

    def test_open(self):
        s = self.session()
        self.assertEqual(len(s.arcs), 3)
        self.assertEqual(sorted(s.arcs[k].logical for k in s.tex_arcs), sorted([self.arc_path, self.helm_path]))
        k = s.tex_arcs[0]
        h = s.arcs[k].header(s.arcs[k].tex[0])
        self.assertTrue(h['supported'])

    def test_full_zip_changes_only_the_staged_texture(self):
        s = self.session()
        self.stage_body(s)
        out = os.path.join(self.dir, 'out.zip')
        s.export(out, True, False, log=lambda m: None)
        a, b = zipfile.ZipFile(self.zip), zipfile.ZipFile(out)
        self.assertEqual(a.namelist(), b.namelist())
        self.assertEqual([n for n in a.namelist() if a.read(n) != b.read(n)], [self.arc_path])
        _, old = M.arc_read(a.read(self.arc_path))
        _, new = M.arc_read(b.read(self.arc_path))
        self.assertEqual([i for i, (x, y) in enumerate(zip(old, new)) if x != y], [1])

    def test_patch_folder_holds_only_modified_arcs(self):
        s = self.session()
        self.stage_body(s)
        out = os.path.join(self.dir, 'patch')
        s.export(out, False, True, log=lambda m: None)
        files = sorted(os.path.relpath(os.path.join(d, f), out).replace(os.sep, '/')
                       for d, _, fs in os.walk(out) for f in fs)
        self.assertEqual(files, [self.arc_path])

    def test_unstage_and_empty_patch(self):
        s = self.session()
        k, _ = self.stage_body(s)
        self.assertTrue(s.unstage(k, s.arcs[k].tex[0]))
        with self.assertRaises(ValueError):
            s.export(os.path.join(self.dir, 'p'), False, True, log=lambda m: None)

    def test_match_images(self):
        s = self.session()
        d = os.path.join(self.dir, 'imgs')
        M.save_png(smooth(64, 64), os.path.join(d, 'helm_BM.png'))                       # unique name
        M.save_png(smooth(128, 64), os.path.join(d, 'x', *self.arc_path[:-4].split('/'),     # export layout
                                                 'enemy', 'em057', 'em057_01', 'mod', 'em057_01_01_BM.png'))
        M.save_png(smooth(8, 8), os.path.join(d, 'nothing.png'))
        found, notes = s.match_images(d)
        names = sorted(s.arcs[k].entries[i][0].split('\\')[-1] for k, i in found)
        self.assertEqual(names, ['em057_01_01_BM', 'helm_BM'])
        self.assertTrue(any('nothing.png' in n for n in notes))

    def test_export_unpacked_and_reapply_raw_tex(self):
        s = self.session()
        k, _ = self.stage_body(s)
        i = s.arcs[k].tex[0]
        out = os.path.join(self.dir, 'unpacked')
        self.assertEqual(s.export_unpacked(out, with_png=True, log=lambda m: None), 1)
        base = os.path.join(out, *self.arc_path[:-4].split('/'), 'enemy', 'em057', 'em057_01', 'mod', 'em057_01_01_BM')
        self.assertTrue(os.path.isfile(base + '.tex') and os.path.isfile(base + '.png'))
        with open(base + '.tex', 'rb') as f:
            self.assertEqual(f.read(), s.staged[(k, i)]['tex'])
        # the same folder applied to a fresh copy of the mod: the .tex wins over its PNG copy, bytes unchanged
        s2 = self.session()
        found, notes = s2.match_images(out)
        self.assertEqual([os.path.splitext(p)[1] for p in found.values()], ['.tex'])
        self.assertEqual(notes, [])
        (k2, i2), p = next(iter(found.items()))
        s2.stage(k2, i2, p)
        self.assertEqual(s2.staged[(k2, i2)]['tex'], s.staged[(k, i)]['tex'])

    def test_compare_zip_and_folder(self):
        a = self.session()
        b_dir = os.path.join(self.dir, 'b')
        zipfile.ZipFile(self.zip).extractall(b_dir)
        root = os.path.join(b_dir, 'MH4U Port')
        os.remove(os.path.join(root, *self.helm_path.split('/')[1:]))                        # only in A
        img = smooth(32, 32)
        with open(os.path.join(root, 'romfs', 'nativeNX', 'arc', 'enemy', 'extra.arc'), 'wb') as f:  # only in B
            f.write(M.arc_build([('enemy\\extra\\x_BM', M.TEX_TYPE, make_tex('bc1', 32, 32, img))]))
        arc = os.path.join(root, *self.arc_path.split('/')[1:])                                # changed in B
        _, ents = M.arc_read(open(arc, 'rb').read())
        ents[2] = (ents[2][0], ents[2][1], make_tex('bc5', 256, 64, smooth(256, 64)[:, ::-1].copy()))
        with open(arc, 'wb') as f:
            f.write(M.arc_build(ents, level=1))                  # other zlib level: same entries must stay 'same'
        b = M.ModSession(root, log=lambda m: None)
        self.addCleanup(b.close)
        rows = M.compare_mods(a, b, log=lambda m: None)
        got = sorted((r['status'], r['texture'].split('\\')[-1]) for r in rows)
        self.assertEqual(got, [('changed', 'em057_01_01_NM'), ('only A', 'helm_BM'), ('only B', 'x_BM')])
        self.assertTrue(all(r['key'].startswith('romfs/nativeNX/') for r in rows))
        rows_all = M.compare_mods(a, b, log=lambda m: None, same=True)
        self.assertIn('same', {r['status'] for r in rows_all})
        csv_path = os.path.join(self.dir, 'r.csv')
        M.write_compare_csv(rows, a, b, csv_path)
        self.assertEqual(len(open(csv_path).read().strip().splitlines()), 4)
        self.assertEqual(M.export_compare_pngs(rows, a, b, os.path.join(self.dir, 'pngs'), log=lambda m: None), 4)

    def test_compare_pngs_match_main_export_with_alpha(self):
        alpha = np.full((64, 256), 255, np.uint8); alpha[:, :128] = np.arange(128) * 2; alpha[:16, :16] = 0
        src = os.path.join(self.dir, 'alpha_src.png')
        M.save_png(smooth(256, 64, np.tile(alpha, (1, 1))[:64, :256]), src)
        a, b = self.session(), self.session()
        k = next(k for k in b.tex_arcs if b.arcs[k].logical == self.arc_path)
        b.stage(k, b.arcs[k].tex[0], src)
        out_b = os.path.join(self.dir, 'b.zip')
        b.export(out_b, True, False, log=lambda m: None)
        b2 = M.ModSession(out_b, log=lambda m: None); self.addCleanup(b2.close)
        rows = M.compare_mods(a, b2, log=lambda m: None)
        cmp_dir, main_dir = os.path.join(self.dir, 'cmp'), os.path.join(self.dir, 'main')
        M.export_compare_pngs(rows, a, b2, cmp_dir, log=lambda m: None)
        b2.export_pngs(main_dir, log=lambda m: None)
        rel = os.path.join(*self.arc_path[:-4].split('/')[1:], 'enemy', 'em057', 'em057_01', 'mod', 'em057_01_01_BM.png')
        with open(os.path.join(cmp_dir, 'B', rel), 'rb') as f1, \
                open(os.path.join(main_dir, *self.arc_path[:-4].split('/'), 'enemy', 'em057', 'em057_01', 'mod',
                                  'em057_01_01_BM.png'), 'rb') as f2:
            self.assertEqual(f1.read(), f2.read())
        with Image.open(os.path.join(cmp_dir, 'B', rel)) as im:
            got = np.array(im)[..., 3]
        self.assertEqual(im.mode, 'RGBA')
        self.assertTrue((got[:16, :16] < 10).all())
        self.assertTrue((np.abs(got[20:, :128].astype(int) - alpha[20:, :128]) <= 8).all())

    def test_folder_source_refuses_writing_into_itself(self):
        src = os.path.join(self.dir, 'src')
        zipfile.ZipFile(self.zip).extractall(src)
        s = M.ModSession(os.path.join(src, 'MH4U Port'), log=lambda m: None)
        self.addCleanup(s.close)
        k = s.tex_arcs[0]
        s.stage(k, s.arcs[k].tex[0], self.png)
        for bad in (os.path.join(src, 'MH4U Port', 'out'), src):
            with self.assertRaises(ValueError):
                s.export(bad, False, True, log=lambda m: None)
        ok = os.path.join(self.dir, 'elsewhere')
        s.export(ok, False, True, log=lambda m: None)
        self.assertTrue(os.path.isfile(os.path.join(ok, 'MH4U Port', *s.arcs[k].logical.split('/')[1:])))


if __name__ == '__main__':
    unittest.main()
