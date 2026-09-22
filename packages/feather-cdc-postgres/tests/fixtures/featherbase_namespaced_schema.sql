-- #3894: current Featherbase platform schema plus one installed app-owned schema.
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
create table featherbase."user" (row_id text primary key);
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
  ('User', 'Core', 'table', null, null, true, null, null, null),
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
