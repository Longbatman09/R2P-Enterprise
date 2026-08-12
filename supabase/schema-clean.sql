CREATE TABLE IF NOT EXISTS public.schools (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL UNIQUE,
  contact_email TEXT,
  plan          TEXT NOT NULL DEFAULT 'basic',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS schools_name_key ON public.schools (name);
ALTER TABLE public.schools ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.students (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id  UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  grade      TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.school_api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id    UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  key_hash     TEXT NOT NULL UNIQUE,
  key_prefix   TEXT NOT NULL,
  name         TEXT NOT NULL DEFAULT 'default',
  created_by   UUID,
  scopes       TEXT[] NOT NULL DEFAULT '{reports,rag,chat}',
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ
);
ALTER TABLE public.school_api_keys ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.invoices (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id         UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  amount_cents      INTEGER NOT NULL DEFAULT 0,
  currency          TEXT NOT NULL DEFAULT 'usd',
  status            TEXT NOT NULL DEFAULT 'draft',
  stripe_invoice_id TEXT,
  pdf_url           TEXT,
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
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
INSERT INTO public.profiles (id, email)
SELECT id, email FROM auth.users
ON CONFLICT (id) DO NOTHING;
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
CREATE OR REPLACE FUNCTION public.current_user_school_id()
RETURNS UUID LANGUAGE sql STABLE AS $$
  SELECT school_id FROM public.profiles WHERE id = auth.uid();
$$;
DROP POLICY IF EXISTS "schools_service_role_all" ON public.schools;
CREATE POLICY "schools_service_role_all"
  ON public.schools FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "schools_owner_all" ON public.schools;
CREATE POLICY "schools_owner_all"
  ON public.schools FOR ALL
  USING (id = public.current_user_school_id());
DROP POLICY IF EXISTS "students_service_role_all" ON public.students;
CREATE POLICY "students_service_role_all"
  ON public.students FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "students_school_all" ON public.students;
CREATE POLICY "students_school_all"
  ON public.students FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "school_keys_service_role_all" ON public.school_api_keys;
CREATE POLICY "school_keys_service_role_all"
  ON public.school_api_keys FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "school_keys_school_all" ON public.school_api_keys;
CREATE POLICY "school_keys_school_all"
  ON public.school_api_keys FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "invoices_service_role_all" ON public.invoices;
CREATE POLICY "invoices_service_role_all"
  ON public.invoices FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "invoices_school_select" ON public.invoices;
CREATE POLICY "invoices_school_select"
  ON public.invoices FOR SELECT
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "report_logs_service_role_all" ON public.report_logs;
CREATE POLICY "report_logs_service_role_all"
  ON public.report_logs FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "report_logs_school_all" ON public.report_logs;
CREATE POLICY "report_logs_school_all"
  ON public.report_logs FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "profiles_service_role_all" ON public.profiles;
CREATE POLICY "profiles_service_role_all"
  ON public.profiles FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "profiles_self_all" ON public.profiles;
CREATE POLICY "profiles_self_all"
  ON public.profiles FOR ALL
  USING (id = auth.uid());
DROP POLICY IF EXISTS "user_keys_service_role_all" ON public.user_api_keys;
CREATE POLICY "user_keys_service_role_all"
  ON public.user_api_keys FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "user_keys_owner_all" ON public.user_api_keys;
CREATE POLICY "user_keys_owner_all"
  ON public.user_api_keys FOR ALL
  USING (auth.uid() = user_id);
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
