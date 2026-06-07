-- CPOI Platform — Database Schema
-- Version: 1.0
-- All table and column definitions match cpoi-sdlc-spec.md Part 3 exactly.
-- PRAGMA foreign_keys = ON is applied at connection time in init_db.py, not here.
-- This file is append-only in intent: changes require a version increment and
-- a corresponding audit_log entry documenting the reason.

-- -----------------------------------------------------------------------------
-- TABLE: clients
-- One record per client engagement.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    client_id              TEXT PRIMARY KEY,
    client_name            TEXT NOT NULL,
    engagement_type        TEXT NOT NULL CHECK (engagement_type IN ('snapshot', 'oeil')),
    engagement_start_date  DATE,
    monthly_retainer       DECIMAL,
    oei_score_intake       INTEGER,
    status                 TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active', 'inactive', 'closed')),
    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: intake_submissions
-- One record per Excel workbook submitted by a client.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intake_submissions (
    submission_id           TEXT PRIMARY KEY,
    client_id               TEXT NOT NULL
                                REFERENCES clients(client_id),
    reporting_period_start  DATE NOT NULL,
    reporting_period_end    DATE NOT NULL,
    submitted_at            TIMESTAMP NOT NULL,
    file_path               TEXT NOT NULL,
    ingestion_status        TEXT NOT NULL DEFAULT 'pending'
                                CHECK (ingestion_status IN ('pending', 'processed', 'error')),
    processed_at            TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: oei_scores
-- One record per scored submission. Stores all five dimension scores,
-- the composite, and classification labels for each.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oei_scores (
    score_id                          TEXT PRIMARY KEY,
    client_id                         TEXT NOT NULL
                                          REFERENCES clients(client_id),
    submission_id                     TEXT NOT NULL
                                          REFERENCES intake_submissions(submission_id),
    period_date                       DATE NOT NULL,
    -- Dimension scores (0-100, higher = higher risk)
    strategic_saturation_score        INTEGER NOT NULL,
    governance_responsiveness_score   INTEGER NOT NULL,
    execution_visibility_score        INTEGER NOT NULL,
    reporting_integrity_score         INTEGER NOT NULL,
    org_sustainability_score          INTEGER NOT NULL,
    oei_composite_score               INTEGER NOT NULL,
    -- Classification labels
    strategic_saturation_class        TEXT NOT NULL,
    governance_responsiveness_class   TEXT NOT NULL,
    execution_visibility_class        TEXT NOT NULL,
    reporting_integrity_class         TEXT NOT NULL,
    org_sustainability_class          TEXT NOT NULL,
    composite_class                   TEXT NOT NULL,
    calculated_at                     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: score_overrides
-- One record per Managing Partner override of a calculated score.
-- A sub-category, dimension, or composite score may be overridden when
-- observed reality diverges from submitted telemetry data.
-- Raw intake data, signal calculation outputs, and audit log entries
-- are never modified — only the score at the interpretation layer.
-- is_active = FALSE marks an override that has been superseded by a newer one.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS score_overrides (
    override_id      TEXT PRIMARY KEY,
    submission_id    TEXT NOT NULL
                         REFERENCES intake_submissions(submission_id),
    client_id        TEXT NOT NULL
                         REFERENCES clients(client_id),
    override_type    TEXT NOT NULL
                         CHECK (override_type IN ('sub_category', 'dimension', 'composite')),
    target_id        TEXT NOT NULL,
    -- target_id values by override_type:
    --   sub_category -> sub-category ID e.g. '1.1', '3.4'
    --   dimension    -> dimension ID e.g. '1', '5'
    --   composite    -> literal string 'composite'
    original_score   REAL NOT NULL,
    override_score   REAL NOT NULL
                         CHECK (override_score >= 0.0 AND override_score <= 100.0),
    justification    TEXT NOT NULL,
    -- justification >= 50 words enforced at the application layer in scoring/overrides.py
    evidence_source  TEXT NOT NULL
                         CHECK (evidence_source IN (
                             'discovery_call',
                             'review_session',
                             'direct_observation',
                             'client_disclosure',
                             'document_review',
                             'third_party_data'
                         )),
    applied_by       TEXT NOT NULL DEFAULT 'managing_partner',
    applied_at       TIMESTAMP NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1
                         CHECK (is_active IN (0, 1))
);

-- -----------------------------------------------------------------------------
-- TABLE: signal_readings
-- One record per signal per submission (12 signals per submission).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_readings (
    reading_id          TEXT PRIMARY KEY,
    submission_id       TEXT NOT NULL
                            REFERENCES intake_submissions(submission_id),
    client_id           TEXT NOT NULL
                            REFERENCES clients(client_id),
    signal_name         TEXT NOT NULL,
    signal_value        DECIMAL NOT NULL,
    threshold_value     DECIMAL NOT NULL,
    threshold_breached  BOOLEAN NOT NULL DEFAULT FALSE,
    period_date         DATE NOT NULL,
    calculated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: alerts
-- One record per threshold breach. Created by alerts/detector.py.
--
-- Schema version note (Session 3):
--   reading_id and submission_id columns added to link each alert to its
--   originating signal_reading and intake_submission. reading_id is the
--   idempotency key used by the detector to prevent duplicate alert creation.
--   Existing databases require: ALTER TABLE alerts ADD COLUMN reading_id TEXT;
--                                ALTER TABLE alerts ADD COLUMN submission_id TEXT;
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    alert_id         TEXT PRIMARY KEY,
    client_id        TEXT NOT NULL
                         REFERENCES clients(client_id),
    submission_id    TEXT NOT NULL
                         REFERENCES intake_submissions(submission_id),
    reading_id       TEXT
                         REFERENCES signal_readings(reading_id),
    signal_name      TEXT NOT NULL,
    signal_value     DECIMAL NOT NULL,
    threshold_value  DECIMAL NOT NULL,
    alert_severity   TEXT NOT NULL
                         CHECK (alert_severity IN ('critical', 'elevated', 'watch')),
    alert_message    TEXT,
    triggered_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged     BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_at  TIMESTAMP,
    email_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    email_sent_at    TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: reports
-- One record per generated report (snapshot, monthly_brief, quarterly_review).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
    report_id                TEXT PRIMARY KEY,
    client_id                TEXT NOT NULL
                                 REFERENCES clients(client_id),
    report_type              TEXT NOT NULL
                                 CHECK (report_type IN ('snapshot', 'monthly_brief', 'quarterly_review')),
    period_date              DATE NOT NULL,
    generated_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_path                TEXT,
    ai_narrative_generated   BOOLEAN NOT NULL DEFAULT FALSE,
    delivered                BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at             TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: audit_log
-- Append-only event log. Every database write, AI generation, email, and
-- report event is recorded here. UPDATE and DELETE are blocked by triggers.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    log_id        TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    entity_type   TEXT,
    entity_id     TEXT,
    description   TEXT NOT NULL,
    performed_by  TEXT NOT NULL DEFAULT 'system',
    performed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata      TEXT  -- JSON stored as TEXT; SQLite has no native JSON column type
);

-- -----------------------------------------------------------------------------
-- TRIGGERS: Enforce append-only on audit_log at the database layer.
-- These fire regardless of which application layer issues the statement.
-- -----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not permitted');
END;

-- -----------------------------------------------------------------------------
-- TABLE: engagement_journal
-- Engagement Intelligence Journal — scoring spec Part 7.
-- Free-text observations recorded by the Managing Partner during discovery
-- calls, review sessions, and ad hoc communications. High and Medium
-- materiality entries are fed as QUALITATIVE_CONTEXT to the AI narrative
-- generator (anonymized). Journal entries are never shown directly to
-- clients. The Managing Partner controls which entries surface in reports
-- via the surfaced_in_report field.
-- Added: Session 4 (Report Generation).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engagement_journal (
    journal_id          TEXT PRIMARY KEY,
    client_id           TEXT NOT NULL
                            REFERENCES clients(client_id),
    submission_id       TEXT
                            REFERENCES intake_submissions(submission_id),
    entry_date          DATE NOT NULL,
    entry_type          TEXT NOT NULL
                            CHECK (entry_type IN (
                                'discovery_call',
                                'review_session',
                                'ad_hoc_communication',
                                'direct_observation',
                                'third_party_disclosure',
                                'pattern_note'
                            )),
    program_reference   TEXT NOT NULL DEFAULT 'General',
    intelligence_note   TEXT NOT NULL,
    materiality         TEXT NOT NULL
                            CHECK (materiality IN ('High', 'Medium', 'Low')),
    surfaced_in_report  TEXT NOT NULL DEFAULT 'No'
                            CHECK (surfaced_in_report IN ('Yes', 'No', 'Partial')),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- TABLE: report_drafts
-- One row per narrative section per report. Holds AI-generated or Managing
-- Partner-edited text while it awaits approval. Each section transitions
-- independently from 'pending_review' to 'approved'. finalize_report() in
-- reports/editor.py checks that every section is 'approved' before file
-- generation is unlocked. The AI never publishes directly; this table is
-- the gate between AI generation and document production.
-- Added: Session 4 (Report Generation).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_drafts (
    report_draft_id  TEXT PRIMARY KEY,
    report_id        TEXT NOT NULL
                         REFERENCES reports(report_id),
    section_name     TEXT NOT NULL,
    draft_text       TEXT NOT NULL,
    word_count       INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending_review'
                         CHECK (status IN ('pending_review', 'approved')),
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (report_id, section_name)
);
