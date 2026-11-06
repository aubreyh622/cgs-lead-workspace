// Static demo build: the same exports as frontend/app/api.js, answered by the recorded workspace (demo/runtime.js)
// instead of a server. Nothing leaves the browser.
import { answer } from "./demo/runtime.js";

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
  return `The server answered ${status}.`;
}

let writes = 0;
export const writeCount = () => writes;

async function call(method, path, body) {
  if (method !== "GET") writes += 1;
  const res = await answer(method, path, body, person.get());
  if (res.status >= 400) throw new ApiError(messageFrom(res.body, res.status), res.status, res.body && res.body.code);
  return res.body;
}

export const api = {
  get: (path) => call("GET", path),
  post: (path, body = {}) => call("POST", path, body),
  put: (path, body = {}) => call("PUT", path, body),
  patch: (path, body = {}) => call("PATCH", path, body),
  del: (path) => call("DELETE", path),
};

export const enc = encodeURIComponent;
