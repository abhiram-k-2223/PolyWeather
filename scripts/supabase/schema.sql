-- PolyWeather minimal identity schema
-- Run in Supabase SQL editor.

create extension if not exists pgcrypto;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  telegram_user_id bigint,
  telegram_username text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_profiles_email
  on public.profiles(email)
  include (id);

create index if not exists idx_profiles_id_lookup
  on public.profiles(id)
  include (email, created_at);

create or replace function public.sync_profile_from_auth()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, coalesce(new.email, ''))
  on conflict (id) do update
  set email = excluded.email,
      updated_at = now();
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_polyweather on auth.users;
create trigger on_auth_user_created_polyweather
  after insert on auth.users
  for each row execute function public.sync_profile_from_auth();