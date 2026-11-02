"""ReelForge Web editing engine: visual AI planning, word-aligned captions and FFmpeg export."""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Callable

MAX_REEL = 58.0
MAX_SOURCE = 900.0
MAX_SEGMENTS = 24
FADE = 0.15


def ffmpeg(args, *, timeout=3600, cwd=None):
    result = subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *map(str,args)],
                            cwd=cwd, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("FFmpeg: " + result.stderr.decode("utf-8", "replace")[-2000:])
    return result.stdout


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(path)], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError("Cannot read video: " + result.stderr.decode("utf-8","replace")[-350:])
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and
                  not (s.get("disposition") or {}).get("attached_pic")), None)
    if not video:
        raise ValueError("This file has no playable video stream.")
    raw = (info.get("format") or {}).get("duration") or video.get("duration")
    try:
        duration = float(raw)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Video duration is missing. Please re-export as H.264 MP4.") from None
    if duration > MAX_SOURCE:
        raise ValueError("First version supports videos up to 15 minutes.")
    return {"duration":round(duration,3),"width":int(video.get("width",0)),
            "height":int(video.get("height",0)),
            "has_audio":any(s.get("codec_type") == "audio" for s in streams)}


def normalize_audio(source, target):
    ffmpeg(["-i",source,"-map","0:a:0","-vn","-sn","-dn",
            "-ac","1","-ar","16000","-c:a","pcm_s16le",target])


def recognize(source, folder, log: Callable):
    from faster_whisper import WhisperModel
    wav = folder / "transcribe.wav"
    normalize_audio(source,wav)
    log("Transcribing and aligning individual spoken words…")
    model = WhisperModel(os.environ.get("WHISPER_MODEL","base"), device="cpu",
                         compute_type="int8",cpu_threads=min(6,os.cpu_count() or 4))
    try:
        segments,_ = model.transcribe(str(wav),beam_size=3,word_timestamps=True,
                                      vad_filter=True,condition_on_previous_text=False)
        words=[]
        for seg in segments:
            for w in (seg.words or []):
                if w.start is not None and w.end is not None and w.end > w.start and w.word.strip():
                    words.append({"start":round(float(w.start),3),"end":round(float(w.end),3),
                                  "text":w.word.strip()})
        return words
    finally:
        wav.unlink(missing_ok=True)


def cues_from_words(words):
    cues=[]; group=[]
    def flush():
        if group:
            cues.append({"id":"c"+str(len(cues)),"start":group[0]["start"],
                         "end":group[-1]["end"],
                         "text":" ".join(w["text"] for w in group)})
            group.clear()
    for w in words:
        if group and (len(group)>=4 or w["start"]-group[-1]["end"]>.34
                      or len(" ".join(x["text"] for x in group))>29):
            flush()
        group.append(w)
    flush()
    return cues


def sample_frames(source,folder,duration):
    folder.mkdir(exist_ok=True,parents=True)
    frames=[]
    for i in range(min(12,max(4,int(math.ceil(duration/18))))):
        at=(i+.4)*duration/min(12,max(4,int(math.ceil(duration/18))))
        target=folder/f"frame_{i}.jpg"
        try:
            ffmpeg(["-ss",f"{at:.3f}","-i",source,"-frames:v","1",
                    "-vf","scale=480:-2","-q:v","6",target],timeout=60)
            if target.exists() and target.stat().st_size>200:
                frames.append({"time":round(at,2),"file":target.name})
        except Exception:
            pass
    return frames


def make_proxy(source,target,has_audio):
    args=["-i",source,"-map","0:v:0"]
    if has_audio: args+=["-map","0:a:0?"]
    args+=["-vf","fps=24,scale=640:-2:force_original_aspect_ratio=decrease",
            "-c:v","libx264","-preset","ultrafast","-crf","30",
            "-pix_fmt","yuv420p","-movflags","+faststart"]
    args+=["-c:a","aac","-b:a","96k"] if has_audio else ["-an"]
    ffmpeg(args+[target],timeout=3600)


