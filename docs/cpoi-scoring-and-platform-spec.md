# CRITERION PARTNERS OPERATIONAL INTELLIGENCE PLATFORM
## Complete Scoring Specification and Platform Parameters
### Internal Reference Document — v2.0

**Author:** Bernadette Akpeko Thompson
**Classification:** Proprietary — Do Not Distribute
**Supersedes:** SDLC Spec v1.0 (scoring section)
**Last Updated:** May 2026

---

## PART 1 — SCORING PHILOSOPHY

### The Governing Principle

Every number in this platform tells a story. The score is not the output. The score is the evidence that supports the interpretation. No score is delivered to a client without human judgment applied to it. The platform calculates. The Managing Partner interprets. The client receives the interpretation, not the calculation.

### Scale Definition

**0 to 100 Risk Exposure Scale**
Higher score = higher operational risk

| Range | Classification | Plain Language |
|---|---|---|
| 0 – 20 | Low Risk | Systems are functioning with adequate visibility and governance |
| 21 – 40 | Moderate | Manageable gaps exist; require monitoring but not immediate action |
| 41 – 60 | Elevated | Structural gaps are present; execution risk is increasing |
| 61 – 80 | High Risk | Material operational gaps; financial exposure is probable without intervention |
| 81 – 100 | Critical | Systemic failure conditions; financial exposure is active or imminent |

### Missing Data Protocol

Missing data is not neutral. Absence of measurement is itself a signal.

**Default Rule:**
If a required data field is absent and no exception has been granted,
the sub-category scores at 75 (High Risk band).

**Rationale:**
An organization that does not track escalation resolution dates does not have
a data problem. It has a governance visibility problem. The inability to
measure is the risk. The score reflects that accurately.

**Exception Protocol:**
A field may be excluded from scoring only when:
1. The Managing Partner formally grants the exception in writing
2. A justification note is documented explaining why the field does not apply
3. The exclusion is logged to the audit trail with timestamp
4. The client-facing report notes the exclusion and its basis
5. The exception is reviewed at each subsequent submission — it does not carry
   forward automatically

**Exception examples that qualify:**
- "This organization runs a fully embedded model with no formal escalation
  pathway — escalation latency is structurally inapplicable"
- "This is a single-program engagement; cross-program dependency tracking
  is not applicable to current scope"

**Exception examples that do not qualify:**
- "The client did not provide this data"
- "We ran out of time to collect this"
- "The data exists but is not organized yet"

---

## PART 2 — MANUAL OVERRIDE PROTOCOL

When telemetry data and observed reality diverge, the Managing Partner may
override a calculated score. Raw data is never modified. The score is adjusted
at the interpretation layer, not the data layer.

**What can be overridden:**
- Any individual sub-category score
- Any dimension composite score
- An impact indicator classification

**What cannot be overridden:**
- Raw intake data
- Signal calculation outputs
- Audit log entries

**Override documentation requirements:**
Every override requires:
1. The original calculated score
2. The override score
3. A minimum 50-word justification note
4. The source of the contradicting evidence
   (options: discovery call, review session, direct observation, client disclosure,
   document review, third-party data)
5. Timestamp and confirmation that the Managing Partner applied the override

**Override visibility:**
- Displayed in the internal platform as "Adjusted Score (Manual Override)"
- The client-facing report shows the adjusted score only
- The client-facing report includes a footnote:
  "This score incorporates qualitative intelligence gathered during the
  engagement period in addition to submitted operational data."
- The delta between calculated and adjusted is stored internally for
  calibration purposes

---

## PART 3 — SUB-CATEGORY IMPACT INDICATORS

Sub-categories are not scored numerically. They are classified by impact.
This avoids false precision from imperfect data while preserving analytical depth.

**Impact Classification System:**

| Label | Meaning |
|---|---|
| DRIVING | This sub-category is the primary contributor to the dimension score |
| CONTRIBUTING | This sub-category meaningfully influences the dimension score |
| PRESENT | This sub-category shows a signal but is not materially driving the score |
| MONITORED | This sub-category shows no current signal but warrants ongoing watch |
| EXCLUDED | Exception granted; documented justification on file |

Each dimension report section shows:
- The dimension score (numeric)
- The dimension classification (Low / Moderate / Elevated / High / Critical)
- The ranked impact indicators (DRIVING first, then CONTRIBUTING, etc.)
- A one-sentence plain-language interpretation per DRIVING indicator

This gives the Managing Partner and the client clarity on which specific
conditions are producing the risk reading without requiring numerical
precision on imperfect sub-data.

---

## PART 4 — GRANULAR SCORING SPECIFICATIONS

### DIMENSION 1 — STRATEGIC SATURATION
**Weight in Composite: 20%**
**What it measures:**
The degree to which the organization's transformation portfolio exceeds
its sustainable execution capacity.

---

**SUB-CATEGORY 1.1 — Priority Inflation Index**
*What it captures:* The percentage of active initiatives labeled Critical or High Priority.

