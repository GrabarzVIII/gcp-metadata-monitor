-- Latest observations for the operator dashboard.
SELECT rule_id, rule_name, project_id, service_type, resource_name, resource_url,
       check_type, status, value, comparison_operator, threshold_value,
       owner, checked_at, window_start, window_end
FROM `PROJECT_ID.DATASET_ID.monitoring_results`
WHERE checked_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
QUALIFY ROW_NUMBER() OVER (PARTITION BY rule_id ORDER BY checked_at DESC) = 1;

-- Last successful check per rule. This is not the last successful source-process execution.
SELECT rule_id, MAX(checked_at) AS last_successful_check
FROM `PROJECT_ID.DATASET_ID.monitoring_results`
WHERE status = 'OK'
GROUP BY rule_id;

-- Failed or incomplete runs that need operator attention.
SELECT * FROM `PROJECT_ID.DATASET_ID.monitoring_runs`
WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND status != 'COMPLETED'
ORDER BY started_at DESC;
