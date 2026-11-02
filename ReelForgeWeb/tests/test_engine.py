"""Integration coverage for the actual web rendering, timeline and caption engine."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

import pytest
from engine import (
    ai_plan, cues_from_words, ffmpeg, make_ass, mapped_cues, probe, render,
    validate_timeline
)


def video_input(target: Path, seconds=2.7, sound=True):
    cmd=["ffmpeg","-hide_banner","-loglevel","error","-y",
         "-f","lavfi","-i",f"testsrc2=size=320x180:rate=30:duration={seconds}"]
    if sound:
        cmd+=["-f","lavfi","-i",f"sine=frequency=500:duration={seconds}"]
    cmd+=["-c:v","mpeg4","-q:v","9"]
    if sound: cmd+=["-c:a","aac"]
    else:cmd+=["-an"]
    cmd+=["-shortest",str(target)]
    subprocess.run(cmd,check=True,capture_output=True)
    return target


def test_validate_keeps_duration_below_one_minute():
    large={"title":"Hook", "segments":[{"start":i*3,"end":i*3+2.7,
               "speed":1.04,"zoom":1.04,"focus_x":.5,
               "transition":"cut","reason":"Sample"} for i in range(24)]}
    result=validate_timeline(large,100,trim=True)
    assert 0<result["duration"]<58
    assert len(result["segments"])>=5
    with pytest.raises(ValueError,match="shorten|duration|58"):
        validate_timeline(large,100,trim=False)


def test_invalid_manual_edits_rejected():
    bad={"segments":[{"start":5,"end":4,"zoom":1,"speed":1,"focus_x":.5}]}
    with pytest.raises(ValueError):
        validate_timeline(bad,20)
    bad["segments"][0].update(start=0,end=5,zoom=float("nan"))
    with pytest.raises(ValueError,match="finite"):
        validate_timeline(bad,20)


def test_caption_cue_mapping_ignores_removed_pauses(tmp_path):
    words=[
        {"start":0.1,"end":.35,"text":"Hello"},
        {"start":.36,"end":.6,"text":"friend"},
        {"start":2.05,"end":2.25,"text":"Welcome"},
        {"start":2.28,"end":2.50,"text":"back"}
    ]
    cues=cues_from_words(words)
    assert len(cues)>=2
    timeline=validate_timeline({"segments":[
        {"start":0,"end":.9,"zoom":1.02,"speed":1,"focus_x":.5,"transition":"cut"},
        {"start":1.9,"end":2.7,"zoom":1.02,"speed":1,"focus_x":.5,"transition":"cut"}]},3)
    p={"words":words,"cues":cues,"timeline":timeline}
    mapped=mapped_cues(p)
    assert len(mapped)==2
    assert mapped[1]["start"]>=.9
    make_ass(p,tmp_path/"captions.ass")
    assert "HELLO" in (tmp_path/"captions.ass").read_text()


def test_actual_two_cut_portrait_export(tmp_path):
    movie=video_input(tmp_path/"source.mp4",seconds=3)
    media=probe(movie)
    timeline=validate_timeline({"title":"Sample reel", "fit":"crop",
        "music":"none","caption_style":"bold","segments":[
         {"start":0,"end":1.15,"zoom":1.03,"speed":1.03,"focus_x":.5,
          "transition":"cut","reason":"Hook"},
         {"start":1.7,"end":2.9,"zoom":1.05,"speed":1.05,"focus_x":.5,
          "transition":"cut","reason":"Detail"}]},3)
    project={"source":"source.mp4","media":media,"timeline":timeline,
             "cues":[{"id":"c0","start":.15,"end":.65,"text":"Hello from reel forge"}]}
    log=[]
    output=render(project,tmp_path,log.append)
    assert output.exists()
    result=probe(output)
    assert result["width"]==360 and result["height"]==640
    assert 1.5<result["duration"]<5
    assert result["has_audio"]


def test_actual_blur_dissolve_music_export(tmp_path):
    movie=video_input(tmp_path/"source.mp4",seconds=3)
    media=probe(movie)
    timeline=validate_timeline({"title":"Broll",
        "fit":"blur","music":"subtle","caption_style":"minimal","segments":[
         {"start":0,"end":1.45,"zoom":1.0,"speed":1,"focus_x":.5,
          "transition":"cut","reason":"Opening"},
         {"start":1.5,"end":2.85,"zoom":1.0,"speed":1,"focus_x":.5,
          "transition":"dissolve","reason":"Transition"}]},3)
    project={"source":"source.mp4","media":media,"timeline":timeline,"cues":[]}
    output=render(project,tmp_path,lambda *_:None)
    assert output.exists()
    result=probe(output)
    assert result["has_audio"] and result["duration"]<60
