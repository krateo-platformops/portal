#!/usr/bin/env python3
"""A version label never comes out as a bare "v" (portal review, Marketplace: "uncategorized · v").

A label like "v1.4.2" is built in jq as "v" + version. When the version is empty, that gives "v";
when it is missing, other spellings gave "v?". Over the RENDERED charts, as they ship:

  1. every `"…v" + …` concatenation in any chart is the one guarded form
         X // "" | if . == "" then <fallback> else "v" + . end
     so an empty or missing version takes the fallback and never reaches the "v";
  2. the marketplace detail page's header subtitle and "Installed" row, run with jq over a present,
     an empty and a missing version, show "v1.4.2" for the first and no bare "v" for the others.

`--root DIR` checks another checkout (the control: origin/main before this change fails both).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

CONCAT = re.compile(r'"[^"\n]*v" \+')
GUARDED = re.compile(r'\| if \. == "" then [^|]*? else "[^"\n]*v" \+ \. end')


def render(root, chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(root, 'helm', chart), copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as handle:
            text = handle.read().replace('CHART_VERSION', '0.0.0').replace('APP_VERSION', '0.0.0')
        with open(meta, 'w', encoding='utf-8') as handle:
            handle.write(text)
        proc = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f'helm template {chart} failed: {proc.stderr.strip()[:400]}')
    return proc.stdout


def expression(docs, kind, name, path):
    doc = next(d for d in docs if d and d.get('kind') == kind and d['metadata']['name'] == name)
    expr = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == path)
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def jq(prog, data):
    proc = subprocess.run(['jq', '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root = parser.parse_args().root
    bad = 0
    charts = sorted(c for c in os.listdir(os.path.join(root, 'helm')) if os.path.isfile(os.path.join(root, 'helm', c, 'Chart.yaml')))
    rendered = {chart: render(root, chart) for chart in charts}

    concatenations = 0
    for chart, text in rendered.items():
        for match in CONCAT.finditer(text):
            concatenations += 1
            line_start = text.rfind('\n', 0, match.start()) + 1
            window = text[max(line_start, match.start() - 160):match.end() + 8]
            if not GUARDED.search(window):
                bad += 1
                print(f'FAIL {chart}: an unguarded version label: ...{text[max(0, match.start() - 80):match.end() + 40].strip()}...')
    if concatenations == 0:
        bad += 1
        print('FAIL no `"v" +` concatenation found in any chart: the scan is not looking')

    docs = list(yaml.safe_load_all(rendered['marketplace-page']))
    subtitle = expression(docs, 'PageHeader', 'marketplace-detail-page-header', 'subtitle')
    for version, want in [('1.4.2', 'v1.4.2'), ('', None), (None, None)]:
        got = jq(subtitle, {'category': 'uncategorized', 'version': version})
        parts = [part.strip() for part in got.split('·')]
        if (want and want not in parts) or (not want and any(re.fullmatch(r'v\??', part) for part in parts)):
            bad += 1
            print(f'FAIL marketplace header subtitle, version {version!r}: {got!r}')

    rows = next(t['expression'] for d in docs if d and d.get('kind') == 'Descriptions'
                for t in d['spec'].get('widgetDataTemplate', []) if 'yes · v' in t['expression'] or '" · v"' in t['expression'])
    rows = rows.strip().removeprefix('${').removesuffix('}').strip()
    for version, want in [('1.4.2', 'yes · v1.4.2'), ('', 'yes'), (None, 'yes')]:
        got = next(r['value'] for r in jq(rows, {'installed': True, 'installedVersion': version, 'version': '1.4.2'}) if r['label'] == 'Installed')
        if got != want:
            bad += 1
            print(f'FAIL marketplace "Installed" row, installed version {version!r}: got {got!r}, want {want!r}')

    print(f'version labels: {concatenations} concatenation(s) in {len(charts)} chart(s), {bad} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
