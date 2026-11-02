"""API contract, local project security and editable timeline checks."""
from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient
import pytest
import app as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server,"BASE",tmp_path)
    monkeypatch.setenv("APP_PASSWORD","")
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    calls=[]
    monkeypatch.setattr(server.POOL,"submit",lambda fun,*args:calls.append((fun,args)))
    with TestClient(server.app) as test:
        yield test,calls


def starter():
    return {"title":"First reel","hook":"Hook","notes":"A neat edit",
            "fit":"crop","music":"none","caption_style":"bold",
            "segments":[
                {"start":0.2,"end":2.4,"zoom":1.03,"speed":1.05,"focus_x":.5,
                 "transition":"cut","reason":"First highlight"},
                {"start":3,"end":5.4,"zoom":1.06,"speed":1.05,"focus_x":.5,
                 "transition":"cut","reason":"Closing highlight"}
            ]}


def test_upload_edit_captions_and_permissions(client,monkeypatch):
    web,calls=client
    response=web.post("/api/projects",files={"file":("demo.mp4",b"0"*2048,"video/mp4")})
    assert response.status_code==200, response.text
    pid=response.json()["id"]
    assert len(pid)>=12
    assert len(calls)==1
    forbidden=web.get("/api/projects/../")
    assert forbidden.status_code in (404,405)
    data=web.get("/api/projects/"+pid).json()
    assert data["status"]=="analyzing"
    folder=server.workdir(pid)
    data["status"]="ready"
    data["media"]={"duration":8,"has_audio":True,"width":1280,"height":720}
    data["timeline"]=server.validate_timeline(starter(),8)
    data["cues"]=[{"id":"c0","start":0.3,"end":.9,"text":"HELLO"}]
    server.save(folder,data)

    changed=starter()
    changed["segments"][0]["end"]=2.0
    updated=web.put(f"/api/projects/{pid}/timeline",json={"timeline":changed})
    assert updated.status_code==200,updated.text
    assert updated.json()["timeline"]["segments"][0]["end"]==2.0
    too_long=starter()
    too_long["segments"][0]["end"]=999
    assert web.put(f"/api/projects/{pid}/timeline",json={"timeline":too_long}).status_code==400

    captions=web.put(f"/api/projects/{pid}/captions",json={
        "cues":[{"id":"c0","start":.3,"end":.9,"text":"HELLO EVERYONE!"}]})
    assert captions.status_code==200
    assert server.load(folder)["cues"][0]["text"]=="HELLO EVERYONE!"

    chat=web.post(f"/api/projects/{pid}/chat",json={"instruction":"Make it faster"})
    assert chat.status_code==503  # Genuine AI is required for chat, no faked AI
    assert web.get(f"/api/projects/{pid}/video/result").status_code==404


def test_authentication_requires_private_password(client,monkeypatch):
    web,_=client
    monkeypatch.setenv("APP_PASSWORD","a-strong-password")
    assert web.get("/health").status_code==200
    unauth=web.get("/api/config")
    assert unauth.status_code==401
    token=base64.b64encode(b"editor:a-strong-password").decode()
    assert web.get("/api/config",headers={"Authorization":"Basic "+token}).status_code==200


def test_render_request_and_chat_revisions(client,monkeypatch):
    web,calls=client
    pid=web.post("/api/projects",files={"file":("demo.mp4",b"1"*2048)}).json()["id"]
    folder=server.workdir(pid)
    data=server.load(folder)
    data["status"]="ready"
    data["media"]={"duration":12,"has_audio":True,"width":1080,"height":1920}
    data["timeline"]=server.validate_timeline(starter(),12)
    server.save(folder,data)
    monkeypatch.setenv("OPENAI_API_KEY","test-only-no-network")
    monkeypatch.setattr(server,"ai_plan",lambda *_args,**_kw:starter())

    result=web.post(f"/api/projects/{pid}/chat",json={"instruction":"Make the hook punchy"})
    assert result.status_code==200
    assert server.load(folder)["status"]=="revising"
    server.do_chat(folder,"Make the hook punchy")
    assert server.load(folder)["revision"]==1
    assert server.load(folder)["status"]=="ready"

    def fake_render(p,folder,log):
        output=folder/"final_reel.mp4"
        output.write_bytes(b"rendered")
        return output
    monkeypatch.setattr(server,"render",fake_render)
    assert web.post(f"/api/projects/{pid}/render").status_code==200
    server.do_render(folder)
    assert server.load(folder)["status"]=="complete"
    assert web.get(f"/api/projects/{pid}/video/result").status_code==200
