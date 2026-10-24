-- ============================================================
-- LexAI - Supabase tables used by the frontend:
--   PART A  "files"            the Library's file list
--   PART B  "collections"      named groups of files
--   PART C  "file_collections" which file is in which collection
--   PART D  "profiles"         one row per user (onboarding + disclaimer flag)
--   PART E  how to check it worked (read-only SELECTs)
--
-- Paste this whole file once into the Supabase SQL Editor and run it.
-- It is written so that running it a second time does no harm.
--
-- IMPORTANT - tables that already exist:
-- every "create table if not exists" below SKIPS a table that is already there
-- and leaves its columns, types and foreign keys exactly as they are. The
-- policies and grants are still applied. If any of these tables existed before
-- you ran this file, run the checks in PART E (E5 and E6) to compare the real
-- columns and foreign keys with what this file expects.
--
-- ============================================================
-- PART A - "files"
--
-- What the frontend does with this table (src/libraryApi.js):
--   recordFile()     inserts  owner_id, filename, file_type, chunk_count, status
--                    and reads the inserted row back (.select().single())
--   listFiles()      selects * where owner_id = <user>, ordered by created_at desc
--   findFileByName() selects * where owner_id = <user> and filename = <name>
--   deleteFile()     deletes where owner_id = <user> and filename = <name>
--   Library.jsx      uses id (row key, collection links) and shows filename,
--                    chunk_count and status
-- So the table needs exactly: id, owner_id, filename, file_type, chunk_count,
-- status, created_at. The app never updates a row, so there is no UPDATE access.
--
-- If an older "file_collections" table has a foreign key to files(id) without
-- "on delete cascade", deleting a file that is still in a collection will fail
-- with a foreign-key error (check E6 in PART E).
-- ============================================================


-- 1. The table.
--    "if not exists" means: if a "files" table is already there, this step is
--    skipped and the existing table is left exactly as it is (its columns are
--    NOT changed to match this definition).
--
--    owner_id: the code calls this column "owner_id" (not "user_id"), so that
--    is the name used. It points at the signed-in Supabase user (auth.users),
--    defaults to the current user (auth.uid()) if the insert leaves it out, and
--    the row is removed automatically if that user account is deleted.
create table if not exists public.files (
  id          uuid        primary key default gen_random_uuid(),
  owner_id    uuid        not null default auth.uid()
                          references auth.users (id) on delete cascade,
  filename    text        not null,
  file_type   text,
  chunk_count integer,
  status      text        not null default 'ready',
  created_at  timestamptz not null default now()
);

-- Speeds up "this user's files, newest first" (listFiles) and the
-- per-filename lookups. Skipped if it already exists.
create index if not exists files_owner_created_idx
  on public.files (owner_id, created_at desc);


-- 2. Turn on row level security.
--    With RLS on, nobody can read or change any row through the API unless a
--    policy below explicitly allows it.
alter table public.files enable row level security;


-- 3. Policies: a signed-in user can see, add and delete only their own rows.
--    auth.uid() is the id of the user whose access token made the request.
--    The "drop policy if exists" lines make re-running this file safe.

-- See only your own rows.
drop policy if exists "files_select_own" on public.files;
create policy "files_select_own" on public.files
  for select to authenticated
  using (owner_id = (select auth.uid()));

-- Add rows only with your own id as owner_id (you cannot insert a row for someone else).
drop policy if exists "files_insert_own" on public.files;
create policy "files_insert_own" on public.files
  for insert to authenticated
  with check (owner_id = (select auth.uid()));

-- Delete only your own rows.
drop policy if exists "files_delete_own" on public.files;
create policy "files_delete_own" on public.files
  for delete to authenticated
  using (owner_id = (select auth.uid()));


-- 4. Grants: let the "authenticated" role reach the table at all.
--    This project has "automatically expose new tables" turned OFF, so without
--    these lines the API cannot see the table even with the policies above.
--    Grants decide WHICH operations are possible; the policies decide WHICH ROWS.
--    Nothing is granted to "anon" (signed-out visitors get no access).
grant usage on schema public to authenticated;
grant select, insert, delete on table public.files to authenticated;


