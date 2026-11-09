#!/usr/bin/env python3
"""
snowplow-path — render a RESTAction stage `path` exactly as snowplow does, with snowplow's own code.

WHY NOT jq. A stage path is a jq program, but snowplow does not dial what jq prints. Since
snowplow#1123 (internal/pathtemplate) the program's STRING LITERALS are the author's template and
every other value spliced into it is escaped: as one path segment before the template's first `?`,
as one query value after it (EscapeQueryValue: `%`, space, `&`, `=`, `+`, … become %XX). A value
that is already `%20`-encoded is therefore encoded AGAIN. Plain jq shows none of that, so a model
that renders the path with jq passed while portal 1.8.99's Traces tab sent ClickHouse
`INTERVAL 24%20HOUR%20AND%20ServiceName…` and showed "No rows" (portal#387 follow-up). The rule
is a rewrite of the jq AST, too subtle to port; this runs the real one.

HOW. scripts/snowplow-pathrender/main.go is overlaid into a snowplow checkout as
go/snowplow/zz_portal_pathrender_main.go (`go build -overlay`, so the checkout is never written to:
the renderer is an internal package, importable only from inside the module) and built once per
run; renders go through one long-lived process.

WHICH SNOWPLOW. SNOWPLOW_SRC names a checkout (its root holds go/snowplow/go.mod). Otherwise the
tag the installer PINS (`snowplow` in installer main's chart/files/component-pins.yaml, read by
lint-pinned-crds.py; SNOWPLOW_VERSION overrides it) is cloned into a temporary directory — the
renderer the cluster runs, as --pinned does for the widget CRDs. Needs `git` and `go`. When it
cannot be built the caller fails: a model without the encoding step is the model that missed this.
"""
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, 'snowplow-pathrender', 'main.go')
SNOWPLOW_GIT = 'https://github.com/krateo-platformops/snowplow'
MODULE = os.path.join('go', 'snowplow')
# A file at the module root, which every checkout has: an overlaid file in a directory that does not
# exist is not found (`stat …: directory not found`). Built by file name, so it is compiled alone and
# the root package is not touched.
OVERLAY_AS = 'zz_portal_pathrender_main.go'


class Refused(Exception):
    """snowplow renders the path but will not dial it (pathtemplate.RefusedError, or a jq error, or
    no path at all). setup.go:227-255 skips the call in every one of these cases."""


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, **kw)


def _source():
    """(checkout root, how it was chosen)."""
    if os.environ.get('SNOWPLOW_SRC'):
        root = os.environ['SNOWPLOW_SRC']
        desc = _run(['git', '-C', root, 'describe', '--tags', '--always', '--dirty'], timeout=30).stdout.strip()
        return root, f'{desc or "an unversioned tree"} (SNOWPLOW_SRC={root})'
    if os.environ.get('SNOWPLOW_VERSION'):
        version, why = os.environ['SNOWPLOW_VERSION'], 'SNOWPLOW_VERSION'
    else:
        version, why = _load('pinned', 'lint-pinned-crds.py').pinned_component_version('snowplow')
        if not version:
            raise SystemExit(f'snowplow-path: cannot determine the pinned snowplow version: {why}')
    root = tempfile.mkdtemp(prefix='snowplow-src-')
    atexit.register(shutil.rmtree, root, True)
    last = ''
    for attempt in range(3):
        out = _run(['git', 'clone', '--quiet', '--depth', '1', '--branch', version, SNOWPLOW_GIT, root],
                   timeout=300)
        if out.returncode == 0:
            return root, f'{version} ({why})'
        last = out.stderr.strip()[-400:]
        shutil.rmtree(root, True)
        time.sleep(3 * (attempt + 1))
    raise SystemExit(f'snowplow-path: could not clone {SNOWPLOW_GIT} at {version}: {last}')


class Renderer:
    def __init__(self):
        if not shutil.which('go'):
            raise SystemExit('snowplow-path: `go` is not on PATH; it builds snowplow\'s path renderer '
                             '(see the docstring of scripts/snowplow-path.py)')
        root, self.source = _source()
        # Real path: go matches overlay keys against the directory it resolves, and on macOS the
        # temporary directory is under /var, a symlink to /private/var.
        module = os.path.realpath(os.path.join(root, MODULE))
        if not os.path.isfile(os.path.join(module, 'go.mod')):
            raise SystemExit(f'snowplow-path: {module} holds no go.mod; SNOWPLOW_SRC names the repo root')
        work = tempfile.mkdtemp(prefix='snowplow-pathrender-')
        atexit.register(shutil.rmtree, work, True)
        overlay = os.path.join(work, 'overlay.json')
        with open(overlay, 'w', encoding='utf-8') as fh:
            json.dump({'Replace': {os.path.join(module, OVERLAY_AS): DRIVER}}, fh)
        binary = os.path.join(work, 'pathrender')
        out = _run(['go', 'build', '-overlay', overlay, '-o', binary, './' + OVERLAY_AS],
                   cwd=module, timeout=900)
        if out.returncode:
            raise SystemExit(f'snowplow-path: building the renderer against {self.source} failed:\n'
                             f'{out.stderr.strip()[-1500:]}')
        self._proc = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        atexit.register(self._proc.kill)
        # The build is only useful if it is the #1123 renderer: a value spliced into the query is
        # escaped once, and an already-%-encoded one is escaped again.
        probe = self.render('${ "/?q=" + .v }', {'v': 'a b%20c'})
        if probe != '/?q=a%20b%2520c':
            raise SystemExit(f'snowplow-path: {self.source} does not escape spliced query values '
                             f'(rendered {probe!r}); this harness models snowplow#1123')

    def render(self, path, data):
        """The path snowplow dials for `data` (the dict, or one iterator element). Raises Refused
        when snowplow would not dial it."""
        self._proc.stdin.write(json.dumps({'path': path or '', 'data': data}) + '\n')
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError('snowplow-path: the renderer exited')
        res = json.loads(line)
        if 'error' in res:
            raise Refused(res['error'])
        if res['path'] == '' and (path or '') != '':
            raise Refused('the path template rendered no path (#1123)')
        return res['path']


_RENDERER = None


def renderer():
    """The process-wide Renderer, built on first use."""
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = Renderer()
    return _RENDERER


def render(path, data):
    return renderer().render(path, data)
