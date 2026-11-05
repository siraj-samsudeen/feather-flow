-- #3894: current Featherbase platform schema plus one installed app-owned schema.
-- #4338: after the Person release (featherbase 0101 + 0102): `user` is `person`, service
-- accounts have their own catalog-declared Table, and the identity tables exist physically.
create schema featherbase;
create schema tasker;

create table featherbase.table_def (
  name text primary key,
  module text,
  kind text,
  data_source text,
  description text,
  system boolean not null,
  owner_app text,
  physical_schema text,
  physical_relation text
);
create table featherbase.column_def (row_id text primary key);
-- password_hash is denied by source.deny.columns (#4346); a deny that binds to no column fails closed.
create table featherbase.person (row_id text primary key, password_hash text);
create table featherbase.service_account (row_id text primary key);
create table featherbase.identifier_system (row_id text primary key);
create table featherbase.role (row_id text primary key);
create table featherbase.has_role (row_id text primary key);
create table featherbase.permission (row_id text primary key);
create table featherbase.data_scope (row_id text primary key);
create table featherbase.share (row_id text primary key);
create table featherbase.activity_log (row_id text primary key);
create table featherbase.user_event (row_id text primary key);
create table featherbase.comment (row_id text primary key);

-- Platform tables that must stay outside the named allow-list.
create table featherbase.background_job (row_id text primary key);
create table featherbase.version (row_id text primary key);
create table featherbase.access_token (id text primary key, token_hash text);

-- The Person release's identity tables. Not catalog-declared (no table_def row), so the
-- adapter never offers them; the registration's deny list is the tripwire if that changes.
create table featherbase.sign_in_identity (id text primary key, person_id text);
create table featherbase.person_id_map (old_id text primary key, new_id text, kind text);
create table featherbase.person_identifier (id text primary key, person_id text, value text);
create table featherbase.person_identifier_fingerprint (person_identifier_id text primary key, fingerprint bytea);
create table featherbase.person_match (id text primary key);
create table featherbase.person_match_member (id text primary key, person_id text);
create table featherbase.person_merge (id text primary key);

create table tasker.project (row_id text primary key, project_name text);
create table tasker.task (row_id text primary key, task_title text);

-- Physical but not catalog-declared: the adapter must exclude it.
create table public.site (row_id text primary key);

insert into featherbase.table_def
  (name, module, kind, data_source, description, system, owner_app,
   physical_schema, physical_relation)
values
  ('Table', 'Core', 'table', null, null, true, null, null, null),
  ('Column', 'Core', 'sub_table', null, null, true, null, null, null),
  ('Person', 'Core', 'table', null, null, true, null, null, null),
  ('Service Account', 'Core', 'table', null, null, true, null, null, null),
  ('Identifier System', 'Core', 'table', null, null, true, null, null, null),
  ('Role', 'Core', 'table', null, null, true, null, null, null),
  ('Has Role', 'Core', 'sub_table', null, null, true, null, null, null),
  ('Permission', 'Core', 'table', null, null, true, null, null, null),
  ('Data Scope', 'Core', 'table', null, null, true, null, null, null),
  ('Share', 'Core', 'table', null, null, true, null, null, null),
  ('Activity Log', 'Core', 'table', null, null, true, null, null, null),
  ('User Event', 'Core', 'table', null, null, true, null, null, null),
  ('Comment', 'Core', 'table', null, null, true, null, null, null),
  ('Background Job', 'Core', 'table', null, null, true, null, null, null),
  ('Version', 'Core', 'table', null, null, true, null, null, null),
  ('Access Token', 'Core', 'table', null, null, true, null, null, null),
  ('tasker.project', 'Tasker', 'table', null, 'Tasker project.', false, 'tasker',
   'tasker', 'project'),
  ('tasker.task', 'Tasker', 'table', null, 'Tasker task.', false, 'tasker',
   'tasker', 'task');
