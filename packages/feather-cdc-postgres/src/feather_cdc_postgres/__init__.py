"""feather_cdc_postgres — declarative PostgreSQL → MotherDuck CDC runtime (#3079).

One code artifact, one isolated service instance per registration (`<source_id>.yml`). The
registrations are the DEPLOYER's, not this package's. Mechanism: slot-based logical replication
(pgoutput) drained cron-shaped into a bronze database, source namespaces preserved.
"""
