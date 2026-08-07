# R2P-Enterprise — One-Shot Deploy Guide

## Local (Mac) — fastest path

```bash
git clone ...
cp mcp_backend/.env.local mcp_backend/.env   # fill in real values
docker compose up --build
# → http://localhost:8100/health   200 OK
# → http://localhost:8100/api/auth/signup
```

## Render — one-click Web Service

1. Connect repo `https://github.com/<you>/R2P-Enterprise`
2. Runtime: **Docker**
3. Dockerfile path: `mcp_backend/Dockerfile`
4. Start command: *(leave blank — Docker CMD handles it)*
5. Health check path: `/health`
6. Add these env vars (from your Supabase / Pinecone / NVIDIA dashboard):

| Key | Required |
|---|---|
| `SUPABASE_URL` | yes |
| `SUPABASE_ANON_KEY` | yes |
| `SUPABASE_SERVICE_ROLE_KEY` | yes |
| `JWT_SECRET` | yes — any random 32+ char string |
| `SECRET_KEY` | yes — any random 32+ char string |
| `CORS_ORIGINS` | yes — comma-separated frontend URLs |
| `PINECONE_API_KEY` | if using RAG |
| `PINECONE_HOST` | if using RAG |
| `PINECONE_INDEX` | if using RAG |
| `NVIDIA_API_KEY` | if using RAG |
| `GEMINI_API_KEY` | if using report analysis |
| `LLMWHISPERER_API_KEY` | if using vision |
| `LOG_LEVEL` | no — defaults to `INFO` |

7. Hit **Deploy**. First build takes ~5 min. Watch logs for `Application startup complete`.

## Supabase — run this SQL once

(Same SQL as before — kept here for convenience.)

```sql
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT, username TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, username)
  VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data->>'username');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

CREATE TABLE IF NOT EXISTS public.user_api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,
  encrypted_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, service)
);
ALTER TABLE public.user_api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users manage own keys"
  ON public.user_api_keys FOR ALL
  USING (auth.uid() = user_id);
```

## School integration — one-liner

See `r2p_school_sdk/` for the pip-installable SDK.
