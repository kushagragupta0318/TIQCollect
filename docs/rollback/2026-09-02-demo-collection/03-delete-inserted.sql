-- Removes every row demo_collect_to_target.py inserted on 2026-09-02 (both runs).
-- Payments first: visits are their foreign key.

DELETE FROM payments WHERE created_at >= '2026-09-02 12:00:00+00';
DELETE FROM visits   WHERE created_at >= '2026-09-02 12:00:00+00';
