"""Build the static demo site (tools/static_demo/README.md) from frontend/ and a recording made by record.py.

    .venv/Scripts/python.exe -B tools/static_demo/build.py --out ../cgs-workspace-demo

The output is plain files for GitHub Pages: the workspace page with api.js replaced by the recorded answers
(site/api.js, site/demo/), a guide and motion effects, and data/recording.json. A privacy scan stops the build if the
recording holds anything that is not synthetic demo data (local paths, personal mail domains, key-like strings).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
KEEP_IN_OUT = {".git", "README.md", "LICENSE"}  # an existing demo repository keeps its git folder and readme

BLOCKED = [
    (r"[A-Za-z]:\\\\(Users|dev)", "a local Windows path"),
    (r"/Users/|/home/", "a local path"),
    (r"hjy12|27410", "a local user name"),
    (r"OneDrive", "a OneDrive path"),
    (r"@(gmail|outlook|hotmail|yahoo|ivey|uwo)\.", "a real mail domain"),
    (r"cgsadvisors\.com", "CGS's own web text"),
    (r"\b(sk-[A-Za-z0-9]{12,}|AIza[0-9A-Za-z_-]{20,}|tvly-[A-Za-z0-9]{8,}|pa-[A-Za-z0-9]{20,})", "a key-like string"),
    (r"\b8263\d{4}\b|\b45652634\b", "an Insightly record id"),
]


def prepare(rec: dict) -> dict:
    """Keep what the site needs. The recorded undo (accept, undo, accept again) becomes one accept from the first
    state; the site makes Undo itself by going back a state, using the recorded undo answer."""
    ts = rec["transitions"]
    for u in [t for t in ts if t.get("undo")]:
        first = next(t for t in ts if t["to"] == u["from"])
        again = next(t for t in ts if t["from"] == u["to"] and t["key"] == first["key"])
        first["retired"] = True
        again["from"] = first["from"]
    reach, todo = {0}, [0]
    while todo:
        e = todo.pop()
        for t in ts:
            if t["from"] == e and not t.get("retired") and not t.get("undo") and t["to"] not in reach:
                reach.add(t["to"])
                todo.append(t["to"])
    epochs = [{"id": e["id"], "label": e["label"], "t": e["t"], "users": e["users"] if e["id"] in reach else {}} for e in rec["epochs"]]
    used = {h for e in epochs for u in e["users"].values() for _, h in u.values()} | {t["res"] for t in ts if t.get("res")}
    keep_fields = ("from", "to", "method", "key", "user", "body", "status", "res", "kind", "auto", "label", "agent", "working",
                   "animate", "created", "undoable", "undo", "retired")
    return {"start": rec["start"], "recordedAt": rec["recordedAt"], "epochs": epochs,
            "transitions": [{k: t[k] for k in keep_fields if k in t} for t in ts],
            "blobs": {h: b for h, b in rec["blobs"].items() if h in used}}


def scan(text: str) -> list[str]:
    found = []
    for pattern, what in BLOCKED:
        m = re.search(pattern, text)
        if m:
            found.append(f"{what}: ...{text[max(0, m.start() - 60):m.end() + 60]}...")
    return found


def patch(path: Path, old: str, new: str, count: int = 1):
    s = path.read_text(encoding="utf-8")
    if s.count(old) != count:
        raise SystemExit(f"{path.name}: expected {count} of {old!r}, found {s.count(old)}. The frontend changed; update build.py.")
    path.write_text(s.replace(old, new), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recording", default=str(ROOT / "runtime" / "static-demo" / "recording.json"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rec = json.loads(Path(args.recording).read_text(encoding="utf-8"))
    data = prepare(rec)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    problems = scan(text)
    if problems:
        raise SystemExit("The recording holds data that must not be published:\n" + "\n".join(problems))

    out = Path(args.out).resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("Build the demo outside this repository (for example ../cgs-workspace-demo).")
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if child.name not in KEEP_IN_OUT:
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    shutil.copytree(FRONTEND / "app", out / "app")
    shutil.copy2(HERE / "site" / "api.js", out / "app" / "api.js")
    shutil.copytree(HERE / "site" / "demo", out / "app" / "demo")
    (out / "data").mkdir()
    (out / "data" / "recording.json").write_text(text, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    index = out / "index.html"
    shutil.copy2(FRONTEND / "index.html", index)
    patch(index, 'href="/app/styles.css">', 'href="app/styles.css">\n<link rel="stylesheet" href="app/demo/demo.css">')
    patch(index, '<script type="module" src="/app/main.js"></script>',
          f'<script>window.DEMO_START = {data["start"]}; if (!localStorage.getItem("cgs.person")) localStorage.setItem("cgs.person", "Alex Morgan");</script>\n'
          '<script src="app/demo/clock.js"></script>\n'
          '<script type="module" src="app/main.js"></script>\n'
          '<script type="module" src="app/demo/fx.js"></script>\n'
          '<script type="module" src="app/demo/tour.js"></script>')
    patch(index, "<title>CGS Lead Workspace</title>", "<title>CGS Lead Workspace | Interactive demo</title>")
    patch(index, 'content="CGS Lead Workspace: review what the agents prepared and decide."',
          'content="Interactive demo of the CGS Lead Workspace on invented companies. Runs in the browser; nothing is sent anywhere."')

    # the page draws itself again when the recorded state moves on (demo/runtime.js calls this)
    main_js = out / "app" / "main.js"
    main_js.write_text(main_js.read_text(encoding="utf-8") + """
// Static demo build: demo/runtime.js calls this when the recorded state moves on by itself.
window.demoRefresh = async () => {
  await updates.check();
  await refreshCounts();
  if (shown && !typing() && !unsaved()) await ctx.refresh();
};
""", encoding="utf-8")
    # research is shown at work for a few seconds: check it more often than the real 5 seconds
    patch(out / "app" / "pages" / "companies.js", "  }, 5000);\n}", "  }, 1500);\n}")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file() and ".git" not in f.parts)
    print(f"Built {out}: {size // 1024} KB, {len(data['blobs'])} recorded answers, "
          f"{sum(1 for t in data['transitions'] if not t.get('retired'))} steps.")


if __name__ == "__main__":
    main()
