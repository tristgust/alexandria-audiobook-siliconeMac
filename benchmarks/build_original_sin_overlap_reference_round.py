#!/usr/bin/env python3
"""Build a blind extraction-quality round for Original Sin adaptation overlaps."""
from __future__ import annotations
import argparse, hashlib, json, random, re, shutil, subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import mlx.core as mx
import mlx_whisper
import numpy as np
import soundfile as sf
from huggingface_hub import snapshot_download
from mlx.utils import tree_unflatten
from scipy.signal import resample_poly
from mlx_audio.sts.models.mel_roformer import MelRoFormer, MelRoFormerConfig
from mlx_audio.sts.models.mossformer2_se.config import MossFormer2SEConfig
from mlx_audio.sts.models.mossformer2_se.model import MossFormer2SEModel
from mlx_audio.sts.models.mossformer2_se.mossformer2_se_wrapper import MossFormer2SE
from model_registry import resolve_model_path

ROUND_ID = "alexandria_original_sin_overlap_reference_cleanliness_v1"
SEED = 20260730
DEFAULT_MEDIA = Path("/Users/tristan/Library/Mobile Documents/3L68KQB4HG~com~readdle~CommonDocuments/Documents/DW7*(RERUN:BF)/7c_divergentTimeline3*(RERUN:BF)/20c_bennyChris&Roz/1_originalSinAdaptation.mp3")
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
VOCAL_MODEL = "mlx-community/mel-roformer-zfturbo-vocals-v1-mlx"
WHISPER_MODEL_KEY = "mlx_whisper_large_v3_turbo"
PADDING_SECONDS = 0.08

@dataclass(frozen=True)
class Selection:
    character: str
    segment_start: int
    segment_end: int
    book_speaker: str

SELECTIONS = (
    Selection("Bernice Summerfield",1110,1110,"BERNICE"),
    Selection("The Doctor",2616,2616,"DOCTOR"),
    Selection("Chris Cwej",190,191,"CHRIS CWEJ"),
    Selection("Roz Forrester",332,332,"ROZ FORRESTER"),
    Selection("Beltempest",2096,2096,"BELTEMPEST"),
    Selection("Under-Sergeant",1106,1106,"UNDER-SERGEANT"),
    Selection("Rashid",196,196,"RASHID"),
    Selection("Computer",721,721,"COMPUTER"),
    Selection("Doc Dantalion",1381,1381,"DOC DANTALION"),
    Selection("Homeless Forsaken",77,77,"HOMELESS FORSAKEN"),
    Selection("Powerless Friendless",2505,2505,"POWERLESS FRIENDLESS"),
    Selection("Zebulon Pryce",1559,1559,"ZEBULON PRYCE"),
    Selection("Hater of Humans",1904,1904,"HATER OF HUMANS"),
    Selection("Evan Claple",267,269,"EVAN CLAPLE"),
    Selection("Shythe Shahid",1841,1841,"SHYTHE SHAHID"),
    Selection("Securitybot",326,326,"BOT"),
    Selection("Tobias Vaughn / Robot",949,949,"TOBIAS VAUGHN"),
)

class RoundBuildError(RuntimeError):
    pass

def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def sha256_file(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()
def words(text):
    return re.findall(r"[a-z0-9']+", str(text or "").casefold().replace("’", "'"))
def word_error_rate(expected, observed):
    left,right=words(expected),words(observed)
    previous=list(range(len(right)+1))
    for i,lw in enumerate(left,1):
        current=[i]
        for j,rw in enumerate(right,1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(lw!=rw)))
        previous=current
    return previous[-1]/max(1,len(left))
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
def transcript_path(project):
    return project/"external_workflows"/"big_finish_overlap_reference_v1"/"private"/"transcript.json"
