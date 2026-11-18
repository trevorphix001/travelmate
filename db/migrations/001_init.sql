-- TravelMate AI initial schema for Supabase Postgres.
-- Run once in the Supabase SQL editor (or: python -m scripts.migrate).

create extension if not exists pgcrypto;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now()
);

create table if not exists public.plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  request jsonb not null,
  plan jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists plans_user_id_idx on public.plans(user_id);

create table if not exists public.plan_edits (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.plans(id) on delete cascade,
  instruction text not null,
  diff jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists plan_edits_plan_id_idx on public.plan_edits(plan_id);

create table if not exists public.cost_table (
  version text not null,
  city_tier smallint not null,          -- 1..3; 0 = applies to every tier
  category text not null,               -- stay | meal | intercity | duration_factor | local_transport | entry_fee
  item text not null,
  unit text not null,
  min_inr integer not null,
  typical_inr integer not null,
  max_inr integer not null,
  source_note text not null,
  source_url text not null check (source_url <> ''),
  checked_on date not null,
  primary key (version, city_tier, category, item)
);

create table if not exists public.cities (
  name text primary key,
  state text not null,
  tier smallint not null check (tier between 1 and 3),
  lat double precision not null,
  lon double precision not null
);
create index if not exists cities_name_lower_idx on public.cities (lower(name) text_pattern_ops);

create table if not exists public.api_cache (
  key text primary key,                 -- sha256 hex
  provider text not null,
  response jsonb not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists api_cache_expires_at_idx on public.api_cache(expires_at);

create table if not exists public.node_metrics (
  id bigserial primary key,
  run_id text not null,
  node text not null,
  provider text,
  model text,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  ms integer not null default 0,
  ok boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists node_metrics_run_id_idx on public.node_metrics(run_id);

create table if not exists public.eval_cases (
  case_id text primary key,
  request jsonb not null,
  expect_infeasible boolean not null default false
);

create table if not exists public.eval_results (
  id bigserial primary key,
  case_id text not null references public.eval_cases(case_id) on delete cascade,
  system text not null check (system in ('graph', 'baseline')),
  metrics jsonb not null,
  created_at timestamptz not null default now()
);

-- Row Level Security: owners only. The FastAPI backend connects as the postgres role
-- (bypasses RLS) and enforces owner checks in SQL as well.
alter table public.profiles enable row level security;
alter table public.plans enable row level security;
alter table public.plan_edits enable row level security;

drop policy if exists profiles_owner on public.profiles;
create policy profiles_owner on public.profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

drop policy if exists plans_owner on public.plans;
create policy plans_owner on public.plans
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists plan_edits_owner on public.plan_edits;
create policy plan_edits_owner on public.plan_edits
  for all using (exists (select 1 from public.plans p where p.id = plan_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.plans p where p.id = plan_id and p.user_id = auth.uid()));

-- Internal tables: no policies, so anon/authenticated roles cannot read them via the REST API.
alter table public.api_cache enable row level security;
alter table public.node_metrics enable row level security;
alter table public.eval_cases enable row level security;
alter table public.eval_results enable row level security;
alter table public.cost_table enable row level security;
alter table public.cities enable row level security;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'display_name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