| Condition | Points |
|---|---|
| 0–30% labeled Critical/High | 0–15 |
| 31–50% labeled Critical/High | 16–30 |
| 51–65% labeled Critical/High | 31–50 |
| 66–80% labeled Critical/High | 51–70 |
| 81–100% labeled Critical/High | 71–90 |
| 100% labeled Critical/High (all programs are "critical") | 91–100 |

*Why it matters:* When everything is a priority, nothing is. Priority inflation
indicates that prioritization has broken down at the leadership level.
The scoring ceiling accounts for organizations that have effectively
eliminated meaningful priority differentiation.

*Missing data behavior:* If priority classifications are absent, this sub-category
scores at 75. An organization that has not classified its initiatives by priority
has a strategic saturation problem by definition.

---

**SUB-CATEGORY 1.2 — Concurrent Transformation Density**
*What it captures:* Active initiatives per delivery team.

| Condition | Points |
|---|---|
| 1–2 active initiatives per team | 0–15 |
| 3 active initiatives per team | 16–35 |
| 4 active initiatives per team | 36–55 |
| 5 active initiatives per team | 56–75 |
| 6+ active initiatives per team | 76–100 |

*Nuance note:* Team size and initiative complexity must be considered.
A team of 12 handling 3 lightweight initiatives is different from a team
of 4 handling 3 ERP programs. The Managing Partner applies judgment here
and may use the override protocol if the raw ratio misrepresents actual load.

---

**SUB-CATEGORY 1.3 — Priority Change Frequency**
*What it captures:* Number of priority reclassifications per initiative per quarter.

| Condition | Points |
|---|---|
| 0 reclassifications per quarter | 0 |
| 1 reclassification per quarter | 5–20 |
| 2 reclassifications per quarter | 21–50 |
| 3 reclassifications per quarter | 51–75 |
| 4+ reclassifications per quarter | 76–100 |

*Why it matters:* Frequent priority changes indicate that leadership is
reacting to incoming demand rather than governing a portfolio. Each
reclassification carries a context-switching cost that degrades throughput.

*Missing data behavior:* If reclassification history is not tracked, scores at 65.
Inability to track priority changes is itself evidence that portfolio governance
is not in place.

---

**SUB-CATEGORY 1.4 — Initiative Persistence Rate**
*What it captures:* Percentage of initiatives that have exceeded their original
target completion date without formal scope change or formal deferral decision.

| Condition | Points |
|---|---|
| 0–10% past original target | 0–10 |
| 11–25% past original target | 11–30 |
| 26–40% past original target | 31–55 |
| 41–60% past original target | 56–75 |
| 61%+ past original target | 76–100 |

*Missing data behavior:* If start and target dates are absent, scores at 70.

---

**DIMENSION 1 COMPOSITE CALCULATION:**
```
Strategic Saturation Score =
  (Priority Inflation × 0.30) +
  (Transformation Density × 0.30) +
  (Priority Change Frequency × 0.25) +
  (Initiative Persistence × 0.15)
```

---

### DIMENSION 2 — GOVERNANCE RESPONSIVENESS
**Weight in Composite: 20%**
**What it measures:**
The velocity, clarity, and accountability of governance and decision-making
structures relative to the organization's transformation demands.

---

**SUB-CATEGORY 2.1 — Escalation Resolution Latency**
*What it captures:* Average days from escalation creation to documented resolution.

| Condition | Points |
|---|---|
| 0–5 days average | 0–10 |
| 6–10 days average | 11–30 |
| 11–15 days average | 31–55 |
| 16–21 days average | 56–75 |
| 22–30 days average | 76–90 |
| 31+ days average | 91–100 |

*Missing data behavior:* Escalation resolution dates not tracked = 80.
This is one of the most critical governance gaps. Absence of measurement
indicates governance is not being held accountable to velocity.

---

**SUB-CATEGORY 2.2 — Escalation Suppression Rate**
*What it captures:* Percentage of escalations resolved below the VP level
without executive visibility.

| Condition | Points |
|---|---|
| 0–30% resolved below VP level | 0–15 |
| 31–50% resolved below VP level | 16–35 |
| 51–65% resolved below VP level | 36–55 |
| 66–75% resolved below VP level | 56–75 |
| 76%+ resolved below VP level | 76–100 |

*Important nuance:* Not all escalations need to reach VP level. The question
is whether the categorization of what requires executive visibility is defined,
documented, and enforced. If no categorization exists, all below-VP resolution
is treated as potential suppression.

*Missing data behavior:* If escalation level is not tracked, scores at 75.

---

**SUB-CATEGORY 2.3 — Decision Latency**
*What it captures:* Average days between when a decision is required (flagged
in governance tracker) and when that decision is documented as made.

| Condition | Points |
|---|---|
| 0–3 days | 0–10 |
| 4–7 days | 11–30 |
| 8–14 days | 31–55 |
| 15–21 days | 56–75 |
| 22+ days | 76–100 |