def cut(media,start,end,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y","-ss",f"{start:.6f}","-t",f"{end-start:.6f}","-i",str(media),"-ar","44100","-ac","2","-c:a","pcm_f32le",str(destination)],check=True)
def load_stereo(path):
    audio,rate=sf.read(str(path),dtype="float32",always_2d=True)
    if rate!=44100:
        raise RoundBuildError(f"Unexpected sample rate {rate}: {path}")
    if audio.shape[1]==1:
        audio=np.repeat(audio,2,axis=1)
    return audio[:,:2]
def write_stereo(path,audio):
    value=np.asarray(audio,dtype=np.float32)
    peak=float(np.max(np.abs(value))) if value.size else 0.0
    if peak>0.98:
        value*=0.98/peak
    path.parent.mkdir(parents=True,exist_ok=True)
    sf.write(str(path),value,44100,subtype="PCM_16")
def separate(model,source,destination):
    audio=load_stereo(source)
    output=model(mx.array(audio.T[None,...]))[0]
    mx.eval(output)
    write_stereo(destination,np.array(output).T)
def load_mossformer():
    snapshot=Path(snapshot_download(
        "starkdmi/MossFormer2_SE_48K_MLX",
        revision="ccd0ded00e26f38e9f5b0ba21608aa6a0bcd6434",
        allow_patterns=["model_fp16.safetensors"],
        local_files_only=True,
    ))
    config=MossFormer2SEConfig()
    wrapper=MossFormer2SE(config)
    weights=mx.load(str(snapshot/"model_fp16.safetensors"))
    wrapper.update(tree_unflatten(list(weights.items())))
    model=MossFormer2SEModel(config=config,model=wrapper)
    model.eval()
    return model

def enhance(source,destination,model):
    audio=load_stereo(source)
    mono=np.mean(audio,axis=1,dtype=np.float32)
    work=resample_poly(mono,48000,44100).astype(np.float32)
    output=np.asarray(model.enhance(work),dtype=np.float32).reshape(-1)
    restored=resample_poly(output,44100,48000).astype(np.float32)
    write_stereo(destination,np.column_stack([restored,restored]))
def transcribe(path,model):
    result=mlx_whisper.transcribe(str(path),path_or_hf_repo=model,language="en",condition_on_previous_text=False,word_timestamps=False,verbose=False)
    return str(result.get("text") or "").strip()
def metrics(path):
    audio,rate=sf.read(str(path),dtype="float32",always_2d=True)
    mono=np.mean(audio,axis=1,dtype=np.float32)
    return {"sha256":sha256_file(path),"sample_rate":int(rate),"duration_seconds":len(mono)/rate,"peak":float(np.max(np.abs(mono))) if mono.size else 0.0,"rms":float(np.sqrt(np.mean(np.square(mono),dtype=np.float64))) if mono.size else 0.0}

def build_review(output,groups):
    review=output/"review"
    audio_root=review/"audio"
    audio_root.mkdir(parents=True,exist_ok=True)
    rng=random.Random(SEED)
    public_groups=[]
    answer_key={}
    for group in groups:
        candidates=list(group["candidates"])
        rng.shuffle(candidates)
        public_candidates=[]
        for candidate in candidates:
            blind_id=hashlib.sha256(f"{ROUND_ID}:{group['book_speaker']}:{candidate['variant']}:{candidate['metrics']['sha256']}".encode()).hexdigest()[:16]
            shutil.copy2(candidate["path"],audio_root/f"{blind_id}.wav")
            public_candidates.append({"id":blind_id,"audio":f"audio/{blind_id}.wav"})
            answer_key[blind_id]={**candidate,"path":str(candidate["path"]),"character":group["character"],"book_speaker":group["book_speaker"],"transcript":group["transcript"]}
        public_groups.append({"character":group["character"],"book_speaker":group["book_speaker"],"transcript":group["transcript"],"candidates":public_candidates})
    write_json(output/"private"/"answer-key.json",{"schema_version":1,"round_id":ROUND_ID,"generated_at":utc_now(),"candidates":answer_key,"production_changes":False})
    (review/"data.js").write_text("window.ORIGINAL_SIN_OVERLAP_ROUND = "+json.dumps({"schema_version":1,"round_id":ROUND_ID,"groups":public_groups},indent=2,ensure_ascii=False)+";\n",encoding="utf-8")
    (review/"index.html").write_text(REVIEW_HTML,encoding="utf-8")

REVIEW_HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original Sin overlap references</title><link rel="icon" href="data:,"> <style>:root{font-family:Inter,system-ui,sans-serif;color:#28231f;background:#f2eee6}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:24px 20px}header{border-bottom:1px solid #d2c8b8}h1,h2{font-family:Georgia,serif}.group{background:#fffdf8;border:1px solid #d5ccbd;border-radius:10px;padding:18px;margin:18px 0}.transcript{font:17px/1.45 Georgia,serif}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.candidate{border:1px solid #d9d0c3;border-radius:8px;padding:12px;background:#fff}audio{width:100%}.ratings{display:grid;gap:8px}label{display:grid;gap:4px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:7px;border:1px solid #b9afa2;border-radius:5px}textarea{min-height:58px}.decision{display:flex;gap:12px;margin:8px 0}.decision label{display:flex;align-items:center;gap:5px}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:780px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Original Sin character-reference extraction</h1><p>Processing is hidden. Reject music, effects, echo, speaker bleed, or damaged identity.</p><button id="export">Export review</button> <span id="progress"></span></header><main id="app"></main><script src="data.js"></script><script>(()=>{const d=window.ORIGINAL_SIN_OVERLAP_ROUND,k='os-overlap:'+d.round_id;let s={};try{s=JSON.parse(localStorage.getItem(k)||'{}')}catch(_){s={}}const app=document.getElementById('app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const g of d.groups){const sec=document.createElement('section');sec.className='group';sec.innerHTML=`<h2>${g.character}</h2><p class="transcript">${g.transcript}</p><div class="grid"></div>`;const grid=sec.querySelector('.grid');g.candidates.forEach((c,i)=>{const card=document.createElement('article');card.className='candidate';card.innerHTML=`<h3>Candidate ${i+1}</h3><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Voice isolation<select data-id="${c.id}" data-name="isolation">${scale}</select></label><label>Naturalness<select data-id="${c.id}" data-name="naturalness">${scale}</select></label><label>Identity clarity<select data-id="${c.id}" data-name="identity">${scale}</select></label><label>Reference usefulness<select data-id="${c.id}" data-name="usefulness">${scale}</select></label></div><div class="decision"><label><input type="radio" name="d-${c.id}" value="pass" data-id="${c.id}" data-name="decision">Pass</label><label><input type="radio" name="d-${c.id}" value="fail" data-id="${c.id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.id}" data-name="notes"></textarea></label>`;grid.appendChild(card)});app.appendChild(sec)}for(const e of document.querySelectorAll('[data-id]')){const v=s[e.dataset.id]?.[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',ev=>{const id=ev.target.dataset.id,n=ev.target.dataset.name;s[id]=s[id]||{};s[id][n]=ev.target.value;localStorage.setItem(k,JSON.stringify(s));progress()})}function progress(){const ids=d.groups.flatMap(g=>g.candidates.map(c=>c.id)),done=ids.filter(id=>s[id]?.decision).length;document.getElementById('progress').textContent=`${done} of ${ids.length} decided`}document.getElementById('export').onclick=()=>{const a=document.createElement('a'),b=new Blob([JSON.stringify({schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:s},null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();</script></body></html>'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--project-root",type=Path,default=DEFAULT_PROJECT)
    parser.add_argument("--media",type=Path,default=DEFAULT_MEDIA)
    parser.add_argument("--output-root",type=Path)
    args=parser.parse_args()
    project=args.project_root.expanduser().resolve()
    media=args.media.expanduser().resolve()
    output=args.output_root.expanduser().resolve() if args.output_root else project/"external_workflows"/"big_finish_overlap_reference_v1"/"reference_cleanliness_round_v1"
    if output.exists(): shutil.rmtree(output)
    segments=json.loads(transcript_path(project).read_text(encoding="utf-8"))["segments"]
    whisper=str(resolve_model_path(WHISPER_MODEL_KEY,local_files_only=True))
    vocal_model=MelRoFormer.from_pretrained(VOCAL_MODEL,config=MelRoFormerConfig.zfturbo_vocals_v1())
    vocal_model.eval()
    enhancer=load_mossformer()
    groups=[]
    for selection in SELECTIONS:
        start=max(0,float(segments[selection.segment_start]["start"])-PADDING_SECONDS)
        end=float(segments[selection.segment_end]["end"])+PADDING_SECONDS
        text=" ".join(str(segments[i].get("text") or "").strip() for i in range(selection.segment_start,selection.segment_end+1)).strip()
        slug=re.sub(r"[^a-z0-9]+","_",selection.book_speaker.casefold()).strip("_")
        private_audio=output/"private"/"audio"
        mix=private_audio/f"{slug}__mix.wav"
        vocal=private_audio/f"{slug}__vocal.wav"
        enhanced=private_audio/f"{slug}__mix_mossformer2.wav"
        cut(media,start,end,mix)
        separate(vocal_model,mix,vocal)
        enhance(mix,enhanced,enhancer)
        candidates=[]
        for variant,path in (("source_mix",mix),("mel_roformer_vocal",vocal),("mossformer2_source_mix",enhanced)):
            observed=transcribe(path,whisper)
            candidates.append({"variant":variant,"path":path,"metrics":metrics(path),"automatic_transcript":observed,"word_error_rate":word_error_rate(text,observed),"first_word_present":bool(words(text) and words(observed) and words(text)[0]==words(observed)[0])})
        groups.append({"character":selection.character,"book_speaker":selection.book_speaker,"transcript":text,"source":{"segment_start":selection.segment_start,"segment_end":selection.segment_end,"start_seconds":start,"end_seconds":end},"candidates":candidates})
        print(f"built {selection.character}",flush=True)
    build_review(output,groups)
    summary={"schema_version":1,"round_id":ROUND_ID,"generated_at":utc_now(),"character_count":len(groups),"candidate_count":sum(len(g["candidates"]) for g in groups),"production_changes":False,"output_root":str(output)}
    write_json(output/"generation-summary.json",summary)
    print(json.dumps(summary,indent=2))
    return 0
if __name__=="__main__": raise SystemExit(main())
