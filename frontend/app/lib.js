// Small helpers shared by every page: escaped HTML templates, icons, dates and URL state.

// ---------- escaped templates ----------
// html`...` escapes every interpolated value unless it is itself the result of html`...`.
// API data comes from the web and from AI models, so nothing reaches innerHTML unescaped.
class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => ESC[ch]);

function part(v) {
  if (v === null || v === undefined || v === false) return "";
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(part).join("");
  return esc(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => { out += part(v) + strings[i + 1]; });
  return new Safe(out);
}

export const raw = (s) => new Safe(s);

// Only web links leave the page; anything else (javascript:, data:) is dropped.
export function safeUrl(u) {
  if (!u) return "";
  try {
    const url = new URL(u, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

// ---------- icons (Lucide geometry, 1.6 stroke) ----------
const PATHS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
  people: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  companies: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3"/><path d="M3 13h18"/>',
  outreach: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  qualification: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  agents: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  bulb: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2Z"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  link: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  compass: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36 6.36-2.12z"/>',
  fileCheck: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m9 15 2 2 4-4"/>',
  calendarCheck: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
  back: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
};

export function icon(name, size = 18) {
  return raw(`<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ""}</svg>`);
}

// ---------- dates ----------
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function parseDay(iso) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}/.test(iso)) return null;
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

// "3 Nov" in the workspace year, "3 Nov 2025" otherwise. Text that is not an ISO date is shown as it is.
export function day(iso, workspaceToday) {
  const d = parseDay(iso);
  if (!d) return iso || "";
  const t = parseDay(workspaceToday);
  const sameYear = t && t.getUTCFullYear() === d.getUTCFullYear();
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${sameYear ? "" : " " + d.getUTCFullYear()}`;
}

export function daysBetween(fromIso, toIso) {
  const a = parseDay(fromIso), b = parseDay(toIso);
  return a && b ? Math.round((b - a) / 86400000) : null;
}

export function addDays(iso, n) {
  const d = parseDay(iso);
  if (!d) return "";
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// ---------- words ----------
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

export function initials(name) {
  return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

// ---------- URL state: #/page?key=value ----------
export function readRoute() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  return { page: path || "home", params: Object.fromEntries(new URLSearchParams(query || "")) };
}

export function href(page, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const s = q.toString();
  return `#/${page}${s ? "?" + s : ""}`;
}

// A brief's source: the site's name as a link that opens in a new tab, with the date. Text that is not a web address
// (a demo source such as "Company news") is shown as it is.
// A LinkedIn people search the person opens in their own browser. Agents never read LinkedIn (its terms forbid
// automated access); finding and checking the person stays a person's job.
export function linkedinSearch(words) {
  return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(words.filter(Boolean).join(" "))}`;
}

export function sourceLink(src, date) {
  const url = /^https?:\/\//i.test(src || "") ? safeUrl(src) : "";
  let site = src;
  if (url) {
    try { site = new URL(url).hostname.replace(/^www\./, ""); } catch { site = src; }
  }
  return html`${url ? html`<a href="${url}" target="_blank" rel="noopener noreferrer" title="${src}">${site}</a>` : src}${date ? `, ${date}` : ""}`;
}
