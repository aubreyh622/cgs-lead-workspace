# CGS Lead Workspace brand and interface spec
> Captured: 2026-10-23
> Sources: `dist/styles.css`, `dist/index.html`, `dist/app.js`, `dist/research.js`, and the current local interface
> Completeness: partial; grounded in the implemented prototype, with no separate formal brand guide supplied

## Core assets

### Logo
- Current mark: the four-cell CSS mark and `CGS` wordmark already implemented in the workspace shell.
- Intended use: persistent product navigation only.
- Constraint: do not replace it with HubSpot branding or an invented logo.

### Product interface
- Source of truth: the current local CGS Lead Workspace.
- Primary surfaces: Lead research, Work queue, Case workspace, Follow-ups, Approvals, Scoring guide, and Settings & agents.
- Preserve: offline synthetic-demo disclosure, evidence citations, human acceptance, qualification separation, and no-email/no-CRM-write boundaries.

## Visual system

### Color palette
- Navigation / primary ink: `#15363d`
- Primary teal: `#245f52`
- Main ink: `#17363c`
- Muted text: `#566d69`
- Canvas: `#f4f6f3`
- Panel: `#ffffff`
- Border: `#d8e1dd`
- Positive surface: `#e4f0e8`
- Attention surface: `#fbefdc`
- Risk surface: `#f8e9e5`

### Typography
- Display and body: `Segoe UI Variable`, `Segoe UI`, system sans-serif fallback.
- Data and IDs: a restrained system monospace only where provenance or machine state is the subject.

### Shape and spacing
- Base spacing rhythm: 4 / 8 / 12 / 16 / 24 / 32.
- Panels use a restrained 12–14 px radius; controls use 8–10 px.
- Shadows are reserved for overlays and floating contextual surfaces, not every card.
- Mobile targets remain at least 44 px.

### Vibe keywords
- Evidence-first
- Calm operations
- Consultant-led
- Accountable automation
- Dense but legible

## Reference boundary
- Borrow from HubSpot: consolidated workspace, saved views, queue-plus-context-panel, agent status visibility, and explicit approvals.
- Do not borrow: HubSpot branding, orange palette, proprietary icons, screenshots, or exact layouts.

## Completeness notes
- No formal CGS brand guideline, production logo asset, or licensed typeface was supplied.
- The current implemented interface is the authoritative visual starting point for this design exploration.
