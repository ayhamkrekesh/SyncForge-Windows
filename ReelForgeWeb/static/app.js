"use strict";

/* ReelForge Web: editable cut timeline, caption editor, conversational re-edits,
   and approximate interactive preview. Final exports are rendered server-side. */
const $ = (id) => document.getElementById(id);
const els = Object.fromEntries(
  ["drop","fileInput","sourceRow","sourceName","sourceDuration","jobState",
   "jobTitle","jobDetail","maxUpload","preview","placeholder","captionOverlay",
   "durationBadge","playerMode","playBtn","sourceBtn","planSummary","planTitle",
   "planNotes","messages","chatForm","chatInput","sendBtn","timelineStrip",
   "timelineTotal","clips","captionRows","saveCaptionsBtn","fitSetting",
   "captionSetting","musicSetting","downloadBar","downloadLink","exportBtn",
   "saveBtn","addClipBtn","newBtn","toast"].map(id => [id, $(id)]));
let project = null;
let draft = null;
let cuesDraft = [];
let dirty = false;
let currentProject = "";
let activeId = "";
let playEdit = false;
let playIndex = 0;
let inSourceMode = false;
let revision = -1;
let lastStatus = "";
let noticeTimer;
let pollTimer;
let busy = false;
const STATUS_BUSY = ["analyzing","revising","rendering"];

function notice(message, delay = 4000) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => els.toast.classList.remove("show"), delay);
}
function fmt(value) {
  const s = Math.max(0, Number(value) || 0);
  return Math.floor(s / 60).toString().padStart(2,"0") + ":" +
    Math.floor(s % 60).toString().padStart(2,"0");
}
function seconds(value) { return Math.max(0, Number(value) || 0).toFixed(2); }
function deep(value) { return JSON.parse(JSON.stringify(value)); }
function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function append(parent, ...children) { children.forEach(c => parent.append(c)); return parent; }
function setHidden(el, value) { el.hidden = !!value; }
function message(role, text) {
  const root = element("div", "bubble " + role);
  append(root, element("small", "", role === "assistant" ? "AI EDITOR" : "YOU"),
    element("p", "", text));
  els.messages.append(root);
  els.messages.scrollTop = els.messages.scrollHeight;
}
async function request(url, options = {}) {
  const response = await fetch(url, options);
  let result = {};
  try { result = await response.json(); }
  catch { result = {detail:"Unexpected server response."}; }
  if (!response.ok) throw new Error(typeof result.detail === "string" ?
      result.detail : "Request failed (" + response.status + ").");
  return result;
}
function jsonRequest(method, url, body) {
  return request(url, {method, headers:{"Content-Type":"application/json"},
                       body:JSON.stringify(body)});
}
function setBusy(flag) {
  busy = flag;
  els.sendBtn.disabled = flag || !draft;
  els.chatInput.disabled = flag || !draft;
  els.saveBtn.disabled = flag || !draft;
  els.addClipBtn.disabled = flag || !draft;
  els.exportBtn.disabled = flag || !draft;
  els.saveCaptionsBtn.disabled = flag || !draft;
}
function showLoading(title, detail) {
  els.jobTitle.textContent = title;
  els.jobDetail.textContent = detail || "";
  setHidden(els.jobState, false);
}
function showReady() { setHidden(els.jobState, true); }
function planLength(timeline) {
  if (!timeline?.segments?.length) return 0;
  return timeline.segments.reduce((n,s,i) =>
    n + (s.end - s.start) / s.speed -
    (i && s.transition === "dissolve" ? .15 : 0),0);
}
function projectUrl(kind) {
  return "/api/projects/" + encodeURIComponent(currentProject) + "/video/" + kind;
}
function clipsForPreview() {
  const result = [];
  let cursor = 0;
  for (const seg of draft?.segments || []) {
    if (result.length && seg.transition === "dissolve") cursor -= .15;
    const duration = (seg.end - seg.start) / seg.speed;
    result.push({seg,offset:cursor,duration});
    cursor += duration;
  }
  return result;
}
function updateCaptionOverlay(index, sourceTime) {
  if (inSourceMode || !draft || index < 0) {
    els.captionOverlay.hidden = true; return;
  }
  const editSegments = clipsForPreview();
  const active = editSegments[index];
  if (!active) {els.captionOverlay.hidden = true;return;}
  const cue = cuesDraft.find(c => +c.start <= sourceTime && +c.end >= sourceTime);
  if (!cue?.text) {els.captionOverlay.hidden = true;return;}
  els.captionOverlay.textContent = cue.text.toUpperCase();
  els.captionOverlay.hidden = false;
}
function selectedClip(index, autoplay = false) {
  if (!draft?.segments?.[index]) return;
  const s = draft.segments[index];
  playIndex = index;
  els.preview.currentTime = Math.min(s.start + .02, s.end);
  els.preview.playbackRate = s.speed;
  activeId = s.id;
  updateCaptionOverlay(index, s.start);
  Array.from(els.timelineStrip.children).forEach((tile, i) => {
    tile.classList.toggle("selected", i === index);
  });
  if (autoplay) {
    playEdit = true;
    inSourceMode = false;
    els.preview.play().catch(() => notice("Press play to start the preview."));
  }
}
function startPreview() {
  if (!draft?.segments?.length) return;
  inSourceMode = false;
  els.preview.classList.toggle("blur-mode", draft.fit === "blur");
  els.preview.pause();
  selectedClip(0, true);
  els.playerMode.textContent = "Edit preview · cuts + approximate captions";
}
els.preview.addEventListener("timeupdate", () => {
  if (!project || !draft?.segments) return;
  if (inSourceMode) {
    els.captionOverlay.hidden = true;
    return;
  }
  const s = draft.segments[playIndex];
  if (!s) return;
  updateCaptionOverlay(playIndex, els.preview.currentTime);
  if (playEdit && els.preview.currentTime >= s.end - .05) {
    if (playIndex + 1 < draft.segments.length) {
      selectedClip(playIndex + 1, true);
    } else {
      playEdit = false; els.preview.pause();
      els.playerMode.textContent = "Preview completed · render to see exact transitions";
    }
  }
});
els.playBtn.addEventListener("click", () => {
  if (playEdit) {
    playEdit = false; els.preview.pause(); els.playBtn.textContent = "▶ Preview edit";
  } else {
    els.playBtn.textContent = "❚❚ Pause";
    startPreview();
  }
});
els.sourceBtn.addEventListener("click", () => {
  inSourceMode = true; playEdit = false;
  els.playBtn.textContent = "▶ Preview edit";
  els.captionOverlay.hidden = true;
  els.preview.classList.remove("blur-mode");
  els.preview.currentTime = 0;
  els.preview.playbackRate = 1;
  els.preview.play().catch(()=>{});
  els.playerMode.textContent = "Full source preview";
});
els.preview.addEventListener("pause", () => {
  if (!playEdit) els.playBtn.textContent = "▶ Preview edit";
});