*Missing data behavior:* Governance decisions not tracked = 80.

---

**SUB-CATEGORY 2.4 — Accountability Clarity Index**
*What it captures:* Percentage of active initiatives with a single, named,
accountable decision-maker documented.

| Condition | Points |
|---|---|
| 90–100% of initiatives have named DRI | 0–10 |
| 75–89% have named DRI | 11–30 |
| 60–74% have named DRI | 31–55 |
| 45–59% have named DRI | 56–75 |
| Less than 45% have named DRI | 76–100 |

*Missing data behavior:* Accountability not documented = 70.

---

**DIMENSION 2 COMPOSITE CALCULATION:**
```
Governance Responsiveness Score =
  (Escalation Resolution Latency × 0.30) +
  (Escalation Suppression Rate × 0.30) +
  (Decision Latency × 0.25) +
  (Accountability Clarity × 0.15)
```

---

### DIMENSION 3 — EXECUTION VISIBILITY
**Weight in Composite: 20%**
**What it measures:**
The degree to which leadership has accurate, timely, and structured
visibility into what is actually happening across delivery programs.

---

**SUB-CATEGORY 3.1 — Telemetry Coverage Ratio**
*What it captures:* Percentage of active initiatives with structured,
documented operational data being collected.

| Condition | Points |
|---|---|
| 90–100% of initiatives have structured telemetry | 0–10 |
| 75–89% have structured telemetry | 11–30 |
| 60–74% have structured telemetry | 31–55 |
| 45–59% have structured telemetry | 56–75 |
| Less than 45% have structured telemetry | 76–100 |

*Missing data behavior:* If the client cannot confirm which programs have
telemetry, that is a 0% coverage confirmation. Scores at 85.

---

**SUB-CATEGORY 3.2 — Dependency Transparency Score**
*What it captures:* Percentage of known cross-program dependencies that
are formally documented and tracked.

| Condition | Points |
|---|---|
| 90–100% of dependencies documented | 0–10 |
| 70–89% documented | 11–30 |
| 50–69% documented | 31–55 |
| 30–49% documented | 56–75 |
| Less than 30% documented | 76–100 |

*Missing data behavior:* If dependency tracking does not exist, scores at 80.

---

**SUB-CATEGORY 3.3 — Reporting Lag Indicator**
*What it captures:* Average calendar days from when an operational event
occurs to when it appears in executive-level reporting.

| Condition | Points |
|---|---|
| 0–3 days | 0–10 |
| 4–7 days | 11–30 |
| 8–14 days | 31–55 |
| 15–21 days | 56–75 |
| 22+ days | 76–100 |

*Missing data behavior:* Reporting cadence not documented = 65.

---

**SUB-CATEGORY 3.4 — Leadership Visibility Index**
*What it captures:* Frequency of direct, unfiltered executive touchpoints
with delivery teams (skip-levels, direct program reviews, etc.) per month.

| Condition | Points |
|---|---|
| 4+ touchpoints per month | 0–10 |
| 2–3 touchpoints per month | 11–30 |
| 1 touchpoint per month | 31–55 |
| 1 touchpoint per quarter | 56–75 |
| No structured touchpoints | 76–100 |

*Missing data behavior:* Not tracked = 65.

---

**DIMENSION 3 COMPOSITE CALCULATION:**
```
Execution Visibility Score =
  (Telemetry Coverage × 0.30) +
  (Dependency Transparency × 0.30) +
  (Reporting Lag × 0.25) +
  (Leadership Visibility × 0.15)
```

---

### DIMENSION 4 — REPORTING INTEGRITY
**Weight in Composite: 20%**
**What it measures:**
The degree to which executive reporting accurately reflects operational
conditions rather than curated, politically optimized, or systematically
delayed information.

---

**SUB-CATEGORY 4.1 — False-Green Incidence Rate**
*What it captures:* Percentage of programs reporting Green status that
have one or more open Critical blockers documented in the same period.

| Condition | Points |
|---|---|
| 0% false-green incidence | 0–5 |
| 1–5% of programs | 6–20 |
| 6–15% of programs | 21–45 |
| 16–30% of programs | 46–70 |
| 31%+ of programs | 71–100 |

*Why this sub-category carries the most weight in this dimension:*
A program with a documented Critical blocker that is simultaneously
reporting Green is definitionally providing inaccurate intelligence
to leadership. This is the most direct indicator of reporting integrity failure.

*Missing data behavior:* If blocker tracking does not exist alongside
status reporting, this sub-category scores at 80. The absence of
structured blocker tracking in a program reporting status is itself
a reporting integrity gap.

---

**SUB-CATEGORY 4.2 — Manual Curation Index**
*What it captures:* Estimated percentage of status reports that pass through
human interpretation or editorial review before reaching executive audiences.

