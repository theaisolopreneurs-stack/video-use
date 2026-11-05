"""The video library behind the Home screen: one folder per video, nothing to set up.

Default location: ~/Videos/AI Video Editor/ (override with the AI_VIDEO_LIBRARY environment
variable or review_server.py --library). Each video is a folder holding its clips, with the
editor's work in <video>/edit/ and finished files in <video>/exports/.

Long jobs (transcribing + first cut via make.py, exports via export.py) run in the
background; the page polls /api/job for progress.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HELPERS = Path(__file__).resolve().parent
REPO = HELPERS.parent
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def worker_python() -> str:
    """Python for background jobs. The desktop icon runs windowless pythonw.exe; jobs use the console
    python.exe beside it, started hidden, so the FFmpeg calls they make share that hidden console
    rather than each flashing a window."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def default_library() -> Path:
    env = os.environ.get("AI_VIDEO_LIBRARY")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Videos" / "AI Video Editor"


def slug(name: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", name).strip()
    return re.sub(r"\s+", " ", s)[:60] or "My video"


def open_in_file_manager(path: Path) -> None:
    """Show a folder in Explorer / Finder / the desktop's file manager."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - local path we created
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Library:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}

    # ---- projects ---------------------------------------------------------
    def folder(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad project name")
        return p

    def projects(self) -> list[dict]:
        out = []
        for d in sorted((p for p in self.root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
            clips = [c for c in d.iterdir() if c.suffix.lower() in VIDEO_EXTS]
            plan_p = d / "edit" / "plan.json"
            plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.exists() else None
            exports = sorted(p.name for p in (d / "exports").glob("*.mp4")) if (d / "exports").is_dir() else []
            if not clips and not plan:
                continue
            status = (plan or {}).get("status") or ("new" if not plan else "draft")
            out.append({
                "name": d.name, "clips": len(clips), "status": status, "exports": exports,
                "length": (plan or {}).get("total_duration_s"), "preset": (plan or {}).get("preset"),
                "has_thumb": bool(clips), "updated": d.stat().st_mtime,
                "busy": any(j["project"] == d.name and j["state"] == "running" for j in self.jobs.values()),
            })
        return out

    def create(self, name: str) -> str:
        base = slug(name)
        cand, n = base, 2
        while (self.root / cand).exists():
            cand = f"{base} ({n})"
            n += 1
        (self.root / cand).mkdir(parents=True)
        return cand

    def save_upload(self, project: str, filename: str, stream, length: int) -> str:
        """Write an uploaded clip straight to disk in chunks (clips can be several GB)."""
        name = Path(filename).name
        if Path(name).suffix.lower() not in VIDEO_EXTS:
            raise ValueError(f"{name} isn't a video file")
        dest = self.folder(project) / name
        remaining = length
        with open(dest, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        return name

    def thumb(self, project: str) -> Path | None:
        d = self.folder(project)
        out = d / "edit" / "storyboard" / "home_thumb.jpg"
        if out.exists():
            return out
        clips = sorted(c for c in d.iterdir() if c.suffix.lower() in VIDEO_EXTS)
        if not clips:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(clips[0]), "-frames:v", "1",
                        "-vf", "scale=360:-2", str(out)], check=False)
        return out if out.exists() else None

    def presets(self) -> list[dict]:
        out = []
        for p in sorted((REPO / "presets").glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": p.stem, "label": d["label"], "about": d["about"],
                        "aspect": (d.get("target") or {}).get("aspect"), "title": bool(d.get("title"))})
        order = ["viral", "reel", "justyn", "youtube", "tutorial", "podcast"]
        return sorted(out, key=lambda x: order.index(x["id"]) if x["id"] in order else 99)

    # ---- background jobs --------------------------------------------------
    def _run(self, job: dict, cmd: list[str]) -> None:
        env = {**os.environ, "PYTHONUTF8": "1", "GLOG_minloglevel": "2"}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            line = line.rstrip()
            if not line or line.lstrip().startswith(("INFO", "W0000", "WARNING", "I0000")):
                continue
            job["log"].append(line)
            job["log"] = job["log"][-200:]
            m = re.match(r"(\d)/(\d) (.*)", line)
            if m:
                job["step"], job["steps"], job["doing"] = int(m.group(1)), int(m.group(2)), m.group(3)
            elif line.startswith("rendering "):
                job["doing"] = line
        proc.wait()
        job["state"] = "done" if proc.returncode == 0 else "failed"
        job["finished"] = time.time()

    def start(self, kind: str, project: str, cmd: list[str]) -> str:
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "project": project, "state": "running", "log": [],
               "step": 0, "steps": 5 if kind == "make" else 1, "doing": "starting…", "started": time.time()}
        self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job, cmd), daemon=True).start()
        return jid

    def start_make(self, project: str, preset: str, title: str | None) -> str:
        cmd = [worker_python(), str(HELPERS / "make.py"), str(self.folder(project)), "--preset", preset]
        if title:
            cmd += ["--title", title]
        return self.start("make", project, cmd)

    def start_export(self, project: str, aspects: list[str], draft: bool = False) -> str:
        cmd = [worker_python(), str(HELPERS / "export.py"), str(self.folder(project) / "edit" / "plan.json"),
               "--aspects", ",".join(aspects)] + (["--draft"] if draft else [])
        return self.start("export", project, cmd)
