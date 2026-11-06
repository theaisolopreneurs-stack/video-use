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

Home mode (no edit_dir): a Home screen over the video library (library.py) - drag in clips,
pick a style, open any video in the Studio, export, open folders. This is what the desktop
shortcut starts.

    GET  /api/home, /project-thumb?name=, /api/job?id=
    POST /api/new {name}, /api/upload?project=&file= (raw bytes), /api/make {project, preset, title}
    POST /api/open {project}, /api/export {aspects}, /api/reveal {what}

Usage:
    python helpers/review_server.py                      # Home: the whole library
    python helpers/review_server.py --library "D:/My Videos"
    python helpers/review_server.py <videos_dir>/edit    # one video's Studio only
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
HOME_HTML = HELPERS / "studio" / "home.html"
THEME_CSS = HELPERS / "studio" / "theme.css"
MIC_JS = HELPERS / "studio" / "mic.js"


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
            "looks": self.look_list(),
            "effects_kit": __import__("effects").effect_list(),
            "transitions": sorted(__import__("render").TRANSITIONS) + sorted(__import__("effects").CUT_EFFECTS),
            "caption_styles": __import__("captions").style_list(),
            "caption_groups": __import__("captions").STYLE_GROUPS,
            "caption_lines": __import__("captions").caption_lines(plan, self.edit_dir),
            "speed_curves": {k: round(planlib.range_len({"start": 0, "end": 1, "speed_curve": k}), 4)
                             for k in planlib.SPEED_CURVES},
            "voice_fx": [{"id": k, "label": v[0]} for k, v in __import__("audio").VOICE_FX.items()],
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
            settings_changed: dict[str, object] = {}
            new_grade = body.get("grade", plan.get("grade"))
            if new_grade != plan.get("grade"):
                settings_changed["grade"] = new_grade
                changed.append(f"look: {plan.get('grade') or 'none'} -> {new_grade or 'none'}")
            new_fx = body.get("effects")
            if new_fx is not None and new_fx != (plan.get("effects") or []):
                settings_changed["effects"] = new_fx
                old_n = {e.get("type") for e in plan.get("effects") or []}
                new_n = {e.get("type") for e in new_fx}
                changed.append("effects: " + (", ".join(sorted(new_n)) or "none") +
                               (f" (removed {', '.join(sorted(old_n - new_n))})" if old_n - new_n else ""))
            for field in ("texts", "stickers"):
                new_list = body.get(field)
                if new_list is not None and new_list != (plan.get(field) or []):
                    settings_changed[field] = new_list
                    changed.append(f"{field}: {len(new_list)} on screen")
            for field in ("captions", "audio", "title", "adjust", "beat_sync", "fade", "background"):
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
                    if k == "text_edits":
                        if old.get(k) != new.get(k):
                            changed.append("the caption text")
                    elif old.get(k) != new.get(k):
                        changed.append(f"{field} {k}: {show(old.get(k))} -> {show(new.get(k))}")
            if new_ranges is not None:
                candidate = dict(plan, ranges=new_ranges)
                changed += planlib.diff(plan, candidate)
                old_by_key = {planlib.range_key(r): r for r in plan["ranges"]}
                for r in new_ranges:
                    old = old_by_key.get(planlib.range_key(r))
                    if old is None:
                        continue
                    name = '"' + (r.get("quote") or r.get("beat") or "")[:40] + '"'
                    if old.get("beat", "") != r.get("beat", ""):
                        changed.append(f"labeled {name} as {r.get('beat') or '(none)'}")
                    for k in planlib.RANGE_FX:
                        if old.get(k) != r.get(k):
                            v = r.get(k)
                            v = v.get("type") if isinstance(v, dict) else v
                            changed.append(f"{k} on {name}: {v if v not in (None, False) else 'off'}")
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
                plan["changes"] = [f"You: {c}" for c in changed]
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
                [__import__("library").worker_python(), str(HELPERS / "render.py"), str(self.plan_path),
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

    def looks(self) -> list[str]:
        return [x["id"] for x in self.look_list()]

    def look_list(self) -> list[dict]:
        """Every look the Studio offers: basics, the recipe looks (looks.py), then any .cube files."""
        import grade
        import looks
        basics = [("none", "None"), ("auto", "Auto-fix"), ("subtle", "Subtle"), ("neutral_punch", "Punchy"),
                  ("warm_cinematic", "Warm cinematic")]
        return ([{"id": k, "label": v, "group": "Basics"} for k, v in basics] + looks.look_list() +
                [{"id": f"lut:{p.name}", "label": p.stem.replace("_", " ").title(), "group": "Your LUTs"}
                 for p in grade.list_luts(self.edit_dir)])

    def _first_frame(self, width: int = 360):
        """A frame half a second into the first kept section, plus its source/time."""
        import framing
        plan = planlib.load(self.plan_path)
        r = plan["ranges"][0]
        src = planlib.resolve(plan["sources"][r["source"]], self.edit_dir)
        t = float(r["start"]) + min(0.5, (float(r["end"]) - float(r["start"])) / 2)
        return plan, src, t, framing.grab(src, t, width)

    def look_sample(self, look: str) -> Path | None:
        """Still frame with a colour look applied (cached per look)."""
        import grade
        if look not in self.looks():
            return None
        safe = look.replace(":", "_").replace(".", "_")
        out = self.edit_dir / "storyboard" / f"look_{safe}.jpg"
        if out.exists():
            return out
        plan, src, t, _ = self._first_frame()
        if look == "auto":
            r = plan["ranges"][0]
            vf, _ = grade.auto_grade_for_clip(src, float(r["start"]), float(r["end"]) - float(r["start"]))
        elif look.startswith("look:"):
            import looks
            vf = looks.look_filter(look)
        elif look.startswith("lut:"):
            vf = grade.lut_filter(look, self.edit_dir)
        else:
            vf = grade.get_preset(look)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                        "-vf", ",".join(x for x in (vf, "scale=-2:640") if x), str(out)], check=False)
        return out if out.exists() else None

    def title_sample(self) -> Path | None:
        """Still of the planned hook title over the first frame (behind the speaker if set)."""
        import numpy as np
        from PIL import Image
        import framing
        import title as titlelib
        from captions import load_brand
        plan = planlib.load(self.plan_path)
        t = plan.get("title") or {}
        if not t.get("text"):
            return None
        out = self.edit_dir / "storyboard" / "title_sample.jpg"
        if out.exists() and out.stat().st_mtime >= self.plan_path.stat().st_mtime:
            return out
        _, _, _, frame = self._first_frame(width=540)
        H, W = frame.shape[:2]
        layer = np.asarray(titlelib.title_image(t, load_brand(self.edit_dir), W, H), dtype=np.float32)
        alpha = layer[..., 3:4] / 255.0
        if t.get("behind_subject", True):
            alpha = alpha * (1.0 - framing.person_mask(frame)[..., None])
        mixed = frame.astype(np.float32) * (1 - alpha) + layer[..., :3] * alpha
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(mixed.astype(np.uint8)).save(out, quality=88)
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


