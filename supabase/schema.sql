-- ============================================================
-- R2P-Enterprise — Supabase Schema
-- Paste this whole file into Supabase → SQL Editor → Run once.
-- Idempotent: safe to run multiple times.
-- ============================================================

-- ------------------------------------------------------------
-- schools — one row per tenant (school)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.schools (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL UNIQUE,
  contact_email TEXT,
  plan          TEXT NOT NULL DEFAULT 'basic',      -- basic | pro | enterprise
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Existing installs: enforce unique school names going forward
CREATE UNIQUE INDEX IF NOT EXISTS schools_name_key ON public.schools (name);

ALTER TABLE public.schools ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- students — one row per student (linked to a school)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.students (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id  UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  grade      TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- school_api_keys — integration keys handed to school apps (SDK).
--   Key value is shown ONCE at creation; only the SHA-256 hash
--   and a display prefix are stored.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.school_api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id    UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  key_hash     TEXT NOT NULL UNIQUE,                -- sha256 of sk_...
  key_prefix   TEXT NOT NULL,                       -- sk_abc123… (display only)
  name         TEXT NOT NULL DEFAULT 'default',
  created_by   UUID,                                -- auth.users.id of creator
  scopes       TEXT[] NOT NULL DEFAULT '{reports,rag,chat}',
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ
);

ALTER TABLE public.school_api_keys ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- invoices — Stripe invoice records per school
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoices (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id         UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  amount_cents      INTEGER NOT NULL DEFAULT 0,
  currency          TEXT NOT NULL DEFAULT 'usd',
  status            TEXT NOT NULL DEFAULT 'draft',  -- draft | open | paid | void
  stripe_invoice_id TEXT,
  pdf_url           TEXT,
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- report_logs — audit log of uploaded/analyzed reports
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.report_logs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id    UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  student_id   TEXT,
  student_name TEXT NOT NULL DEFAULT '',
  file_name    TEXT NOT NULL DEFAULT '',
  pages        INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.report_logs ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- profiles — one row per auth user (id = auth.users.id).
-- WARNING: the legacy R2P app created profiles with a DIFFERENT
-- shape (uid, full_name, student_id, ...). That structure is
-- incompatible with the new enterprise backend, so we DROP and
-- recreate it with the canonical shape. Idempotent: safe to
-- re-run.
-- ------------------------------------------------------------
DROP TABLE IF EXISTS public.profiles CASCADE;

CREATE TABLE public.profiles (
  id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email      TEXT,
  username   TEXT,
  school_id  UUID REFERENCES public.schools(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Backfill profiles for users that existed before this schema/trigger
-- was installed (e.g. accounts created manually in the Supabase dashboard).
INSERT INTO public.profiles (id, email)
SELECT id, email FROM auth.users
ON CONFLICT (id) DO NOTHING;

-- Auto-create a profile when a new auth user is added manually
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, username)
  VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data->>'username')
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- ------------------------------------------------------------
-- user_api_keys — per-user third-party service keys (NVIDIA,
-- Pinecone, Gemini…) used by the dashboard. Distinct from
-- school_api_keys above.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_api_keys (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  service       TEXT NOT NULL,
  encrypted_key TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, service)
);

ALTER TABLE public.user_api_keys ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- Helper: the school_id of the currently logged-in user
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.current_user_school_id()
RETURNS UUID LANGUAGE sql STABLE AS $$
  SELECT school_id FROM public.profiles WHERE id = auth.uid();
$$;

-- ============================================================
-- ROW LEVEL SECURITY POLICIES
-- ============================================================

-- ---- schools: users see/manage only their own school ----
DROP POLICY IF EXISTS "schools_service_role_all" ON public.schools;
CREATE POLICY "schools_service_role_all"
  ON public.schools FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "schools_owner_all" ON public.schools;
CREATE POLICY "schools_owner_all"
  ON public.schools FOR ALL
  USING (id = public.current_user_school_id());

-- ---- students: scoped to the user's school ----
DROP POLICY IF EXISTS "students_service_role_all" ON public.students;
CREATE POLICY "students_service_role_all"
  ON public.students FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "students_school_all" ON public.students;
CREATE POLICY "students_school_all"
  ON public.students FOR ALL
  USING (school_id = public.current_user_school_id());

-- ---- school_api_keys: scoped to the user's school ----
DROP POLICY IF EXISTS "school_keys_service_role_all" ON public.school_api_keys;
CREATE POLICY "school_keys_service_role_all"
  ON public.school_api_keys FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "school_keys_school_all" ON public.school_api_keys;
CREATE POLICY "school_keys_school_all"
  ON public.school_api_keys FOR ALL
  USING (school_id = public.current_user_school_id());

-- ---- invoices: scoped to the user's school ----
DROP POLICY IF EXISTS "invoices_service_role_all" ON public.invoices;
CREATE POLICY "invoices_service_role_all"
  ON public.invoices FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "invoices_school_select" ON public.invoices;
CREATE POLICY "invoices_school_select"
  ON public.invoices FOR SELECT
  USING (school_id = public.current_user_school_id());

-- ---- report_logs: scoped to the user's school ----
DROP POLICY IF EXISTS "report_logs_service_role_all" ON public.report_logs;
CREATE POLICY "report_logs_service_role_all"
  ON public.report_logs FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "report_logs_school_all" ON public.report_logs;
CREATE POLICY "report_logs_school_all"
  ON public.report_logs FOR ALL
  USING (school_id = public.current_user_school_id());

-- ---- profiles: user manages own row ----
DROP POLICY IF EXISTS "profiles_service_role_all" ON public.profiles;
CREATE POLICY "profiles_service_role_all"
  ON public.profiles FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "profiles_self_all" ON public.profiles;
CREATE POLICY "profiles_self_all"
  ON public.profiles FOR ALL
  USING (id = auth.uid());

-- ---- user_api_keys: owner only ----
DROP POLICY IF EXISTS "user_keys_service_role_all" ON public.user_api_keys;
CREATE POLICY "user_keys_service_role_all"
  ON public.user_api_keys FOR ALL
  USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "user_keys_owner_all" ON public.user_api_keys;
CREATE POLICY "user_keys_owner_all"
  ON public.user_api_keys FOR ALL
  USING (auth.uid() = user_id);

-- ============================================================
-- updated_at trigger helper
-- ============================================================
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_invoices_updated ON public.invoices;
CREATE TRIGGER trg_invoices_updated
  BEFORE UPDATE ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS trg_profiles_updated ON public.profiles;
CREATE TRIGGER trg_profiles_updated
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
