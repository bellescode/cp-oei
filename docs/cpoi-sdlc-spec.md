# CRITERION PARTNERS — OEI INTELLIGENCE PLATFORM
## Full SDLC Specification
### IBM Bob Plan Mode — Pre-Execution Document

**Version:** 1.0
**Author:** Bernadette Akpeko Thompson, Managing Partner
**Classification:** Internal — Proprietary Architecture
**Build Target:** 15 hours
**Platform:** IBM Bob (AI-First Enterprise IDE)

---

## PART 1 — PROBLEM STATEMENT AND BUILD OBJECTIVE

### The Problem This Software Solves

The current OEIL operating model requires data to pass through:
Jira → manual export → Airtable → n8n automation → scoring spreadsheet → report template → email

That is 6 tool handoffs per client per month. Each handoff is a compliance gap, a formatting inconsistency risk, a version control problem, and a time cost. At 4 concurrent clients, the fragmentation multiplies.

### What This Platform Replaces

| Current Tool | Function | Replaced By |
|---|---|---|
| Airtable | Telemetry warehouse | Platform database layer |
| n8n | Automation/signal calculation | Platform workflow engine |
| Excel (manual) | Data normalization | Standardized Excel intake schema + bidirectional API |
| Google Docs/Word | Report drafting | Platform report generator |
| Manual email | Alert delivery | Platform notification engine |
| Separate OEI Snapshot records | Client history | Unified client intelligence record |

### What This Platform Does

One application that:
1. Ingests client data through a standardized Excel template via bidirectional API
2. Runs the OEI scoring model against ingested data automatically
3. Detects exception thresholds and fires alerts
4. Generates the monthly intelligence brief as a structured document
5. Generates the OEI Snapshot report for new engagements
6. Maintains a full audit trail of all data, scores, alerts, and decisions per client
7. Sends automated email notifications when triggers fire
8. Is accessible to Bernadette only — single-user, compliance-governed

---

## PART 2 — ARCHITECTURE SPECIFICATION

### System Name
**CPOI — Criterion Partners Operational Intelligence Platform**

### Architecture Pattern
**Three-Layer Monolith with API Gateway**
Not a microservices architecture. This is a single, governed application with clear internal layer separation. Chosen because:
- Single operator (no multi-tenant complexity needed now)
- Compliance traceability requires unified audit log
- 15-hour build constraint requires minimum moving parts
- IBM Bob's incremental modernization approach fits monolith-first, extract-later

```
┌─────────────────────────────────────────────────┐
│              CPOI PLATFORM                        │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────┐  │
│  │  INTAKE     │  │ INTELLIGENCE │  │ OUTPUT  │  │
│  │  LAYER      │→ │ ENGINE       │→ │ LAYER   │  │
│  └─────────────┘  └──────────────┘  └─────────┘  │
│         ↑                                  ↓      │
│  Excel Bidirectional API          Email + PDF     │
│  (Client Data In)                 (Reports Out)   │
└─────────────────────────────────────────────────┘
```

### Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| Backend | Python (FastAPI) | IBM Bob native; fast to build; enterprise-grade; async-ready |
| Database | SQLite → PostgreSQL path | SQLite for v1 (zero setup, file-based, auditable); migrate to Postgres when multi-client volume warrants |
| Excel API | openpyxl + xlwings | Bidirectional read/write to .xlsx; client pushes data in, platform pulls it out |
| AI Agent | IBM Granite (classification) + local model (narrative) | Bob orchestrates: Granite for scoring/classification. Narrative generation runs on a LOCAL model only. Client data is never sent to an external AI API. Anonymization applied before any AI processing regardless. |
| Report Generation | Python-docx + Jinja2 templates | Structured template population; outputs .docx and .pdf |
| Email Engine | SMTP via SendGrid API | Triggered alerts and report delivery |
| Frontend | Streamlit (v1) | Fast to build, single-user dashboard; no public-facing exposure |
| Auth | Single-user token auth (v1) | One operator; no OAuth complexity needed now |
| Audit Log | Append-only SQLite table | Every data ingestion, score calculation, alert, and report generation is logged with timestamp |
| Hosting | Local build → Vultr Dedicated (US data center) | Build locally first; deploy to US-based dedicated server. US jurisdiction matches client base. Access via Tailscale VPN. |
| Secrets Management | Infisical | All credentials, API keys, and encryption keys stored in Infisical. Never in code, .env files, or plain environment variables. |

---

## PART 3 — DATA ARCHITECTURE

### The Excel Intake Schema (The Universal Intermediary)

Every client receives one Excel workbook: **CP_OEI_DataIntake_[ClientName]_[YYYY_MM].xlsx**

The workbook has 8 standardized tabs. The client fills in data. The platform reads it. This never changes. The schema is the compliance contract.

---

**TAB 1 — INITIATIVES**
```
Column A: Initiative_ID (auto, client assigns)
Column B: Initiative_Name
Column C: Priority_Classification (Critical / High / Medium / Low)
Column D: Status_Reported (Green / Yellow / Red)
Column E: Program_Owner
Column F: Start_Date
Column G: Target_Completion_Date
Column H: Current_Phase
Column I: Open_Blockers (count)
Column J: Critical_Blockers (count)
Column K: Last_Status_Update_Date
Column L: Notes
```