| Condition | Points |
|---|---|
| 0–10% manually curated | 0–10 |
| 11–25% manually curated | 11–25 |
| 26–50% manually curated | 26–50 |
| 51–75% manually curated | 51–70 |
| 76–100% manually curated | 71–100 |

*Nuance note:* Some curation is appropriate — summarization and synthesis
add value. The risk is curation that filters, softens, or reframes unfavorable
data. The Managing Partner uses judgment here and the override protocol
applies when observable evidence contradicts the client's self-reported figure.

---

**SUB-CATEGORY 4.3 — Reporting Incentive Alignment**
*What it captures:* This is a qualitative sub-category. It assesses whether
the organizational culture and management incentives encourage accurate
reporting or encourage optimistic reporting.

*Assessment method:* Managing Partner observation during discovery session
and review calls. Scored based on evidence gathered, not client self-report.

| Condition | Points |
|---|---|
| Explicit norms rewarding candor; safe to report bad news | 0–15 |
| Neutral environment; neither penalized nor rewarded | 16–35 |
| Some evidence of preference for positive framing | 36–60 |
| Clear pattern of messaging management before escalation | 61–80 |
| Direct evidence of fear-based reporting suppression | 81–100 |

*Default behavior if no observation possible:* 40 (moderate).
This sub-category requires at least one direct client interaction to score accurately.
It is one of the primary reasons the discovery session exists.

---

**SUB-CATEGORY 4.4 — Confidence Reliability Score**
*What it captures:* Percentage of programs where reported confidence levels
(High / Medium / Low) are supported by documented evidence.

| Condition | Points |
|---|---|
| 90–100% of confidence statements documented | 0–10 |
| 70–89% documented | 11–30 |
| 50–69% documented | 31–55 |
| 30–49% documented | 56–75 |
| Less than 30% documented | 76–100 |

*Missing data behavior:* Confidence levels not tracked = 70.

---

**DIMENSION 4 COMPOSITE CALCULATION:**
```
Reporting Integrity Score =
  (False-Green Incidence × 0.35) +
  (Manual Curation Index × 0.25) +
  (Reporting Incentive Alignment × 0.25) +
  (Confidence Reliability × 0.15)
```

---

### DIMENSION 5 — ORGANIZATIONAL SUSTAINABILITY
**Weight in Composite: 20%**
**What it measures:**
The organization's capacity to sustain current transformation demand without
degrading its people, processes, or long-term execution capability.

---

**SUB-CATEGORY 5.1 — Team Utilization Pressure**
*What it captures:* Highest team utilization level reported across the portfolio.

| Condition | Points |
|---|---|
| No team above 90% utilization | 0–10 |
| Highest team at 91–100% | 11–30 |
| Highest team at 101–110% | 31–55 |
| Highest team at 111–125% | 56–75 |
| Highest team at 126–140% | 76–90 |
| Highest team above 140% | 91–100 |

*Why single highest vs average:* The average can mask critical overload
on specific teams. A platform team at 145% utilization is an existential
delivery risk regardless of whether other teams are at 80%.

*Missing data behavior:* Utilization not tracked = 75.

---

**SUB-CATEGORY 5.2 — Reprioritization Impact Rate**
*What it captures:* Average number of reprioritization events per team per month
and the estimated throughput cost of each event.

| Condition | Points |
|---|---|
| 0–0.5 reprioritizations per team per month | 0–10 |
| 0.6–1.0 per month | 11–30 |
| 1.1–2.0 per month | 31–55 |
| 2.1–3.0 per month | 56–75 |
| 3.1+ per month | 76–100 |

*Missing data behavior:* Reprioritization events not tracked = 65.

---

**SUB-CATEGORY 5.3 — Reactive Work Ratio**
*What it captures:* Estimated percentage of team capacity consumed by
unplanned, reactive work (fire-fighting, rework, unscheduled escalation support).

| Condition | Points |
|---|---|
| 0–10% reactive | 0–10 |
| 11–20% reactive | 11–30 |
| 21–30% reactive | 31–55 |
| 31–40% reactive | 56–75 |
| 41%+ reactive | 76–100 |

*Missing data behavior:* Not tracked = 60.

---

**SUB-CATEGORY 5.4 — Adaptive Capacity Reserve**
*What it captures:* Estimated percentage of organizational capacity available
to absorb new transformation demand without degrading existing programs.

| Condition | Points |
|---|---|
| 25%+ available capacity | 0–10 |
| 15–24% available | 11–30 |
| 10–14% available | 31–55 |
| 5–9% available | 56–75 |
| Less than 5% available | 76–100 |

*Missing data behavior:* Not tracked = 70.

---

**SUB-CATEGORY 5.5 — Retention Risk Indicator**
*What it captures:* Number of identified high-risk retention flags on
critical-path program staff.

| Condition | Points |
|---|---|
| 0 high-risk flags | 0–5 |
| 1–2 high-risk flags | 6–25 |
| 3–5 high-risk flags | 26–55 |
| 6–8 high-risk flags | 56–75 |
| 9+ high-risk flags | 76–100 |

