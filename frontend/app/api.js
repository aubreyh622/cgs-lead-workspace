// The one place the page talks to the backend. Every write names the person acting (X-User).

const PERSON_KEY = "cgs.person";

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code || "";
  }
}

export const person = {
  get: () => localStorage.getItem(PERSON_KEY) || "",
  set: (name) => localStorage.setItem(PERSON_KEY, name),
  clear: () => localStorage.removeItem(PERSON_KEY),
};

function messageFrom(body, status) {
  if (body && typeof body.error === "string") return body.error;
  if (body && typeof body.detail === "string") return body.detail;
  if (body && Array.isArray(body.detail)) {
    // FastAPI validation errors: name the fields that are wrong.
    return body.detail.map((d) => `${(d.loc || []).slice(1).join(".") || "request"}: ${d.msg}`).join("; ");
  }
  return `The server answered ${status}.`;
}

// Counts the person's changes, so a page drawn from data held from before a change is never shown as current.
let writes = 0;
export const writeCount = () => writes;

async function call(method, path, body) {
  if (method !== "GET") writes += 1;
  const headers = { Accept: "application/json" };
  const who = person.get();
  if (who) headers["X-User"] = who;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError("The workspace server is not answering. Check that it is still running.", 0, "offline");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new ApiError(messageFrom(data, res.status), res.status, data && data.code);
  return data;
}

export const api = {
  get: (path) => call("GET", path),
  post: (path, body = {}) => call("POST", path, body),
  put: (path, body = {}) => call("PUT", path, body),
  patch: (path, body = {}) => call("PATCH", path, body),
  del: (path) => call("DELETE", path),
};

export const enc = encodeURIComponent;
