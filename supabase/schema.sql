-- Emergency Language Bridge - Supabase schema
--
-- Storage only. Call signalling never touches this database; that is LiveKit's
-- job. Everything here is either configured before an emergency (profiles,
-- trusted_contacts) or written during/after one (calls, transcripts, events,
-- metrics, reports, report_links, deliveries).
--
-- Apply with:  supabase db push       (or paste into the SQL editor)

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- profiles
-- One row per person who might need to call. Kept separate from auth.users so
-- the demo works without configuring Supabase Auth.
create table if not exists profiles (
  id                 uuid primary key default gen_random_uuid(),
  display_name       text not null default '',
  preferred_language text not null default 'en',
  phone_e164         text,
  created_at         timestamptz not null default now()
);

-- --------------------------------------------------------- trusted_contacts
-- Configured BEFORE any emergency, and stored server-side on purpose: the
-- whole point is that this survives losing the phone.
create table if not exists trusted_contacts (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references profiles(id) on delete cascade,
  name                text not null,
  relationship        text not null default '',
  phone_e164          text,
  email               text,
  locale              text not null default 'es',
  priority            int  not null default 100,
  active              boolean not null default true,

  -- Per-contact consent about WHEN to be told
  notify_early        boolean not null default true,
  notify_final        boolean not null default true,

  created_at          timestamptz not null default now(),
  -- Email is the only delivery channel, so an address is what makes a contact
  -- reachable at all. phone_e164 is kept for identification, not for delivery.
  constraint contact_reachable check (email is not null)
);
create index if not exists trusted_contacts_user_idx on trusted_contacts(user_id, active, priority);
create index if not exists trusted_contacts_phone_idx on trusted_contacts(phone_e164);

-- ------------------------------------------------------------------- calls
create table if not exists calls (
  id             uuid primary key,
  room           text not null,
  user_id        uuid references profiles(id) on delete set null,
  caller_lang    text not null default 'en',
  operator_lang  text not null default 'es',
  started_at     timestamptz not null default now(),
  ended_at       timestamptz,
  duration_s     int,
  turns          int not null default 0,
  fallback_turns int not null default 0,
  status         text not null default 'active'   -- active | closed | failed
);
create index if not exists calls_started_idx on calls(started_at desc);