**TAB 2 — ESCALATIONS**
```
Column A: Escalation_ID
Column B: Initiative_ID (FK)
Column C: Date_Raised
Column D: Raised_By_Level (IC / Manager / Director / VP / C-Suite)
Column E: Date_Resolved
Column F: Resolved_At_Level (same options)
Column G: Resolution_Days (auto-calc)
Column H: Escalation_Category (Resource / Dependency / Scope / Governance / Technical)
Column I: Description (brief)
Column J: Outcome
```

**TAB 3 — DEPENDENCIES**
```
Column A: Dependency_ID
Column B: Upstream_Initiative_ID
Column C: Downstream_Initiative_ID
Column D: Dependency_Type (Blocking / Enabling / Informational)
Column E: Status (Active / Resolved / At-Risk)
Column F: Days_Open
Column G: Owner
Column H: Notes
```

**TAB 4 — RESOURCE_UTILIZATION**
```
Column A: Team_Name
Column B: Team_Size (headcount)
Column C: Allocated_Programs (count)
Column D: Estimated_Utilization_Pct
Column E: Open_Requisitions
Column F: Avg_Days_Open_Reqs
Column G: Notes
```

**TAB 5 — GOVERNANCE_EVENTS**
```
Column A: Event_ID
Column B: Event_Type (Decision / Approval / Review / Steering Committee)
Column C: Date_Scheduled
Column D: Date_Occurred
Column E: Delay_Days (auto-calc)
Column F: Decision_Made (Y/N)
Column G: Initiative_ID (FK, if applicable)
Column H: Notes
```

**TAB 6 — REPORTING_VARIANCE**
```
Column A: Initiative_ID
Column B: Reported_Status (from Tab 1)
Column C: Actual_Blocker_Count (from Tab 1)
Column D: Milestone_At_Risk (Y/N)
Column E: Variance_Detected (auto-calculated by platform)
Column F: Leadership_Aware (Y/N)
Column G: Notes
```

**TAB 7 — HEADCOUNT_SIGNALS**
```
Column A: Role_Title
Column B: Team
Column C: Tenure_Months
Column D: Program_Assignment
Column E: Escalation_Count_Last_90_Days
Column F: Retention_Risk_Flag (High / Medium / Low — client fills)
Column G: Notes
```

**TAB 8 — METADATA**
```
Client_Name:
Reporting_Period_Start:
Reporting_Period_End:
Submitted_By:
Submission_Date:
Engagement_Type: (Snapshot / OEIL_Month_1 / OEIL_Month_2 / etc.)
Data_Version: (for audit trail)
```

---

### Platform Database Schema (SQLite v1)