*Missing data behavior:* Retention risk not assessed = 55.

---

**DIMENSION 5 COMPOSITE CALCULATION:**
```
Organizational Sustainability Score =
  (Team Utilization Pressure × 0.30) +
  (Reprioritization Impact × 0.25) +
  (Reactive Work Ratio × 0.20) +
  (Adaptive Capacity Reserve × 0.15) +
  (Retention Risk Indicator × 0.10)
```

---

## PART 5 — OEI COMPOSITE SCORE

```
OEI Composite =
  (Strategic Saturation × 0.20) +
  (Governance Responsiveness × 0.20) +
  (Execution Visibility × 0.20) +
  (Reporting Integrity × 0.20) +
  (Organizational Sustainability × 0.20)
```

**All five dimensions equally weighted at 20%.**
This is reviewed annually. Any weighting adjustment requires a documented
rationale and applies only to new submissions — it is never retroactively
applied to prior period scores.

---

## PART 6 — PERIOD-OVER-PERIOD ANOMALY DETECTION

When a client submits data for a second or subsequent period, the platform
automatically compares current submission to the prior period and flags
the following conditions for Managing Partner review before scores are finalized:

**Flag Type 1 — Suspicious Improvement**
Any dimension that improves by 15+ points in a single period triggers a
Suspicious Improvement Flag.

*Reasoning:* Genuine structural improvement rarely moves this fast.
A large single-period improvement may indicate:
- Data was cleaned up before submission
- Different respondents filled in the data
- A definition changed between periods

*Action required:* Managing Partner reviews before finalizing score.
Override protocol available if improvement is verified as genuine.

**Flag Type 2 — Inconsistent Sub-Category Movement**
If a dimension score improves but its primary driving sub-category worsens,
the platform flags this as an Inconsistent Signal.

*Reasoning:* Dimension scores should track with their primary drivers.
Divergence may indicate selective data improvement.

**Flag Type 3 — Structural Data Gap Closure**
If a sub-category that previously scored at the missing-data default
suddenly receives complete data, the platform flags this for review.

*Reasoning:* Not suspicious on its own — this is often genuine progress.
But it should be validated: did they actually build the tracking capability,
or did someone fill in estimated numbers to satisfy the intake template?

**Flag Type 4 — Zero-Movement Submission**
If all five dimension scores move by less than 3 points in either direction
for two consecutive periods, the platform flags this as Stagnation.

*Reasoning:* Genuine operational environments change. Zero movement
suggests either the data is not fresh or the engagement is not producing
the intelligence needed to drive decisions.

All flags appear in the Managing Partner dashboard as a pre-finalization
checklist. Scores cannot be published to the client portal until all flags
are either cleared or overridden with justification.

---

## PART 7 — QUALITATIVE INTELLIGENCE NOTES

**The Problem This Solves:**
Operational telemetry captures what was submitted. It does not capture
what was said in the discovery session, what the Managing Partner observed
on the review call, what a program manager disclosed informally, or what
the pattern of communication from the executive sponsor reveals over time.
That qualitative intelligence is sometimes the most material signal of all.

**Where It Lives:**
Each client record has an Engagement Intelligence Journal — a structured
note-taking layer that is separate from the scored data but feeds into the
AI narrative generation prompt as context.

**Journal Entry Structure:**
```
Date:
Entry Type: [Discovery Call / Review Session / Ad Hoc Communication /
            Direct Observation / Third-Party Disclosure / Pattern Note]
Program Reference: [Initiative ID if applicable, or "General"]
Intelligence Note: [Free text — no length limit]
Materiality: [High / Medium / Low — Managing Partner assessment]
Surfaced in Report: [Yes / No / Partial]
```

**How It Feeds the AI Draft:**
When the AI narrative generation runs, the platform pulls all High and Medium
materiality journal entries from the current period and includes them in the
prompt context block labeled QUALITATIVE_CONTEXT. The AI uses these as
interpretive framing without including client-identifiable details.

**Client Visibility:**
Journal entries are never shown to the client. They are internal to the
platform. When qualitative intelligence influences a score or narrative,
the Managing Partner decides whether and how to surface it in the report.

---

## PART 8 — MISSED SUBMISSION PROTOCOL

**Day 1 past deadline:**
Platform sends an automated internal alert to the Managing Partner.
Client record is flagged as Submission Overdue.

**Day 3 past deadline:**
If no submission received, the platform generates a client portal notification
(visible when the sponsor logs in) stating:
"Your data submission for [period] has not been received. Please contact
your Criterion Partners Managing Partner."

**Day 5 past deadline:**
If no submission received, the Managing Partner receives a second escalation
alert. A Delinquency Record is created in the client's engagement file.

**Escalation tracker:**
The platform tracks missed submissions against a rolling 3-strike counter.

