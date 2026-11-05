-- GENERATED 2026-09-04 from the live source catalog (schema 'control'); DDL only, for tests.
create schema if not exists "control";
create table "control"."budget_usage" (
 "run_id" text not null,
 "extractor" text not null,
 "budget_name" text not null,
 "limit_val" bigint,
 "used" bigint,
 "remaining" bigint,
 "hit" boolean,
 "ran_at" timestamp with time zone
);
alter table "control"."budget_usage" add PRIMARY KEY (run_id, extractor, budget_name);
create table "control"."event" (
 "extractor" text not null,
 "run_id" text not null,
 "seq" bigint not null,
 "ran_at" timestamp with time zone not null,
 "entity" text,
 "unit" text,
 "business_date" date,
 "status" text not null,
 "http_status" integer,
 "rows" bigint,
 "source_attempts" integer,
 "sink_attempts" integer,
 "pull_ms" integer,
 "load_ms" integer,
 "error_detail" text,
 "extra" jsonb
);
alter table "control"."event" add PRIMARY KEY (extractor, run_id, seq);
create table "control"."gofrugal_grid" (
 "ajaxid" character varying not null,
 "store_code" character varying not null,
 "business_date" date not null,
 "status" character varying not null,
 "rows" bigint,
 "http_status" integer,
 "retry_count" integer default 0,
 "fetched_at" timestamp with time zone
);
alter table "control"."gofrugal_grid" add PRIMARY KEY (ajaxid, store_code, business_date);
create table "control"."lease" (
 "extractor" text not null,
 "run_id" text not null,
 "lease_expires_at" timestamp with time zone not null
);
alter table "control"."lease" add PRIMARY KEY (extractor);
create table "control"."petpooja_grid" (
 "rest_id" character varying not null,
 "order_date" date not null,
 "status" character varying not null,
 "rows" bigint,
 "code" character varying,
 "fetched_at" timestamp with time zone
);
alter table "control"."petpooja_grid" add PRIMARY KEY (rest_id, order_date);
create table "control"."run" (
 "run_id" text not null,
 "extractor" text not null,
 "mode" text,
 "host" text,
 "pid" integer,
 "started_at" timestamp with time zone not null,
 "finished_at" timestamp with time zone,
 "status" text not null,
 "stop_reason" text,
 "n_ok" integer,
 "n_err" integer,
 "n_skipped" integer,
 "reconciled_by" text,
 "wal_path" text,
 "extra" jsonb
);
alter table "control"."run" add PRIMARY KEY (run_id);
create table "control"."sap_backfill_grid" (
 "table_name" text not null,
 "chunk_start" date not null,
 "chunk_end" date,
 "status" text,
 "attempts" integer default 0,
 "rows_loaded" bigint,
 "completed_at" timestamp with time zone,
 "error_msg" text
);
alter table "control"."sap_backfill_grid" add PRIMARY KEY (table_name, chunk_start);
create table "control"."sap_partition_grid" (
 "table_name" text not null,
 "axis" text,
 "chunk_lo" text not null,
 "chunk_hi" text,
 "status" text,
 "attempts" integer default 0,
 "rows_loaded" bigint,
 "completed_at" timestamp with time zone,
 "error_msg" text
);
alter table "control"."sap_partition_grid" add PRIMARY KEY (table_name, chunk_lo);
create table "control"."sap_partition_grid_legacy" (
 "table_name" character varying,
 "axis" character varying,
 "chunk_lo" character varying,
 "chunk_hi" character varying,
 "status" character varying,
 "attempts" integer,
 "rows_loaded" bigint,
 "completed_at" timestamp with time zone,
 "error_msg" character varying
);
create table "control"."sap_reconcile" (
 "checked_at" timestamp with time zone,
 "run_id" text,
 "table_name" text,
 "kind" text,
 "source_count" bigint,
 "bronze_count" bigint,
 "delta" bigint,
 "status" text
);
create table "control"."sap_run_events_legacy" (
 "run_id" character varying,
 "ran_at" timestamp with time zone,
 "table_name" character varying,
 "schema_name" character varying,
 "pattern" character varying,
 "status" character varying,
 "attempts" integer,
 "rows_loaded" bigint,
 "duration_s" double precision,
 "load_id" character varying
);
create table "control"."zakya_cursor" (
 "source" character varying not null,
 "watermark" timestamp with time zone,
 "fetched_at" timestamp with time zone
);
alter table "control"."zakya_cursor" add PRIMARY KEY (source);
create table "control"."zakya_grid" (
 "source" character varying not null,
 "branch" character varying not null,
 "business_date" date not null,
 "status" character varying not null,
 "rows" bigint,
 "http_status" integer,
 "attempts" integer default 0,
 "fetched_at" timestamp with time zone
);
alter table "control"."zakya_grid" add PRIMARY KEY (source, branch, business_date);
create table "control"."zakya_quarantine" (
 "source" character varying,
 "branch" character varying,
 "business_date" date,
 "run_id" character varying,
 "reason" character varying,
 "raw_span" character varying,
 "captured_at" timestamp with time zone
);
