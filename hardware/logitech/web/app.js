const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="camera-token"]').content;
let current = null, mode = 'enhance', rotation = 0, mirror = false, busy = false, online = false;
let config = {}, frameLoading = false, frameURL = null, lastSpeech = 'Your moment is ready.';
function notice(message, error=false) { $('notice').textContent=message; $('notice').classList.toggle('error', error); }
async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : {'Content-Type':'application/json','X-Camera-Token':token}, body: data === undefined ? undefined : JSON.stringify(data)});
  const value = await response.json(); if (!response.ok) throw Error(value.error || 'Request failed'); return value;
}
function buttons() {
  $('capture').disabled = busy || !online;
  $('render').disabled = busy || !current || (mode === 'ai' && !config.openai);
  $('start').disabled=busy; $('stop').disabled=busy || !config.camera?.running;
  $('speak').disabled=busy || !current;
  $('render').textContent = busy ? 'Developing…' : 'Develop this moment ✧';
}
function transformPreview(){ $('preview').style.transform=`scaleX(${mirror?-1:1}) rotate(${rotation}deg) scale(${rotation%180 ? 0.625 : 1})`; }
async function status() {
  try {
    config = await api('/api/status'); online = config.camera.fresh;
    $('connection').textContent=online?'Camera connected':config.camera.running?'Connecting…':'Camera off';
    $('connection').classList.toggle('on', online);
    $('live').hidden=!online; $('preview').hidden=!online; $('placeholder').hidden=online;
    $('start').textContent = online ? 'Camera connected' : config.camera.running ? 'Waiting for camera…' : 'Connect camera ↗';
    $('start').hidden=online;
    $('device').textContent = `${config.camera.name} · ${config.camera.resolution}`;
    $('aiHint').textContent=config.openai?'Sends this capture + prompt to OpenAI. Uses API credits.':'OpenAI key not configured. Local effects work now.';
    $('voiceProvider').textContent=config.elevenlabs?'ElevenLabs':'Device voice';
    const s=config.sensors,v=s.values;
    $('lightValue').textContent=v.light===undefined?'—':`${Math.round(v.light*100)}%`;
    $('tempValue').textContent=v.temperature_c===undefined?'—':`${v.temperature_c.toFixed(1)} °C`;
    $('soundValue').textContent=v.sound===undefined?'—':`${Math.round(v.sound*100)}%`;
    $('arduinoValue').textContent=config.board?.connected?'USB connected':s.fresh?(s.source==='simulation'?'SIMULATED':'Sensor link'):'Not connected';
    $('sensorHint').textContent=s.fresh?`${s.source==='simulation'?'Simulated test inputs':'Live sensor inputs'} · snapshot frozen at capture`:'No fresh sensor measurements. No readings are invented.';
    if(config.camera.error) notice(config.camera.error,true);
    buttons();
  } catch(e){online=false;buttons();notice('Local server unavailable. Restart python3 server.py.',true);}
}
async function preview() {
  if(!config.camera?.running || frameLoading || document.hidden) return;
  frameLoading=true;
  try {
    const r=await fetch('/api/frame');if(!r.ok)return;
    const url=URL.createObjectURL(await r.blob()); $('preview').src=url;
    if(frameURL)URL.revokeObjectURL(frameURL);frameURL=url;
  } finally{frameLoading=false;}
}
async function act(fn){if(busy)return;busy=true;buttons();try{await fn();}catch(e){notice(e.message,true);}finally{busy=false;buttons();}}
$('start').onclick=()=>act(async()=>{await api('/api/camera/start',{});notice('Connecting to Logitech. First frames may take a few seconds.');await status();});
$('stop').onclick=()=>act(async()=>{await api('/api/camera/stop',{});notice('Camera released. Saved moments remain available.');await status();});
$('rotate').onclick=()=>{rotation=(rotation+90)%360;transformPreview();notice(`Next capture rotated ${rotation}°. Existing captures stay unchanged.`);};
$('mirror').onclick=()=>{mirror=!mirror;$('mirror').setAttribute('aria-pressed',String(mirror));transformPreview();};
$('strength').oninput=()=>{$('strengthValue').textContent=$('strength').value+'%';};
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{
 mode=button.dataset.mode;
 document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});
 $('localControl').hidden=['ai','sports','food'].includes(mode);$('aiControl').hidden=mode!=='ai';buttons();
});
function showCapture(job){
 current=job;$('resultSection').hidden=false;$('original').src=job.original;
 const edit=job.edits.at(-1);$('result').src=edit?.url||job.original;
 $('resultLabel').textContent=edit?(edit.provider==='openai'?'AI TRANSFORMATION':`LOCAL · ${edit.mode.toUpperCase()}`):'READY TO DEVELOP';
 $('download').href=edit?.url||job.original;
 $('resultMeta').textContent=`${job.width} × ${job.height} · ${new Date(job.created_at*1000).toLocaleTimeString()} · ${job.sensors.fresh?job.sensors.source+' snapshot':'No connected sensor readings'}`;
 buttons();
}
$('capture').onclick=()=>act(async()=>{
 const job=await api('/api/capture',{rotation,mirror});showCapture(job);
 $('flash').classList.remove('flashing');void $('flash').offsetWidth;$('flash').classList.add('flashing');
 notice('Captured and saved locally. Choose a dimension, then develop.');lastSpeech='Moment captured. Choose a style and I will develop it.';
 await gallery(); if($('voice').checked)await optionalFeedback();
});
$('render').onclick=()=>act(async()=>{
 const selected=mode;notice(selected==='ai'?'Creating your AI transformation. Your original is saved.':'Developing your moment locally…');
 const edit=await api('/api/render',{id:current.id,mode:selected,strength:Number($('strength').value)/100,prompt:$('prompt').value});
 current.edits.push(edit);showCapture(current);notice(`Ready in ${(edit.elapsed_ms/1000).toFixed(2)}s. ${edit.provider==='local'?'Local effect; no AI request made.':'AI-generated transformation.'}`);
 lastSpeech=selected==='sports'?'Your collectible card is ready.':selected==='food'?'Your fresh take is ready.':selected==='ai'?'Your imagined moment is ready. Take a look.':'Your moment is developed. Compare it with the original.';
 await gallery();if($('voice').checked)await optionalFeedback();
});
async function optionalFeedback(){try{await speakFeedback();}catch(e){notice(`Image saved. Voice playback unavailable: ${e.message}`,true);}}
async function speakFeedback(){
 if(config.elevenlabs){const a=await api('/api/speech',{id:current.id,text:lastSpeech});await new Audio(a.url).play();}
 else if('speechSynthesis' in window){speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(lastSpeech));}
 else notice('Speech playback is unavailable in this browser.',true);
}
$('speak').onclick=()=>act(speakFeedback);
async function gallery(){
 const jobs=await api('/api/gallery');$('gallery').replaceChildren();
 if(!jobs.length){const p=document.createElement('p');p.className='hint';p.textContent='Your saved captures will appear here.';$('gallery').append(p);}
 for(const job of jobs){const b=document.createElement('button'),im=document.createElement('img'),t=document.createElement('small');im.src=job.edits.at(-1)?.url||job.original;im.alt='Saved camera moment';t.textContent=new Date(job.created_at*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});b.append(im,t);b.onclick=()=>{if(!busy){showCapture(job);$('resultSection').scrollIntoView({behavior:'smooth'});}};$('gallery').append(b);}
}
document.addEventListener('keydown',e=>{if(e.code==='Space'&&!['INPUT','TEXTAREA','BUTTON'].includes(document.activeElement.tagName)){e.preventDefault();if(!$('capture').disabled)$('capture').click();}});
status();gallery().catch(e=>notice(e.message,true));setInterval(status,2000);setInterval(()=>preview().catch(()=>{}),250);