| Count | Status |
|---|---|
| 1 missed submission | Noted; Managing Partner follows up |
| 2 missed submissions | Formal engagement review triggered |
| 3 missed submissions | Engagement health flag raised; Managing Partner initiates conversation about continuation |

**The business rule:**
Three consecutive missed submissions without documented justification
triggers an Engagement Status Review. The Managing Partner conducts a
direct conversation with the executive sponsor. If the pattern continues,
the engagement is closed. No refunds apply per engagement terms.

---

## PART 9 — CLIENT PORTAL SPECIFICATION

### Two Portal Modes

**MODE A — SNAPSHOT PORTAL**
For OEI Snapshot engagements.

Access: Executive sponsor only
Authentication: Email + one-time secure link per session (no persistent password in MVP)
Access window: 14 calendar days from report delivery date
After expiry: Access revoked, portal shows "This engagement has concluded.
Contact Criterion Partners for further assistance."

*What the sponsor sees:*
- Engagement status tracker (milestones — not granular tasks)
- Secure message thread with Managing Partner
- Report download button (active only when report is published by Managing Partner)
- Notification center

*Milestone display for Snapshot:*
```
MILESTONE 1: Intake Assessment Submitted ✓
MILESTONE 2: Engagement Confirmed ✓
MILESTONE 3: Discovery Session Completed ✓
MILESTONE 4: Data Submission Received ✓
MILESTONE 5: Intelligence Analysis In Progress [active]
MILESTONE 6: Report Delivered [ ]
MILESTONE 7: Executive Intelligence Review Scheduled [ ]
MILESTONE 8: Engagement Complete [ ]
```

*Report download:*
Report is not available until the Managing Partner explicitly publishes it
in the backend. Publishing triggers:
1. A portal notification to the sponsor
2. An encrypted email notification: "Your OEI Snapshot report is now
   available in your secure portal."

---

**MODE B — OEIL PORTAL**
For Operational Executive Intelligence Layer engagements.

Access: Executive sponsor only
Authentication: Username + password + TOTP (time-based one-time password)
for MFA. This is mandatory for the layer — no exceptions.
Access window: Active for the duration of the engagement.
Post-engagement: 7-day grace period to download final quarterly report,
then access revoked.

*What the sponsor sees:*

**Dashboard Tab:**
- OEI Composite Score (current period, large display)
- Five dimension score cards (current period)
- Score trajectory chart (current period vs all prior periods, up to 4 quarters)
- Active alerts (severity and description — no raw signal values)
- Next milestone or deliverable date

**Reports Tab:**
- All published monthly briefs (download as PDF)
- Quarterly Intelligence Review report (when generated)
- End-of-engagement historical report (when generated)

**Messages Tab:**
- Secure encrypted message thread with Managing Partner
- Notifications visible here (not email-only)

**Engagement Tab:**
- Current engagement status
- Active milestones
- Invoice status display (Paid / Due / Overdue — read only)

*Historical data rules:*
- Up to 4 quarters of score data displayed
- At end of engagement, a full historical report is generated and
  provided to the client before access is revoked
- After access revocation, the client retains their downloaded reports
  but cannot access the platform

*What the sponsor does NOT see:*
- Raw signal readings
- Signal calculation methodology
- Other client data of any kind
- Audit log
- Engagement Intelligence Journal entries
- AI generation logs
- Managing Partner internal notes

---

## PART 10 — SECURITY ARCHITECTURE

### Hosting Decision (FINALIZED)

**Decision: Vultr Dedicated Instance, US Data Center**

*Rationale:*
- US jurisdiction matches the jurisdiction of all current and target clients.
  This keeps data sovereignty consistent and familiar to US executive sponsors
  and avoids cross-border explanation overhead with clients.
- Dedicated hardware — not shared with other tenants.
- US-based company means US-accessible customer service during an outage,
  which is a deliberate operational requirement.
- Recommended data center locations: Atlanta, Chicago, Dallas, or New York
  (choose nearest to primary client base for latency).
- Monthly cost: approximately $120/month for a dedicated instance sized
  to handle 10+ concurrent clients.

*Why not German/EU hosting:*
German hosting (Hetzner) was evaluated and rejected. While EU hosting offers
strong data protection law, it introduces a jurisdiction mismatch for US
clients, complicates the data sovereignty conversation, and adds friction
given the current US/EU political climate. The CLOUD Act consideration that
favors EU hosting applies to EU clients; it does not apply to a US firm
serving US clients. US hosting is the correct decision for this client base.

*How remote access works:*
Tailscale creates a private encrypted network between the Managing Partner's
laptop and the server regardless of physical location. The platform is
accessed as if local. No public-facing ports except HTTPS (443). International
travel does not affect access. If the laptop is lost or stolen, the device
is revoked from Tailscale in under one minute from any other device,
immediately cutting server access from the lost machine. The server and
its data are unaffected by laptop loss because all data resides on Vultr,
never on the laptop.

