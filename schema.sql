-- Supabase schema for Telegram Manager
-- Run this in Supabase SQL Editor

-- Admins: anyone who /start the bot
create table if not exists admins (
    user_id bigint primary key,
    username text,
    first_name text,
    lang text default 'en',
    is_vip boolean default false,
    vip_gifted_by bigint,
    vip_gifted_at timestamptz,
    registered_at timestamptz default now()
);

alter table admins add column if not exists is_vip boolean default false;
alter table admins add column if not exists vip_gifted_by bigint;
alter table admins add column if not exists vip_gifted_at timestamptz;

-- Managed accounts (Telethon sessions stored as StringSession)
create table if not exists accounts (
    id bigserial primary key,
    admin_id bigint not null references admins(user_id) on delete cascade,
    phone text not null,
    alias text not null,
    first_name text default '',
    last_name text default '',
    username text,
    user_id bigint,
    is_2fa boolean default false,
    device_preset text default 'random',
    session_string text not null,
    created_at timestamptz default now(),
    unique(admin_id, phone),
    unique(admin_id, alias)
);

-- Index for anti-double check (lookup by managed user_id)
create index if not exists idx_accounts_user_id on accounts(user_id);

-- Broadcast lists
create table if not exists broadcast_lists (
    id bigserial primary key,
    admin_id bigint not null references admins(user_id) on delete cascade,
    name text not null,
    targets text[] not null default '{}',
    created_at timestamptz default now(),
    unique(admin_id, name)
);


-- Saved broadcast messages
create table if not exists saved_messages (
    id bigserial primary key,
    admin_id bigint not null references admins(user_id) on delete cascade,
    name text not null,
    text text not null,
    has_media boolean default false,
    created_at timestamptz default now(),
    unique(admin_id, name)
);

-- Auto-reply columns (optional; detected at runtime via _account_optional_columns)
alter table accounts add column if not exists auto_reply_enabled boolean default false;
alter table accounts add column if not exists auto_reply_text text default '';
alter table accounts add column if not exists connected_ip text default '';
alter table accounts add column if not exists last_connected_at timestamptz;
alter table accounts add column if not exists broadcast_status text default '';
alter table accounts add column if not exists broadcast_job_id text;
alter table accounts add column if not exists broadcast_updated_at timestamptz;

create table if not exists broadcast_jobs (
    job_id text primary key,
    admin_id bigint not null references admins(user_id) on delete cascade,
    list_name text not null,
    status text not null default 'running',
    text_mode text not null default 'single',
    message_html text not null default '',
    saved_texts text[] not null default '{}',
    has_media boolean default false,
    media_blob_base64 text,
    media_filename text,
    group_delay_min double precision default 0,
    group_delay_max double precision default 0,
    round_delay_min double precision default 0,
    round_delay_max double precision default 0,
    round_num integer default 0,
    started_at timestamptz default now(),
    updated_at timestamptz default now(),
    completed_at timestamptz
);

create index if not exists idx_broadcast_jobs_admin_status on broadcast_jobs(admin_id, status);

create table if not exists broadcast_job_items (
    job_id text not null references broadcast_jobs(job_id) on delete cascade,
    admin_id bigint not null references admins(user_id) on delete cascade,
    account_phone text not null,
    target text not null,
    status text not null default 'pending',
    last_error text,
    attempts integer not null default 0,
    last_attempted_at timestamptz,
    primary key (job_id, account_phone, target)
);

create index if not exists idx_broadcast_job_items_job_status on broadcast_job_items(job_id, status);
create index if not exists idx_broadcast_job_items_admin_status on broadcast_job_items(admin_id, status);

create table if not exists account_locks (
    admin_id bigint not null references admins(user_id) on delete cascade,
    phone text not null,
    holder text not null,
    purpose text default '',
    acquired_at timestamptz default now(),
    heartbeat_at timestamptz default now(),
    ttl_seconds integer not null default 60,
    primary key (admin_id, phone)
);

create index if not exists idx_account_locks_heartbeat on account_locks(heartbeat_at);

create or replace function acquire_account_lock(
    p_admin_id bigint,
    p_phone text,
    p_holder text,
    p_purpose text,
    p_ttl_seconds integer
) returns boolean
language plpgsql
as $$
begin
    insert into account_locks (admin_id, phone, holder, purpose, acquired_at, heartbeat_at, ttl_seconds)
    values (p_admin_id, p_phone, p_holder, p_purpose, now(), now(), p_ttl_seconds)
    on conflict (admin_id, phone) do update set
        holder = excluded.holder,
        purpose = excluded.purpose,
        acquired_at = now(),
        heartbeat_at = now(),
        ttl_seconds = excluded.ttl_seconds
    where account_locks.heartbeat_at + make_interval(secs => account_locks.ttl_seconds) < now();

    return exists (
        select 1 from account_locks
        where admin_id = p_admin_id
          and phone = p_phone
          and holder = p_holder
    );
end;
$$;

create or replace function heartbeat_account_lock(
    p_admin_id bigint,
    p_phone text,
    p_holder text
) returns boolean
language plpgsql
as $$
begin
    update account_locks
    set heartbeat_at = now()
    where admin_id = p_admin_id
      and phone = p_phone
      and holder = p_holder;

    return found;
end;
$$;

create or replace function release_account_lock(
    p_admin_id bigint,
    p_phone text,
    p_holder text
) returns boolean
language plpgsql
as $$
begin
    delete from account_locks
    where admin_id = p_admin_id
      and phone = p_phone
      and holder = p_holder;

    return found;
end;
$$;

create or replace function cleanup_stale_account_locks()
returns integer
language plpgsql
as $$
declare
    deleted_count integer;
begin
    delete from account_locks
    where heartbeat_at + make_interval(secs => ttl_seconds) < now();

    get diagnostics deleted_count = row_count;
    return deleted_count;
end;
$$;
