"""Idempotent schema upgrades for PIT security eligibility facts."""


def apply_security_eligibility_migrations(connection) -> None:
    connection.execute("ALTER TABLE universe_snapshots ADD COLUMN IF NOT EXISTS listing_snapshot_id VARCHAR")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS security_listing_snapshots ("
        "listing_snapshot_id VARCHAR PRIMARY KEY, source_channel VARCHAR NOT NULL, raw_artifact_path VARCHAR NOT NULL, "
        "raw_artifact_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS security_listing_values ("
        "listing_snapshot_id VARCHAR NOT NULL REFERENCES security_listing_snapshots(listing_snapshot_id), "
        "ticker VARCHAR NOT NULL, list_date DATE NOT NULL, delist_date DATE, list_status VARCHAR NOT NULL, "
        "PRIMARY KEY (listing_snapshot_id, ticker))"
    )
