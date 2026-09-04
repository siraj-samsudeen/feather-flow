"""pg_md_cdc — declarative PostgreSQL → MotherDuck CDC runtime (#3079).

One code artifact, one isolated service instance per registration (`registrations/<id>.yml`).
Mechanism: slot-based logical replication (pgoutput) drained cron-shaped into a bronze
database, source namespaces preserved. Plan: docs/plans/issue_3079_pg_md_cdc_runtime.md.
"""
