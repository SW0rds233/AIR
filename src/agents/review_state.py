"""Select the last review's findings without resurrecting historical issues."""


def latest_review_issues(rows):
    recorded = [row for row in rows if row.get("dedupe_key")]
    if recorded:
        latest = {}
        for row in sorted(recorded, key=lambda item: item.get("_submitted_at", "")):
            latest[row["dedupe_key"]] = row
        return [row for row in latest.values() if row.get("status") in {"open", "not_rechecked"}]
    stamped = [row for row in rows if row.get('_task_id') and row.get('_submitted_at')]
    if not stamped:
        return rows  # Legacy/unpersisted contexts retain their supplied scope.
    task_id = max(stamped, key=lambda row: row['_submitted_at'])['_task_id']
    return [row for row in rows if row.get('_task_id') == task_id]