def json_schema():
    fields={
        "start":{"type":"number"},"end":{"type":"number"},
        "zoom":{"type":"number"},"speed":{"type":"number"},
        "focus_x":{"type":"number"},
        "transition":{"type":"string","enum":["cut","dissolve"]},
        "reason":{"type":"string"}
    }
    seg={"type":"object","additionalProperties":False,"properties":fields,
         "required":list(fields.keys())}
    props={"title":{"type":"string"},"hook":{"type":"string"},"notes":{"type":"string"},
           "fit":{"type":"string","enum":["crop","blur"]},
           "music":{"type":"string","enum":["none","subtle"]},
           "caption_style":{"type":"string","enum":["bold","minimal"]},
           "segments":{"type":"array","items":seg}}
    return {"type":"object","additionalProperties":False,"properties":props,
            "required":list(props.keys())}


def ai_plan(project,folder,instruction=None):
    """Send real timestamped video frames plus transcript to an AI editor."""
    provider = os.getenv("AI_PROVIDER", "openai").lower()
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured on the server.")
    duration=project["media"]["duration"]
    words=project.get("words",[])
    sampled=words if len(words)<=2600 else words[::math.ceil(len(words)/2600)]
    transcript=" ".join(f"[{w['start']:.1f}] {w['text']}" for w in sampled)[:30000]
    user=f"SOURCE VIDEO {duration}s; speech transcript with timestamps:\n{transcript}\n"
    if instruction:
        user+="\nCURRENT TIMELINE:\n"+json.dumps(project.get("timeline"),ensure_ascii=False)[:16000]
        user+="\nREVISION REQUEST:\n"+instruction[:1800]
        user+="\nOnly make the changes requested. Preserve other timing as much as possible."
    else:
        user+="\nChoose a professional, high-retention, truthful reel edit."
    content=[{"type":"input_text","text":user}]
    for frame in project.get("frames",[])[:12]:
        path=folder/"frames"/frame["file"]
        if path.exists():
            content.append({"type":"input_text","text":f"Original video frame at {frame['time']} seconds:"})
            content.append({"type":"input_image","image_url":"data:image/jpeg;base64,"+
                            base64.b64encode(path.read_bytes()).decode(),"detail":"low"})
    system=(
        "You are an exceptionally good professional short-form video editor. "
        "You can see sampled ORIGINAL frames with timestamps and an accurately timestamped transcript. "
        "Choose 4–16 coherent clips (each 0.5–12 seconds), total output approximately 40–55s, NEVER over 58s. "
        "Use actual source timestamps and don't invent visual events. Make a compelling hook, a coherent middle "
        "and ending. REMOVE PAUSES and dead air. Cut at speech/phrase boundaries and preserve truthful meaning. "
        "For talking heads use rhythmic jump cuts, zoom variety 1.0–1.07, and mostly hard cuts. "
        "Dissolves only when visually appropriate, not mid-sentence. "
        "Speed must be 1.0–1.12 for speech, 1.0–1.22 for b-roll. "
        "focus_x 0–1 means horizontal reframing. Choose crop vs blurred fit from frames. "
        "Use subtle music only when tonally appropriate, otherwise none. "
        "The supplied frames are samples not continuous video, so avoid asserting unseen details. "
        "Select chronological clips unless a later sound bite is a truthful strong hook. "
        "The reel can be further refined via chat; respond with full revised timeline."
    )
    if provider == "ollama":
        from local_ai import local_edit_plan
        return local_edit_plan(system, user, folder, project.get("frames", []), json_schema())
    if provider != "openai":
        raise RuntimeError("Unknown AI_PROVIDER: " + provider)
    from openai import OpenAI
    client=OpenAI(timeout=150,max_retries=1)
    response=client.responses.create(
        model=os.getenv("VISION_MODEL","gpt-4.1-mini"),
        instructions=system,
        input=[{"role":"user","content":content}],
        text={"format":{"type":"json_schema","name":"reel_edit","strict":True,
                        "schema":json_schema()}},
        max_output_tokens=4500,store=False)
    if not response.output_text:
        raise RuntimeError("AI returned no editorial plan.")
    return json.loads(response.output_text)


