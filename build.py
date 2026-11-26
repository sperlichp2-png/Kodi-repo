#!/usr/bin/env python3
"""Sestaví ZIPy doplňků ze složky src/ do zips/ a vygeneruje addons.xml + addons.xml.md5.

Použití:  python3 build.py
Před vydáním nové verze zvyš version="…" v src/<doplněk>/addon.xml.
"""
import hashlib
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'src')
ZIPS = os.path.join(ROOT, 'zips')
SKIP = ('__pycache__', '.pyc', '.DS_Store')


def addon_meta(path):
    xml = open(os.path.join(path, 'addon.xml'), encoding='utf-8').read()
    m = re.search(r'<addon[^>]*\bid="([^"]+)"[^>]*\bversion="([^"]+)"', xml, re.S)
    return m.group(1), m.group(2), re.sub(r'<\?xml[^>]*\?>\s*', '', xml).strip()


def make_zip(addon_dir, addon_id, version):
    out_dir = os.path.join(ZIPS, addon_id)
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):          # ponech jen aktuální verzi
        if old.endswith('.zip'):
            os.remove(os.path.join(out_dir, old))
    out = os.path.join(out_dir, '%s-%s.zip' % (addon_id, version))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(addon_id + '/', '')
        for base, dirs, files in os.walk(addon_dir):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            rel = os.path.relpath(base, addon_dir)
            if rel != '.':
                z.writestr('%s/%s/' % (addon_id, rel.replace(os.sep, '/')), '')
            for f in sorted(files):
                if f.endswith(SKIP):
                    continue
                arc = os.path.normpath(os.path.join(addon_id, rel, f)).replace(os.sep, '/')
                z.write(os.path.join(base, f), arc)
    icon = os.path.join(addon_dir, 'icon_logo.png')
    if os.path.exists(icon):
        import shutil
        shutil.copy(icon, os.path.join(out_dir, 'icon_logo.png'))
    return out


def main():
    parts = []
    for name in sorted(os.listdir(SRC)):
        path = os.path.join(SRC, name)
        if not os.path.isfile(os.path.join(path, 'addon.xml')):
            continue
        addon_id, version, xml = addon_meta(path)
        print('%-28s %s  ->  %s' % (addon_id, version, os.path.relpath(make_zip(path, addon_id, version), ROOT)))
        parts.append(xml)
        if addon_id.startswith('repository.'):   # instalační ZIP repozitáře i v kořeni (pro flashku / zdroj)
            import shutil
            for old in os.listdir(ROOT):
                if old.startswith(addon_id + '-') and old.endswith('.zip'):
                    os.remove(os.path.join(ROOT, old))
            shutil.copy(os.path.join(ZIPS, addon_id, '%s-%s.zip' % (addon_id, version)), ROOT)
    out = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + '\n\n'.join(parts) + '\n</addons>\n'
    open(os.path.join(ZIPS, 'addons.xml'), 'w', encoding='utf-8').write(out)
    open(os.path.join(ZIPS, 'addons.xml.md5'), 'w').write(hashlib.md5(out.encode('utf-8')).hexdigest())
    print('addons.xml + md5 hotovo')


if __name__ == '__main__':
    main()
