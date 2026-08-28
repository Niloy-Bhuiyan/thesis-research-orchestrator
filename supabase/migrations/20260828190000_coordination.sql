-- Coordination layer between the Vercel dashboard and the local daemon.
--
-- This database holds only what remote control needs: liveness, a command
-- queue, approvals, and a thin event feed. Research artifacts, logs, code and
-- credentials stay on the researcher's machine. Nothing here is authoritative:
-- the local SQLite database remains the source of truth for experiments.
--
-- The daemon never accepts an inbound connection. It polls this table outbound,
-- which is what lets the laptop stay behind NAT with no open port.

create extension if not exists "pgcrypto";

-- One row per machine running a daemon.
create table if not exists runners (
    id                  text primary key,
    owner               uuid not null references auth.users (id) on delete cascade,
    label               text,
    status              text not null default 'stopped',
    project_id          text,
    mode                text,
    active_experiment   text,
    pause_reason        text,
    last_heartbeat      timestamptz,
    created_at          timestamptz not null default now()
);

-- Commands issued from the dashboard, drained by the daemon.
create table if not exists commands (
    id            uuid primary key default gen_random_uuid(),
    owner         uuid not null references auth.users (id) on delete cascade,
    runner_id     text not null,
    type          text not null,
    params        jsonb not null default '{}'::jsonb,
    status        text not null default 'pending',
    result        text,
    issued_by     text not null default 'dashboard',
    created_at    timestamptz not null default now(),
    acked_at      timestamptz,
    completed_at  timestamptz,
    -- Expiry exists so a command queued while the laptop was off does not
    -- execute unexpectedly hours later.
    expires_at    timestamptz not null default now() + interval '1 hour',
    constraint commands_status_check
        check (status in ('pending','acked','running','done','failed','rejected','expired')),
    constraint commands_type_check
        check (type in ('pause','resume','stop','set_mode','approve','reject'))
);

-- Proposals mirrored up so approvals can happen away from the laptop.
create table if not exists proposals (
    id                uuid primary key default gen_random_uuid(),
    owner             uuid not null references auth.users (id) on delete cascade,
    runner_id         text not null,
    local_id          text not null,
    experiment_id     text not null,
    kind              text,
    summary           text not null,
    detail            text,
    target_field      text,
    scientific_impact text,
    status            text not null default 'pending',
    created_at        timestamptz not null default now(),
    decided_at        timestamptz,
    unique (runner_id, local_id)
);

-- Thin event feed. Metadata only, deliberately not full logs.
create table if not exists events (
    id            bigserial primary key,
    owner         uuid not null references auth.users (id) on delete cascade,
    runner_id     text not null,
    level         text not null default 'info',
    kind          text not null,
    message       text not null,
    experiment_id text,
    occurred_at   timestamptz not null default now()
);

create index if not exists commands_pending_idx
    on commands (runner_id, status, created_at);
create index if not exists events_recent_idx on events (runner_id, id desc);
create index if not exists proposals_status_idx on proposals (runner_id, status);

-- Row level security: every table is owner-scoped, so one account can never
-- see or command another account's runner.
alter table runners   enable row level security;
alter table commands  enable row level security;
alter table proposals enable row level security;
alter table events    enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array['runners','commands','proposals','events'] loop
        execute format('drop policy if exists %I on %I', t || '_owner_all', t);
        execute format(
            'create policy %I on %I for all to authenticated
               using (owner = auth.uid()) with check (owner = auth.uid())',
            t || '_owner_all', t
        );
    end loop;
end $$;

-- Expire stale commands rather than letting them run late.
create or replace function expire_stale_commands() returns void
language sql security definer set search_path = public as $$
    update commands set status = 'expired'
     where status = 'pending' and expires_at < now();
$$;