function syncSettingInputs() {
  if (!draft) return;
  els.fitSetting.value = draft.fit;
  els.captionSetting.value = draft.caption_style;
  els.musicSetting.value = draft.music;
}
function applySettings() {
  if (!draft) return;
  draft.fit = els.fitSetting.value;
  draft.caption_style = els.captionSetting.value;
  draft.music = els.musicSetting.value;
  dirty = true;
  updatePlanInfo();
}
for (const select of [els.fitSetting, els.captionSetting, els.musicSetting])
  select.addEventListener("change", applySettings);

function updatePlanInfo() {
  if (!draft) return;
  els.planTitle.textContent = draft.title || "Untitled reel";
  els.planNotes.textContent = (draft.hook ? "Opening hook: " + draft.hook + "\n" : "") +
    (draft.notes || "Timeline created. Adjust any section or ask the AI to refine it.");
  setHidden(els.planSummary, false);
  els.timelineTotal.textContent = fmt(planLength(draft));
  els.durationBadge.textContent = fmt(planLength(draft)) + " / 00:58";
}
function numberInput(value, label, min, max, step, onChange) {
  const wrap = element("label", "", label);
  const input = element("input");
  input.type = "number"; input.value = String(value);
  input.min = String(min); input.max = String(max); input.step = String(step);
  input.setAttribute("aria-label", label);
  input.addEventListener("change", () => {
    const n = Number(input.value);
    if (!Number.isFinite(n)) return notice("Enter a valid number.");
    onChange(n);
  });
  wrap.append(input);
  return wrap;
}
function selector(value, label, options, onChange) {
  const wrap = element("label", "", label);
  const sel = element("select");
  options.forEach(opt => {
    const node = element("option", "", opt[1]);
    node.value = opt[0];
    sel.append(node);
  });
  sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value));
  wrap.append(sel);
  return wrap;
}
function renderClips() {
  els.clips.replaceChildren();
  els.timelineStrip.replaceChildren();
  if (!draft?.segments) return;
  const total = Math.max(.01, planLength(draft));
  draft.segments.forEach((segment,index) => {
    const tile = element("button","segment-tile");
    tile.style.flexGrow = String(Math.max(.2, (segment.end - segment.start) / segment.speed));
    tile.style.minWidth = "58px";
    tile.draggable = true;
    tile.title = segment.reason || "Selected video moment";
    append(tile, element("span","",String(index+1).padStart(2,"0")+" · "+fmt(segment.start)),
      element("small","",(segment.end-segment.start).toFixed(1)+"s · "+
        (segment.transition === "dissolve" ? "DISSOLVE" : "CUT")));
    tile.addEventListener("click", () => {
      els.preview.pause(); playEdit=false; selectedClip(index);
    });
    tile.addEventListener("dragstart", e => e.dataTransfer?.setData("text/plain", String(index)));
    tile.addEventListener("dragover", e=>e.preventDefault());
    tile.addEventListener("drop", e => {
      e.preventDefault();
      const from = Number(e.dataTransfer?.getData("text/plain"));
      if (!Number.isInteger(from) || from < 0 || from >= draft.segments.length) return;
      const [moved] = draft.segments.splice(from,1);
      draft.segments.splice(index,0,moved);
      draft.segments[0].transition="cut";
      dirty = true; renderClips(); updatePlanInfo();
    });
    els.timelineStrip.append(tile);

    const row = element("div","clip-row");
    append(row, element("span","clip-index","#"+String(index+1).padStart(2,"0")));
    append(row, numberInput(seconds(segment.start),"Source in (s)",0,project.media.duration,.01,
      n=>{segment.start=n; dirty=true;updatePlanInfo();renderClips();}));
    append(row, numberInput(seconds(segment.end),"Source out (s)",0,project.media.duration,.01,
      n=>{segment.end=n; dirty=true;updatePlanInfo();renderClips();}));
    append(row, numberInput(segment.speed,"Speed",.85,1.3,.01,
      n=>{segment.speed=n;dirty=true;updatePlanInfo();renderClips();}));
    append(row, numberInput(segment.zoom,"Zoom",1,1.12,.01,
      n=>{segment.zoom=n;dirty=true;updatePlanInfo();renderClips();}));
    append(row, numberInput(segment.focus_x,"Pan X",0,1,.05,
      n=>{segment.focus_x=n;dirty=true;updatePlanInfo();renderClips();}));
    append(row, selector(segment.transition,"Cut type",
      [["cut","Hard cut"],["dissolve","Dissolve"]], v=>{
        segment.transition=v;dirty=true;renderClips();updatePlanInfo();
      }));
    const ctrls=element("div","clip-controls");
    [["↑","Move up",-1],["↓","Move down",1],["×","Remove",0]].forEach(([symbol,title,delta])=>{
      const button=element("button","",symbol);
      button.title=title;
      button.setAttribute("aria-label",title+" clip "+(index+1));
      button.addEventListener("click",()=>{
        if (delta===0) {
          if (draft.segments.length===1) return notice("Keep at least one clip.");
          draft.segments.splice(index,1);
        } else {
          const target=index+delta;
          if (target<0 || target>=draft.segments.length) return;
          [draft.segments[index],draft.segments[target]]=
            [draft.segments[target],draft.segments[index]];
        }
        draft.segments[0].transition="cut";
        dirty=true; renderClips();updatePlanInfo();
      });ctrls.append(button);
    });
    row.append(ctrls); els.clips.append(row);
  });
}
function renderCaptionRows() {
  els.captionRows.replaceChildren();
  cuesDraft.forEach((cue,index)=>{
    const row=element("div","caption-entry");
    const time=element("span","",fmt(cue.start)+"–"+fmt(cue.end));
    const input=element("textarea");
    input.rows=2; input.value=cue.text;
    input.setAttribute("aria-label","Subtitle line "+(index+1));
    input.addEventListener("input",()=>cue.text=input.value);
    append(row,time,input);els.captionRows.append(row);
  });
  if (!cuesDraft.length)
    els.captionRows.append(element("p","helper",
      "No spoken words detected. The editor won't invent captions."));
}
function loadTimelineFromProject(data) {
  if (!data.timeline) return;
  draft=deep(data.timeline);
  cuesDraft=deep(data.cues || []);
  revision=data.revision ?? 0;dirty=false;
  els.playBtn.disabled=false;els.sourceBtn.disabled=false;
  syncSettingInputs();renderClips();renderCaptionRows();updatePlanInfo();
  setBusy(false);
  els.playerMode.textContent="Editable preview · final effects render in export";
  if (data.status==="ready" && data.revision>0)
    message("assistant",data.timeline.notes || "I've applied your editing changes. Review the timeline and render when ready.");
  if (data.status==="ready" && data.revision===0)
    message("assistant","I've studied the visual frames and transcript. "+
      (data.timeline.notes||"Your first cut is ready.")+" You can edit every clip or ask me for changes.");
}
function updateProject(data) {
  project=data;
  els.sourceRow.hidden=false;
  els.sourceName.textContent=data.source_name||"Source video";
  els.sourceDuration.textContent=data.media?.duration ? fmt(data.media.duration) : "Analyzing";
  if (data.status==="analyzing"){
    showLoading("AI is studying your video","Inspecting frames, words and pauses…");
    setBusy(true);
  } else if (data.status==="revising"){
    showLoading("AI is revising your edit","Applying your directions to the timeline…");
    setBusy(true);
  } else if (data.status==="rendering"){
    showLoading("Rendering the final reel","Building captions, zooms, cuts and audio…");
    setBusy(true);
  } else if (data.status==="error"){
    showLoading("Analysis could not finish",data.error||"Check the configured AI and media dependencies.");
    setBusy(false);
    notice(data.error||"Analysis failed",10000);
  } else {
    showReady();
    if (data.timeline && (!draft || data.revision!==revision)) loadTimelineFromProject(data);
    setBusy(false);
  }
  if (data.status==="complete" && data.output){
    els.downloadLink.href=projectUrl("result");
    els.downloadBar.hidden=false;
    if (lastStatus!=="complete") notice("Final MP4 ready. Download your reel.");
  } else if (lastStatus==="complete" && data.status!=="complete"){
    els.downloadBar.hidden=true;
  }
  const recent=(data.activity||[]).slice(-1)[0];
  if (recent && STATUS_BUSY.includes(data.status)) els.jobDetail.textContent=recent;
  lastStatus=data.status;
  if (data.media?.duration && els.preview.hidden){
    els.preview.src=projectUrl("preview");
    els.preview.hidden=false;els.placeholder.hidden=true;
    els.playerMode.textContent="Source loaded";
    els.preview.onerror=()=>{els.preview.hidden=true;els.placeholder.hidden=false;};
  }
}
async function poll() {
  if (!currentProject) return;
  try {
    const data=await request("/api/projects/"+encodeURIComponent(currentProject));
    updateProject(data);
  } catch(err){notice(err.message,6000);}
}
async function startPolling() {
  clearInterval(pollTimer);
  await poll();
  pollTimer=setInterval(poll,2100);
}
async function upload(file) {
  if (!file) return;
  const max = Number(els.maxUpload.textContent)||450;
  if(file.size > max*1024*1024) return notice("Video is above the "+max+"MB limit.",6000);
  if (busy) return notice("Finish the current job before uploading another video.");
  const form=new FormData();form.append("file",file,file.name);
  els.downloadBar.hidden=true;
  showLoading("Uploading "+file.name,"Uploading to your private editor…");
  setBusy(true);
  try{
    const data=await request("/api/projects",{method:"POST",body:form});
    currentProject=data.id;
    history.replaceState(null,"","/?project="+encodeURIComponent(data.id));
    project=null;draft=null;revision=-1;lastStatus="";
    els.messages.replaceChildren();
    message("assistant","I received your video. I'm examining visuals, pauses and transcript. I’ll show you the proposed cut as soon as analysis finishes.");
    els.preview.pause();els.preview.removeAttribute("src");els.preview.load();
    els.preview.hidden=true;els.placeholder.hidden=false;
    await startPolling();
  }catch(err){showReady();setBusy(false);notice(err.message,9000);}
}
els.drop.addEventListener("click",()=>els.fileInput.click());
els.drop.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();els.fileInput.click();}});
els.fileInput.addEventListener("change",()=>{if(els.fileInput.files[0])upload(els.fileInput.files[0]);});
["dragenter","dragover"].forEach(name=>els.drop.addEventListener(name,e=>{e.preventDefault();els.drop.classList.add("over");}));
["dragleave","drop"].forEach(name=>els.drop.addEventListener(name,e=>{e.preventDefault();els.drop.classList.remove("over");}));
els.drop.addEventListener("drop",e=>{if(e.dataTransfer?.files?.[0])upload(e.dataTransfer.files[0]);});
els.newBtn.addEventListener("click",()=>{window.location.href="/";});

