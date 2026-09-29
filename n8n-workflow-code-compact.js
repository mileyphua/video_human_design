import { workflow, node, trigger, newCredential } from '@n8n/workflow-sdk';

const drive = newCredential('Human Design google drive');

function n(type, version, name, parameters, more) {
  const config = { name: name, parameters: parameters };
  if (more && more.credentials) config.credentials = more.credentials;
  return node({ type: type, version: version, config: config, output: [{}] });
}

function t(type, version, name, parameters) {
  return trigger({
    type: type,
    version: version,
    config: { name: name, parameters: parameters },
    output: [{}],
  });
}

const notes = n('n8n-nodes-base.stickyNote', 1, 'Setup notes', {
  content: 'Env required: THD_API_KEY, GEMINI_API_KEY. Optional: THD_BASE_URL, GEMINI_MODEL. Set n8n/PikaPods timezone to Asia/Kuala_Lumpur for the 08:00 daily run. Video rework requires FFmpeg installed in the n8n runtime.',
  width: 560,
  height: 220,
  color: 4,
});

const daily = t('n8n-nodes-base.scheduleTrigger', 1.4, 'Daily 08:00 Script Run', {
  rule: { interval: [{ field: 'days', triggerAtHour: 8, triggerAtMinute: 0 }] },
});

const thd = n('n8n-nodes-base.httpRequest', 4.5, 'Fetch THD Daily Transit', {
  method: 'GET',
  url: '={{($env.THD_BASE_URL || "https://api.totalhumandesign.com") + "/api/transits/all"}}',
  sendHeaders: true,
  headerParameters: { parameters: [{ name: 'Authorization', value: '=Bearer {{$env.THD_API_KEY}}' }] },
  sendQuery: true,
  queryParameters: {
    parameters: [
      { name: 'startDate', value: '={{$now.toFormat("yyyy-MM-dd")}}' },
      { name: 'endDate', value: '={{$now.toFormat("yyyy-MM-dd")}}' },
      { name: 'granularity', value: 'daily' },
      { name: 'format', value: 'full' },
    ],
  },
});

const scriptPrompt = n('n8n-nodes-base.code', 2, 'Build Viral Script Prompt', {
  mode: 'runOnceForAllItems',
  jsCode: `
const transit = items[0].json;
const text = [
  "System Prompt: You are an expert short-form viral creator specializing in Human Design and astrology. Take today's transit data and write a 45-second high-retention script for a talking-head video.",
  "Return JSON only: title, hook, script, captions, hashtags, heygen_manual_steps.",
  "Formula:",
  "1. Pattern-Interrupt Hook 0-3s. Never say: Today's Human Design transit is. Use a hook like: If you're feeling weirdly irritable or blocked today, do not make any big decisions. Here's why.",
  "2. Mechanism 3-20s. Explain Sun/Earth Gate activation in plain English.",
  "3. Practical Hack 20-35s. Advice for Manifestors, Generators, Projectors, Reflectors.",
  "4. CTA 35-45s. End with: Check your chart to see if your throat center is open, and drop your type in the comments.",
  "Transit JSON:", JSON.stringify(transit)
].join("\\n");
return [{ json: { date: new Date().toISOString().slice(0,10), transit, prompt: text } }];
`,
});

const geminiScript = n('n8n-nodes-base.httpRequest', 4.5, 'Generate Script with Gemini', {
  method: 'POST',
  url: '={{"https://generativelanguage.googleapis.com/v1beta/models/" + ($env.GEMINI_MODEL || "gemini-2.5-flash") + ":generateContent?key=" + $env.GEMINI_API_KEY}}',
  sendBody: true,
  specifyBody: 'json',
  jsonBody: '={{ { contents: [{ role: "user", parts: [{ text: $json.prompt }] }], generationConfig: { temperature: 0.85, responseMimeType: "application/json" } } }}',
});

const scriptFile = n('n8n-nodes-base.code', 2, 'Build HeyGen Script File', {
  mode: 'runOnceForAllItems',
  jsCode: `
function txt(r){return r?.candidates?.[0]?.content?.parts?.map(p=>p.text||"").join("\\n")||""}
function parse(s){try{return JSON.parse(s)}catch{} const m=String(s).match(/\\{[\\s\\S]*\\}/); if(!m)return null; try{return JSON.parse(m[0])}catch{return null}}
const date = new Date().toISOString().slice(0,10);
const ai = parse(txt(items[0].json)) || {};
const payload = {
  date,
  title: ai.title || "Daily Human Design Transit Script",
  hook: ai.hook || "If today feels weirdly tense, pause before you make a big decision.",
  script: ai.script || "If today feels weirdly tense, pause before you make a big decision. The Sun and Earth activations are showing a pressure pattern, not a command. Manifestors: inform first. Generators: wait for response. Projectors: wait for recognition. Reflectors: give it time. Check your chart to see if your throat center is open, and drop your type in the comments.",
  captions: ai.captions || [],
  hashtags: ai.hashtags || ["#humandesign","#humandesigntransit","#astrology"],
  heygen_manual_steps: ai.heygen_manual_steps || ["Open HeyGen Creator manually.", "Create a 9:16 talking-head avatar video.", "Paste this script.", "Export MP4.", "Upload MP4 to Google Drive.", "Submit the file ID to the rework form."]
};
return [{ json: payload, binary: { data: { data: Buffer.from(JSON.stringify(payload,null,2)).toString("base64"), mimeType: "application/json", fileName: "THD-viral-script-"+date+".json" } } }];
`,
});