def tighten_pauses(candidate, words, gap_seconds=.62):
    """Remove long internal dead air from AI-selected speech clips.

    AI chooses the story and visual cuts. This deterministic second pass
    tightens long pauses at the individual word timestamp level, without
    touching b-roll where there is no recognized speech. It runs only on
    AI-generated plans, not on the user's manually edited timeline.
    """
    if not words or not isinstance(candidate, dict):
        return candidate
    segments = candidate.get("segments")
    if not isinstance(segments, list):
        return candidate
    result = []
    for seg in segments:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (ValueError, TypeError, KeyError):
            result.append(seg)
            continue
        speech = [w for w in words
                  if float(w["start"]) >= start and float(w["end"]) <= end]
        if len(speech) < 3 or end - start < 2:
            result.append(seg)
            continue
        speech.sort(key=lambda w: w["start"])
        cut_points = []
        for left, right in zip(speech, speech[1:]):
            silence = float(right["start"]) - float(left["end"])
            if silence >= gap_seconds:
                cut_points.append((float(left["end"]), float(right["start"])))
        trimmed_start = start if float(speech[0]["start"]) - start < .45 else max(start, float(speech[0]["start"]) - .12)
        trimmed_end = end if end - float(speech[-1]["end"]) < .45 else min(end, float(speech[-1]["end"]) + .17)
        if not cut_points:
            if trimmed_end - trimmed_start >= .45:
                result.append(dict(seg, start=round(trimmed_start,3), end=round(trimmed_end,3)))
            else:
                result.append(seg)
            continue
        prev = trimmed_start
        chunks = []
        for before, after in cut_points:
            finish = min(trimmed_end, before + .15)
            if finish - prev >= .43:
                chunks.append((prev,finish))
            prev = max(prev, after - .12)
        if trimmed_end - prev >= .43:
            chunks.append((prev,trimmed_end))
        if not chunks or len(result) + len(chunks) > MAX_SEGMENTS:
            result.append(seg)
            continue
        for i, (a,b) in enumerate(chunks):
            copy = dict(seg, start=round(a,3), end=round(b,3))
            if i:
                copy["transition"]="cut"
                copy["zoom"]=min(1.09, float(seg.get("zoom",1.03))+(i%2)*.016)
            result.append(copy)
    if len(result) > MAX_SEGMENTS or not result:
        return candidate
    return dict(candidate, segments=result)


