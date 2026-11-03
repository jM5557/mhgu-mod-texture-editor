#!/usr/bin/env python3
"""make_release.py : build the release downloads into dist/ (attach them to a GitHub release).

  python scripts/make_release.py

dist/mhgu-mod-texture-editor-<version>-windows.zip     tool, .bat launchers, requirements, icon, README, LICENSE
dist/mhgu-mod-texture-editor-<version>-linux.tar.gz    tool, launcher, install.sh, icon, README, docs, LICENSE
Line endings follow the platform (CRLF for .bat, LF for the shell scripts), executables keep their mode.
"""
import io, os, re, tarfile, time, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'mhgu-mod-texture-editor'
COMMON = ['mhgu_tex_tool.py', 'icon.png', 'README.md', 'LICENSE', 'CHANGELOG.md', 'docs/formats.md',
          'docs/screenshot.png']
WINDOWS = COMMON + ['MHGU Texture Tool.bat', 'Install requirements.bat', 'requirements.txt']
LINUX = COMMON + ['mhgu-texture-tool', 'install.sh', 'requirements.txt', 'docs/linux.md']
EXEC = {'mhgu-texture-tool', 'install.sh', 'mhgu_tex_tool.py'}
TEXT = ('.py', '.bat', '.md', '.txt', '.sh', '')


def version():
    src = open(os.path.join(ROOT, 'mhgu_tex_tool.py'), encoding='utf-8').read()
    return re.search(r"__version__ = '([^']+)'", src).group(1)


def content(rel, eol):
    data = open(os.path.join(ROOT, rel), 'rb').read()
    if os.path.splitext(rel)[1] in TEXT and b'\0' not in data:
        data = data.replace(b'\r\n', b'\n')
        if eol == 'crlf' or rel.endswith('.bat'):
            data = data.replace(b'\n', b'\r\n')
    return data


def main():
    v = version()
    dist = os.path.join(ROOT, 'dist')
    os.makedirs(dist, exist_ok=True)
    top = '%s-%s' % (NAME, v)
    stamp = time.localtime()[:6]

    wz = os.path.join(dist, top + '-windows.zip')
    with zipfile.ZipFile(wz, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in WINDOWS:
            z.writestr(zipfile.ZipInfo('%s/%s' % (top, rel), date_time=stamp), content(rel, 'crlf'),
                       compress_type=zipfile.ZIP_DEFLATED)

    lt = os.path.join(dist, top + '-linux.tar.gz')
    with tarfile.open(lt, 'w:gz') as t:
        for rel in LINUX:
            data = content(rel, 'lf')
            info = tarfile.TarInfo('%s/%s' % (top, rel))
            info.size, info.mtime = len(data), int(time.time())
            info.mode = 0o755 if rel in EXEC else 0o644
            t.addfile(info, io.BytesIO(data))

    for p in (wz, lt):
        print('%-70s %8d bytes' % (os.path.relpath(p, ROOT), os.path.getsize(p)))


if __name__ == '__main__':
    main()