const uploadScript = n('n8n-nodes-base.googleDrive', 3, 'Upload Script Brief to Google Drive', {
  resource: 'file',
  operation: 'upload',
  inputDataFieldName: 'data',
  name: '={{$binary.data.fileName}}',
}, { credentials: { googleDriveOAuth2Api: drive } });

const form = t('n8n-nodes-base.formTrigger', 2.6, 'Submit Finished HeyGen Video for Viral Rework', {
  formTitle: 'Submit Finished HeyGen Video for Viral Rework',
  formFields: { values: [
    { fieldLabel: 'googleDriveFileId', fieldType: 'text', requiredField: true },
    { fieldLabel: 'originalScript', fieldType: 'textarea' },
    { fieldLabel: 'audience', fieldType: 'text' },
    { fieldLabel: 'feedbackNotes', fieldType: 'textarea' },
    { fieldLabel: 'durationSeconds', fieldType: 'number' },
  ] },
  responseMode: 'lastNode',
});

const download = n('n8n-nodes-base.googleDrive', 3, 'Download Uploaded HeyGen Video', {
  resource: 'file',
  operation: 'download',
  fileId: { __rl: true, mode: 'id', value: '={{$json.googleDriveFileId}}' },
  options: { binaryPropertyName: 'data' },
}, { credentials: { googleDriveOAuth2Api: drive } });

const writeVideo = n('n8n-nodes-base.readWriteFile', 1.1, 'Write Original Video to tmp', {
  operation: 'write',
  fileName: '=/tmp/heygen-{{$execution.id}}.mp4',
  dataPropertyName: 'data',
});

const ffmpegCheck = n('n8n-nodes-base.executeCommand', 1, 'FFmpeg Preflight', {
  command: 'ffmpeg -version',
  executeOnce: true,
});

const preview = n('n8n-nodes-base.executeCommand', 1, 'Extract Video Preview Info', {
  command: '=ffmpeg -y -i /tmp/heygen-{{$execution.id}}.mp4 -vf "fps=1/12,scale=360:-1" -frames:v 4 /tmp/heygen-frame-{{$execution.id}}-%02d.jpg 2> /tmp/heygen-info-{{$execution.id}}.txt; tail -40 /tmp/heygen-info-{{$execution.id}}.txt || true',
  executeOnce: true,
});

const audit = n('n8n-nodes-base.httpRequest', 4.5, 'Generate Virality Audit and Edit Plan', {
  method: 'POST',
  url: '={{"https://generativelanguage.googleapis.com/v1beta/models/" + ($env.GEMINI_MODEL || "gemini-2.5-flash") + ":generateContent?key=" + $env.GEMINI_API_KEY}}',
  sendBody: true,
  specifyBody: 'json',
  jsonBody: '={{ { contents: [{ role: "user", parts: [{ text: "You are a viral short-form video editor. Return strict JSON: {viralityScore,diagnosis,hookText,ctaText,captions:[{start,end,text}],emphasisBeats,visualStyle,musicMood}. Create an FFmpeg-ready edit plan for a Human Design HeyGen video. Original script: " + ($node["Submit Finished HeyGen Video for Viral Rework"].json.originalScript || "") + "\\nAudience: " + ($node["Submit Finished HeyGen Video for Viral Rework"].json.audience || "Human Design beginners") + "\\nFeedback: " + ($node["Submit Finished HeyGen Video for Viral Rework"].json.feedbackNotes || "") + "\\nFFmpeg info: " + ($json.stdout || "") }] }], generationConfig: { temperature: 0.75, responseMimeType: "application/json" } } }}',
});