def validate_timeline(candidate,source_duration,trim=False):
    if not isinstance(candidate,dict):
        raise ValueError("Invalid timeline.")
    items=candidate.get("segments")
    if not isinstance(items,list) or not 1<=len(items)<=MAX_SEGMENTS:
        raise ValueError("Select 1–24 clips.")
    segments=[]
    for i,seg in enumerate(items):
        if not isinstance(seg,dict): raise ValueError("Invalid clip.")
        try:
            start,end=float(seg["start"]),float(seg["end"])
            zoom=float(seg.get("zoom",1.0)); speed=float(seg.get("speed",1.05))
            focus=float(seg.get("focus_x",.5))
        except (ValueError,KeyError,TypeError):
            raise ValueError(f"Clip {i+1} has invalid numbers.") from None
        if not all(map(math.isfinite,(start,end,zoom,speed,focus))):
            raise ValueError("Clip numbers must be finite.")
        if trim:
            start=max(0.,start);end=min(source_duration,end)
            zoom=max(1.,min(zoom,1.12));speed=max(.85,min(speed,1.3));focus=max(0.,min(focus,1.))
        if not(0<=start<end<=source_duration+.01):
            raise ValueError(f"Clip {i+1} outside video duration.")
        if end-start<.4:
            if trim:continue
            raise ValueError(f"Clip {i+1} is too short.")
        if not (1.<=zoom<=1.12 and .85<=speed<=1.3 and 0<=focus<=1):
            raise ValueError("Zoom, speed or focus outside accepted range.")
        transition=seg.get("transition","cut")
        if transition not in ("cut","dissolve"):
            raise ValueError("Transition must be cut or dissolve.")
        segments.append({"id":str(seg.get("id") or f"s{i}")[:50],
                         "start":round(start,3),"end":round(end,3),
                         "zoom":round(zoom,3),"speed":round(speed,3),
                         "focus_x":round(focus,3),"transition":transition,
                         "reason":str(seg.get("reason",""))[:500]})
    if not segments:raise ValueError("The edit has no valid clips.")
    segments[0]["transition"]="cut"
    for i,s in enumerate(segments[1:],1):
        if s["transition"]=="dissolve" and min(
            (s["end"]-s["start"])/s["speed"],
            (segments[i-1]["end"]-segments[i-1]["start"])/segments[i-1]["speed"])<.6:
            s["transition"]="cut"
    def total_length(items):
        return sum((s["end"]-s["start"])/s["speed"] for s in items)-sum(
            FADE for s in items[1:] if s["transition"]=="dissolve")
    if trim and total_length(segments) > MAX_REEL:
        # Single linear pass. Never retry rounding tiny fractional adjustments:
        # that previously could create a non-terminating edit.
        shortened = []
        remaining = 55.0
        for original in segments:
            seg = dict(original)
            overlap = FADE if seg["transition"] == "dissolve" and shortened else 0.0
            allowed = remaining + overlap
            if allowed < .42:
                break
            current_length = (seg["end"] - seg["start"]) / seg["speed"]
            if current_length > allowed:
                seg["end"] = round(seg["start"] + max(.4, allowed-.004) * seg["speed"], 3)
                current_length = (seg["end"] - seg["start"]) / seg["speed"]
            if current_length < .4:
                break
            shortened.append(seg)
            remaining -= current_length - overlap
            if remaining < .42:
                break
        segments = shortened
    duration=total_length(segments)
    if duration>MAX_REEL or duration<.4:
        raise ValueError(f"Reel duration must be 0.4–58 seconds, got {duration:.1f}.")
    fit=candidate.get("fit","crop");music=candidate.get("music","none")
    caption_style=candidate.get("caption_style","bold")
    if fit not in ("crop","blur") or music not in ("none","subtle") or caption_style not in ("bold","minimal"):
        raise ValueError("Unknown caption, framing or music setting.")
    return {"title":str(candidate.get("title","ReelForge edit"))[:150],
            "hook":str(candidate.get("hook",""))[:400],
            "notes":str(candidate.get("notes",""))[:1200],
            "fit":fit,"music":music,"caption_style":caption_style,
            "segments":segments,"duration":round(duration,3)}


def mapped_cues(project):
    cues=project.get("cues",[]);segments=project["timeline"]["segments"]
    result=[];cursor=0.0
    for i,seg in enumerate(segments):
        if i and seg["transition"]=="dissolve":cursor-=FADE
        for cue in cues:
            a=max(seg["start"],float(cue["start"]))
            b=min(seg["end"],float(cue["end"]))
            if b-a<.05:continue
            result.append({"start":round(cursor+(a-seg["start"])/seg["speed"],3),
                           "end":round(cursor+(b-seg["start"])/seg["speed"],3),
                           "text":str(cue.get("text",""))[:140]})
        cursor+=(seg["end"]-seg["start"])/seg["speed"]
    return result


def _timestamp(t):
    t=max(0.,float(t))
    return f"{int(t//3600)}:{int(t//60)%60:02}:{int(t)%60:02}.{int(t*100)%100:02}"