*Backup target:*
Backblaze B2 (US-based, S3-compatible) for encrypted offsite backups.
Hetzner Object Storage is not used.

---

### Encryption Layers

**Data at rest:**
- SQLite database encrypted with SQLCipher (AES-256)
- Encryption key stored in Infisical (secrets manager), never in code,
  never in a plain environment variable or .env file committed to version control
- Intake Excel files encrypted at rest using AES-256 before storage
- Backups encrypted with a separate key, also stored in Infisical

**Data in transit:**
- All platform traffic HTTPS only (TLS 1.3)
- No HTTP fallback
- Certificate managed via Let's Encrypt with auto-renewal

**Client portal messaging:**
- Messages stored in database as AES-256 encrypted blobs
- Encryption key derived from a combination of server secret and
  session token — no single point holds both
- Messages are encrypted before write, decrypted only on read
  by authenticated session
- This is application-layer encryption, not end-to-end encryption in the
  Signal protocol sense. This is the FINALIZED decision for MVP. True E2E
  was evaluated and explicitly deferred to v2.0 because it would add 20 to
  30 hours to the build, requires a key exchange and key management
  infrastructure that does not yet exist, and defends against a threat
  (compromised server administrator) that does not apply to a single-operator
  platform where the Managing Partner controls the server. Application-layer
  AES-256 with a dedicated US server, TLS 1.3 in transit, and strong access
  control is the correct security posture for MVP. E2E is revisited when a
  full-stack engineer joins.

**Report delivery:**
- Reports downloaded directly from the portal over HTTPS — this is the
  primary secure delivery method
- If email delivery is triggered, reports are attached as password-protected PDFs
  Password is communicated to the sponsor via the portal message thread —
  never in the same email as the attachment

---

### Access Control

**MVP (single operator):**
- One admin account — Managing Partner
- Session tokens expire after 4 hours of inactivity
- Failed login attempts: 5 attempts triggers a 30-minute lockout
- All login events logged to audit trail

**Future team member access (scaffolded now, activated later):**
Role structure is defined now so adding a team member never requires
architectural changes:

```
ROLE: admin
  - Full platform access
  - Can publish reports
  - Can manage client records
  - Can override scores
  - Can access audit log

ROLE: analyst
  - Can view client data
  - Can run scoring engine
  - Cannot publish reports (requires admin approval)
  - Cannot override scores without admin countersignature
  - Cannot access other clients' data (client-scoped access only)

ROLE: auditor
  - Read-only access to audit log
  - No access to client data
  - Can export audit log to CSV
  - Cannot modify anything
```

---

### Cybersecurity Posture

**What is protected:**
1. The platform application code
2. The client database
3. The scoring model (proprietary IP)
4. Client intake files
5. Generated reports
6. Engagement Intelligence Journal entries
7. All audit logs

**Security controls implemented:**

*Input validation:*
All Excel intake data is validated against schema before entering the
database. SQL injection is not possible through Excel intake.
All API inputs sanitized. Parameterized queries only — no string
concatenation in SQL.

*Rate limiting:*
Portal login attempts limited. API endpoints rate-limited.
Unusual access patterns (multiple failed logins, rapid report downloads)
trigger an internal alert.

*Audit log immutability:*
Audit log table has database-level trigger that prevents UPDATE and DELETE.
Append-only enforced at both application and database layer.

*Code quality:*
No vibe-coded sections. Every function has a defined input type,
output type, and documented behavior. Every external integration
has a circuit breaker (if SendGrid is down, the alert is queued
locally and retried, not silently dropped). Error handling is
explicit — no bare except clauses. Logging is structured JSON,
not print statements.

*Backup:*
Automated daily backup to an encrypted S3-compatible bucket
(Backblaze B2, US-based, S3-compatible).
Backup retention: 90 days rolling.
Monthly backup snapshot: retained for 5 years (report archival requirement).

---

### Compliance Readiness

**Current posture:**
Platform is designed to be SOC 2 Type II and HIPAA-ready.
Not currently certified — certification requires an audit engagement
that makes sense when client revenue warrants it.

**What SOC 2 readiness requires (already built):**
- Audit log (all events, immutable, timestamped) ✓
- Access control with role-based permissions ✓
- Encryption at rest and in transit ✓
- Backup and recovery procedures ✓
- Incident response procedure (documented below) ✓

**What HIPAA readiness requires (built where relevant):**
- No PHI is currently processed
- When a healthcare client onboards: a Business Associate Agreement (BAA)
  is required before any engagement begins
- Data handling procedures do not change — the platform was built
  to handle sensitive operational data with equivalent care to PHI

---

### Data Breach Response Procedure

**Immediate (within 1 hour of confirmed breach):**
1. Revoke all active client portal sessions
2. Take the platform offline
3. Preserve all logs — do not wipe or modify anything
4. Document everything that is known about the breach in writing

**Within 24 hours:**
1. Notify all affected clients directly via phone call — not email,
   not portal message — phone call to the executive sponsor
