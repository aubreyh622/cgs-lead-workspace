// Choices the backend accepts. They must match api/services.py exactly;
// tests/v2/test_frontend.py fails if the two drift apart.

export const SKIP_REASONS = [
  "Wrong industry or company type",
  "Too small",
  "Wrong contact",
  "Existing relationship or already in progress",
  "Competitor or company we do not work with",
  "Signal too old or unreliable",
  "Other",
];

export const REJECT_REASONS = [
  "Outside our target client profile",
  "No clear business problem",
  "Contact lacks decision authority",
  "Existing relationship or already in progress",
  "Evidence too thin or unreliable",
  "Other",
];

export const ACCEPT_REASONS = ["Relevant signal", "Strong contact", "Relationship to build on", "Other"];

// Who a piece of feedback should teach. "cgs" (firm-wide) is left to the Sources page.
export const SCOPES = [
  ["theme", "The team's research direction"],
  ["consultant", "Only my own searches"],
  ["opportunity", "Only this company"],
];

// Labels only: the backend's stage values stay as they are. Nurture shows as "Parked" (redesign decision D2).
export const STAGE_LABELS = {
  researching: "Researching",
  review: "Your decision",
  identified: "Identified",
  contacted: "Contacted",
  active: "Active",
  approval: "Awaiting approval",
  qualification: "Qualified",
  nurture: "Parked",
  rejected: "Rejected",
  closed: "Closed",
};

export const CLOSE_REASONS = ["Client declined", "No budget this year", "Problem solved internally", "Went with another advisor", "Other"];

export const APPROVER_ROLES = ["Lead owner", "Another consultant", "Director of Operations"];

export const NOTE_TYPES = ["Meeting notes", "Email", "Phone call"];

export const NEED_FIELDS = [
  ["team", "Team"],
  ["process", "Process"],
  ["problem", "Problem"],
  ["impact", "Business impact"],
  ["target", "Target or deadline"],
  ["service", "CGS service"],
];
