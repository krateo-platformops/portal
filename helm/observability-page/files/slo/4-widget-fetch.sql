SELECT
    sum(Value)                                                    AS fetches,
    sumIf(Value, Attributes['krateo.sli.error'] = 'true')         AS failed,
    if(fetches = 0, NULL, failed / fetches)                       AS failure_share
FROM otel_metrics_sum
WHERE MetricName = 'traces.span.metrics.calls'
  AND ServiceName = 'krateo-frontend'
  AND Attributes['span.name'] = 'krateo.widget.fetch'
  AND TimeUnix >= {start:DateTime64(9)} AND TimeUnix < {end:DateTime64(9)}