-- ============================================================
-- PART B - "collections"
--
-- What the frontend does with this table (src/libraryApi.js):
--   createCollection()  inserts owner_id, name, colour and reads the row back
--   listCollections()   selects * where owner_id = <user>, ordered by created_at asc
--   deleteCollection()  deletes where id = <collection id>
--   Library.jsx         uses id and name (colour is stored but never displayed)
-- So the table needs exactly: id, owner_id, name, colour, created_at.
-- The app never updates a collection, so there is no UPDATE access.
-- Guess: id is uuid (the code only passes it around, never inspects it).
-- ============================================================

-- B1. The table. owner_id points at the user and the row goes away if the
--     user account is deleted. colour defaults to the same value the code uses.
create table if not exists public.collections (
  id          uuid        primary key default gen_random_uuid(),
  owner_id    uuid        not null default auth.uid()
                          references auth.users (id) on delete cascade,
  name        text        not null,
  colour      text        not null default '#1a1a1a',
  created_at  timestamptz not null default now()
);

-- Speeds up "this user's collections, oldest first" (listCollections).
create index if not exists collections_owner_created_idx
  on public.collections (owner_id, created_at);

-- B2. Turn on row level security (no access unless a policy allows it).
alter table public.collections enable row level security;

-- B3. Policies: a signed-in user can see, add and delete only their own collections.
drop policy if exists "collections_select_own" on public.collections;
create policy "collections_select_own" on public.collections
  for select to authenticated
  using (owner_id = (select auth.uid()));

drop policy if exists "collections_insert_own" on public.collections;
create policy "collections_insert_own" on public.collections
  for insert to authenticated
  with check (owner_id = (select auth.uid()));

drop policy if exists "collections_delete_own" on public.collections;
create policy "collections_delete_own" on public.collections
  for delete to authenticated
  using (owner_id = (select auth.uid()));

-- B4. Grants: only the operations the app performs. Nothing to "anon".
grant select, insert, delete on table public.collections to authenticated;


-- ============================================================
-- PART C - "file_collections"
--
-- What the frontend does with this table (src/libraryApi.js):
--   addFileToCollection()      inserts file_id, collection_id
--   removeFileFromCollection() deletes where file_id = .. and collection_id = ..
--   getFileIdsInCollection()   selects file_id where collection_id = ..
--   getCollectionsForFile()    selects collection_id where file_id = ..
-- So the table needs exactly: file_id, collection_id. It has no owner column:
-- a link belongs to whoever owns the file and the collection it joins.
-- The app never updates a link, so there is no UPDATE access.
-- Guess: the pair (file_id, collection_id) is the primary key, so the same file
-- cannot be added to the same collection twice (Library.jsx only adds a link
-- when the file is not already in that collection).
-- ============================================================

-- C1. The table. Deleting a file or a collection automatically deletes its
--     links ("on delete cascade"), so neither delete is blocked by a link.
create table if not exists public.file_collections (
  file_id       uuid not null references public.files (id) on delete cascade,
  collection_id uuid not null references public.collections (id) on delete cascade,
  primary key (file_id, collection_id)
);

-- Speeds up "which files are in this collection" (the primary key already
-- covers lookups by file_id).
create index if not exists file_collections_collection_idx
  on public.file_collections (collection_id);

-- C2. Turn on row level security.
alter table public.file_collections enable row level security;

-- C3. Policies. A link counts as yours only when BOTH the file and the
--     collection are yours. Checking both on insert stops anyone linking their
--     file to someone else's collection, or the other way round.
drop policy if exists "file_collections_select_own" on public.file_collections;
create policy "file_collections_select_own" on public.file_collections
  for select to authenticated
  using (
    exists (select 1 from public.files f
            where f.id = file_id and f.owner_id = (select auth.uid()))
    and exists (select 1 from public.collections c
                where c.id = collection_id and c.owner_id = (select auth.uid()))
  );

drop policy if exists "file_collections_insert_own" on public.file_collections;
create policy "file_collections_insert_own" on public.file_collections
  for insert to authenticated
  with check (
    exists (select 1 from public.files f
            where f.id = file_id and f.owner_id = (select auth.uid()))
    and exists (select 1 from public.collections c
                where c.id = collection_id and c.owner_id = (select auth.uid()))
  );

