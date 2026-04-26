-- Reverse 008: restore the "Crowe Logic extension for VS Code" copy.
-- Only useful if you need to roll back the no-tech-stack fix; expected to
-- stay un-run.

UPDATE plans
SET highlights = jsonb_set(
        highlights,
        '{0}',
        '"Crowe Logic extension for VS Code"'::jsonb
    )
WHERE id IN ('personal', 'byok')
  AND highlights->>0 = 'Crowe Logic Workstation';