-- -------------------------------------------------------------- transcripts
create table if not exists transcripts (
  id          bigserial primary key,
  call_id     uuid not null references calls(id) on delete cascade,
  t_offset_ms int  not null,
  speaker     text not null,          -- caller | operator
  lang        text not null,
  kind        text not null,          -- source | translation | fallback
  text        text not null,
  hits        jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists transcripts_call_idx on transcripts(call_id, t_offset_ms);

-- ------------------------------------------------------------------ events
create table if not exists events (
  id         bigserial primary key,
  call_id    uuid references calls(id) on delete cascade,
  type       text not null,
  payload    jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists events_call_idx on events(call_id, created_at);

-- ----------------------------------------------------------------- metrics
create table if not exists metrics (
  id         bigserial primary key,
  call_id    uuid references calls(id) on delete cascade,
  direction  text not null,
  metric     text not null,           -- translate_ms | e2e_ms
  value      numeric not null,
  fallback   boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists metrics_call_idx on metrics(call_id, metric);

-- ----------------------------------------------------------------- reports
-- Both scopes are rendered and stored. The family text is generated
-- separately, not derived by stripping the operator text, so a rendering bug
-- cannot leak clinical detail into it.
create table if not exists reports (
  id             text primary key,               -- elb_xxxxxxxx
  call_id        uuid references calls(id) on delete cascade,
  is_final       boolean not null default false,
  operator_txt   text not null default '',
  family_txt     text not null default '',
  extraction     jsonb not null default '{}'::jsonb,
  critical_flags jsonb not null default '[]'::jsonb,
  lat            double precision,
  lon            double precision,
  caller_lang    text,
  operator_lang  text,
  revoked_at     timestamptz,                    -- kill every link at once
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists reports_call_idx on reports(call_id);

-- ------------------------------------------------------------- report_links
-- One row per (report, contact) link handed out. The token itself is NOT
-- stored - it is a stateless HMAC. This table exists for audit and revocation.
create table if not exists report_links (
  id          bigserial primary key,
  report_id   text not null references reports(id) on delete cascade,
  contact_id  text not null,
  scope       text not null check (scope in ('family','operator')),
  kind        text not null default 'final',     -- early | final
  expires_at  timestamptz not null,
  revoked_at  timestamptz,
  opened_at   timestamptz,
  open_count  int not null default 0,
  created_at  timestamptz not null default now()
);
create index if not exists report_links_lookup on report_links(report_id, contact_id);

-- --------------------------------------------------------------- deliveries
-- Audit trail of every outbound message, and the idempotency guard that keeps
-- a retry from double-notifying a frightened relative.
create table if not exists deliveries (
  id            bigserial primary key,
  report_id     text not null,
  contact_id    text not null,
  kind          text not null,                   -- early | final
  channel       text not null,                   -- email | none
  status        text not null,                   -- sent | failed | skipped
  provider_id   text,
  error         text,
  attempt       int not null default 1,
  created_at    timestamptz not null default now(),
  unique (report_id, contact_id, kind, channel)
);
create index if not exists deliveries_report_idx on deliveries(report_id);

-- ------------------------------------------------------------------- views
create or replace view call_overview as
select
  c.id,
  c.room,
  c.caller_lang,
  c.operator_lang,
  c.started_at,
  c.ended_at,
  c.duration_s,
  c.turns,
  c.fallback_turns,
  c.status,
  p.display_name as caller_name,
  r.id           as report_id,
  r.extraction->'emergency_type'->>'value' as emergency_type,
  r.extraction->'location'->>'value'       as location,
  r.critical_flags
from calls c
left join profiles p on p.id = c.user_id
left join reports  r on r.call_id = c.id and r.is_final;

-- --------------------------------------------------------------------- RLS
-- The agent and the Next.js API routes use the service-role key and bypass
-- RLS. These policies exist so that adding a real end-user client later is
-- safe by default rather than open by default.
alter table profiles         enable row level security;
alter table trusted_contacts enable row level security;
alter table calls            enable row level security;
alter table transcripts      enable row level security;
alter table events           enable row level security;
alter table metrics          enable row level security;
alter table reports          enable row level security;
alter table report_links     enable row level security;
alter table deliveries       enable row level security;

do $$
begin
  if not exists (select 1 from pg_policies where tablename='profiles' and policyname='own profile') then
    create policy "own profile" on profiles
      for all using (id = auth.uid()) with check (id = auth.uid());
  end if;
  if not exists (select 1 from pg_policies where tablename='trusted_contacts' and policyname='own contacts') then
    create policy "own contacts" on trusted_contacts
      for all using (user_id = auth.uid()) with check (user_id = auth.uid());
  end if;
  if not exists (select 1 from pg_policies where tablename='calls' and policyname='own calls') then
    create policy "own calls" on calls for select using (user_id = auth.uid());
  end if;
end $$;

-- -------------------------------------------------------------- demo seed
-- The caller profile only. Its id is the one the APK ships with by default, so
-- contacts added from the app attach to it. Add the contacts from the app.
insert into profiles (id, display_name, preferred_language, phone_e164)
values ('11111111-1111-1111-1111-111111111111', 'Amara Okafor', 'en', '+573001112233')
on conflict (id) do nothing;


-- --------------------------------------------------------------- migrations
-- `create table if not exists` above leaves an EXISTING database untouched, so
-- a database created before email became the only channel keeps three columns
-- that nothing reads any more. Dropping them is safe and optional; the code
-- neither writes nor selects them.
alter table trusted_contacts drop column if exists push_token;
alter table trusted_contacts drop column if exists whatsapp_opt_in_at;
alter table trusted_contacts drop column if exists whatsapp_opt_in_ref;

-- The reachability rule changed from "phone OR email OR push" to "email".
-- Recreated rather than altered because a check constraint cannot be modified
-- in place. Contacts already stored without an email would violate it, so they
-- are reported instead of silently blocking the migration.
do $$
declare unreachable int;
begin
  select count(*) into unreachable from trusted_contacts where email is null;
  if unreachable > 0 then
    raise warning 'ELB: % trusted contact(s) have no email and can no longer be notified. Add an address before enforcing the constraint.', unreachable;
  else
    alter table trusted_contacts drop constraint if exists contact_reachable;
    alter table trusted_contacts add constraint contact_reachable check (email is not null);
  end if;
end $$;
