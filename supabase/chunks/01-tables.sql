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
