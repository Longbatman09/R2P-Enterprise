-- ============================================================
-- R2P Supabase Schema Fixes
-- Run everything in this file in Supabase → SQL Editor → Run
-- ============================================================

-- -----------------------------------------------------------
-- Fix 1: profiles RLS — allow service_role to UPSERT student rows
-- The app populates profiles.csv for every student found in PDFs
-- via the service-role key. The existing RLS policies use
-- auth.uid() which is NULL under service_role, so all writes
-- were silently blocked.
-- -----------------------------------------------------------
DROP POLICY IF EXISTS "service_role_all_profiles" ON public.profiles;

CREATE POLICY "service_role_all_profiles"
  ON public.profiles
  FOR ALL
  USING (auth.role() = 'service_role');

-- Keep the existing user-self policies; they are additive.

-- -----------------------------------------------------------
-- Fix 2: pipeline_runs — student_id may be NULL for batch jobs
-- that don't target a single student. Remove the empty-string
-- default and allow NULL so we don't pollute the table.
-- -----------------------------------------------------------
ALTER TABLE public.pipeline_runs
  ALTER COLUMN student_id DROP DEFAULT,
  ALTER COLUMN student_id DROP NOT NULL;

-- Optional: make exam_name NULLable too for batch runs
-- (uncomment if you want fully unreserved pipeline_runs)
-- ALTER TABLE public.pipeline_runs
--   ALTER COLUMN exam_name DROP NOT NULL;

-- -----------------------------------------------------------
-- Fix 3: Add updated_at trigger helper (idempotent)
-- Keeps student_reports / student_roster updated_at fresh on UPDATE
-- -----------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_student_reports_updated ON public.student_reports;
CREATE TRIGGER trg_student_reports_updated
  BEFORE UPDATE ON public.student_reports
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS trg_student_roster_updated ON public.student_roster;
CREATE TRIGGER trg_student_roster_updated
  BEFORE UPDATE ON public.student_roster
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS trg_profiles_updated ON public.profiles;
CREATE TRIGGER trg_profiles_updated
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- -----------------------------------------------------------
-- Fix 4: Verify existing constraints are correct
-- (No changes needed — just a sanity check query)
-- -----------------------------------------------------------
-- SELECT conname, contype, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conrelid = 'student_reports'::regclass;
