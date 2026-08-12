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
