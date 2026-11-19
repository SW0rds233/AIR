"""Select the last review's findings without resurrecting historical issues."""


def latest_review_issues(rows):
    stamped = [row for row in rows if row.get('_task_id') and row.get('_submitted_at')]
    if not stamped:
        return rows  # Legacy/unpersisted contexts retain their supplied scope.
    task_id = max(stamped, key=lambda row: row['_submitted_at'])['_task_id']
    return [row for row in rows if row.get('_task_id') == task_id]
