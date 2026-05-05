-- ============================================================
-- TraceX Synthetic Data — Layer 0 (Raw Source Tables)
-- Target: PostgreSQL 14+
-- ============================================================

-- ============================================================
-- src_customer
-- ============================================================
CREATE TABLE src_customer (
    customer_id         VARCHAR(20)     NOT NULL,
    first_name          VARCHAR(100)    NOT NULL,
    last_name           VARCHAR(100)    NOT NULL,
    dob                 DATE            NOT NULL,
    ssn_hash            CHAR(64)        NOT NULL,           -- SHA-256 of SSN, never raw
    country_of_birth    CHAR(2)         NOT NULL,           -- ISO 3166-1 alpha-2
    citizenship         CHAR(2)         NOT NULL,           -- ISO 3166-1 alpha-2
    onboarded_date      DATE            NOT NULL,
    kyc_status          VARCHAR(20)     NOT NULL            -- PENDING | APPROVED | EXPIRED | REJECTED
                            CHECK (kyc_status IN ('PENDING','APPROVED','EXPIRED','REJECTED')),
    kyc_reviewed_at     TIMESTAMP,                          -- NULL if never reviewed
    branch_id           VARCHAR(10)     NOT NULL,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_src_customer PRIMARY KEY (customer_id)
);

-- ============================================================
-- src_account
-- ============================================================
CREATE TABLE src_account (
    account_id          VARCHAR(20)     NOT NULL,
    customer_id         VARCHAR(20)     NOT NULL,
    account_type        VARCHAR(20)     NOT NULL
                            CHECK (account_type IN ('CHECKING','SAVINGS','MONEY_MARKET','CD','LOAN','CREDIT')),
    product_code        VARCHAR(20)     NOT NULL,
    open_date           DATE            NOT NULL,
    close_date          DATE,                               -- NULL = still open
    status              VARCHAR(20)     NOT NULL
                            CHECK (status IN ('ACTIVE','CLOSED','FROZEN','DORMANT')),
    interest_rate       NUMERIC(6,4),                       -- annual %, e.g. 0.0425 = 4.25%
    credit_limit        NUMERIC(15,2),                      -- NULL for non-credit accounts
    branch_id           VARCHAR(10)     NOT NULL,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_src_account PRIMARY KEY (account_id),
    CONSTRAINT fk_account_customer FOREIGN KEY (customer_id) REFERENCES src_customer(customer_id)
);

-- ============================================================
-- src_transaction
-- ============================================================
CREATE TABLE src_transaction (
    txn_id                  VARCHAR(30)     NOT NULL,
    account_id              VARCHAR(20)     NOT NULL,
    txn_date                DATE            NOT NULL,
    txn_timestamp           TIMESTAMP       NOT NULL,
    txn_type                VARCHAR(20)     NOT NULL
                                CHECK (txn_type IN ('CREDIT','DEBIT','TRANSFER','FEE','REVERSAL','WIRE','ACH')),
    amount                  NUMERIC(15,2)   NOT NULL,
    currency                CHAR(3)         NOT NULL,       -- ISO 4217
    counterparty_account    VARCHAR(30),                    -- NULL for fees/internal
    counterparty_bank_bic   VARCHAR(11),                    -- SWIFT BIC, NULL for domestic ACH
    channel                 VARCHAR(20)     NOT NULL
                                CHECK (channel IN ('BRANCH','ATM','ONLINE','MOBILE','WIRE','ACH','POS')),
    status                  VARCHAR(20)     NOT NULL
                                CHECK (status IN ('SETTLED','PENDING','FAILED','REVERSED')),
    reversal_flag           CHAR(1)         NOT NULL DEFAULT 'N'
                                CHECK (reversal_flag IN ('Y','N')),
    original_txn_id         VARCHAR(30),                    -- populated only if reversal_flag = 'Y'
    created_at              TIMESTAMP       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_src_transaction PRIMARY KEY (txn_id),
    CONSTRAINT fk_txn_account FOREIGN KEY (account_id) REFERENCES src_account(account_id)
);

-- ============================================================
-- src_branch
-- ============================================================
CREATE TABLE src_branch (
    branch_id           VARCHAR(10)     NOT NULL,
    branch_name         VARCHAR(200)    NOT NULL,
    address_line1       VARCHAR(200),
    city                VARCHAR(100),
    state               CHAR(2),                            -- US state code
    country             CHAR(2)         NOT NULL DEFAULT 'US',
    region              VARCHAR(50)                         -- NORTHEAST | SOUTHEAST | MIDWEST | WEST | SOUTHWEST
                            CHECK (region IN ('NORTHEAST','SOUTHEAST','MIDWEST','WEST','SOUTHWEST')),
    active_flag         CHAR(1)         NOT NULL DEFAULT 'Y'
                            CHECK (active_flag IN ('Y','N')),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_src_branch PRIMARY KEY (branch_id)
);

-- ============================================================
-- src_fx_rate
-- ============================================================
CREATE TABLE src_fx_rate (
    rate_id             SERIAL,
    rate_date           DATE            NOT NULL,
    from_currency       CHAR(3)         NOT NULL,           -- ISO 4217
    to_currency         CHAR(3)         NOT NULL DEFAULT 'USD',
    rate                NUMERIC(18,8)   NOT NULL,           -- 1 from_currency = rate to_currency
    rate_source         VARCHAR(50)     NOT NULL DEFAULT 'ECB',  -- ECB | FED | MANUAL
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_src_fx_rate PRIMARY KEY (rate_id),
    CONSTRAINT uq_fx_rate UNIQUE (rate_date, from_currency, to_currency)
);

-- ============================================================
-- Indexes — kept minimal, only what lineage queries will need
-- ============================================================
CREATE INDEX idx_account_customer     ON src_account(customer_id);
CREATE INDEX idx_account_branch       ON src_account(branch_id);
CREATE INDEX idx_txn_account          ON src_transaction(account_id);
CREATE INDEX idx_txn_date             ON src_transaction(txn_date);
CREATE INDEX idx_txn_reversal         ON src_transaction(reversal_flag);
CREATE INDEX idx_fx_date_currency     ON src_fx_rate(rate_date, from_currency, to_currency);
CREATE INDEX idx_customer_branch      ON src_customer(branch_id);
CREATE INDEX idx_customer_kyc         ON src_customer(kyc_status);
