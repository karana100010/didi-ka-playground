import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const chat = readFileSync(new URL('../web/chat.js', import.meta.url), 'utf8');
const voice = chat.slice(chat.indexOf("// One tap starts"), chat.indexOf('// --- operator panel'));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise, resolve, reject}; };
const tick = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function harness() {
    const recordings = [], timers = new Map(), sent = [], consents = [];
    let click, id=0;
    const state = {session:{}, didi:{language:'hi'}, t:{}, mic:'idle', voiceActive:false, voiceEpoch:0, speechEpoch:0};
    const mic = {addEventListener:(_,f)=>click=f, setAttribute(){}, classList:{toggle(){}}};
    const bhashini = {
        async startRecording() { const d=deferred(); const r={done:d.promise, cancel:()=>{r.cancelled=true; d.resolve(null);}, finish:d.resolve}; recordings.push(r); return r; },
        async transcribe() { return 'question'; }, async speak(){}, stop(){}
    };
    const context=vm.createContext({state,bhashini,els:{mic,'mic-label':{},text:{},'text-form':{querySelector:()=>({})}},
        setStatus(){}, traceAdd(){}, toast(){}, window:{addEventListener(){}}, document:{addEventListener(){}},
        setTimeout:f=>{timers.set(++id,f);return id;}, clearTimeout:i=>timers.delete(i),
        send:async text=>{sent.push(text);}, answerInWords:async text=>{consents.push(text);}});
    vm.runInContext(voice,context);
    return {state,bhashini,recordings,sent,consents,click:()=>click(),run:code=>vm.runInContext(code,context),
        flush:async()=>{const fs=[...timers.values()];timers.clear();for(const f of fs) f();await tick();},tick};
}
test('one tap captures successive utterances without another click', async()=>{
    const h=harness(); h.click(); await tick();
    h.recordings[0].finish('wav'); await tick(); assert.deepEqual(h.sent,['question']);
    await h.flush(); assert.equal(h.recordings.length,2); assert.equal(h.state.mic,'listening');
});
test('speech cancels capture, then resumes after playback',async()=>{
    const h=harness();h.click();await tick(); const playback=deferred(); h.bhashini.speak=()=>playback.promise;
    const speaking=h.run("speak('reply')");await tick();assert.equal(h.recordings[0].cancelled,true);
    await h.flush();assert.equal(h.recordings.length,1);assert.equal(h.state.mic,'speaking');
    playback.resolve();await speaking;await h.flush();assert.equal(h.recordings.length,2);assert.equal(h.sent.length,0);
});
test('stop while ASR is pending discards its late transcript',async()=>{
    const h=harness(), asr=deferred();h.bhashini.transcribe=()=>asr.promise;
    h.click();await tick();h.recordings[0].finish('wav');await tick();h.click();asr.resolve('late');await tick();await h.flush();
    assert.equal(h.sent.length,0);assert.equal(h.state.voiceActive,false);assert.equal(h.recordings.length,1);
});
test('silent recording rearms without sending audio for transcription',async()=>{
    const h=harness();let calls=0;h.bhashini.transcribe=async()=>{calls++;return '';};
    h.click();await tick();h.recordings[0].finish(null);await tick();await h.flush();
    assert.equal(calls,0);assert.equal(h.recordings.length,2);
});
test('pending consent receives voice words even while the turn is busy',async()=>{
    const h=harness();h.state.pending={};h.state.busy=true;h.click();await tick();h.recordings[0].finish('wav');await tick();
    assert.deepEqual(h.consents,['question']);assert.equal(h.sent.length,0);
});
test('late microphone permission result is closed after stop',async()=>{
    const h=harness(), permission=deferred(), d=deferred();let cancelled=false;
    h.bhashini.startRecording=()=>permission.promise;h.click();h.click();
    permission.resolve({done:d.promise,cancel(){cancelled=true;}});await tick();
    assert.equal(cancelled,true);assert.equal(h.state.mic,'idle');
});
test('microphone failure ends the session without a retry loop',async()=>{
    const h=harness();h.bhashini.startRecording=async()=>{throw new Error('denied');};h.click();await tick();await h.flush();
    assert.equal(h.state.voiceActive,false);assert.equal(h.state.mic,'idle');
});
const audioSource = readFileSync(new URL('../web/bhashini.js',import.meta.url),'utf8');
const {createBhashini} = await import(`data:text/javascript;base64,${Buffer.from(audioSource).toString('base64')}`);
test('cancelled synthesis never starts late audio playback', async()=>{
    const synthesis=deferred();let plays=0;
    const oldFetch=globalThis.fetch, oldAudio=globalThis.Audio;
    globalThis.fetch=()=>synthesis.promise;
    globalThis.Audio=class {play(){plays++;return Promise.resolve();}};
    try { const b=createBhashini({workletUrl:"http://localhost/pcm-worklet.js"});const p=b.speak('reply','hi');b.stop();
        synthesis.resolve({ok:true,json:async()=>({segments:[{mime_type:'audio/wav',audio_content:'x'}]})});
        await p;assert.equal(plays,0);
    } finally {globalThis.fetch=oldFetch;globalThis.Audio=oldAudio;}
});
test('listening waits until the model has finished its turn',async()=>{
    const h=harness();h.state.busy=true;h.click();await h.flush();assert.equal(h.recordings.length,0);
    h.state.busy=false;h.run('resumeListening()');await h.flush();assert.equal(h.recordings.length,1);
});
test('an older reply finishing cannot resume capture over a newer reply',async()=>{
    const h=harness(), first=deferred(), second=deferred();h.state.voiceActive=true;
    let n=0;h.bhashini.speak=()=>++n===1?first.promise:second.promise;
    const a=h.run("speak('first')"), b=h.run("speak('second')");
    first.resolve();await a;await h.flush();assert.equal(h.recordings.length,0);assert.equal(h.state.mic,'speaking');
    second.resolve();await b;await h.flush();assert.equal(h.recordings.length,1);
});
