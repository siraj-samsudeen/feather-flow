-- GENERATED 2026-09-04 from the live source catalog (schema 'public'); DDL only, for tests.
create schema if not exists "public";
create table "public"."access_log" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "user" character varying(140),
 "operation" character varying(140),
 "ref_table" character varying(140),
 "reference_name" character varying(140),
 "method" character varying(140)
);
alter table "public"."access_log" add PRIMARY KEY (row_id);
create table "public"."access_token" (
 "id" character varying(140) not null,
 "label" character varying(140) not null,
 "owner" character varying(140) not null,
 "token_hash" character(64) not null,
 "created_at" timestamp with time zone default now() not null,
 "expires_at" timestamp with time zone,
 "last_used_at" timestamp with time zone,
 "revoked_at" timestamp with time zone
);
CREATE UNIQUE INDEX access_token_token_hash_key ON public.access_token USING btree (token_hash);
alter table "public"."access_token" add PRIMARY KEY (id);
create table "public"."activity_log" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "user" character varying(140),
 "operation" character varying(140),
 "full_name" character varying(140),
 "ip_address" character varying(140)
);
alter table "public"."activity_log" add PRIMARY KEY (row_id);
create table "public"."assignment_rule" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "description" character varying(140),
 "assign_condition" text,
 "assign_to_field" character varying(140),
 "disabled" boolean,
 "last_user" character varying(140)
);
alter table "public"."assignment_rule" add PRIMARY KEY (row_id);
create table "public"."assignment_rule_user" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "user" character varying(140)
);
alter table "public"."assignment_rule_user" add PRIMARY KEY (row_id);
create table "public"."auto_email_report" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "report" character varying(140),
 "recipients" text,
 "file_format" text,
 "frequency" text,
 "enabled" boolean,
 "last_sent" timestamp with time zone
);
alter table "public"."auto_email_report" add PRIMARY KEY (row_id);
create table "public"."background_job" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "method" character varying(140),
 "payload" jsonb,
 "job_status" text,
 "attempts" bigint,
 "max_attempts" bigint,
 "run_at" timestamp with time zone,
 "error" text,
 "repeat_every" bigint
);
alter table "public"."background_job" add PRIMARY KEY (row_id);
create table "public"."client_script" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "script" text,
 "enabled" boolean
);
alter table "public"."client_script" add PRIMARY KEY (row_id);
create table "public"."column_def" (
 "row_id" text default (gen_random_uuid())::text not null,
 "parent" text not null,
 "position" integer default 0 not null,
 "column_name" text not null,
 "label" text,
 "column_type" text not null,
 "reference_table" text,
 "choices" text,
 "row_table" text,
 "reqd" boolean default false not null,
 "unique" boolean default false not null,
 "default_value" text,
 "read_only" boolean default false not null,
 "hidden" boolean default false not null,
 "in_list_view" boolean default false not null,
 "tier" text default 'basic'::text not null,
 "created_by" text default 'Administrator'::text not null,
 "updated_by" text default 'Administrator'::text not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "status" text default 'draft'::text not null,
 "parenttype" text default 'Table'::text not null,
 "parentfield" text default 'columns'::text not null,
 "custom" boolean default false not null,
 "source_column" text,
 "source_fk_schema" text,
 "source_fk_table" text,
 "source_fk_column" text
);
CREATE UNIQUE INDEX column_def_parent_column_name ON public.column_def USING btree (parent, column_name);
alter table "public"."column_def" add PRIMARY KEY (row_id);
create table "public"."comment" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "ref_name" character varying(140),
 "content" text
);
alter table "public"."comment" add PRIMARY KEY (row_id);
create table "public"."custom_field" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "dt" character varying(140),
 "column_name" character varying(140),
 "label" character varying(140),
 "column_type" character varying(140),
 "reference_table" character varying(140),
 "choices" text,
 "row_table" character varying(140),
 "reqd" boolean,
 "in_list_view" boolean
);
alter table "public"."custom_field" add PRIMARY KEY (row_id);
create table "public"."dashboard" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "label" character varying(140),
 "config" jsonb
);
alter table "public"."dashboard" add PRIMARY KEY (row_id);
create table "public"."data_scope" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "user" character varying(140),
 "allow_table" character varying(140),
 "for_value" character varying(140)
);
alter table "public"."data_scope" add PRIMARY KEY (row_id);
create table "public"."data_source" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "engine" text,
 "url_env" character varying(140),
 "root_path" character varying(140),
 "default_schema" character varying(140),
 "access" text,
 "pool_max" bigint,
 "statement_timeout_ms" bigint,
 "table_allowlist" text,
 "conn_status" text,
 "last_checked_at" timestamp with time zone,
 "last_error" text
);
alter table "public"."data_source" add PRIMARY KEY (row_id);
create table "public"."email_account" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "email_id" character varying(140),
 "smtp_host" character varying(140),
 "smtp_port" bigint,
 "is_default" boolean
);
alter table "public"."email_account" add PRIMARY KEY (row_id);
create table "public"."email_queue" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "sender" character varying(140),
 "recipient" character varying(140),
 "subject" character varying(140),
 "body" text,
 "send_status" text,
 "error" text,
 "ref_table" character varying(140),
 "reference_name" character varying(140),
 "attachments" jsonb
);
alter table "public"."email_queue" add PRIMARY KEY (row_id);
create table "public"."email_rule" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "event" text,
 "condition_field" character varying(140),
 "condition_value" character varying(140),
 "recipient" character varying(140),
 "subject" character varying(140),
 "message" text,
 "enabled" boolean
);
alter table "public"."email_rule" add PRIMARY KEY (row_id);
create table "public"."email_sink" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "mail_from" character varying(140),
 "mail_to" character varying(140),
 "subject" character varying(140),
 "body" text,
 "attachment_names" character varying(140),
 "attachment_b64" text
);
alter table "public"."email_sink" add PRIMARY KEY (row_id);
create table "public"."file" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "file_name" character varying(140),
 "file_url" character varying(140),
 "mime_type" character varying(140),
 "file_size" bigint,
 "is_private" boolean,
 "ref_table" character varying(140),
 "ref_name" character varying(140),
 "thumbnail_url" text
);
alter table "public"."file" add PRIMARY KEY (row_id);
create table "public"."has_role" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "role" character varying(140)
);
alter table "public"."has_role" add PRIMARY KEY (row_id);
create table "public"."home_page" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "label" character varying(140),
 "icon" character varying(140),
 "module" character varying(140),
 "sequence" bigint,
 "shortcuts" jsonb
);
alter table "public"."home_page" add PRIMARY KEY (row_id);
create table "public"."home_page_link" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "label" character varying(140),
 "type" text,
 "link_to" character varying(140)
);
alter table "public"."home_page_link" add PRIMARY KEY (row_id);
create table "public"."home_page_role" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "role" character varying(140)
);
alter table "public"."home_page_role" add PRIMARY KEY (row_id);
create table "public"."import_log" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "file_name" character varying(140),
 "sheet_name" character varying(140),
 "table_created" boolean,
 "inserted" bigint,
 "updated" bigint,
 "failed" bigint,
 "error_summary" text,
 "key_column" character varying(140),
 "empty_cells" text,
 "part" bigint,
 "parts" bigint,
 "run_id" character varying(140),
 "touched" jsonb,
 "reverted_at" timestamp with time zone,
 "batch_id" character varying(140)
);
alter table "public"."import_log" add PRIMARY KEY (row_id);
create table "public"."installed_app" (
 "name" text not null,
 "tables" jsonb default '[]'::jsonb not null,
 "installed_at" timestamp with time zone default now() not null,
 "roles" jsonb default '[]'::jsonb not null,
 "perms" jsonb default '[]'::jsonb not null,
 "manifest" jsonb,
 "fixtures" jsonb default '[]'::jsonb not null,
 "sources" jsonb
);
alter table "public"."installed_app" add PRIMARY KEY (name);
create table "public"."internal_metadata" (
 "key" text not null,
 "value" text not null,
 "updated_at" timestamp with time zone default now() not null
);
alter table "public"."internal_metadata" add PRIMARY KEY (key);
create table "public"."job_execution" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "job" character varying(140),
 "method" character varying(140),
 "attempt" bigint,
 "outcome" text,
 "error" text
);
alter table "public"."job_execution" add PRIMARY KEY (row_id);
create table "public"."letter_head" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "is_default" boolean,
 "header_html" text,
 "footer_html" text
);
alter table "public"."letter_head" add PRIMARY KEY (row_id);
create table "public"."metadata_override" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "table_name" character varying(140),
 "column_name" character varying(140),
 "property" character varying(140),
 "value" character varying(140)
);
alter table "public"."metadata_override" add PRIMARY KEY (row_id);
create table "public"."migration" (
 "name" text not null,
 "applied_at" timestamp with time zone default now() not null
);
alter table "public"."migration" add PRIMARY KEY (name);
create table "public"."notification_log" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "for_user" character varying(140),
 "subject" character varying(140),
 "ref_table" character varying(140),
 "ref_name" character varying(140),
 "read" boolean
);
alter table "public"."notification_log" add PRIMARY KEY (row_id);
create table "public"."password_reset" (
 "token" text not null,
 "user" character varying(140) not null,
 "expires_at" timestamp with time zone not null,
 "created_at" timestamp with time zone default now() not null
);
alter table "public"."password_reset" add PRIMARY KEY (token);
create table "public"."patch_log" (
 "patch" text not null,
 "applied_at" timestamp with time zone default now() not null
);
alter table "public"."patch_log" add PRIMARY KEY (patch);
create table "public"."permission" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "role" character varying(140),
 "tier" text,
 "own_rows_only" boolean,
 "can_read" boolean,
 "can_write" boolean,
 "can_create" boolean,
 "can_delete" boolean,
 "can_submit" boolean,
 "can_cancel" boolean,
 "can_amend" boolean
);
alter table "public"."permission" add PRIMARY KEY (row_id);
create table "public"."print_format" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "is_default" boolean,
 "template" text,
 "letter_head" character varying(140)
);
alter table "public"."print_format" add PRIMARY KEY (row_id);
create table "public"."report" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "config" jsonb,
 "report_type" text,
 "query" text,
 "report_script" character varying(140)
);
alter table "public"."report" add PRIMARY KEY (row_id);
create table "public"."role" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "disabled" boolean
);
alter table "public"."role" add PRIMARY KEY (row_id);
create table "public"."saved_view" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "label" character varying(140),
 "filters" jsonb,
 "shared" boolean
);
alter table "public"."saved_view" add PRIMARY KEY (row_id);
create table "public"."series" (
 "name" text not null,
 "current" bigint default 0 not null
);
alter table "public"."series" add PRIMARY KEY (name);
create table "public"."server_script" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "script_type" text,
 "ref_table" character varying(140),
 "event" text,
 "api_method" character varying(140),
 "script" text,
 "enabled" boolean
);
alter table "public"."server_script" add PRIMARY KEY (row_id);
create table "public"."service_level_agreement" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "enabled" boolean,
 "priority_field" character varying(140),
 "fulfilled_states" text,
 "escalation_role" character varying(140)
);
alter table "public"."service_level_agreement" add PRIMARY KEY (row_id);
create table "public"."share" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "share_table" character varying(140),
 "share_name" character varying(140),
 "user" character varying(140),
 "read" boolean,
 "write" boolean,
 "share" boolean
);
alter table "public"."share" add PRIMARY KEY (row_id);
create table "public"."single_value" (
 "table_name" character varying(140) not null,
 "field" character varying(140) not null,
 "value" text
);
alter table "public"."single_value" add PRIMARY KEY (table_name, field);
create table "public"."site" (
 "name" text not null,
 "host" text not null,
 "schema" text not null,
 "created_at" timestamp with time zone default now() not null
);
CREATE UNIQUE INDEX site_host_key ON public.site USING btree (host);
alter table "public"."site" add PRIMARY KEY (name);
create table "public"."sla_priority" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "priority" character varying(140),
 "response_hours" double precision,
 "resolution_hours" double precision
);
alter table "public"."sla_priority" add PRIMARY KEY (row_id);
create table "public"."table_def" (
 "name" text not null,
 "module" text default 'Core'::text not null,
 "kind" text default 'table'::text not null,
 "is_submittable" boolean default false not null,
 "id_pattern" text default 'hash'::text not null,
 "title_column" text,
 "sort_column" text default 'updated_at'::text not null,
 "sort_order" text default 'desc'::text not null,
 "track_changes" boolean default true not null,
 "description" text,
 "custom" boolean default false not null,
 "system" boolean default false not null,
 "created_by" text default 'Administrator'::text not null,
 "updated_by" text default 'Administrator'::text not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "data_source" character varying(140),
 "external_schema" text,
 "external_table" text,
 "external_pk" text,
 "external_modified" text
);
alter table "public"."table_def" add PRIMARY KEY (name);
create table "public"."tag_link" (
 "ref_table" character varying(140) not null,
 "ref_name" character varying(140) not null,
 "tag" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null
);
alter table "public"."tag_link" add PRIMARY KEY (ref_table, ref_name, tag);
create table "public"."test" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "name" character varying(140)
);
alter table "public"."test" add PRIMARY KEY (row_id);
create table "public"."todo" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "allocated_to" character varying(140),
 "ref_table" character varying(140),
 "reference_name" character varying(140),
 "description" text,
 "todo_status" text,
 "priority" text
);
alter table "public"."todo" add PRIMARY KEY (row_id);
create table "public"."translation" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "language" character varying(140),
 "source_text" character varying(140),
 "translated_text" text
);
CREATE UNIQUE INDEX translation_lang_src ON public.translation USING btree (language, source_text);
alter table "public"."translation" add PRIMARY KEY (row_id);
create table "public"."user" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "email" character varying(140),
 "full_name" character varying(140),
 "enabled" boolean,
 "password_hash" character varying(140),
 "language" character varying(140),
 "user_image" text,
 "api_rate_limit" bigint,
 "theme" text,
 "social_login" character varying(140),
 "palette" text,
 "user_type" character varying(20) default 'human'::character varying not null
);
CREATE UNIQUE INDEX user_email_uq ON public."user" USING btree (email);
alter table "public"."user" add PRIMARY KEY (row_id);
create table "public"."user_event" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "kind" text,
 "ref_key" text,
 "label" text,
 "sub_label" text,
 "path" text,
 "occurred_at" timestamp with time zone
);
alter table "public"."user_event" add PRIMARY KEY (row_id);
create table "public"."user_settings" (
 "user" character varying(140) not null,
 "table_name" character varying(140) not null,
 "settings" jsonb default '{}'::jsonb not null,
 "updated_at" timestamp with time zone default now() not null
);
alter table "public"."user_settings" add PRIMARY KEY ("user", table_name);
create table "public"."version" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "ref_name" character varying(140),
 "data" jsonb
);
alter table "public"."version" add PRIMARY KEY (row_id);
create table "public"."web_form" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "title" character varying(140),
 "route" character varying(140),
 "ref_table" character varying(140),
 "web_fields" jsonb,
 "published" boolean,
 "success_message" character varying(140)
);
CREATE UNIQUE INDEX web_form_route_uq ON public.web_form USING btree (route);
alter table "public"."web_form" add PRIMARY KEY (row_id);
create table "public"."web_page" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "title" character varying(140),
 "route" character varying(140),
 "content" text,
 "published" boolean
);
CREATE UNIQUE INDEX web_page_route_uq ON public.web_page USING btree (route);
alter table "public"."web_page" add PRIMARY KEY (row_id);
create table "public"."webhook" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "webhook_table" character varying(140),
 "webhook_event" text,
 "request_url" character varying(140),
 "webhook_secret" character varying(140),
 "enabled" boolean
);
alter table "public"."webhook" add PRIMARY KEY (row_id);
create table "public"."workflow" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "is_active" boolean,
 "state_field" character varying(140)
);
alter table "public"."workflow" add PRIMARY KEY (row_id);
create table "public"."workflow_action" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "ref_table" character varying(140),
 "ref_name" character varying(140),
 "action" character varying(140),
 "from_state" character varying(140),
 "to_state" character varying(140),
 "actor" character varying(140)
);
alter table "public"."workflow_action" add PRIMARY KEY (row_id);
create table "public"."workflow_document_state" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "state" character varying(140),
 "target_status" text
);
alter table "public"."workflow_document_state" add PRIMARY KEY (row_id);
create table "public"."workflow_transition" (
 "row_id" character varying(140) not null,
 "created_by" character varying(140) default 'Administrator'::character varying not null,
 "created_at" timestamp with time zone default now() not null,
 "updated_at" timestamp with time zone default now() not null,
 "updated_by" character varying(140) default 'Administrator'::character varying not null,
 "status" text default 'draft'::text not null,
 "position" integer default 0 not null,
 "parent" character varying(140),
 "parenttype" character varying(140),
 "parentfield" character varying(140),
 "state" character varying(140),
 "action" character varying(140),
 "next_state" character varying(140),
 "allowed" character varying(140),
 "condition" text
);
alter table "public"."workflow_transition" add PRIMARY KEY (row_id);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Access Log', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Activity Log', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Assignment Rule', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Assignment Rule User', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Auto Email Report', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Background Job', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Client Script', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Column', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Comment', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Custom Field', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Dashboard', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Data Scope', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Data Source', 'Integrations', 'table', null, 'A connection to an external store (Postgres, MySQL, DuckDB/MotherDuck, or a CSV folder). Credentials are read from the named environment variable at connect time and are never stored. For read-only access, prefer a database role/token that is itself read-only — the access flag here is a guard rail, not the security boundary.');
insert into "public".table_def ("name", module, kind, data_source, description) values ('Email Account', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Email Queue', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Email Rule', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Email Sink', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('File', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Has Role', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Home Page', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Home Page Link', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Home Page Role', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Import Log', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Job Execution', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Letter Head', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Metadata Override', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Notification Log', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Permission', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Print Format', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Report', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Role', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Saved View', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Server Script', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Service Level Agreement', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Share', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('SLA Priority', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('System Settings', 'Core', 'settings', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Table', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Test', 'Custom', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('ToDo', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Translation', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('User', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('User Event', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Version', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Web Form', 'Website', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Webhook', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Web Page', 'Website', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Workflow', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Workflow Action', 'Core', 'table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Workflow Document State', 'Core', 'sub_table', null, null);
insert into "public".table_def ("name", module, kind, data_source, description) values ('Workflow Transition', 'Core', 'sub_table', null, null);
