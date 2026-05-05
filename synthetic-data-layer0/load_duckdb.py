"""Build a DuckDB instance for TraceX Layer 0 and populate it from the CSVs."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "tracex_layer0.duckdb"

# DuckDB-flavoured DDL: SERIAL -> INTEGER, NOW() -> CURRENT_TIMESTAMP, no separate index step needed
# (DuckDB has its own PK/UNIQUE indexes; the secondary indexes from the Postgres DDL are added below).
DDL = [
    """
    CREATE TABLE src_branch (
        branch_id      VARCHAR NOT NULL,
        branch_name    VARCHAR NOT NULL,
        address_line1  VARCHAR,
        city           VARCHAR,
        state          VARCHAR,
        country        VARCHAR NOT NULL DEFAULT 'US',
        region         VARCHAR CHECK (region IN ('NORTHEAST','SOUTHEAST','MIDWEST','WEST','SOUTHWEST')),
        active_flag    VARCHAR NOT NULL DEFAULT 'Y' CHECK (active_flag IN ('Y','N')),
        created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT pk_src_branch PRIMARY KEY (branch_id)
    );
    """,
    """
    CREATE TABLE src_customer (
        customer_id      VARCHAR NOT NULL,
        first_name       VARCHAR NOT NULL,
        last_name        VARCHAR NOT NULL,
        dob              DATE NOT NULL,
        ssn_hash         VARCHAR NOT NULL,
        country_of_birth VARCHAR NOT NULL,
        citizenship      VARCHAR NOT NULL,
        onboarded_date   DATE NOT NULL,
        kyc_status       VARCHAR NOT NULL CHECK (kyc_status IN ('PENDING','APPROVED','EXPIRED','REJECTED')),
        kyc_reviewed_at  TIMESTAMP,
        branch_id        VARCHAR NOT NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT pk_src_customer PRIMARY KEY (customer_id)
    );
    """,
    """
    CREATE TABLE src_account (
        account_id     VARCHAR NOT NULL,
        customer_id    VARCHAR NOT NULL,
        account_type   VARCHAR NOT NULL CHECK (account_type IN ('CHECKING','SAVINGS','MONEY_MARKET','CD','LOAN','CREDIT')),
        product_code   VARCHAR NOT NULL,
        open_date      DATE NOT NULL,
        close_date     DATE,
        status         VARCHAR NOT NULL CHECK (status IN ('ACTIVE','CLOSED','FROZEN','DORMANT')),
        interest_rate  DECIMAL(6,4),
        credit_limit   DECIMAL(15,2),
        branch_id      VARCHAR NOT NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT pk_src_account PRIMARY KEY (account_id),
        CONSTRAINT fk_account_customer FOREIGN KEY (customer_id) REFERENCES src_customer(customer_id)
    );
    """,
    """
    CREATE TABLE src_transaction (
        txn_id                VARCHAR NOT NULL,
        account_id            VARCHAR NOT NULL,
        txn_date              DATE NOT NULL,
        txn_timestamp         TIMESTAMP NOT NULL,
        txn_type              VARCHAR NOT NULL CHECK (txn_type IN ('CREDIT','DEBIT','TRANSFER','FEE','REVERSAL','WIRE','ACH')),
        amount                DECIMAL(15,2) NOT NULL,
        currency              VARCHAR NOT NULL,
        counterparty_account  VARCHAR,
        counterparty_bank_bic VARCHAR,
        channel               VARCHAR NOT NULL CHECK (channel IN ('BRANCH','ATM','ONLINE','MOBILE','WIRE','ACH','POS')),
        status                VARCHAR NOT NULL CHECK (status IN ('SETTLED','PENDING','FAILED','REVERSED')),
        reversal_flag         VARCHAR NOT NULL DEFAULT 'N' CHECK (reversal_flag IN ('Y','N')),
        original_txn_id       VARCHAR,
        created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT pk_src_transaction PRIMARY KEY (txn_id),
        CONSTRAINT fk_txn_account FOREIGN KEY (account_id) REFERENCES src_account(account_id)
    );
    """,
    """
    CREATE TABLE src_fx_rate (
        rate_id       INTEGER NOT NULL,
        rate_date     DATE NOT NULL,
        from_currency VARCHAR NOT NULL,
        to_currency   VARCHAR NOT NULL DEFAULT 'USD',
        rate          DECIMAL(18,8) NOT NULL,
        rate_source   VARCHAR NOT NULL DEFAULT 'ECB',
        created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT pk_src_fx_rate PRIMARY KEY (rate_id),
        CONSTRAINT uq_fx_rate UNIQUE (rate_date, from_currency, to_currency)
    );
    """,
]

INDEXES = [
    "CREATE INDEX idx_account_customer  ON src_account(customer_id);",
    "CREATE INDEX idx_account_branch    ON src_account(branch_id);",
    "CREATE INDEX idx_txn_account       ON src_transaction(account_id);",
    "CREATE INDEX idx_txn_date          ON src_transaction(txn_date);",
    "CREATE INDEX idx_txn_reversal      ON src_transaction(reversal_flag);",
    "CREATE INDEX idx_fx_date_currency  ON src_fx_rate(rate_date, from_currency, to_currency);",
    "CREATE INDEX idx_customer_branch   ON src_customer(branch_id);",
    "CREATE INDEX idx_customer_kyc      ON src_customer(kyc_status);",
]

# Load order respects FK dependencies: branch -> customer -> account -> transaction; fx_rate is standalone.
LOAD_ORDER = [
    ("src_branch",      "src_branch.csv"),
    ("src_customer",    "src_customer.csv"),
    ("src_account",     "src_account.csv"),
    ("src_transaction", "src_transaction.csv"),
    ("src_fx_rate",     "src_fx_rate.csv"),
]


def main() -> None:
    if DB_PATH.exists():
        os.remove(DB_PATH)
        print(f"removed existing {DB_PATH.name}")

    con = duckdb.connect(str(DB_PATH))
    try:
        for stmt in DDL:
            con.execute(stmt)
        for stmt in INDEXES:
            con.execute(stmt)
        print("schema created")

        for table, csv_name in LOAD_ORDER:
            csv_path = (HERE / csv_name).as_posix()
            con.execute(
                f"INSERT INTO {table} SELECT * FROM read_csv_auto(?, header=true, nullstr='')",
                [csv_path],
            )
            (count,) = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"loaded {table:18s} {count:>8,} rows from {csv_name}")
    finally:
        con.close()

    print(f"\nDuckDB ready at: {DB_PATH}")


if __name__ == "__main__":
    main()