def make_ass(project,path):
    style=project["timeline"]["caption_style"]
    size=75 if style=="bold" else 55
    header=("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
            "ScaledBorderAndShadow: yes\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
            " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Reel,DejaVu Sans,{size},&H00FFFFFF,&H0018E5F5,&H00111A28,&H88000000,"
            "-1,0,0,0,100,100,0,0,1,5,2,2,65,65,325,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events=[]
    for cue in mapped_cues(project):
        text=cue["text"].replace("\\","").replace("{","(").replace("}",")").replace("\n"," ").upper()
        if not text.strip():continue
        events.append(f"Dialogue: 0,{_timestamp(cue['start'])},{_timestamp(cue['end'])},"
                      f"Reel,,0,0,0,,{{\\fad(80,100)}}{text}")
    Path(path).write_text(header+"\n".join(events)+"\n",encoding="utf-8")


def _render_size():
    """Default is high-quality 1080p; CI can render small test videos quickly."""
    width = int(os.getenv("REEL_WIDTH", "1080"))
    height = int(os.getenv("REEL_HEIGHT", "1920"))
    if (width, height) not in ((1080, 1920), (720, 1280), (360, 640)):
        raise ValueError("Unsupported portrait output size.")
    return width, height


def _clip_filter(seg, fit):
    width, height = _render_size()
    z = seg["zoom"]
    focus = seg["focus_x"]
    if fit == "blur":
        return (
            f"fps=30,split[bb][ff];"
            f"[bb]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[back];"
            f"[ff]scale={width}:{height}:force_original_aspect_ratio=decrease[front];"
            f"[back][front]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
        )
    return (
        f"fps=30,scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-{width})*{focus:.3f}:(ih-{height})/2,"
        f"zoompan=z='min({z:.4f}+on*0.0001,1.12)':"
        f"x='(iw-iw/zoom)*{focus:.3f}':y='(ih-ih/zoom)/2':"
        f"d=1:s={width}x{height}:fps=30,format=yuv420p"
    )


def original_music(path,length):
    """Make simple original quiet percussion, avoiding unlicensed songs."""
    import wave,struct
    rate=16000;bpm=106
    n=int((length+1)*rate)
    with wave.open(str(path),"wb") as wf:
        wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(rate)
        for offset in range(0,n,4096):
            frames=[]
            for i in range(offset,min(n,offset+4096)):
                t=i/rate;beat=t*bpm/60
                phase=beat-math.floor(beat)
                tone=.13*math.sin(2*math.pi*75*t)*math.exp(-22*phase) 
                hat=.013*math.sin(2*math.pi*5500*t)*math.exp(-75*((beat*2)%1))
                frames.append(struct.pack("<h",int(max(-.9,min(.9,tone+hat))*32767)))
            wf.writeframes(b"".join(frames))


def render(project,folder,notify:Callable[[str],None]):
    """Render editable segment timeline to a validated MP4, with synchronized audio."""
    timeline=validate_timeline(project["timeline"],project["media"]["duration"])
    project["timeline"]=timeline
    source=folder/project["source"]
    parts_dir=folder/"parts";parts_dir.mkdir(exist_ok=True)
    clips=[]
    for idx,seg in enumerate(timeline["segments"]):
        target=parts_dir/f"{idx:02}.mp4"
        length=(seg["end"]-seg["start"])/seg["speed"]
        vfilter=_clip_filter(seg,timeline["fit"])
        # Input duration is source-relative; audio and video sped together.
        video_graph=(f"[0:v]setpts=PTS-STARTPTS,{vfilter},"
                     f"setpts=(PTS-STARTPTS)/{seg['speed']:.5f}[v]")
        # Build a single source input with trim achieved via -ss/-t on each segment.
        # ffmpeg -ss seeks source, -t is duration in original seconds.
        if project["media"]["has_audio"]:
            inputs=["-ss",seg["start"],"-t",seg["end"]-seg["start"],"-i",source]
            graph=video_graph + (f";[0:a]asetpts=PTS-STARTPTS,"
                                 f"atempo={seg['speed']:.5f}[a]")
            args=[*inputs,"-filter_complex",graph,"-map","[v]","-map","[a]"]
        else:
            inputs=["-ss",seg["start"],"-t",seg["end"]-seg["start"],"-i",source,
                    "-f","lavfi","-i","anullsrc=r=48000:cl=stereo"]
            graph=video_graph+(f";[1:a]atrim=duration={length:.3f},"
                               "asetpts=PTS-STARTPTS[a]")
            args=[*inputs,"-filter_complex",graph,"-map","[v]","-map","[a]"]
        args+=["-c:v","libx264","-preset","veryfast","-crf","23",
               "-c:a","aac","-b:a","160k","-ar","48000",
               "-r","30","-pix_fmt","yuv420p","-t",length,target]
        ffmpeg(args)
        clips.append(target)
        notify(f"Rendered scene {idx+1} of {len(timeline['segments'])}")
    # Reassemble segments. Crossfade only where AI/user marked dissolve.
    inputs=[]
    for p in clips:inputs.extend(["-i",p])
    g=[];cv="0:v";ca="0:a";running=(timeline["segments"][0]["end"]-timeline["segments"][0]["start"])/timeline["segments"][0]["speed"]
    for i,seg in enumerate(timeline["segments"][1:],1):
        nv=f"v{i}";na=f"a{i}"
        if seg["transition"]=="dissolve":
            offset=max(.01,running-FADE)
            g.append(f"[{cv}][{i}:v]xfade=transition=fade:duration={FADE:.3f}:offset={offset:.3f}[{nv}]")
            g.append(f"[{ca}][{i}:a]acrossfade=d={FADE:.3f}:c1=tri:c2=tri[{na}]")
            running+= (seg["end"]-seg["start"])/seg["speed"]-FADE
        else:
            g.append(f"[{cv}][{ca}][{i}:v][{i}:a]concat=n=2:v=1:a=1[{nv}][{na}]")
            running+=(seg["end"]-seg["start"])/seg["speed"]
        cv,ca=nv,na
    if len(clips)==1: # no graph required for single clip
        combined=clips[0]
    else:
        combined=folder/"joined.mp4"
        ffmpeg(inputs+["-filter_complex",";".join(g),"-map",f"[{cv}]",
                       "-map",f"[{ca}]","-c:v","libx264","-crf","21",
                       "-preset","veryfast","-pix_fmt","yuv420p",
                       "-c:a","aac","-b:a","160k",
                       "-t",min(running,58),combined])
    make_ass(project,folder/"captions.ass")
    final=folder/"final_reel.mp4"
    second=["-i",combined]
    if timeline["music"]=="subtle":
        original_music(folder/"original_music.wav",timeline["duration"])
        second+=["-i",folder/"original_music.wav"]
        audio_filter="[0:a]volume=1.0[voice];[1:a]volume=0.09[music];[voice][music]amix=inputs=2:duration=first[outa]"
        second+=["-filter_complex",audio_filter,"-map","0:v:0","-map","[outa]"]
    else:
        second+=["-map","0:v:0","-map","0:a:0"]
    # Burn in a few-word headline captions with fade; no subtitle drift.
    second+=["-vf","ass=captions.ass",
             "-c:v","libx264","-crf","21","-preset","veryfast",
             "-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",
             "-movflags","+faststart","-t",min(58.0,timeline["duration"]),final]
    ffmpeg(second,cwd=folder)
    actual=probe(final)
    if actual["duration"]>=60 or actual["duration"]<.35:
        final.unlink(missing_ok=True)
        raise RuntimeError("Rendered duration failed under-one-minute verification.")
    notify(f"Final export complete ({actual['duration']:.1f}s)")
    return final
