// Where each opportunity is handled: the page and URL for its stage.
import { href } from "../lib.js";

export function pageFor(stage) {
  if (["researching", "review", "rejected", "closed"].includes(stage)) return "companies";
  if (["identified", "contacted"].includes(stage)) return "outreach";
  if (["active", "approval", "qualification", "nurture"].includes(stage)) return "qualification";
  return "overview";
}

const QUAL_TAB = { active: "active", approval: "approval", qualification: "qualified", nurture: "parked" };

export function linkFor(o) {
  const page = pageFor(o.stage);
  if (page === "companies") {
    const tab = o.stage === "review" ? "decision" : o.stage === "researching" ? "research" : "aside";
    return href("companies", { tab, id: o.id });
  }
  if (page === "qualification") return href(page, { tab: QUAL_TAB[o.stage], id: o.id });
  return href(page, { id: o.id });
}
