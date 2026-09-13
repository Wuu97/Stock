"""Idempotent upgrades for instrument-aware security reference data."""


def apply_security_migrations(connection) -> None:
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS instrument_type VARCHAR DEFAULT 'A_SHARE'")
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS settlement_cycle VARCHAR DEFAULT 'T1'")
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS board_lot INTEGER DEFAULT 100")
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS price_tick DECIMAL(20,4) DEFAULT 0.0100")
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS price_limit_ratio DECIMAL(12,8)")
    connection.execute("ALTER TABLE security_master ADD COLUMN IF NOT EXISTS sell_stamp_duty_rate DECIMAL(12,8) DEFAULT 0.00050000")
    connection.execute(
        "UPDATE security_master SET instrument_type = COALESCE(instrument_type, 'A_SHARE'), "
        "settlement_cycle = COALESCE(settlement_cycle, 'T1'), board_lot = COALESCE(board_lot, 100), "
        "price_tick = COALESCE(price_tick, 0.0100), sell_stamp_duty_rate = COALESCE(sell_stamp_duty_rate, 0.00050000)"
    )
