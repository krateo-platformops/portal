WITH
    agg AS (
        SELECT ExplicitBounds AS b, sumForEach(BucketCounts) AS bc
        FROM otel_metrics_histogram
        WHERE MetricName = 'traces.span.metrics.duration'
          AND ServiceName = 'snowplow'
          AND TimeUnix >= {start:DateTime64(9)} AND TimeUnix < {end:DateTime64(9)}
        GROUP BY ExplicitBounds
    )
SELECT
    b                                                                          AS bounds_ms,
    arraySum(bc)                                                               AS n,
    arrayFirstIndex(x -> x >= 0.50 * n, arrayCumSum(bc))                       AS i50,
    arrayFirstIndex(x -> x >= 0.95 * n, arrayCumSum(bc))                       AS i95,
    if(i50 <= 1, 0, b[i50 - 1])                                                AS p50_lower_ms,
    if(i50 > length(b), inf, b[i50])                                           AS p50_upper_ms,
    if(i95 <= 1, 0, b[i95 - 1])                                                AS p95_lower_ms,
    if(i95 > length(b), inf, b[i95])                                           AS p95_upper_ms,
    p95_upper_ms <= 500                                                        AS p95_within_target
FROM agg
WHERE n > 0
