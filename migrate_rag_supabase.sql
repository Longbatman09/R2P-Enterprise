-- migrate_rag_supabase.sql
── Data-Math-connect RAG row-level storage ──────────────────────
── Run with: supabase db push   or   psql "$DATABASE_URL"
────────────────────────────────────────────────────────────────

-- Extensions
create extension if not exists vector;

-- table: textbooks
create table if not exists textbooks (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  namespace   text not null unique,
  file_name   text,
  page_count  integer default 0,
  chunk_count integer default 0,
  chunk_size  integer default 400,
  chunk_overlap integer default 60,
  embed_model text default 'nvidia/nv-embedqa-e5-v5',
  status      text default 'ready',   -- ready | ingesting | failed
  error_msg   text,
  owner_id    uuid,                   -- align with auth.users
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);

create index if not exists idx_textbooks_namespace
  on textbooks(namespace);

-- table: chats
create table if not exists chats (
  id            uuid primary key default gen_random_uuid(),
  textbook_id   uuid references textbooks(id) on delete cascade,
  owner_id      uuid,
  title         text,
  created_at    timestamptz default now(),
  last_message  timestamptz,
  last_message_at timestamptz default now()
);

create index if not exists idx_chats_owner
  on chats(owner_id, last_message_at desc);

-- table: chat_messages
create table if not exists chat_messages (
  id          uuid primary key default gen_random_uuid(),
  chat_id     uuid references chats(id) on delete cascade,
  owner_id    uuid,
  role        text not null check (role in ('user','assistant','system')),
  content     text not null,
  meta        jsonb default '{}',
  created_at  timestamptz default now()
);

create index if not exists idx_messages_chat
  on chat_messages(chat_id, created_at asc);

-- table: chat_sources
create table if not exists chat_sources (
  id        uuid primary key default gen_random_uuid(),
  message_id uuid references chat_messages(id) on delete cascade,
  page      integer,
  snippet   text,
  score     real,
  created_at timestamptz default now()
);

-- RPC: query_top_chunks
-- Uses HNSW index on vector column for fast ANN.
-- Call: select rag_query_top_chunks('namespace', '[0.1,0.2,...]', 4);
create or replace function rag_query_top_chunks(
  p_namespace text,
  p_embedding vector(1024),   -- match your embed dimension
  p_top_k     int default 4,
  p_match_threshold float default 0.5
)
returns table (
  id          text,
  text        text,
  page        int,
  score       float
)
language sql stable
as $$
  select
    id,
    metadata->>'text'   as text,
    (metadata->>'page')::int as page,
    score
  from items
  where namespace = p_namespace
    and score >= p_match_threshold
  order by score desc
  limit p_top_k;
$$;

-- helper RPC: append_message
create or replace function rag_append_message(
  p_chat_id   uuid,
  p_owner_id  uuid,
  p_role      text,
  p_content   text,
  p_meta      jsonb default '{}'
)
returns uuid
language plpgsql
as $$
declare
  v_id uuid;
begin
  insert into chat_messages (chat_id, owner_id, role, content, meta)
  values (p_chat_id, p_owner_id, p_role, p_content, p_meta)
  returning id into v_id;

  update chats
     set last_message    = p_content,
         last_message_at = now()
   where id = p_chat_id;

  return v_id;
end;
$$;

-- trigger: refresh updated_at
create or replace function refresh_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists tr_textbooks_updated on textbooks;
create trigger tr_textbooks_updated
  before update on textbooks
  for each row execute function refresh_updated_at();