els.chatForm.addEventListener("submit",async e=>{
  e.preventDefault();
  const instruction=els.chatInput.value.trim();
  if(!draft||instruction.length<2)return;
  if(dirty)return notice("Save your manual timeline edits before asking AI to revise it.",6000);
  els.chatInput.value="";
  message("user",instruction);
  try{
    await jsonRequest("POST","/api/projects/"+currentProject+"/chat",{instruction});
    message("assistant","I'm reviewing the footage and updating your edit…");
    setBusy(true); await poll();
  }catch(err){message("assistant","I couldn't apply that revision: "+err.message);notice(err.message);}
});
document.querySelectorAll(".suggestions button").forEach(button=>{
  button.addEventListener("click",()=>{
    if (!draft)return notice("Upload a video first.");
    els.chatInput.value=button.dataset.prompt;
    els.chatInput.focus();
  });
});
els.saveBtn.addEventListener("click",async()=>{
  if(!draft)return;
  applySettings();
  try {
    const data=await jsonRequest("PUT","/api/projects/"+currentProject+"/timeline",{timeline:draft});
    draft=deep(data.timeline); dirty=false;
    renderClips();updatePlanInfo();notice("Timeline saved successfully.");
    els.downloadBar.hidden=true;
  }catch(err){notice("Timeline not saved: "+err.message,7000);}
});
els.saveCaptionsBtn.addEventListener("click",async()=>{
  if(!draft)return;
  try {
    const data=await jsonRequest("PUT","/api/projects/"+currentProject+"/captions",{cues:cuesDraft});
    cuesDraft=deep(data.cues);
    els.downloadBar.hidden=true;
    notice("Captions saved.");
  }catch(err){notice("Could not save captions: "+err.message,7000);}
});
els.addClipBtn.addEventListener("click",()=>{
  if(!draft||!project)return;
  if(draft.segments.length>=24)return notice("Maximum 24 clips.");
  const last=draft.segments[draft.segments.length-1];
  let start=last?last.end:0;
  if(start+1>=project.media.duration)start=0;
  const segment={id:"manual"+Date.now(),start:+start.toFixed(2),
    end:+Math.min(project.media.duration,start+3.2).toFixed(2),
    zoom:1.02,speed:1.0,focus_x:.5,transition:"cut",reason:"User-added clip"};
  draft.segments.push(segment);dirty=true;renderClips();updatePlanInfo();
  notice("Clip added. Edit source in/out and click Save timeline.");
});
els.exportBtn.addEventListener("click",async()=>{
  if(!draft)return;
  if(dirty){
    notice("Save your timeline first, then render.",6000);
    els.saveBtn.focus();return;
  }
  try{
    await request("/api/projects/"+currentProject+"/render",{method:"POST"});
    els.downloadBar.hidden=true;setBusy(true);await poll();
  }catch(err){notice(err.message,6500);}
});
document.querySelectorAll(".tab").forEach(button=>{
  button.addEventListener("click",()=>{
    document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active",t===button));
    for(const name of ["timeline","captions","settings"])
      $(name+"Panel").hidden=button.dataset.tab!==name;
  });
});
(async()=>{
  try{
    const config=await request("/api/config");
    els.maxUpload.textContent=config.max_upload_mb||450;
    if(!config.ai_ready)notice("Server AI key is not configured yet. Add OPENAI_API_KEY in Render.",8500);
  }catch(err){notice("Unable to reach the editing server: "+err.message,7000);}
  const id=new URLSearchParams(location.search).get("project");
  if(id&&/^[a-zA-Z0-9_-]{8,60}$/.test(id)){
    currentProject=id;
    await startPolling();
  }
})();
