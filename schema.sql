-- Supabase schema for Telegram Manager
-- Run this in Supabase SQL Editor

-- Admins: anyone who /start the bot
create table if not exists admins (
    user_id bigint primary key,
    username text,
    first_name text,
    lang text default 'en',
    registered_at timestamptz default now()
);

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
