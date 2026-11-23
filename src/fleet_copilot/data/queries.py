"""All SQL for the data layer. Repositories import these constants; no SQL lives in logic.

Templates with {placeholders} are structural (aggregate / join / where fragments) and are
filled by the repository with validated, non-user values; row values always bind via params.
"""

# Columns exposed by the history query, mapped from metric name to snapshot column.
HISTORY_METRICS: dict[str, str] = {
    "disk_free_pct": "disk_free_pct",
    "ram_used_pct": "ram_used_pct",
    "battery_cycle_count": "battery_cycle_count",
    "battery_pct": "battery_pct",
}

# --- schema (DDL) --------------------------------------------------------- #
SCHEMA = """
CREATE TABLE snapshots (
    snapshot_id          INTEGER PRIMARY KEY,
    device_id            TEXT NOT NULL,
    company_id           TEXT NOT NULL,
    employee_id          TEXT,
    collected_at         TEXT NOT NULL,
    agent_version        TEXT,
    platform             TEXT,
    product_name         TEXT,
    os_version           TEXT,
    os_major             INTEGER,
    model_name           TEXT,
    processor            TEXT,
    total_memory_bytes   INTEGER,
    used_memory_bytes    INTEGER,
    ram_used_pct         REAL,
    disk_size_bytes      INTEGER,
    disk_available_bytes INTEGER,
    disk_free_pct        REAL,
    battery_present      INTEGER,       -- NULL = no battery block at all
    battery_pct          INTEGER,
    battery_cycle_count  INTEGER,
    battery_condition    TEXT,
    battery_full_charge  INTEGER,
    has_network          INTEGER,
    raw_json             TEXT NOT NULL
);

CREATE TABLE compliance_results (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
    device_id     TEXT NOT NULL,
    company_id    TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    check_id      TEXT NOT NULL,
    status        TEXT NOT NULL,
    severity      TEXT NOT NULL
);

CREATE TABLE installed_software (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
    device_id     TEXT NOT NULL,
    company_id    TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    name          TEXT NOT NULL,
    version       TEXT,
    publisher     TEXT
);

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    trace_id     TEXT,
    company_id   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    tool         TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    decision     TEXT NOT NULL,         -- proposed | approved | executed | denied | refused
    result_json  TEXT
);

CREATE INDEX idx_snap_company        ON snapshots(company_id);
CREATE INDEX idx_snap_company_device ON snapshots(company_id, device_id);
CREATE INDEX idx_snap_collected      ON snapshots(collected_at);
CREATE INDEX idx_comp_company        ON compliance_results(company_id);
CREATE INDEX idx_comp_check          ON compliance_results(check_id);
CREATE INDEX idx_sw_company          ON installed_software(company_id);
CREATE INDEX idx_audit_company       ON audit_log(company_id);
"""

