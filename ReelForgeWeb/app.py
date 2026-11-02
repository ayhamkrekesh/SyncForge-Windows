"""FastAPI web service for ReelForge AI video analysis and manual timeline edits.

Security: HTTP Basic auth, guess-resistant project IDs, limited upload sizes,
single-worker processing. Use a persistent /data disk on Render. For a large
multiuser deployment, replace this single-process executor with a job queue.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Body
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from engine import probe, recognize, cues_from_words, sample_frames, ai_plan, tighten_pauses, validate_timeline, make_proxy, render

BASE=Path(os.getenv("REEL_DATA_DIR","/data" if Path("/data").exists() else "./data")).resolve()
BASE.mkdir(parents=True,exist_ok=True)
if os.getenv("REQUIRE_AUTH")=="1" and not os.getenv("APP_PASSWORD"):
    raise RuntimeError("APP_PASSWORD must be configured for the public website.")
if os.getenv("REQUIRE_AI")=="1" and not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY must be configured for AI editing.")
ASSETS=Path(__file__).parent/"static"
MAX_BYTES=int(os.getenv("MAX_UPLOAD_MB","450"))*1024*1024
TYPES={".mp4",".mov",".mkv",".webm",".mxf",".avi",".mts",".m2ts"}
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix="reelforge")
LOCK=threading.RLock()
app=FastAPI(title="ReelForge AI Studio",docs_url=None,redoc_url=None)


@app.middleware("http")
async def auth(request: Request,call_next):
    if request.url.path=="/health":
        return await call_next(request)
    password=os.environ.get("APP_PASSWORD","")
    if password:
        import base64
        from urllib.parse import unquote
        header=request.headers.get("authorization","")
        ok=False
        if header.startswith("Basic "):
            try:
                data=base64.b64decode(header[6:]).decode("utf-8")
                supplied=data.split(":",1)[1]
                ok=hmac.compare_digest(supplied,password)
            except Exception:
                pass
        if not ok:
            return Response(status_code=401,headers={"WWW-Authenticate":'Basic realm="ReelForge Private Studio"'})
    return await call_next(request)


def workdir(project_id):
    if not isinstance(project_id,str) or len(project_id)>60 or not project_id.replace("_","").replace("-","").isalnum():
        raise HTTPException(404,"Project not found.")
    directory=BASE/project_id
    if not directory.is_dir() or not (directory/"project.json").exists():
        raise HTTPException(404,"Project not found.")
    return directory


def save(folder,data):
    with LOCK:
        tmp=folder/"project.partial.json"
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        tmp.replace(folder/"project.json")


def load(folder):
    with LOCK:
        return json.loads((folder/"project.json").read_text(encoding="utf-8"))


def log_change(folder,message,status=None,**fields):
    with LOCK:
        data=load(folder)
        data["activity"]=(data.get("activity",[])+[message])[-70:]
        if status:data["status"]=status
        data.update(fields)
        save(folder,data)


def process(folder):
    try:
        project=load(folder)
        source=folder/project["source"]
        log_change(folder,"Inspecting source media…")
        info=probe(source)
        log_change(folder,f"Input: {info['duration']} seconds, {info['width']}×{info['height']}.",media=info)
        make_proxy(source,folder/"proxy.mp4",info["has_audio"])
        log_change(folder,"Preparing browser preview…")
        frames=sample_frames(source,folder/"frames",info["duration"])
        log_change(folder,f"Analyzed {len(frames)} actual video frames.",frames=frames)
        if info["has_audio"]:
            words=recognize(source,folder,lambda msg:log_change(folder,msg))
        else:
            words=[]
        cues=cues_from_words(words)
        log_change(folder,f"Transcript: {len(words)} words. Asking the visual AI to edit the video…",
                   words=words,cues=cues)
        candidate=ai_plan(load(folder),folder)
        timeline=validate_timeline(tighten_pauses(candidate,words),info["duration"],trim=True)
        log_change(folder,"AI edit ready. Adjust the timeline or chat to revise it.",
                   status="ready",timeline=timeline,revision=0)
    except Exception as exc:
        log_change(folder,f"Analysis failed: {type(exc).__name__}: {exc}",status="error",
                   error=str(exc))


def do_chat(folder,instruction):
    try:
        current=load(folder)
        log_change(folder,"AI is reviewing your requested changes…")
        candidate=ai_plan(current,folder,instruction)
        timeline=validate_timeline(tighten_pauses(candidate,current.get("words") or []),current["media"]["duration"],trim=True)
        log_change(folder,"AI revision complete. Timeline updated.",
                   status="ready",timeline=timeline,revision=current.get("revision",0)+1)
    except Exception as exc:
        log_change(folder,f"AI revision failed: {exc}",status="ready",error=str(exc))


def do_render(folder):
    try:
        log_change(folder,"Rendering all cuts, transitions, captions and audio…")
        project=load(folder)
        video=render(project,folder,lambda msg:log_change(folder,msg))
        log_change(folder,"Your final MP4 is ready for download.",status="complete",
                   output=video.name)
    except Exception as exc:
        log_change(folder,f"Render failed: {type(exc).__name__}: {exc}",status="ready",
                   error=str(exc))


@app.get("/health")
def health():
    return {"ok":True,"product":"ReelForge AI Studio"}


@app.get("/api/config")
def config():
    return {"ai_ready":bool(os.getenv("OPENAI_API_KEY")),
            "max_upload_mb":MAX_BYTES//(1024*1024),
            "auth_enabled":bool(os.getenv("APP_PASSWORD"))}


@app.post("/api/projects")
async def create(file:UploadFile=File(...)):
    filename=Path(file.filename or "").name
    extension=Path(filename).suffix.lower()
    if extension not in TYPES:
        raise HTTPException(400,"Supported formats: MP4, MOV, MKV, WEBM, AVI, MXF, MTS, M2TS.")
    size=0
    project_id=secrets.token_urlsafe(19).replace("-","_")
    folder=BASE/project_id
    folder.mkdir()
    source=folder/("source"+extension)
    try:
        with source.open("wb") as out:
            while chunk:=await file.read(2*1024*1024):
                size+=len(chunk)
                if size>MAX_BYTES:
                    raise HTTPException(413,f"Video exceeds {MAX_BYTES//1048576} MB.")
                out.write(chunk)
        if size<1024:
            raise HTTPException(400,"Video appears empty or incomplete.")
        project={"id":project_id,"source":source.name,"source_name":filename[:180],
                 "status":"analyzing","activity":["Upload complete. Starting AI analysis…"],
                 "media":{},"words":[],"cues":[],"frames":[],"timeline":None,"revision":0}
        save(folder,project)
        POOL.submit(process,folder)
        return {"id":project_id,"status":"analyzing"}
    except Exception:
        import shutil
        shutil.rmtree(folder,ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get("/api/projects/{pid}")
def get_project(pid:str):
    project=load(workdir(pid))
    # Do not expose server-local filesystem paths.
    project.pop("source_path",None)
    return project


@app.get("/api/projects/{pid}/video/{kind}")
def get_media(pid:str,kind:str):
    folder=workdir(pid)
    if kind=="preview":path=folder/"proxy.mp4"
    elif kind=="result":path=folder/"final_reel.mp4"
    else:raise HTTPException(404,"Unknown video.")
    if not path.is_file():raise HTTPException(404,"Video is not available yet.")
    return FileResponse(path,media_type="video/mp4",filename="ReelForge_Reel.mp4" if kind=="result" else None,
                        content_disposition_type="attachment" if kind=="result" else "inline")


class TimelineRequest(BaseModel):
    timeline:dict


@app.put("/api/projects/{pid}/timeline")
def edit_timeline(pid:str,req:TimelineRequest):
    folder=workdir(pid)
    with LOCK:
        project=load(folder)
        if project["status"] not in ("ready","complete"):
            raise HTTPException(409,"Finish the current analysis or render first.")
        try:
            timeline=validate_timeline(req.timeline,project["media"]["duration"])
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        project.update(timeline=timeline,status="ready",output=None)
        project["activity"]=(project.get("activity",[])+["Manual timeline updated."])[-70:]
        save(folder,project)
    return {"timeline":timeline}


class CaptionRequest(BaseModel):
    cues:list[dict]


@app.put("/api/projects/{pid}/captions")
def edit_captions(pid:str,req:CaptionRequest):
    folder=workdir(pid)
    with LOCK:
        data=load(folder)
        if data["status"] not in ("ready","complete"):
            raise HTTPException(409,"Current job is in progress.")
        valid={c["id"]:c for c in data["cues"]}
        if set(str(c.get("id")) for c in req.cues)!=set(valid):
            raise HTTPException(400,"Do not add or remove cue IDs in this version.")
        for changed in req.cues:
            cue=valid[str(changed["id"])]
            text=str(changed.get("text","")).strip()
            if len(text)>180:raise HTTPException(400,"Caption is too long.")
            cue["text"]=text
        data["cues"]=list(valid.values())
        data["status"]="ready"
        data["output"]=None
        data["activity"]=(data.get("activity",[])+["Caption text updated."])[-70:]
        save(folder,data)
    return {"cues":data["cues"]}


class ChatRequest(BaseModel):
    instruction:str


@app.post("/api/projects/{pid}/chat")
def chat(pid:str,req:ChatRequest):
    folder=workdir(pid)
    instruction=req.instruction.strip()
    if not 2<=len(instruction)<=1800:
        raise HTTPException(400,"Explain the change in 2–1800 characters.")
    with LOCK:
        project=load(folder)
        if project["status"] not in ("ready","complete"):
            raise HTTPException(409,"Current job is in progress.")
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(503,"AI is not configured. Set OPENAI_API_KEY.")
        project["status"]="revising"
        project["error"]=None
        save(folder,project)
        POOL.submit(do_chat,folder,instruction)
    return {"status":"revising"}


@app.post("/api/projects/{pid}/render")
def render_project(pid:str):
    folder=workdir(pid)
    with LOCK:
        project=load(folder)
        if project["status"] not in ("ready","complete") or not project.get("timeline"):
            raise HTTPException(409,"No editable timeline is ready.")
        validate_timeline(project["timeline"],project["media"]["duration"])
        project["status"]="rendering"
        project["error"]=None
        save(folder,project)
        POOL.submit(do_render,folder)
    return {"status":"rendering"}


# Serve React-free, static HTML/JS editor. No frontend build system required.
app.mount("/",StaticFiles(directory=str(ASSETS),html=True),name="studio")
