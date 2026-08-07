-- PolyWeather Supabase Disk IO mitigation indexes.
-- Run this in the Supabase SQL Editor for an existing production project.

drop index if exists public.idx_profiles_email;
create index if not exists idx_profiles_email
  on public.profiles(email)
  include (id);

drop index if exists public.idx_profiles_id_lookup;
create index if not exists idx_profiles_id_lookup
  on public.profiles(id)
  include (email, created_at);

analyze public.profiles;