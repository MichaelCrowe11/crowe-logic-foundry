-- Replace "Crowe Logic extension for VS Code" with "Crowe Logic Workstation"
-- in plan highlights, per the no-tech-stack feedback rule and the rebrand
-- finalized in PR #6. Affects personal and byok only (the two launch plans
-- that named VS Code in the first bullet).
--
-- Idempotent: re-running is a no-op when the offending string is already gone.

UPDATE plans
SET highlights = jsonb_set(
        highlights,
        '{0}',
        '"Crowe Logic Workstation"'::jsonb
    )
WHERE id IN ('personal', 'byok')
  AND highlights->>0 = 'Crowe Logic extension for VS Code';
