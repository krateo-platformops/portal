SELECT
    d.service                                                     AS sli_service,
    sum(d.calls)                                                  AS calls,
    sumIf(d.calls, d.sli_error)                                   AS sli_errors,
    if(calls = 0, NULL, 1 - sli_errors / calls)                   AS availability,
    if(calls = 0, NULL, (1 - 0.999) * calls)                      AS error_budget_calls,
    if(calls = 0, NULL, 1 - sli_errors / ((1 - 0.999) * calls))   AS budget_remaining,
    min(d.day)                                                    AS first_day,
    max(d.day)                                                    AS last_day,
    uniqExact(d.day)                                              AS days_with_data
FROM krateo_slo_daily AS d FINAL
WHERE empty(d.bounds)
  AND d.service IN ('snowplow', 'krateo-frontend')
  AND d.day >= toDate({start:DateTime64(9)}, 'UTC') AND d.day < toDate({end:DateTime64(9)}, 'UTC')
GROUP BY d.service
ORDER BY d.service
