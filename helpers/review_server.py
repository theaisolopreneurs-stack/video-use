"""Review Studio: a local page to see, change, and approve the edit plan.

Serves helpers/studio/index.html plus a small JSON API over one project's
edit directory. Standard library only; binds to 127.0.0.1, so footage never
leaves the machine.

    GET  /api/state            plan, inventory, versions, diff vs previous, notes, render status
    GET  /thumb?source=&t=     frame at time t (cached in <edit>/storyboard/thumbs/)
    GET  /media/<path>         a file under <edit>/ (rough.mp4, contact sheets), with Range support
    POST /api/save             {ranges?, notes} -> snapshot + apply your direct edits, save notes
    POST /api/approve          {gate: plan|rough}
    POST /api/rough            start a --draft render of plan.json -> rough.mp4

Usage:
    python helpers/review_server.py <edit_dir>
    python helpers/review_server.py <videos_dir>/edit --port 8765 --no-open
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import plan as planlib

HELPERS = Path(__file__).resolve().parent
STUDIO_HTML = HELPERS / "studio" / "index.html"


class Studio:
    def __init__(self, edit_dir: Path):
        self.edit_dir = edit_dir
        self.plan_path = edit_dir / "plan.json"
        self.notes_path = edit_dir / "review_notes.json"
        self.thumbs = edit_dir / "storyboard" / "thumbs"
        self.lock = threading.Lock()
        self.render = {"state": "idle", "started": None, "finished": None, "error": None}

    # ---- state -----------------------------------------------------------
    def versions(self) -> list[int]:
        return sorted(int(p.stem.split("_v")[1]) for p in self.edit_dir.glob("plan_v*.json")
                      if p.stem.split("_v")[1].isdigit())

    def state(self) -> dict:
        plan = planlib.load(self.plan_path)
        vs = self.versions()
        prev_diff: list[str] = []
        if vs:
            prev_diff = planlib.diff(planlib.load(self.edit_dir / f"plan_v{vs[-1]}.json"), plan)
        inv = self.edit_dir / "inventory.json"
        rough = self.edit_dir / "rough.mp4"
        stamp = self.edit_dir / "rough.json"
        rendered_cut = json.loads(stamp.read_text(encoding="utf-8")).get("cut") if stamp.exists() else None
        return {
            "plan": plan,
            "problems": planlib.validate(plan, self.edit_dir),
            "total": planlib.total_duration(plan),
            "inventory": json.loads(inv.read_text(encoding="utf-8")) if inv.exists() else [],
            "versions": vs,
            "diff_prev": prev_diff,
            "notes": json.loads(self.notes_path.read_text(encoding="utf-8")) if self.notes_path.exists() else None,
            "rough": {"exists": rough.exists(),
                      "mtime": rough.stat().st_mtime if rough.exists() else None,
                      # Stale = the cut (sources + ranges) changed since rough.mp4 was rendered.
                      # Approving or adding notes rewrites plan.json but doesn't make it stale.
                      "stale": rough.exists() and rendered_cut != self.cut_signature(plan),
                      **self.render},
            "project": self.edit_dir.parent.name,
            "brand_captions": self.brand_captions(),
            "music": [self.music_name(p) for p in self.music_files()],
            "sfx_kit": sorted(__import__("audio").SFX_KIT),
        }

    def music_files(self) -> list[Path]:
        import audio
        return audio.list_music(self.edit_dir)

    def music_name(self, p: Path) -> str:
        """How a music file is written in plan.json: repo-relative if possible, else absolute."""
        import audio
        try:
            return p.relative_to(audio.REPO).as_posix()
        except ValueError:
            return str(p)

    def brand_captions(self) -> dict:
        import captions
        return captions.settings({}, captions.load_brand(self.edit_dir))

    @staticmethod
    def cut_signature(plan: dict) -> list:
        return planlib.cut_signature(plan)

    # ---- actions ---------------------------------------------------------
    def save(self, body: dict) -> dict:
        with self.lock:
            plan = planlib.load(self.plan_path)
            new_ranges = body.get("ranges")
            changed: list[str] = []
            settings_changed: dict[str, dict] = {}
            for field in ("captions", "audio"):
                new = body.get(field)
                old = plan.get(field) or {}
                if new is None or new == old:
                    continue
                settings_changed[field] = new
                def show(v):
                    if isinstance(v, dict):
                        return ", ".join(f"{a} {Path(str(b)).name if a == 'file' else b}" for a, b in v.items())
                    if isinstance(v, list):
                        return ", ".join(map(str, v)) or "(none)"
                    return "(default)" if v is None else str(v)
                for k in sorted(set(old) | set(new)):
                    if old.get(k) != new.get(k):
                        changed.append(f"{field} {k}: {show(old.get(k))} -> {show(new.get(k))}")
            if new_ranges is not None:
                candidate = dict(plan, ranges=new_ranges)
                changed += planlib.diff(plan, candidate)
                old_beats = {planlib.range_key(r): r.get("beat", "") for r in plan["ranges"]}
                for r in new_ranges:
                    old = old_beats.get(planlib.range_key(r))
                    if old is not None and old != r.get("beat", ""):
                        changed.append(f"labeled \"{(r.get('quote') or '')[:40]}\" as {r.get('beat') or '(none)'}")
            if changed:
                v = int(plan.get("version", 1))
                shutil.copyfile(self.plan_path, self.edit_dir / f"plan_v{v}.json")
                # Sections you switched off become cuts, so they stay visible (and restorable).
                for r in (plan["ranges"] if new_ranges is not None else []):
                    if not any(n["source"] == r["source"] and n["start"] < r["end"] and r["start"] < n["end"]
                               for n in new_ranges):
                        plan.setdefault("cuts", []).append({
                            "source": r["source"], "start": r["start"], "end": r["end"],
                            "text": r.get("quote", ""), "kind": "removed",
                            "reason": "switched off in Review Studio"})
                if new_ranges is not None:
                    plan["ranges"] = new_ranges
                plan.update(settings_changed)
                plan["version"] = v + 1
                plan["status"] = "draft"
                plan["changes"] = [f"v{v + 1} (you, in Review Studio): {c}" for c in changed]
                plan["total_duration_s"] = planlib.total_duration(plan)
                planlib.save(self.plan_path, plan)
            notes = body.get("notes") or {}
            has_notes = bool((notes.get("overall") or "").strip() or notes.get("cards") or notes.get("timestamped"))
            if has_notes or changed:
                notes.update({"version_reviewed": plan["version"], "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "direct_edits": changed, "status": "open"})
                self.notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "direct_edits": changed, "version": plan["version"], "notes_saved": has_notes}

    def approve(self, gate: str) -> dict:
        with self.lock:
            plan = planlib.load(self.plan_path)
            need, becomes = planlib.APPROVALS[gate]
            if plan.get("status", "draft") != need:
                return {"ok": False, "error": f"status is '{plan.get('status')}', needs '{need}'"}
            problems = planlib.validate(plan, self.edit_dir)
            if problems:
                return {"ok": False, "error": "; ".join(problems)}
            plan["status"] = becomes
            planlib.save(self.plan_path, plan)
            return {"ok": True, "status": becomes}

    def start_rough(self) -> dict:
        if self.render["state"] == "running":
            return {"ok": False, "error": "already rendering"}
        self.render.update(state="running", started=time.time(), finished=None, error=None)

        def work():
            proc = subprocess.run(
                [sys.executable, str(HELPERS / "render.py"), str(self.plan_path),
                 "-o", str(self.edit_dir / "rough.mp4"), "--draft"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONUTF8": "1"})  # helpers print "→"; Windows pipes default to cp1252
            self.render.update(state="done" if proc.returncode == 0 else "failed",
                               finished=time.time(),
                               error=None if proc.returncode == 0 else proc.stderr[-1500:])
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def caption_sample(self, style: str) -> Path | None:
        """Still frame showing a caption style on this footage; rebuilt when plan or brand change."""
        import captions
        if style not in captions.STYLES or style == "none":
            return None
        out = self.edit_dir / "storyboard" / f"caption_{style}.jpg"
        deps = [self.plan_path, self.edit_dir.parent / "brand.json", captions.REPO / "brand" / "brand.json"]
        newest = max((p.stat().st_mtime for p in deps if p.exists()), default=0)
        if not out.exists() or out.stat().st_mtime < newest:
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                captions.sample_frame(planlib.load(self.plan_path), self.edit_dir, style, out, height=640)
            except Exception:
                return None
        return out

    def thumb(self, source: str, t: float) -> Path | None:
        plan = planlib.load(self.plan_path)
        src = plan.get("sources", {}).get(source)
        if not src:
            return None
        self.thumbs.mkdir(parents=True, exist_ok=True)
        out = self.thumbs / f"{source}_{t:.2f}.jpg"
        if not out.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
                            "-i", str(planlib.resolve(src, self.edit_dir)), "-frames:v", "1",
                            "-vf", "scale=200:-2", str(out)], check=False)
        return out if out.exists() else None


def make_handler(studio: Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def send_json(self, obj, code: int = 200) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_file(self, path: Path) -> None:
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else max(0, size - int(b))
                end = int(b) if (a and b) else size - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(1 << 16, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    pass

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self.send_file(STUDIO_HTML)
            if url.path == "/api/state":
                return self.send_json(studio.state())
            if url.path == "/thumb":
                q = parse_qs(url.query)
                p = studio.thumb(q.get("source", [""])[0], float(q.get("t", ["0"])[0]))
                return self.send_file(p) if p else self.send_error(404)
            if url.path == "/music-file":
                want = parse_qs(url.query).get("f", [""])[0]
                p = next((m for m in studio.music_files() if studio.music_name(m) == want), None)
                return self.send_file(p) if p else self.send_error(404)
            if url.path == "/sfx":
                import audio
                name = parse_qs(url.query).get("name", [""])[0]
                p = audio.make_sfx().get(name)
                return self.send_file(p) if p else self.send_error(404)
            if url.path == "/caption-sample":
                p = studio.caption_sample(parse_qs(url.query).get("style", [""])[0])
                return self.send_file(p) if p else self.send_error(404)
            if url.path.startswith("/media/"):
                p = (studio.edit_dir / unquote(url.path[len("/media/"):])).resolve()
                if studio.edit_dir not in p.parents or not p.is_file():
                    return self.send_error(404)
                return self.send_file(p)
            self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/save":
                return self.send_json(studio.save(body))
            if self.path == "/api/approve":
                return self.send_json(studio.approve(body.get("gate", "")))
            if self.path == "/api/rough":
                return self.send_json(studio.start_rough())
            self.send_error(404)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Open the Review Studio for an edit directory")
    ap.add_argument("edit_dir", type=Path, help="<videos_dir>/edit (must contain plan.json)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    edit_dir = args.edit_dir.resolve()
    if not (edit_dir / "plan.json").exists():
        sys.exit(f"no plan.json in {edit_dir} - run suggest_cuts.py first")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Studio(edit_dir)))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Review Studio for {edit_dir.parent.name}: {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