class App:
    """What the server is showing: the library (Home), the Studio for one video, or both."""
    def __init__(self, studio: Studio | None = None, library=None):
        self.studio, self.library = studio, library

    def open(self, project: str) -> Studio:
        edit = self.library.folder(project) / "edit"
        if not (edit / "plan.json").exists():
            raise FileNotFoundError("this video hasn't been edited yet")
        if not self.studio or self.studio.edit_dir != edit:
            self.studio = Studio(edit)
        return self.studio


def make_handler(app: App):
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
            q = parse_qs(url.query)
            lib, studio = app.library, app.studio
            if url.path in ("/", "/index.html"):
                return self.send_file(HOME_HTML if lib else STUDIO_HTML)
            if url.path == "/mic.js":
                return self.send_file(MIC_JS)
            if url.path == "/theme.css":
                return self.send_file(THEME_CSS)
            if url.path == "/studio":
                if not studio:
                    return self.redirect("/")
                return self.send_file(STUDIO_HTML)
            if lib and url.path == "/api/home":
                return self.send_json({"library": str(lib.root), "projects": lib.projects(),
                                       "presets": lib.presets(), "current": studio.edit_dir.parent.name if studio else None,
                                       "shortcut": __import__("shortcut").installed()})
            if lib and url.path == "/project-thumb":
                p = lib.thumb(q.get("name", [""])[0])
                return self.send_file(p) if p else self.send_error(404)
            if lib and url.path == "/api/job":
                j = lib.jobs.get(q.get("id", [""])[0])
                return self.send_json(j or {"state": "missing"})
            if not studio:
                return self.send_error(404)
            if url.path == "/api/state":
                st = studio.state()
                st["home"] = bool(lib)
                if lib:
                    d = studio.edit_dir.parent
                    st["exports"] = sorted(p.name for p in (d / "exports").glob("*.mp4")) if (d / "exports").is_dir() else []
                    st["jobs"] = [j for j in lib.jobs.values() if j["project"] == d.name][-1:]
                return self.send_json(st)
            if url.path == "/thumb":
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
            if url.path == "/look-sample":
                p = studio.look_sample(parse_qs(url.query).get("look", [""])[0])
                return self.send_file(p) if p else self.send_error(404)
            if url.path == "/title-sample":
                p = studio.title_sample()
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

        def redirect(self, where: str) -> None:
            self.send_response(302)
            self.send_header("Location", where)
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
            lib, studio = app.library, app.studio
            if lib and url.path == "/api/upload":  # raw file bytes, streamed straight to disk
                q = parse_qs(url.query)
                try:
                    name = lib.save_upload(q.get("project", [""])[0], q.get("file", [""])[0],
                                           self.rfile, int(self.headers.get("Content-Length", 0)),
                                           sample=q.get("sample", [""])[0] == "1")
                except ValueError as e:
                    return self.send_json({"ok": False, "error": str(e)}, 400)
                return self.send_json({"ok": True, "file": name})
            if url.path == "/api/dictate":  # a voice note from a mic button -> text
                import dictate
                size = int(self.headers.get("Content-Length", 0))
                if size > dictate.MAX_BYTES:
                    return self.send_json({"ok": False, "error": "that recording is too long"}, 400)
                try:
                    return self.send_json({"ok": True, "text": dictate.to_text(self.rfile.read(size))})
                except (subprocess.SubprocessError, OSError, RuntimeError) as e:
                    return self.send_json({"ok": False, "error": str(e)[:200]})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if lib and url.path == "/api/new":
                return self.send_json({"ok": True, "name": lib.create(body.get("name") or "My video")})
            if lib and url.path == "/api/make":
                return self.send_json({"ok": True, "job": lib.start_make(body["project"], body.get("preset", "reel"),
                                                                         (body.get("title") or "").strip() or None)})
            if lib and url.path == "/api/shortcut":  # the person clicked "Add it" on the Home screen
                import shortcut
                try:
                    shortcut.create()
                except (SystemExit, OSError, subprocess.SubprocessError) as e:
                    return self.send_json({"ok": False, "error": str(e)})
                return self.send_json({"ok": True})
            if lib and url.path == "/api/sample":  # a style sample given as a link
                import inspire
                try:
                    path = inspire.fetch(str(body.get("url", "")).strip(), lib.folder(body["project"]) / "inspiration")
                except (RuntimeError, ValueError) as e:
                    return self.send_json({"ok": False, "error": str(e)})
                return self.send_json({"ok": True, "file": path.name})
            if lib and url.path == "/api/open":
                try:
                    app.open(body["project"])
                except (FileNotFoundError, ValueError) as e:
                    return self.send_json({"ok": False, "error": str(e)})
                return self.send_json({"ok": True})
            if lib and url.path == "/api/reveal":
                from library import open_in_file_manager
                what = body.get("what", "library")
                name = body.get("project") or (studio.edit_dir.parent.name if studio else "")
                target = lib.root if what == "library" or not name else lib.folder(name)
                open_in_file_manager(target / "exports" if what == "exports" else target)
                return self.send_json({"ok": True})
            if not studio:
                return self.send_error(404)
            if lib and url.path == "/api/export":
                aspects = [a for a in body.get("aspects") or ["9:16"] if a in ("9:16", "4:5", "1:1", "16:9")]
                if studio.state()["plan"].get("status") not in ("rough_approved", "final"):
                    return self.send_json({"ok": False, "error": "Approve the rough cut first."})
                return self.send_json({"ok": True, "job": lib.start_export(studio.edit_dir.parent.name, aspects)})
            if self.path == "/api/save":
                return self.send_json(studio.save(body))
            if self.path == "/api/approve":
                return self.send_json(studio.approve(body.get("gate", "")))
            if self.path == "/api/rough":
                return self.send_json(studio.start_rough())
            self.send_error(404)

    return Handler


