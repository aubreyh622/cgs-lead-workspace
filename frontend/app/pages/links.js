// Where each opportunity is handled: the page and URL for its stage.
import { href } from "../lib.js";

export function pageFor(stage) {
  if (["researching", "review", "rejected"].includes(stage)) return "companies";
  if (["identified", "contacted", "nurture"].includes(stage)) return "outreach";
  if (["active", "approval", "qualification", "closed"].includes(stage)) return "qualification";
  return "home";
}

export function linkFor(o) {
  const page = pageFor(o.stage);
  if (page === "companies") {
    const tab = o.stage === "review" ? "review" : o.stage === "researching" ? "research" : "aside";
    return href("companies", { tab, id: o.stage === "review" ? o.id : undefined });
  }
  return href(page, { id: o.id });
}