const plan = n('n8n-nodes-base.code', 2, 'Create FFmpeg Edit Plan', {
  mode: 'runOnceForAllItems',
  jsCode: `
function txt(r){return r?.candidates?.[0]?.content?.parts?.map(p=>p.text||"").join("\\n")||""}
function parse(s){try{return JSON.parse(s)}catch{} const m=String(s).match(/\\{[\\s\\S]*\\}/); if(!m)return {}; try{return JSON.parse(m[0])}catch{return {}}}
function q(s){return "'" + String(s).replace(/'/g,"'\\\\''") + "'"}
function clean(s,f){return String(s||f||"").replace(/[\\r\\n{}\\\\]/g," ").slice(0,100)}
function ts(x){x=Math.max(0,Number(x)||0); const h=Math.floor(x/3600),m=Math.floor(x%3600/60),s=Math.floor(x%60),c=Math.floor((x-Math.floor(x))*100); return h+":"+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0")+"."+String(c).padStart(2,"0")}
const id=$execution.id, date=new Date().toISOString().slice(0,10), d=Math.max(10,Number($node["Submit Finished HeyGen Video for Viral Rework"].json.durationSeconds||45));
const ai=parse(txt(items[0].json));
const p={date,viralityScore:Number(ai.viralityScore||72),diagnosis:ai.diagnosis||["Strengthen the first 3 seconds.","Add larger captions and a clear comment CTA."],hookText:clean(ai.hookText,"If today feels emotionally loud, pause before you decide."),ctaText:clean(ai.ctaText,"Check your throat center and drop your type."),captions:Array.isArray(ai.captions)&&ai.captions.length?ai.captions:[{start:0,end:4,text:"Pause before you decide."},{start:4,end:20,text:"The transit is showing the pressure underneath the mood."},{start:20,end:35,text:"Use your strategy: inform, respond, wait, observe."},{start:35,end:d,text:"Check your throat center and drop your type."}],emphasisBeats:ai.emphasisBeats||["0-3s hook","20-35s type hack","35-45s CTA"],visualStyle:ai.visualStyle||"bold burned captions, progress bar, vertical crop",musicMood:ai.musicMood||"soft energetic pulse"};
const ass=["[Script Info]","ScriptType: v4.00+","PlayResX: 1080","PlayResY: 1920","","[V4+ Styles]","Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding","Style: Hook,Arial,70,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,8,60,60,160,1","Style: Cap,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,2,70,70,250,1","Style: CTA,Arial,58,&H0000FFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,2,70,70,170,1","","[Events]","Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text","Dialogue: 0,"+ts(0)+","+ts(Math.min(4,d))+",Hook,,0,0,0,,"+p.hookText.replace(/,/g,";"),...p.captions.map(c=>"Dialogue: 0,"+ts(c.start)+","+ts(c.end)+",Cap,,0,0,0,,"+clean(c.text).replace(/,/g,";")),"Dialogue: 0,"+ts(Math.min(35,d-1))+","+ts(d)+",CTA,,0,0,0,,"+p.ctaText.replace(/,/g,";")].join("\\n");
const input="/tmp/heygen-"+id+".mp4", assPath="/tmp/captions-"+id+".ass", output="/tmp/improved-"+id+".mp4";
const vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles="+q(assPath)+",drawbox=x=0:y=1908:w='min(1080,1080*t/"+d+")':h=12:color=cyan@0.85:t=fill";
const ffmpegCommand="ffmpeg -y -i "+q(input)+" -vf "+q(vf)+" -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart "+q(output);
return [{json:{...p,durationSeconds:d,input,assPath,output,ffmpegCommand},binary:{ass:{data:Buffer.from(ass).toString("base64"),mimeType:"text/plain",fileName:"captions-"+id+".ass"},plan:{data:Buffer.from(JSON.stringify(p,null,2)).toString("base64"),mimeType:"application/json",fileName:"viral-edit-plan-"+date+"-"+id+".json"}}}];
`,
});

const uploadPlan = n('n8n-nodes-base.googleDrive', 3, 'Upload Audit Edit Plan to Google Drive', {
  resource: 'file',
  operation: 'upload',
  inputDataFieldName: 'plan',
  name: '={{$binary.plan.fileName}}',
}, { credentials: { googleDriveOAuth2Api: drive } });

const writeAss = n('n8n-nodes-base.readWriteFile', 1.1, 'Write ASS Captions to tmp', {
  operation: 'write',
  fileName: '={{$node["Create FFmpeg Edit Plan"].json.assPath}}',
  dataPropertyName: 'ass',
});

const render = n('n8n-nodes-base.executeCommand', 1, 'Render Improved Video with FFmpeg', {
  command: '={{$node["Create FFmpeg Edit Plan"].json.ffmpegCommand}}',
  executeOnce: true,
});

const readMp4 = n('n8n-nodes-base.readWriteFile', 1.1, 'Read Improved MP4 from tmp', {
  operation: 'read',
  fileSelector: '=/tmp/improved-{{$execution.id}}.mp4',
  dataPropertyName: 'data',
});

const uploadMp4 = n('n8n-nodes-base.googleDrive', 3, 'Upload Improved Video to Google Drive', {
  resource: 'file',
  operation: 'upload',
  inputDataFieldName: 'data',
  name: '=improved-heygen-{{$execution.id}}.mp4',
}, { credentials: { googleDriveOAuth2Api: drive } });

export default workflow('thd-daily-viral-script-heygen-video-rework-agent', 'THD Daily Viral Script + HeyGen Video Rework Agent')
  .add(notes)
  .add(daily).to(thd).to(scriptPrompt).to(geminiScript).to(scriptFile).to(uploadScript)
  .add(form).to(download).to(writeVideo).to(ffmpegCheck).to(preview).to(audit).to(plan).to(writeAss).to(render).to(readMp4).to(uploadMp4)
  .add(plan).to(uploadPlan);