2. Send written notification via encrypted email within 24 hours
3. Notify applicable authorities:
   - If client data includes any EU resident data: GDPR requires
     notification to the supervisory authority within 72 hours
   - If financial services client: relevant regulatory notification
   - Consult legal counsel before any public statement

**Within 72 hours:**
1. Forensic assessment of scope and cause
2. Remediation plan with timeline
3. Client communication with remediation status

**Professional Liability Insurance:**
Obtain before first client engagement begins.
Recommended coverage: Technology E&O (Errors and Omissions) +
Cyber Liability, minimum $1M per occurrence.
Carriers to evaluate: Hiscox, Chubb, Coalition.

---

## PART 11 — REPORT SECURITY AND DELIVERY

**Digital Watermark:**
Every report PDF is watermarked with:
- Client name
- Executive sponsor name
- Report date
- Unique report ID (UUID)
- Text: "Prepared exclusively for [Sponsor Name] — Confidential"

The watermark is embedded at the PDF metadata level and as a
visible watermark on every page footer. It is generated at
report creation time and is immutable after generation.

**Report Read Receipt:**
When a report is downloaded from the portal, the platform records:
- Timestamp of download
- Session ID of the authenticated user who downloaded
- IP address (for audit purposes, not shared with client)

When an encrypted report email is delivered:
- SendGrid delivery confirmation logged
- Portal notification marked as delivered
- If no portal access or download within 5 business days of delivery,
  Managing Partner receives an internal alert

**Report Delivery to Third Parties:**
Reports are delivered exclusively to the executive sponsor account.
No exceptions. If a client requests delivery to a third party (board member,
external auditor), the procedure is:
1. Executive sponsor must make the formal request via portal message
2. Managing Partner creates a read-only temporary access token
3. Token is shared directly with the executive sponsor — not with the
   third party directly
4. The sponsor shares it with the third party at their discretion
5. Token expires after 48 hours
6. All access is logged to the audit trail

---

## PART 12 — ENGAGEMENT ONBOARDING FLOW (PLATFORM)

**Trigger:** Managing Partner decides to onboard a new client after the
post-intake qualification conversation.

**Step 1 — Client Record Creation (Managing Partner action)**
Managing Partner creates client record in admin dashboard:
- Client name, sponsor name, sponsor email
- Engagement type (Snapshot or OEIL)
- Engagement start date
- Monthly retainer (if OEIL)
- Initial milestone setup

**Step 2 — Milestone Configuration**
For Snapshot: Standard milestone template auto-populates. Managing Partner
reviews and adjusts if scope requires.
For OEIL: Monthly cadence template auto-populates. Managing Partner
configures reporting period dates.

**Step 3 — Portal Account Creation**
System generates the sponsor's portal access credentials.
For Snapshot: One-time secure link protocol configured.
For OEIL: Username + temporary password + MFA setup instructions.

**Step 4 — Welcome Communication**
Managing Partner sends the welcome message via the portal (first message
in the secure thread). Contains:
- Confirmation of engagement scope
- First milestone and what is needed from the client
- Instructions for accessing the portal
- Excel intake template attached (for OEIL; for Snapshot, provided
  after discovery session)

**Step 5 — Invoice Trigger**
First invoice is not generated by the platform automatically.
Managing Partner triggers invoice generation after the scope conversation.
Platform records invoice status as Issued.

**Step 6 — Engagement Activation**
Engagement is marked Active only after:
- Invoice marked as Paid by Managing Partner
- Start date confirmed
- Excel intake template shared with client

Before activation, all milestones show as Pending.
After activation, Milestone 1 marks as In Progress.

---

## PART 13 — DATA LIFECYCLE

| Data Type | Retention Period | End of Life Action |
|---|---|---|
| Raw intake Excel files | 1 year post-engagement | Securely deleted; deletion logged |
| Scored dimension data | 1 year post-engagement | Anonymized and archived as case study |
| Generated reports | 5 years post-engagement | Secure deletion at year 5; logged |
| Audit logs | 7 years (legal standard) | Archived offline; encrypted |
| Engagement Intelligence Journal | 1 year post-engagement | Deleted with intake data |
| Anonymized case study data | Indefinite | Held for benchmarking and IP development |
| Client portal accounts | Revoked at engagement end | Account deactivated; not deleted for 90 days |

**End-of-Engagement Data Transfer:**
Before an engagement closes, the Managing Partner generates a
Data Package for the client containing:
- All published reports (PDF)
- The end-of-engagement historical summary (quarterly for OEIL,
  single period for Snapshot)
- A data receipt confirming what is being retained and for how long

The client signs (or acknowledges via portal) receipt of this package.
Acknowledgment is logged to the audit trail.

---

*Document Status: APPROVED*
*This document governs platform scoring, access, security, and data handling.*
*No scoring parameter changes are made without updating this document.*
*Version control is maintained by the Managing Partner.*
