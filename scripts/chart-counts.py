#!/usr/bin/env python3
"""
chart-counts — the checks every count chart shares (charts mockup, CHART-3), loaded by the page tests
(test-incidents-list, test-alerts-overview, test-audit-page, test-platform-review, test-service-desk).

A count per bucket is a BarChart, not a smoothed line:
  - whole-number ticks: scale.y from 0 to the smallest multiple of 4 at or above the largest count, at
    least 4, with 5 ticks (0..4, 0..8, ...), so one incident is never a bump on a 0.2 axis;
  - the unit on the y axis (axis.y.title);
  - the newest bucket, the one still filling, is slot "partial" ("so far"), and no other is marked;
  - the colours the mockup gives the series (a zero is the BarChart's grey hairline, never a colour).
Not a test of its own: `problems(...)` returns what is wrong, and the page test reports it.
"""
import math


def domain_max(values):
    top = max([v for v in values if v is not None] or [0])
    return 4 if top <= 4 else math.ceil(top / 4) * 4


def problems(label, wd, *, title, colors, resolve_rows=None, rows_of=None):
    """`wd` is the BarChart's resolved widgetData. `resolve_rows(rows)` resolves the same widget over an
    output holding these trend rows (to try other counts); `rows_of(rows)` is unused when absent."""
    f = []

    def expect(what, got, want):
        if got != want:
            f.append(f'{label}: {what}: got {got!r}, want {want!r}')

    expect('a bar chart, not a smoothed line', [k for k in ('shapeField', 'area', 'point') if k in wd], [])
    expect('the slot field', wd.get('slotField'), 'slot')
    expect('the colours', wd.get('colorMap'), colors)
    expect('the unit on the y axis', (wd.get('axis') or {}).get('y', {}).get('title'), title)
    data = wd.get('data') or []
    if data:
        newest = data[-1]['t']
        expect('only the newest bucket is partial', sorted({str(d['t']) for d in data if d.get('slot') == 'partial'}), [str(newest)])
        expect('every other bucket is whole', [d.get('slot') for d in data if d['t'] != newest], [None] * len([d for d in data if d['t'] != newest]))
    y = (wd.get('scale') or {}).get('y') or {}
    expect('whole-number ticks: 0 to a multiple of 4 at or above the largest count, 5 ticks',
           (y.get('domainMin'), y.get('domainMax'), y.get('tickCount')), (0, domain_max([d.get('v') for d in data]), 5))
    if resolve_rows:
        series = sorted({d['series'] for d in data}) or sorted(colors)
        for top, want in ((0, 4), (1, 4), (4, 4), (5, 8), (9, 12), (16, 16)):
            rows = [{'t': 1795000000 + 3600 * i, 'series': s, 'v': (top if (i == 1 and s == series[-1]) else 0)}
                    for i in range(3) for s in series]
            got = resolve_rows(rows)
            expect(f'largest count {top}: the top of the axis', ((got.get('scale') or {}).get('y') or {}).get('domainMax'), want)
            expect(f'largest count {top}: the newest bucket partial', [d.get('slot') for d in got['data']],
                   [None] * (2 * len(series)) + ['partial'] * len(series))
    return f
