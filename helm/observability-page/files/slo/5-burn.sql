SELECT
    ServiceName                                                   AS sli_service,
    sum(Value)                                                    AS calls,
    sumIf(Value, Attributes['krateo.sli.error'] = 'true')         AS sli_errors,
    if(calls = 0, NULL, (sli_errors / calls) / (1 - 0.999))       AS burn_rate
FROM otel_metrics_sum
WHERE MetricName = 'traces.span.metrics.calls'
  AND ServiceName IN ('snowplow', 'krateo-frontend')
  AND TimeUnix >= {start:DateTime64(9)} AND TimeUnix < {end:DateTime64(9)}
GROUP BY ServiceName
ORDER BY ServiceName