drop policy if exists "file_collections_delete_own" on public.file_collections;
create policy "file_collections_delete_own" on public.file_collections
  for delete to authenticated
  using (
    exists (select 1 from public.files f
            where f.id = file_id and f.owner_id = (select auth.uid()))
    and exists (select 1 from public.collections c
                where c.id = collection_id and c.owner_id = (select auth.uid()))
  );

-- C4. Grants: only the operations the app performs. Nothing to "anon".
grant select, insert, delete on table public.file_collections to authenticated;


-- ============================================================
-- PART D - "profiles"
--
-- What the frontend does with this table:
--   App.jsx loadProfile()  selects * where id = <user> (.maybeSingle(): no row is fine)
--   App.jsx                reads preferred_name and accepted_warning
--   Onboarding.jsx         upserts id, full_name, preferred_name, birthday, purpose
--   Warning.jsx            updates accepted_warning = true where id = <user>
-- So the table needs exactly: id, full_name, preferred_name, birthday, purpose,
-- accepted_warning. The app never deletes a profile, so there is no DELETE access.
--
-- No trigger on auth.users: the app does NOT expect a profile row after first
-- sign-in. When loadProfile finds no row it shows the Onboarding screen, and
-- Onboarding creates the row itself with an upsert.
-- ============================================================

-- D1. The table. id IS the user's id (one profile per user) and the profile
--     goes away if the user account is deleted. accepted_warning starts false,
--     so a new user sees the disclaimer screen once. birthday is a date (the
--     form uses an input of type "date" and sends null when left empty).
create table if not exists public.profiles (
  id               uuid    primary key default auth.uid()
                           references auth.users (id) on delete cascade,
  full_name        text,
  preferred_name   text,
  birthday         date,
  purpose          text,
  accepted_warning boolean not null default false
);

-- D2. Turn on row level security.
alter table public.profiles enable row level security;

-- D3. Policies: a signed-in user can read, create and change only their own profile.
--     The upsert in Onboarding.jsx needs all three: insert for a new row, update
--     when the row already exists, and select (Postgres checks it on upsert).
drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

drop policy if exists "profiles_insert_own" on public.profiles;
create policy "profiles_insert_own" on public.profiles
  for insert to authenticated
  with check (id = (select auth.uid()));

-- "using" picks which rows can be changed; "with check" stops a row being
-- changed into someone else's (for example by setting a different id).
drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own" on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- D4. Grants: only the operations the app performs. Nothing to "anon".
grant select, insert, update on table public.profiles to authenticated;


-- ============================================================
-- PART E - How to check it worked.
-- Read-only queries: run them one at a time in the SQL Editor.
-- ============================================================

-- E1. The four tables exist (expect 4 rows).
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('files', 'collections', 'file_collections', 'profiles')
order by table_name;

-- E2. Row level security is on (expect rowsecurity = true on all 4).
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
  and tablename in ('files', 'collections', 'file_collections', 'profiles')
order by tablename;

-- E3. The policies (expect 3 per table, 12 rows, all for role authenticated).
select tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
  and tablename in ('files', 'collections', 'file_collections', 'profiles')
order by tablename, cmd;

-- E4. The grants. Expect "authenticated" only, never "anon":
--     files, collections, file_collections: SELECT, INSERT, DELETE
--     profiles: SELECT, INSERT, UPDATE
--     If "anon" shows up, it was granted earlier by something else.
select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('files', 'collections', 'file_collections', 'profiles')
  and grantee in ('authenticated', 'anon')
order by table_name, grantee, privilege_type;

-- E5. The real columns of each table, to compare with this file if a table
--     already existed before you ran it.
select table_name, column_name, data_type, is_nullable, column_default
from information_schema.columns
where table_schema = 'public'
  and table_name in ('files', 'collections', 'file_collections', 'profiles')
order by table_name, ordinal_position;

-- E6. The foreign keys and what happens on delete. Expect delete_action = c
--     (cascade) on every row; a or r means a delete will be blocked instead.
select conrelid::regclass  as on_table,
       conname             as constraint_name,
       confrelid::regclass as points_to,
       confdeltype         as delete_action
from pg_constraint
where contype = 'f'
  and conrelid in ('public.files'::regclass, 'public.collections'::regclass,
                   'public.file_collections'::regclass, 'public.profiles'::regclass)
order by on_table, constraint_name;