def run_windowless() -> None:
    """Settings for when the desktop icon starts us under pythonw.exe (no console at all)."""
    if sys.stdout is None or sys.stderr is None:  # keep errors somewhere they can be found
        import tempfile
        log = open(Path(tempfile.gettempdir()) / "ai_video_editor.log", "a", encoding="utf-8", buffering=1)
        sys.stdout, sys.stderr = log, log
    if os.name == "nt":  # no console window flashing for each ffmpeg/ffprobe call
        original = subprocess.Popen.__init__

        def hidden(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            original(self, *args, **kwargs)
        subprocess.Popen.__init__ = hidden


def main() -> None:
    run_windowless()
    ap = argparse.ArgumentParser(description="Open the video editor (Home) or one video's Review Studio")
    ap.add_argument("edit_dir", type=Path, nargs="?", help="<videos_dir>/edit for a single video; omit for Home")
    ap.add_argument("--library", type=Path, default=None, help="video library folder (Home mode)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    if args.edit_dir:
        edit_dir = args.edit_dir.resolve()
        if not (edit_dir / "plan.json").exists():
            sys.exit(f"no plan.json in {edit_dir} - run suggest_cuts.py first")
        app, label = App(studio=Studio(edit_dir)), f"Review Studio for {edit_dir.parent.name}"
    else:
        from library import Library, default_library
        lib = Library((args.library or default_library()).expanduser().resolve())
        app, label = App(library=lib), f"AI Video Editor (videos in {lib.root})"

    url = f"http://127.0.0.1:{args.port}/"
    try:
        # Windows lets two programs share a port when address reuse is on, which would leave two
        # editors answering at once instead of reopening the running one.
        ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        # Already running (e.g. the desktop icon was clicked twice): just open it.
        print(f"already running: {url}")
        if not args.no_open:
            webbrowser.open(url)
        return
    print(f"{label}: {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
