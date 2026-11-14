#!/usr/bin/env python3
"""
gen-iso-3166-1 — regenerate helm/public-pages/files/iso-3166-1.yaml, the request form's country list.

The list is the COMPLETE ISO 3166-1 alpha-2 set, generated from pycountry (Debian iso-codes data),
never typed from memory. pycountry is not a CI dependency: the file is checked in, and
test-public-pages.py checks its shape (249 unique quoted codes) everywhere and cross-checks it
against pycountry wherever pycountry is installed.

Usage: python3 -m pip install pycountry && python3 scripts/gen-iso-3166-1.py > helm/public-pages/files/iso-3166-1.yaml
"""
import importlib.metadata, sys, unicodedata, json
import pycountry
ver = importlib.metadata.version('pycountry')
rows = []
for c in pycountry.countries:
    rows.append((c.alpha_2, getattr(c, 'common_name', None) or c.name))
key = lambda r: unicodedata.normalize('NFKD', r[1]).encode('ascii', 'ignore').decode().casefold()
rows.sort(key=key)
assert len(rows) == len({r[0] for r in rows}) == 249
out = [
    '# ISO 3166-1: every officially assigned alpha-2 code, with the English short name shown in the',
    '# request form\'s Country select (the code is what is submitted). GENERATED, never typed:',
    f'# pycountry {ver} (its data is Debian iso-codes), the common name where iso-codes gives one',
    '# ("Bolivia", not "Bolivia, Plurinational State of"), else the official short name; sorted by',
    '# that name with accents folded, so a searchable Select lists them in reading order.',
    '# Every code is QUOTED: YAML 1.1 reads a bare NO (Norway) as false, and so do Helm and PyYAML.',
    '# Regenerate with scripts/gen-iso-3166-1.py, never by hand.',
    'countries:',
]
for code, name in rows:
    out.append(f'- {{code: "{code}", name: {json.dumps(name, ensure_ascii=False)}}}')
sys.stdout.write('\n'.join(out) + '\n')