**Table: clients**
```sql
CREATE TABLE clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT NOT NULL,
  engagement_type TEXT NOT NULL, -- 'snapshot' or 'oeil'
  engagement_start_date DATE,
  monthly_retainer DECIMAL,
  oei_score_intake INTEGER,
  status TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Table: intake_submissions**
```sql
CREATE TABLE intake_submissions (
  submission_id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(client_id),
  reporting_period_start DATE,
  reporting_period_end DATE,
  submitted_at TIMESTAMP,
  file_path TEXT,
  ingestion_status TEXT, -- 'pending', 'processed', 'error'
  processed_at TIMESTAMP
);
```

**Table: oei_scores**
```sql
CREATE TABLE oei_scores (
  score_id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(client_id),
  submission_id TEXT REFERENCES intake_submissions(submission_id),
  period_date DATE,
  -- Five dimension scores
  strategic_saturation_score INTEGER,
  governance_responsiveness_score INTEGER,
  execution_visibility_score INTEGER,
  reporting_integrity_score INTEGER,
  org_sustainability_score INTEGER,
  oei_composite_score INTEGER,
  -- Classification for each
  strategic_saturation_class TEXT,
  governance_responsiveness_class TEXT,
  execution_visibility_class TEXT,
  reporting_integrity_class TEXT,
  org_sustainability_class TEXT,
  composite_class TEXT,
  calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Table: signal_readings**
```sql
CREATE TABLE signal_readings (
  reading_id TEXT PRIMARY KEY,
  submission_id TEXT REFERENCES intake_submissions(submission_id),
  client_id TEXT REFERENCES clients(client_id),
  signal_name TEXT NOT NULL,
  signal_value DECIMAL,
  threshold_value DECIMAL,
  threshold_breached BOOLEAN DEFAULT FALSE,
  period_date DATE,
  calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Table: alerts**
```sql
CREATE TABLE alerts (
  alert_id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(client_id),
  signal_name TEXT,
  signal_value DECIMAL,
  threshold_value DECIMAL,
  alert_severity TEXT, -- 'critical', 'elevated', 'watch'
  alert_message TEXT,
  triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  acknowledged BOOLEAN DEFAULT FALSE,
  acknowledged_at TIMESTAMP,
  email_sent BOOLEAN DEFAULT FALSE,
  email_sent_at TIMESTAMP
);
```

**Table: reports**
```sql
CREATE TABLE reports (
  report_id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(client_id),
  report_type TEXT, -- 'snapshot', 'monthly_brief', 'quarterly_review'
  period_date DATE,
  generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  file_path TEXT,
  ai_narrative_generated BOOLEAN DEFAULT FALSE,
  delivered BOOLEAN DEFAULT FALSE,
  delivered_at TIMESTAMP
);
```

**Table: audit_log**
```sql
CREATE TABLE audit_log (
  log_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  description TEXT,
  performed_by TEXT DEFAULT 'system',
  performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  metadata JSON
);
```

---

## PART 4 — SCORING ENGINE SPECIFICATION

This is the IP. IBM Bob governs this code with maximum compliance strictness.

### Signal Calculation Functions (12 signals, deterministic Python)

```python
# Every function takes normalized data from the Excel intake
# Returns: signal_value (float), threshold_breached (bool), severity (str)

def calc_governance_latency_index(escalations_df):
    """Avg days from escalation raised to resolved"""
    resolved = escalations_df[escalations_df['Date_Resolved'].notna()]
    avg_days = resolved['Resolution_Days'].mean()
    return avg_days, avg_days > 10, classify_severity(avg_days, [10, 15, 21])

def calc_escalation_suppression_rate(escalations_df):
    """% of escalations resolved below VP level"""
    below_vp = escalations_df[
        escalations_df['Resolved_At_Level'].isin(['IC','Manager','Director'])
    ]
    rate = len(below_vp) / len(escalations_df) if len(escalations_df) > 0 else 0
    return rate, rate > 0.60, classify_severity(rate, [0.60, 0.70, 0.80])

def calc_false_green_indicator(initiatives_df):
    """Programs reporting green with >2 open critical blockers"""
    false_greens = initiatives_df[
        (initiatives_df['Status_Reported'] == 'Green') &
        (initiatives_df['Critical_Blockers'] > 2)
    ]
    count = len(false_greens)
    return count, count > 0, 'critical' if count > 0 else 'clear'

def calc_reporting_divergence_score(initiatives_df):
    """Gap between reported status and actual blocker density"""
    green_programs = initiatives_df[initiatives_df['Status_Reported'] == 'Green']
    with_blockers = green_programs[green_programs['Open_Blockers'] > 0]
    divergence = len(with_blockers) / len(green_programs) if len(green_programs) > 0 else 0
    return divergence, divergence > 0.30, classify_severity(divergence, [0.30, 0.45, 0.60])

def calc_priority_collision_index(initiatives_df, resources_df):
    """Programs sharing top-3 resource dependencies"""
    critical_programs = initiatives_df[
        initiatives_df['Priority_Classification'] == 'Critical'
    ]
    collision_count = len(critical_programs) - 3 if len(critical_programs) > 3 else 0
    return collision_count, collision_count > 2, classify_severity(collision_count, [2,4,6])

def calc_platform_utilization_pressure(resources_df):
    """Max utilization across teams"""
    max_util = resources_df['Estimated_Utilization_Pct'].max()
    return max_util, max_util > 120, classify_severity(max_util, [120, 135, 150])

def calc_initiative_saturation_ratio(initiatives_df, resources_df):
    """Active programs per delivery team"""
    active = len(initiatives_df[initiatives_df['Status_Reported'] != 'Cancelled'])
    teams = len(resources_df)
    ratio = active / teams if teams > 0 else 0
    return ratio, ratio > 3, classify_severity(ratio, [3, 4, 5])

def calc_dependency_fragility_score(dependencies_df):
    """Unresolved cross-program dependencies >7 days old"""
    fragile = dependencies_df[
        (dependencies_df['Status'] == 'At-Risk') &
        (dependencies_df['Days_Open'] > 7)
    ]
    count = len(fragile)
    return count, count > 5, classify_severity(count, [5, 8, 12])

def calc_headcount_stability_index(headcount_df, resources_df):
    """Open roles on critical path programs"""
    open_reqs = resources_df['Open_Requisitions'].sum()
    avg_days = resources_df['Avg_Days_Open_Reqs'].mean()
    risk_score = open_reqs * (avg_days / 30)
    return risk_score, risk_score > 4, classify_severity(risk_score, [4, 7, 10])

def calc_reprioritization_frequency(initiatives_df):
    """Estimated from priority classification distribution"""
    critical_count = len(initiatives_df[
        initiatives_df['Priority_Classification'] == 'Critical'
    ])
    total = len(initiatives_df)
    inflation_rate = critical_count / total if total > 0 else 0
    return inflation_rate, inflation_rate > 0.70, classify_severity(inflation_rate, [0.70, 0.80, 0.90])

def calc_reactive_work_ratio(resources_df):
    """Inverse of planned utilization headroom"""
    avg_util = resources_df['Estimated_Utilization_Pct'].mean()
    reactive_proxy = max(0, (avg_util - 80) / avg_util) if avg_util > 0 else 0
    return reactive_proxy, reactive_proxy > 0.30, classify_severity(reactive_proxy, [0.30, 0.40, 0.50])

def classify_severity(value, thresholds):
    """thresholds = [watch, elevated, critical]"""
    if value >= thresholds[2]:
        return 'critical'
    elif value >= thresholds[1]:
        return 'elevated'
    elif value >= thresholds[0]:
        return 'watch'
    return 'clear'
```

### OEI Dimension Scoring (Composite from Signals)

```python
def calculate_dimension_scores(signals: dict) -> dict:
    """
    Maps 12 signals to 5 OEI dimensions.
    Each dimension scored 0-100 (higher = higher risk/lower capability).
    Returns dimension scores and composite.
    """

    # Strategic Saturation (signals: priority_collision, saturation_ratio, reprioritization)
    strategic_saturation = weighted_score([
        (signals['priority_collision_index'], 0.35),
        (signals['initiative_saturation_ratio'], 0.35),
        (signals['reprioritization_frequency'], 0.30)
    ])

    # Governance Responsiveness (signals: latency, suppression)
    governance_responsiveness = weighted_score([
        (signals['governance_latency_index'], 0.55),
        (signals['escalation_suppression_rate'], 0.45)
    ])

    # Execution Visibility (signals: dependency fragility, headcount, reactive work)
    execution_visibility = weighted_score([
        (signals['dependency_fragility_score'], 0.40),
        (signals['headcount_stability_index'], 0.30),
        (signals['reactive_work_ratio'], 0.30)
    ])

    # Reporting Integrity (signals: false green, divergence)
    reporting_integrity = weighted_score([
        (signals['false_green_indicator'], 0.55),
        (signals['reporting_divergence_score'], 0.45)
    ])

    # Organizational Sustainability (signals: platform utilization, saturation)
    org_sustainability = weighted_score([
        (signals['platform_utilization_pressure'], 0.50),
        (signals['initiative_saturation_ratio'], 0.50)
    ])

    composite = round(
        (strategic_saturation * 0.20) +
        (governance_responsiveness * 0.20) +
        (execution_visibility * 0.20) +
        (reporting_integrity * 0.20) +
        (org_sustainability * 0.20)
    )

    return {
        'strategic_saturation': strategic_saturation,
        'governance_responsiveness': governance_responsiveness,
        'execution_visibility': execution_visibility,
        'reporting_integrity': reporting_integrity,
        'org_sustainability': org_sustainability,
        'oei_composite': composite
    }
```

---

## PART 5 — AI AGENT SPECIFICATION

### IBM Bob Orchestration Logic

Bob routes tasks as follows:

| Task | Model Routed To | Rationale |
|---|---|---|
| Signal classification (is this a breach?) | IBM Granite (fast, cheap, deterministic) | Binary decision, rule-based |
| Dimension score interpretation | IBM Granite | Pattern classification, not synthesis |
| Monthly brief narrative generation | Local model (self-hosted) | Requires contextual synthesis and executive voice; MUST run locally because it processes client engagement context. Client data never leaves the server. |
| Exception alert message drafting | Local model (self-hosted) | Requires specificity; processes client signal data, must stay local |
| Report section population | Local model (self-hosted) | Requires narrative coherence; processes client findings, must stay local |
| Audit log entries | Granite | Structured logging, no synthesis needed |

**FINALIZED AI PRIVACY DECISION:**
All narrative generation that touches client engagement context runs on a
LOCAL self-hosted model. No client data, even anonymized, is sent to an
external AI API for narrative generation. The local model produces a first
draft. The Managing Partner edits it. This is the hybrid approach: the model
speeds up drafting, the human judgment finalizes the intelligence. The local
model choice (e.g., a Llama, Mistral, or IBM Granite variant sized to the
server) is confirmed during Session 4. Anything flagged for the external
report is surfaced internally in bullet format for the Managing Partner to
write the external narrative.

### AI Agent Prompt Templates (Governed — Do Not Modify Without Version Update)

**Prompt: Monthly Brief Narrative Section**
```
SYSTEM: You are the intelligence synthesis layer for Criterion Partners, a boutique 
Operational Executive Intelligence firm. Your output will be delivered directly 
to C-suite executives. Write with precision. No hedging. No filler. Every sentence 
must be specific, grounded in the data provided, and written as if the Managing 
Partner is speaking directly to the executive sponsor.

DATA INPUT:
- Client: {client_name}
- Reporting Period: {period}
- OEI Composite: {composite_score} ({composite_class})
- Dimension Scores: {dimension_json}
- Signals Breached: {breached_signals}
- Prior Month Composite: {prior_composite}
- Movement: {score_delta} points ({direction})

TASK: Write the Executive Summary narrative section for the monthly 
OEI Intelligence Brief. 
- 3 paragraphs maximum
- Paragraph 1: What the composite score movement indicates about the 
  organization's current operational intelligence posture
- Paragraph 2: The single most material finding from this period and 
  its specific business implication
- Paragraph 3: The one governance action most likely to produce 
  measurable signal improvement in the next 30 days
- Do not use em dashes
- Do not hedge
- Do not list more than one recommendation
- Tone: direct, measured, authoritative
```

**Prompt: Exception Alert Message**
```
SYSTEM: Write a brief operational intelligence alert for {client_name}. 
This is sent to the executive sponsor when a signal threshold is breached. 
Maximum 4 sentences. Be specific. State the signal, the measured value, 
the threshold, and the specific business risk. Do not recommend actions. 
State what the intelligence indicates.

SIGNAL: {signal_name}
MEASURED VALUE: {signal_value}
THRESHOLD: {threshold_value}
CONTEXT DATA: {context_json}
```

---

## PART 6 — APPLICATION MODULES (BUILD ORDER)

IBM Bob will execute these modules in sequence. Each module is a discrete, 
testable unit. Do not begin the next module until the current one passes validation.

---

### MODULE 1 — Data Intake Engine
**Estimated Build Time: 2.5 hours**
**IBM Bob Governance Level: HIGH**

**What it does:**
- Reads the standardized Excel intake workbook
- Validates all 8 tabs against the defined schema
- Rejects malformed data with a specific error message
- Normalizes data into Python dataframes
- Writes clean data to the SQLite database
- Logs every ingestion event to audit_log

**Inputs:** .xlsx file matching intake schema
**Outputs:** Populated database tables, audit log entry
**Validation criteria:**
- All required columns present
- Date formats uniform (YYYY-MM-DD)
- No null values in required fields
- Foreign key references valid across tabs
- Metadata tab complete

**Failure behavior:**
- Do not partially ingest
- Log full error to audit_log with specific field that failed
- Return error report to user interface

---

### MODULE 2 — Signal Calculation Engine
**Estimated Build Time: 2.5 hours**
**IBM Bob Governance Level: MAXIMUM (this is the IP)**

**What it does:**
- Takes normalized dataframes from Module 1
- Runs all 12 signal calculation functions
- Stores signal_readings records
- Identifies threshold breaches
- Triggers alert creation for any breach

**Inputs:** Clean dataframes from Module 1
**Outputs:** 12 signal_reading records, alerts table populated
**Validation criteria:**
- All 12 signals calculated for every submission
- Signal values stored with 4 decimal precision
- Every threshold breach creates exactly one alert record
- No alert created without a corresponding signal_reading

**Governance requirement:**
Signal calculation functions are READ ONLY after initial approval.
Any modification requires a version increment and audit log entry.
IBM Bob enforces this with change control flags.

---

### MODULE 3 — OEI Scoring Engine
**Estimated Build Time: 1.5 hours**
**IBM Bob Governance Level: MAXIMUM**

**What it does:**
- Takes signal readings from Module 2
- Calculates 5 dimension scores
- Calculates OEI composite
- Applies risk classification labels
- Stores oei_scores record
- Compares to prior period and calculates delta

**Inputs:** signal_readings for current submission
**Outputs:** oei_scores record, score delta calculated
**Validation criteria:**
- All dimension scores between 0 and 100
- Composite score equals weighted average of dimensions within ±1 rounding
- Classification labels drawn only from approved vocabulary
- Delta calculated only when prior period record exists

---

### MODULE 4 — Alert and Notification Engine
**Estimated Build Time: 2 hours**
**IBM Bob Governance Level: HIGH**

**What it does:**
- Reads pending alerts from alerts table
- Routes to local AI model for message drafting (client data stays on server)
- Formats email with standardized CP header
- Sends via SendGrid to configured recipient
- Updates alert record: email_sent = TRUE, email_sent_at = now()
- Logs to audit_log

**Alert email format:**
```
FROM: intelligence@criterion-partners.com
TO: [configured executive sponsor email per client]
SUBJECT: [SEVERITY] Operational Intelligence Alert — [Client] — [Signal Name]
BODY:
  [CP letterhead]
  [AI-generated 4-sentence alert message]
  [Signal data table: Signal | Value | Threshold | Severity]
  [Footer: This alert was generated by the CPOI Platform. 
   Full analysis will be included in your next Monthly Intelligence Brief.]
```

**Inputs:** Unacknowledged alerts, client email config
**Outputs:** Sent emails, updated alert records, audit log entries
**Validation criteria:**
- No email sent without a valid alert record
- No duplicate emails for same alert_id
- All emails logged before marked as sent
- SendGrid delivery confirmation stored

---

### MODULE 5 — Report Generation Engine
**Estimated Build Time: 3.5 hours**
**IBM Bob Governance Level: HIGH**

**What it does:**

**For OEI Snapshot reports:**
- Pulls all signal and score data for the submission
- Routes to local AI model for narrative sections (client data stays on server)
- Populates the 12-page report template
- Generates .docx and .pdf versions
- Stores file path in reports table

**For OEIL Monthly Briefs:**
- Pulls current and prior period data for comparison
- Routes to local AI model for executive summary narrative (client data stays on server)
- Populates the 8–10 page brief template
- Includes score trajectory chart (matplotlib)
- Generates .docx and .pdf
- Stores file path in reports table

**Template structure (Jinja2):**
- Fixed sections (headers, client name, period, scores) populated from database
- Variable sections (narratives, interpretations) populated from local AI model output, then edited by Managing Partner
- Score visualization tables auto-populated from oei_scores
- Signal readings table auto-populated from signal_readings

**Inputs:** Submission ID, report type
**Outputs:** .docx file, .pdf file, reports table record
**Validation criteria:**
- All template variables populated before render
- No AI-generated text inserted without length validation (min 50, max 300 words per section)
- Both file formats generated successfully
- Report record created before files written

---

### MODULE 6 — Dashboard Interface (Streamlit)
**Estimated Build Time: 2.5 hours**
**IBM Bob Governance Level: STANDARD**

**Pages:**

**Page 1 — Client Overview**
- Client list with OEI composite scores
- Score trend sparklines per client
- Active alerts count per client
- Last submission date per client

**Page 2 — Client Detail**
- Select client from dropdown
- Current OEI scorecard (5 dimensions + composite)
- Score history chart (all periods)
- Active alerts table
- Signal readings table (current period)
- Quick actions: Generate Brief, Acknowledge Alert, View Reports

**Page 3 — Data Intake**
- Upload Excel intake file
- Select client and engagement type
- Run ingestion button
- Validation results display
- Ingestion log

**Page 4 — Report Center**
- Reports table (all clients, all types)
- Download buttons for .docx and .pdf
- Generate new report button
- Delivery status tracking

**Page 5 — Audit Log**
- Full audit_log table with filters
- Export to CSV button
- Date range filter
- Event type filter

---

### MODULE 7 — Excel Bidirectional API
**Estimated Build Time: 1 hour**
**IBM Bob Governance Level: HIGH**

**What it does:**
Two-way functionality:

INBOUND (client → platform):
- Client fills the standardized Excel template
- Uploads via Page 3 of dashboard
- Module 1 processes it

OUTBOUND (platform → Excel):
- Platform can write back to a client's Excel file
- Specifically: writes a VARIANCE_FLAGS tab after processing
- Flags rows where platform detected issues the client did not flag
- Client receives a marked-up version of their own submission

**VARIANCE_FLAGS tab written back to client Excel:**
```
Column A: Tab_Source
Column B: Row_Reference
Column C: Flag_Type (False_Green_Detected / Missing_Dependency / Utilization_Risk)
Column D: Platform_Finding
Column E: Recommended_Client_Review
Column F: Severity
```

This closes the loop: client submits data, platform processes it, platform returns findings in the same Excel format, client reviews before the briefing call. Compliance-friendly. No data leaves the Excel ecosystem unexpectedly.

---

## PART 7 — BUILD SCHEDULE (15 HOURS)

| Session | Duration | Modules | IBM Bob Plan Mode Step |
|---|---|---|---|
| Session 1 | 3 hours | DB schema + Module 1 (Intake) | Define schema → Bob generates, human approves → validate ingestion |
| Session 2 | 3 hours | Module 2 (Signals) + Module 3 (Scoring) | Define signal logic → Bob generates functions → human validates math → run test submission |
| Session 3 | 2.5 hours | Module 4 (Alerts) + SendGrid config | Define alert logic → Bob generates → configure SendGrid → test alert email |
| Session 4 | 3.5 hours | Module 5 (Report Generation) | Build templates → define Jinja2 vars → configure local model prompts → test report output |
| Session 5 | 2 hours | Module 6 (Dashboard) + Module 7 (Excel API) | Build Streamlit pages → wire to database → test bidirectional Excel write |
| Session 6 | 1 hour | End-to-end integration test + audit log validation | Run a full test submission through all 7 modules → verify audit trail complete |
| **Total** | **15 hours** | All 7 modules | Full SDLC from schema to working application |

---

## PART 8 — COMPLIANCE AND GOVERNANCE REQUIREMENTS

### Data Handling
- All client data stored on the Vultr US dedicated server, encrypted at rest
- SQLite database file encrypted at rest using SQLCipher (AES-256)
- Excel intake files stored in a dedicated /intake directory, encrypted, not in the database
- No client data transmitted to any external service except:
  - SendGrid (alert email notification text only, no raw client data)
- All AI narrative generation runs on a LOCAL self-hosted model. No client
  data is transmitted to any external AI API. The anonymization function below
  is applied as defense-in-depth before any data reaches even the local model,
  so that logs, prompt caches, and any future model interaction never contain
  identifiable client information.

### AI Prompt Data Anonymization Rule
Before any data is passed to the local AI model, the ingestion layer applies:
```python
def anonymize_for_ai(client_data: dict) -> dict:
    """
    Remove all client-identifiable information before AI processing.
    Replace with standardized placeholders.
    """
    anonymized = client_data.copy()
    anonymized['client_name'] = 'CLIENT_A'
    anonymized['program_names'] = [f'PROGRAM_{i}' for i in range(len(anonymized.get('program_names', [])))]
    anonymized['owner_names'] = ['OWNER_REDACTED'] * len(anonymized.get('owner_names', []))
    return anonymized
```

### Audit Requirements
- Every database write event logged to audit_log
- Every AI generation logged (prompt hash, model used, token count, timestamp)
- Every report generation logged
- Every email sent logged
- Audit log is append-only (no UPDATE or DELETE on audit_log permitted)
- IBM Bob enforces this constraint at the code generation level

### Version Control
- Signal calculation functions versioned (v1.0, v1.1, etc.)
- Any change to scoring logic requires:
  1. New version number
  2. Audit log entry with reason for change
  3. Re-run of all existing client submissions against new version
  4. Score delta comparison report

---

## PART 9 — IBM BOB PLAN MODE EXECUTION INSTRUCTIONS

When opening IBM Bob for each session, use this exact Plan Mode input:

**Session 1 Plan Mode Input:**
```
I am building a single-operator enterprise operational intelligence platform 
called CPOI. This session covers: SQLite database schema creation and the 
Data Intake Module (Excel file ingestion).

Compliance requirements:
- Append-only audit log
- Full validation before any write
- Reject partial ingestions
- SQLCipher encryption on database file

Schema is defined in the SDLC spec document [attach document].
Module 1 spec is defined in Part 6, Module 1.

Generate the following in order and pause for approval after each:
1. Database schema (all tables)
2. Excel validation function
3. Ingestion function
4. Audit log write function
5. Unit tests for all four

Do not proceed to the next item until I approve the previous one.
```

Repeat this pattern for each session, referencing the specific module spec.

---

## PART 10 — POST-BUILD ROADMAP

These are OUT OF SCOPE for the 15-hour build but represent the natural evolution path:

| Phase | Feature | Trigger |
|---|---|---|
| v1.1 | Direct Jira API integration (replaces Excel for Jira data) | When 3+ clients use Jira |
| v1.2 | Multi-client concurrent processing | When 4th client onboards |
| v1.3 | PostgreSQL migration | When database exceeds 500MB |
| v2.0 | Client-facing portal (read-only dashboard per client) | When clients request direct access |
| v2.1 | Benchmark comparison across client portfolio | When 5+ clients have 6+ months of data |
| v3.0 | Predictive intelligence (ML on historical signal patterns) | When telemetry density warrants it |

---

*This document is the complete specification for the CPOI v1.0 build.*
*IBM Bob Plan Mode input for each session is derived from the relevant section of this document.*
*Do not begin coding without this document approved and version-controlled.*

**Document Status: APPROVED FOR BUILD**
**Version: 1.0**
**Last Updated: May 2026**

---
---

# ADDENDUM A — BUILDING WITH CLAUDE CODE

**Added:** May 2026
**Purpose:** This addendum adapts the build for Claude Code (Anthropic's
agentic coding tool) as an alternative to IBM Bob. The specification above
is tool-agnostic. Everything in Parts 1 through 10 applies unchanged. This
addendum provides the Claude Code setup and the per-session prompts.

---

## A.1 — WHY THIS WORKS WITH CLAUDE CODE

Claude Code runs in your terminal or desktop, reads and writes your file
system, executes code, runs tests, and iterates. It can build this entire
platform end to end. The discipline that IBM Bob enforced through Plan Mode
is reproduced here by you, through three rules:

1. **One module per session.** Do not let Claude Code move to the next
   module until the current one is built, run, and tested.
2. **Approve before execution.** Ask Claude Code to explain its plan for
   each file before it writes it. This is your manual Plan Mode.
3. **Test at every step.** No module is "done" until its validation
   criteria (defined in Part 6 of this spec) pass.

The 15-hour estimate holds because the constraint was always the tightness
of the spec, not the tool. The spec is done.

---

## A.2 — ONE-TIME SETUP

**Step 1 — Install Claude Code**
Follow the current installation instructions at the official Anthropic
documentation (docs.claude.com). Claude Code requires Node.js. Install
Node.js first if it is not already on your Windows machine, then install
Claude Code per the documented method for your platform.

**Step 2 — Create the project directory**
```bash
mkdir cpoi
cd cpoi
```

**Step 3 — Set up a Python virtual environment**
```bash
python -m venv venv
venv\Scripts\activate
```

**Step 4 — Place the spec where Claude Code can read it**
Copy this SDLC spec document and the scoring/platform spec into the
project directory (for example, into a /docs subfolder). Claude Code
can read these files directly, which means it builds against your actual
specification rather than your description of it.

```
cpoi/
  docs/
    cpoi-sdlc-spec.md
    cpoi-scoring-and-platform-spec.md
  venv/
```

**Step 5 — Start Claude Code in the project directory**
Launch Claude Code from inside the cpoi folder so it has the project
context. Confirm it can see the docs folder before you begin.

---

## A.3 — GOVERNANCE RULES TO STATE AT THE START OF EVERY SESSION

Paste this at the start of each session so the discipline carries across
the whole build:

```
Before we begin: governance rules for this entire build.

1. We build one module at a time. Do not write code for a module until
   I confirm the prior module passes its validation criteria.
2. Before writing any file, show me your plan for that file: what it does,
   its inputs, its outputs, and how it meets the validation criteria in
   the spec. Wait for my approval before writing.
3. Code quality is compliance-grade. Every function has typed inputs and
   outputs and a docstring. No bare except clauses. Explicit error handling.
   Structured logging, not print statements. No placeholder or stubbed
   logic presented as complete.
4. The scoring methodology is proprietary IP. When we build it, it is
   version-controlled and every calculation matches the scoring spec exactly.
5. Client data privacy is absolute. Narrative generation uses a local model
   only. No client data is ever sent to an external API. The anonymization
   function runs before any AI processing.
6. After each module, write the tests defined in the spec and run them.
   A module is not done until its tests pass.
7. No emojis anywhere in code, comments, or output.

The full specification is in docs/cpoi-sdlc-spec.md and
docs/cpoi-scoring-and-platform-spec.md. Read the relevant section before
each module. Confirm you have read these rules and we will begin.
```

---

## A.4 — PER-SESSION PROMPTS

Each prompt maps to a session in the Part 7 build schedule. Run them in order.

---

### SESSION 1 — Database Schema + Module 1 (Data Intake)
**Target: 3 hours**

```
Session 1. Read docs/cpoi-sdlc-spec.md Part 3 (Data Architecture) and
Part 6 Module 1 (Data Intake Engine).

We are building, in this order, pausing for my approval after each:

1. The SQLite database schema for all tables defined in Part 3, with
   SQLCipher encryption configured. The audit_log table must be append-only
   (enforce with a trigger preventing UPDATE and DELETE).
2. The Excel validation function that checks an uploaded intake workbook
   against the 8-tab schema in Part 3. It rejects malformed data with a
   specific error naming the field that failed. No partial ingestion.
3. The ingestion function that normalizes validated data into the database.
4. The audit log write function that records every ingestion event.
5. Unit tests for all four, covering both valid and malformed input.

Show me your plan for item 1 before writing it. Do not proceed to item 2
until I confirm item 1 passes its tests.
```

---

### SESSION 2 — Module 2 (Signals) + Module 3 (Scoring)
**Target: 3 hours**

```
Session 2. Read docs/cpoi-scoring-and-platform-spec.md Parts 4 and 5 (the
granular scoring specifications and composite calculation), and
docs/cpoi-sdlc-spec.md Part 6 Modules 2 and 3.

This is the proprietary IP. Maximum care.

Build in this order, pausing for approval after each:

1. All 12 signal calculation functions exactly as specified in the scoring
   spec. Each returns the signal value, whether the threshold is breached,
   and the severity. Every threshold and weight matches the spec precisely.
2. The missing-data protocol: absent data scores at the specified default,
   never excluded unless a documented exception flag is set.
3. The 5 dimension scoring functions and the composite calculation, with
   all weights exactly as specified.
4. The manual override capability: a score can be overridden with a required
   50-word justification, stored separately from the calculated score, logged
   to audit. Raw data is never modified.
5. Unit tests validating every signal and every dimension against hand-
   calculated expected values.

Show me your plan for the signal functions before writing. Validate the
math against the spec tables. Do not move to dimension scoring until the
signal tests pass.
```

---

### SESSION 3 — Module 4 (Alerts) + SendGrid
**Target: 2.5 hours**

```
Session 3. Read docs/cpoi-sdlc-spec.md Part 6 Module 4 and Part 5 (AI agent
spec, noting that narrative and alert drafting use a LOCAL model, not an
external API).

Build in this order, pausing for approval after each:

1. The alert detection logic: when a signal breach is recorded, create
   exactly one alert record. No alert without a corresponding signal reading.
2. The local-model integration for drafting the 4-sentence alert message.
   Client data stays on the server. Anonymization runs before the model call.
3. The SendGrid integration that sends the alert email in the format defined
   in Module 4, updates the alert record, and logs to audit. No email sent
   without a valid alert record. No duplicate emails for the same alert.
4. A circuit breaker: if SendGrid is unavailable, queue the alert locally
   and retry, never silently drop it.
5. Unit tests, including a test that SendGrid failure does not lose an alert.

Use a test SendGrid key and a test recipient. Show me the alert email format
before sending the first test.
```

---

### SESSION 4 — Module 5 (Report Generation)
**Target: 3.5 hours — the hardest session, do nothing else alongside it**

```
Session 4. Read docs/cpoi-sdlc-spec.md Part 6 Module 5 and the report
template structure referenced there.

This session sets up the local AI model for narrative generation. Confirm
the server has adequate compute for the chosen model before we start; if
running locally on CPU, select a model sized accordingly.

Build in this order, pausing for approval after each:

1. The Jinja2 templates: the 12-page OEI Snapshot report and the 8-to-10
   page OEIL monthly brief. Fixed sections populate from the database.
   Variable narrative sections are placeholders the model fills.
2. The local-model narrative generation: pulls the score and signal data
   plus the Engagement Intelligence Journal entries (High and Medium
   materiality) as context. Anonymization applied. Produces a first draft.
   Length validation: 50 to 300 words per section.
3. The hybrid editing flow: the draft is presented for the Managing Partner
   to edit before the report is finalized. The AI never publishes directly.
4. The docx and pdf generation, with the per-report watermark (client,
   sponsor, date, unique report ID) from the platform spec.
5. The reports table record and audit logging.
6. Tests: template renders with all variables populated; no unfilled
   placeholders; both file formats generate; watermark present.

Show me a rendered sample report with synthetic data before we finalize.
```

---

### SESSION 5 — Module 6 (Dashboard) + Module 7 (Excel Bidirectional API)
**Target: 2 hours**

```
Session 5. Read docs/cpoi-sdlc-spec.md Part 6 Modules 6 and 7, and
docs/cpoi-scoring-and-platform-spec.md Part 9 (Client Portal) for the
distinction between the internal admin dashboard and the client portal.

Build in this order, pausing for approval after each:

1. The Streamlit internal admin dashboard, all five pages defined in
   Module 6 (Client Overview, Client Detail, Data Intake, Report Center,
   Audit Log). This is the Managing Partner's interface, not the client
   portal.
2. The period-over-period anomaly detection from the scoring spec Part 6
   (the four flag types). Flags appear as a pre-finalization checklist;
   scores cannot publish until flags are cleared or overridden.
3. Module 7: the Excel bidirectional API. Inbound ingestion already exists
   from Session 1. Add the outbound VARIANCE_FLAGS tab written back to a
   copy of the client's workbook.
4. Tests for dashboard data display and the Excel write-back.

Show me the Client Detail page plan before building it.
```

---

### SESSION 6 — Integration Test + Audit Validation
**Target: 1 hour**

```
Session 6. Final integration. Read the full validation criteria across all
modules in docs/cpoi-sdlc-spec.md Part 6.

1. Run a complete synthetic client submission through all seven modules
   end to end: Excel intake, validation, ingestion, signal calculation,
   scoring, anomaly detection, alert generation, report generation,
   dashboard display, Excel write-back.
2. Verify the audit log captured every event with no gaps.
3. Verify no client data appears in any external call or log.
4. Confirm SQLCipher encryption is active on the database file.
5. Confirm the append-only constraint on audit_log cannot be bypassed
   (attempt an UPDATE and a DELETE; both must fail).
6. Produce a short build report: what was built, what tests pass, any
   known gaps or deferred items.

Do not declare the build complete until every module's validation criteria
in Part 6 pass.
```

---

## A.5 — WHAT DOES NOT CHANGE BY USING CLAUDE CODE

The following obligations from the main spec and the security/maintenance
document apply identically regardless of which tool builds the platform:

- Local AI model only for any narrative touching client context
- SQLCipher encryption at rest, TLS 1.3 in transit
- Append-only, immutable audit log
- Anonymization before any AI processing
- Secrets in Infisical, never in code or committed env files
- The scoring methodology is version-controlled proprietary IP
- Hosting on the Vultr US dedicated instance, accessed via Tailscale
- Dependency scanning with pip audit before deployment
- Full testing before any real client data enters the platform

No coding tool removes the obligation to validate that client data is
handled correctly before the first real client engagement. Build with
synthetic data. Test against the validation criteria. Only then go live.

---

**Addendum Status: APPROVED FOR BUILD**
**Applies to: Claude Code build path**
**The IBM Bob Plan Mode instructions in Part 9 remain valid for that tool.**
**Choose one tool and follow its path start to finish.**