# --- inserts (ingest) ----------------------------------------------------- #
# The snapshots insert is assembled from the flattened record's keys in ingest.py.
INSERT_COMPLIANCE = (
    "INSERT INTO compliance_results "
    "(snapshot_id, device_id, company_id, collected_at, check_id, status, severity) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
INSERT_SOFTWARE = (
    "INSERT INTO installed_software "
    "(snapshot_id, device_id, company_id, collected_at, name, version, publisher) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

# --- tenant metadata ------------------------------------------------------ #
COMPANIES = "SELECT DISTINCT company_id FROM snapshots ORDER BY company_id"
DEVICE_IDS = "SELECT DISTINCT device_id FROM snapshots WHERE company_id = ? ORDER BY device_id"
DEVICE_IN_TENANT = "SELECT 1 FROM snapshots WHERE company_id = ? AND device_id = ? LIMIT 1"
EMPLOYEE_IN_TENANT = "SELECT 1 FROM snapshots WHERE company_id = ? AND employee_id = ? LIMIT 1"

# Latest snapshot per device, within a tenant (:cid).
LATEST_CTE = """
WITH latest AS (
    SELECT device_id, MAX(collected_at) AS mx
    FROM snapshots WHERE company_id = :cid GROUP BY device_id
)
"""

# --- snapshot reads ------------------------------------------------------- #
FLEET_SUMMARY = LATEST_CTE + """
SELECT s.device_id, s.employee_id, s.product_name, s.os_version, s.model_name,
       s.disk_free_pct, s.ram_used_pct, s.battery_cycle_count, s.battery_condition,
       s.collected_at, s.snapshot_id
FROM snapshots s JOIN latest l
  ON s.device_id = l.device_id AND s.collected_at = l.mx
WHERE s.company_id = :cid
ORDER BY s.device_id
"""

DISK_LATEST = LATEST_CTE + """
SELECT s.device_id, s.disk_free_pct AS value, s.disk_available_bytes,
       s.collected_at, s.snapshot_id
FROM snapshots s JOIN latest l
  ON s.device_id = l.device_id AND s.collected_at = l.mx
WHERE s.company_id = :cid AND s.disk_free_pct IS NOT NULL
  AND s.disk_free_pct <= :thr
ORDER BY s.disk_free_pct
"""

# {agg} is AVG(disk_free_pct) (mean) or MAX(disk_free_pct) (sustained).
DISK_AGG = """
SELECT device_id, {agg} AS value, MIN(disk_free_pct) AS min_free_pct,
       MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
FROM snapshots
WHERE company_id = :cid AND disk_free_pct IS NOT NULL
GROUP BY device_id
HAVING {agg} <= :thr
ORDER BY value
"""

MEMORY = """
SELECT device_id, AVG(ram_used_pct) AS value, MAX(ram_used_pct) AS max_used_pct,
       MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
FROM snapshots
WHERE company_id = :cid AND ram_used_pct IS NOT NULL
GROUP BY device_id
HAVING AVG(ram_used_pct) >= :thr
ORDER BY value DESC
"""

# {having} is the OR-joined clause set built from min_cycle_count / conditions.
BATTERY = """
SELECT device_id,
       MAX(battery_cycle_count) AS max_cycle_count,
       MAX(battery_condition) AS battery_condition,
       MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
FROM snapshots
WHERE company_id = :cid AND battery_cycle_count IS NOT NULL
GROUP BY device_id
HAVING {having}
ORDER BY max_cycle_count DESC
"""

# {placeholders} is the comma-joined :c0,:c1,... for the condition IN (...) list.
BATTERY_CONDITION_CLAUSE = "MAX(CASE WHEN battery_condition IN ({placeholders}) THEN 1 ELSE 0 END) = 1"

OS_OLDER_THAN = LATEST_CTE + """
SELECT s.device_id, s.product_name, s.os_version, s.os_major AS value,
       s.collected_at, s.snapshot_id
FROM snapshots s JOIN latest l
  ON s.device_id = l.device_id AND s.collected_at = l.mx
WHERE s.company_id = :cid AND s.product_name = :pn
  AND s.os_major IS NOT NULL AND s.os_major < :floor
ORDER BY s.os_major, s.device_id
"""

# {column} is a validated HISTORY_METRICS value.
DEVICE_HISTORY = """
SELECT collected_at, {column} AS value, snapshot_id
FROM snapshots
WHERE company_id = ? AND device_id = ? AND {column} IS NOT NULL
ORDER BY collected_at
"""

# --- compliance reads ----------------------------------------------------- #
# {latest_join} is empty or COMPLIANCE_LATEST_JOIN; {where} is the AND-joined filter set.
COMPLIANCE = """
SELECT DISTINCT c.device_id, c.check_id, c.status, c.severity,
       c.collected_at, c.snapshot_id
FROM compliance_results c {latest_join}
WHERE {where}
ORDER BY c.device_id, c.check_id
"""
COMPLIANCE_LATEST_JOIN = (
    "JOIN (SELECT device_id, MAX(collected_at) AS mx FROM compliance_results "
    "WHERE company_id = :cid GROUP BY device_id) l "
    "ON c.device_id = l.device_id AND c.collected_at = l.mx"
)
COMPLIANCE_SEQUENCE = """
SELECT collected_at, status, snapshot_id
FROM compliance_results
WHERE company_id = ? AND device_id = ? AND check_id = ?
ORDER BY collected_at
"""

# --- software reads ------------------------------------------------------- #
# {where} is the AND-joined filter set.
SOFTWARE = """
SELECT DISTINCT sw.device_id, sw.name, sw.version, sw.publisher,
       sw.collected_at, sw.snapshot_id
FROM installed_software sw
WHERE {where}
ORDER BY sw.device_id, sw.name
"""

# --- audit ---------------------------------------------------------------- #
INSERT_AUDIT = """
INSERT INTO audit_log (ts, trace_id, company_id, actor, tool, params_json, decision, result_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
AUDIT_ENTRIES = "SELECT * FROM audit_log WHERE company_id = ? ORDER BY id"
