import { workflow, node, trigger, newCredential } from '@n8n/workflow-sdk';

const googleDriveCredential = newCredential('Human Design google drive');

const notes = node({
  type: 'n8n-nodes-base.stickyNote',
  version: 1,
  config: {
    name: 'Setup notes',
    parameters: {
      content:
        'Required env vars: THD_API_KEY and GEMINI_API_KEY. Optional: THD_BASE_URL, GEMINI_MODEL. If the schedule node has no timezone field, 08:00 uses the n8n instance timezone; set the PikaPods/n8n timezone to Asia/Kuala_Lumpur. The video rework branch requires FFmpeg installed in the n8n runtime and Code node access to Node child_process, usually NODE_FUNCTION_ALLOW_BUILTIN=child_process.',
      width: 560,
      height: 240,
      color: 4,
    },
  },
});

const dailySchedule = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.4,
  config: {
    name: 'Daily 08:00 Script Run',
    parameters: {
      rule: {
        interval: [
          {
            field: 'days',
            triggerAtHour: 8,
            triggerAtMinute: 0,
          },
        ],
      },
    },
  },
  output: [{}],
});

const fetchTransit = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {
    name: 'Fetch THD Daily Transit',
    parameters: {
      method: 'GET',
      url: '={{($env.THD_BASE_URL || "https://api.totalhumandesign.com") + "/api/transits/all"}}',
      sendHeaders: true,
      headerParameters: {
        parameters: [
          {
            name: 'Authorization',
            value: '=Bearer {{$env.THD_API_KEY}}',
          },
        ],
      },
      sendQuery: true,
      queryParameters: {
        parameters: [
          {
            name: 'startDate',
            value: '={{$now.toFormat("yyyy-MM-dd")}}',
          },
          {
            name: 'endDate',
            value: '={{$now.toFormat("yyyy-MM-dd")}}',
          },
          {
            name: 'granularity',
            value: 'daily',
          },
          {
            name: 'format',
            value: 'full',
          },
        ],
      },
      options: {},
    },
  },
  output: [{}],
});

const buildScriptPrompt = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Build Viral Script Prompt',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const today = new Date().toISOString().slice(0, 10);
const rawTransit = items[0]?.json ?? {};

function findActivation(obj, planetName) {
  const lower = planetName.toLowerCase();
  const stack = [obj];
  while (stack.length) {
    const current = stack.shift();
    if (!current || typeof current !== 'object') continue;
    if (Array.isArray(current)) {
      stack.push(...current);
      continue;
    }
    const name = String(current.planet || current.name || current.body || current.key || '').toLowerCase();
    if (name.includes(lower)) {
      return {
        planet: current.planet || current.name || planetName,
        gate: current.gate || current.gateNumber || current.humanDesignGate || current.hexagram || current.line?.gate || null,
        line: current.line || current.lineNumber || null,
        sign: current.sign || current.zodiacSign || null,
        raw: current,
      };
    }
    for (const value of Object.values(current)) stack.push(value);
  }
  return { planet: planetName, gate: null, line: null, sign: null, raw: null };
}

const sun = findActivation(rawTransit, 'Sun');
const earth = findActivation(rawTransit, 'Earth');
const prompt = {
  system: "You are an expert short-form viral creator specializing in Human Design and astrology. Take today's transit data and write a 45-second high-retention script for a talking-head video.",
  instructions: [
    'Return strict JSON only with title, hook, script, captions, hashtags, and heygen_manual_steps.',
    'Pattern-Interrupt Hook, 0-3 seconds: never say "Today\\\\'s Human Design transit is..."; use a hook like "If you are feeling weirdly irritable or blocked today, do not make any big decisions. Here is why."',
    'Mechanism, 3-20 seconds: break down the Sun/Earth Gate activation in plain English.',
    'Practical Hack, 20-35 seconds: actionable advice for Manifestors, Generators, Projectors, and Reflectors.',
    'Call to Action, 35-45 seconds: "Check your chart to see if your throat center is open, and drop your type in the comments."',
    'Make it TikTok/Reels/Shorts ready, direct, emotional, and easy to caption.',
  ],
  date: today,
  transitSummary: { sun, earth },
  rawTransit,
};

return [{ json: { date: today, transit: rawTransit, sun, earth, prompt } }];
`,
    },
  },
  output: [{}],
});

const geminiScript = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {
    name: 'Generate Script with Gemini',
    parameters: {
      method: 'POST',
      url: '={{"https://generativelanguage.googleapis.com/v1beta/models/" + ($env.GEMINI_MODEL || "gemini-2.5-flash") + ":generateContent?key=" + $env.GEMINI_API_KEY}}',
      sendBody: true,
      specifyBody: 'json',
      jsonBody: `={{
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": $json.prompt.system + "\\n\\n" + JSON.stringify($json.prompt, null, 2)
        }
      ]
    }
  ],
  "generationConfig": {
    "temperature": 0.85,
    "responseMimeType": "application/json"
  }
}
}}`,
      options: {},
    },
  },
  output: [{}],
});

const buildScriptFile = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Build HeyGen Script File',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const today = new Date().toISOString().slice(0, 10);
const item = items[0] || { json: {} };
const source = item.json;

function extractText(response) {
  if (typeof response === 'string') return response;
  return response?.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('\\n') || '';
}

function parseJsonLoose(text) {
  if (!text) return null;
  try { return JSON.parse(text); } catch {}
  const match = text.match(/\\{[\\s\\S]*\\}/);
  if (!match) return null;
  try { return JSON.parse(match[0]); } catch {}
  return null;
}

const generated = parseJsonLoose(extractText(source)) || {};
const sun = source.sun || {};
const earth = source.earth || {};
const title = generated.title || \`Human Design Transit: Gate \${sun.gate || '?'} / Gate \${earth.gate || '?'}\`;
const hook = generated.hook || 'If your mood feels strangely loud today, pause before you make the decision.';
const script = generated.script || [
  hook,
  \`The Sun is highlighting Gate \${sun.gate || 'unknown'}, while Earth grounds the lesson through Gate \${earth.gate || 'unknown'}. Treat this as pressure to notice, not pressure to prove.\`,
  'Manifestors: inform before you move. Generators: wait for your body response. Projectors: do not chase the room. Reflectors: give the feeling space before you name it.',
  'Check your chart to see if your throat center is open, and drop your type in the comments.',
].join(' ');
const captions = Array.isArray(generated.captions) && generated.captions.length ? generated.captions : [
  { start: 0, end: 4, text: hook },
  { start: 4, end: 20, text: \`Sun Gate \${sun.gate || '?'} / Earth Gate \${earth.gate || '?'}: notice the pattern before reacting.\` },
  { start: 20, end: 35, text: 'Use your strategy today, not panic.' },
  { start: 35, end: 45, text: 'Check your throat center and drop your type.' },
];
const payload = {
  date: today,
  title,
  hook,
  script,
  captions,
  hashtags: generated.hashtags || ['#humandesign', '#humandesigntransit', '#astrology', '#spiritualtiktok'],
  heygen_manual_steps: generated.heygen_manual_steps || [
    'Open HeyGen Creator manually.',
    'Create a 9:16 talking-head avatar video.',
    'Paste the script exactly.',
    'Export the finished MP4.',
    'Upload the MP4 to Google Drive, then submit its file ID to the rework form.',
  ],
  transit: source.transit || source,
  sun,
  earth,
};
const json = JSON.stringify(payload, null, 2);
return [{
  json: payload,
  binary: {
    data: {
      data: Buffer.from(json, 'utf8').toString('base64'),
      mimeType: 'application/json',
      fileName: \`THD-viral-script-\${today}.json\`,
    },
  },
}];
`,
    },
  },
  output: [{}],
});

const uploadScript = node({
  type: 'n8n-nodes-base.googleDrive',
  version: 3,
  config: {
    name: 'Upload Script Brief to Google Drive',
    parameters: {
      resource: 'file',
      operation: 'upload',
      inputDataFieldName: 'data',
      name: '={{$binary.data.fileName}}',
    },
    credentials: {
      googleDriveOAuth2Api: googleDriveCredential,
    },
  },
  output: [{}],
});

const videoForm = trigger({
  type: 'n8n-nodes-base.formTrigger',
  version: 2.6,
  config: {
    name: 'Submit Finished HeyGen Video for Viral Rework',
    parameters: {
      formTitle: 'Submit Finished HeyGen Video for Viral Rework',
      formFields: {
        values: [
          { fieldLabel: 'googleDriveFileId', fieldType: 'text', requiredField: true },
          { fieldLabel: 'originalScript', fieldType: 'textarea' },
          { fieldLabel: 'audience', fieldType: 'text' },
          { fieldLabel: 'feedbackNotes', fieldType: 'textarea' },
          { fieldLabel: 'durationSeconds', fieldType: 'number' },
        ],
      },
      responseMode: 'lastNode',
      options: {},
    },
  },
  output: [{}],
});

const downloadVideo = node({
  type: 'n8n-nodes-base.googleDrive',
  version: 3,
  config: {
    name: 'Download Uploaded HeyGen Video',
    parameters: {
      resource: 'file',
      operation: 'download',
      fileId: {
        __rl: true,
        mode: 'id',
        value: '={{$json.googleDriveFileId}}',
      },
      options: {
        binaryPropertyName: 'data',
      },
    },
    credentials: {
      googleDriveOAuth2Api: googleDriveCredential,
    },
  },
  output: [{}],
});

const writeOriginalVideo = node({
  type: 'n8n-nodes-base.readWriteFile',
  version: 1.1,
  config: {
    name: 'Write Original Video to /tmp',
    parameters: {
      operation: 'write',
      fileName: '=/tmp/heygen-{{$execution.id}}.mp4',
      dataPropertyName: 'data',
    },
  },
  output: [{}],
});

const ffmpegPreflight = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'FFmpeg Preflight',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const { execFileSync } = require('child_process');
const stdout = execFileSync('ffmpeg', ['-version'], { encoding: 'utf8' });
return [{ json: { stdout } }];
`,
    },
  },
  output: [{}],
});

const extractPreview = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Extract Video Preview Frames',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const { spawnSync } = require('child_process');
const fs = require('fs');
const id = $execution.id;
const input = \`/tmp/heygen-\${id}.mp4\`;
const result = spawnSync('ffmpeg', ['-y', '-i', input, '-vf', 'fps=1/12,scale=360:-1', '-frames:v', '4', \`/tmp/heygen-frame-\${id}-%02d.jpg\`], { encoding: 'utf8' });
const info = [result.stdout || '', result.stderr || ''].join('\\n').slice(-4000);
fs.writeFileSync(\`/tmp/heygen-info-\${id}.txt\`, info);
return [{ json: { stdout: info } }];
`,
    },
  },
  output: [{}],
});

const geminiAudit = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {
    name: 'Generate Virality Audit and Edit Plan',
    parameters: {
      method: 'POST',
      url: '={{"https://generativelanguage.googleapis.com/v1beta/models/" + ($env.GEMINI_MODEL || "gemini-2.5-flash") + ":generateContent?key=" + $env.GEMINI_API_KEY}}',
      sendBody: true,
      specifyBody: 'json',
      jsonBody: `={{
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "You are a short-form video retention editor. Return strict JSON only. Audit this Human Design HeyGen talking-head video from the provided script, creator notes, and FFmpeg preview/info text. Then create an edit plan that can be executed with FFmpeg for a 9:16 viral version. Schema: { viralityScore:number, diagnosis:string[], hookText:string, ctaText:string, captions:[{start:number,end:number,text:string}], emphasisBeats:string[], visualStyle:string, musicMood:string }. Original script: " + ($json.originalScript || "") + "\\nAudience: " + ($json.audience || "Human Design / astrology beginners") + "\\nFeedback notes: " + ($json.feedbackNotes || "") + "\\nFFmpeg preview/info: " + ($json.stdout || "")
        }
      ]
    }
  ],
  "generationConfig": {
    "temperature": 0.75,
    "responseMimeType": "application/json"
  }
}
}}`,
      options: {},
    },
  },
  output: [{}],
});

const buildEditPlan = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Create FFmpeg Edit Plan',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const response = items[0]?.json ?? {};
const now = new Date().toISOString().slice(0, 10);
const id = $execution.id;

function textFromGemini(value) {
  if (typeof value === 'string') return value;
  return value?.candidates?.[0]?.content?.parts?.map((p) => p.text || '').join('\\n') || '';
}
function parseJsonLoose(text) {
  try { return JSON.parse(text); } catch {}
  const match = String(text || '').match(/\\{[\\s\\S]*\\}/);
  if (!match) return null;
  try { return JSON.parse(match[0]); } catch {}
  return null;
}
function cleanText(text, fallback) {
  return String(text || fallback || '').replace(/[\\r\\n]+/g, ' ').replace(/[{}\\\\]/g, '').slice(0, 110);
}
function assTime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = Math.floor(total % 60);
  const cs = Math.floor((total - Math.floor(total)) * 100);
  return \`\${h}:\${String(m).padStart(2, '0')}:\${String(s).padStart(2, '0')}.\${String(cs).padStart(2, '0')}\`;
}
function assEscape(text) {
  return cleanText(text, '').replace(/,/g, ';');
}
function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\\\''") + "'";
}

const parsed = parseJsonLoose(textFromGemini(response)) || {};
const duration = Math.max(10, Number($json.durationSeconds || parsed.durationSeconds || 45));
const plan = {
  date: now,
  viralityScore: Number(parsed.viralityScore || 72),
  diagnosis: Array.isArray(parsed.diagnosis) ? parsed.diagnosis : ['Hook needs faster tension and larger captions.', 'Add pattern interrupts, progress bar, and clearer CTA.'],
  hookText: cleanText(parsed.hookText, 'If today feels emotionally loud, pause before you decide.'),
  ctaText: cleanText(parsed.ctaText, 'Check your throat center and drop your type.'),
  captions: Array.isArray(parsed.captions) && parsed.captions.length ? parsed.captions : [
    { start: 0, end: 4, text: cleanText(parsed.hookText, 'If today feels emotionally loud, pause before you decide.') },
    { start: 4, end: 14, text: 'The transit is showing you the pressure pattern underneath the mood.' },
    { start: 14, end: 25, text: 'Manifestors inform. Generators wait for response.' },
    { start: 25, end: 35, text: 'Projectors wait for recognition. Reflectors give it time.' },
    { start: 35, end: duration, text: cleanText(parsed.ctaText, 'Check your throat center and drop your type.') },
  ],
  emphasisBeats: Array.isArray(parsed.emphasisBeats) ? parsed.emphasisBeats : ['0-3s pattern interrupt', '20-35s type-specific hack', '35-45s comment CTA'],
  visualStyle: parsed.visualStyle || 'clean kinetic captions, strong contrast, quick retention overlays',
  musicMood: parsed.musicMood || 'soft energetic pulse under voice',
};

const assLines = [
  '[Script Info]',
  'ScriptType: v4.00+',
  'PlayResX: 1080',
  'PlayResY: 1920',
  '',
  '[V4+ Styles]',
  'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
  'Style: Hook,Arial,70,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,8,60,60,160,1',
  'Style: Caption,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,2,70,70,250,1',
  'Style: CTA,Arial,58,&H0000FFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,2,70,70,170,1',
  '',
  '[Events]',
  'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
  \`Dialogue: 0,\${assTime(0)},\${assTime(Math.min(4, duration))},Hook,,0,0,0,,\${assEscape(plan.hookText)}\`,
  ...plan.captions.map((c) => \`Dialogue: 0,\${assTime(c.start)},\${assTime(c.end)},Caption,,0,0,0,,\${assEscape(c.text)}\`),
  \`Dialogue: 0,\${assTime(Math.min(35, duration - 1))},\${assTime(duration)},CTA,,0,0,0,,\${assEscape(plan.ctaText)}\`,
].join('\\n');

const inputPath = \`/tmp/heygen-\${id}.mp4\`;
const assPath = \`/tmp/captions-\${id}.ass\`;
const outputPath = \`/tmp/improved-\${id}.mp4\`;
const filter = \`scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles=\${shellQuote(assPath)},drawbox=x=0:y=1908:w='min(1080,1080*t/\${duration})':h=12:color=cyan@0.85:t=fill\`;
const ffmpegArgs = [
  '-y',
  '-i', inputPath,
  '-vf', filter,
  '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
  '-c:v', 'libx264',
  '-preset', 'veryfast',
  '-crf', '20',
  '-c:a', 'aac',
  '-b:a', '192k',
  '-movflags', '+faststart',
  outputPath,
];

return [{
  json: {
    ...plan,
    durationSeconds: duration,
    inputPath,
    assPath,
    outputPath,
    ffmpegArgs,
  },
  binary: {
    ass: {
      data: Buffer.from(assLines, 'utf8').toString('base64'),
      mimeType: 'text/plain',
      fileName: \`captions-\${id}.ass\`,
    },
    plan: {
      data: Buffer.from(JSON.stringify({ ...plan, inputPath, assPath, outputPath }, null, 2), 'utf8').toString('base64'),
      mimeType: 'application/json',
      fileName: \`viral-edit-plan-\${now}-\${id}.json\`,
    },
  },
}];
`,
    },
  },
  output: [{}],
});

const uploadEditPlan = node({
  type: 'n8n-nodes-base.googleDrive',
  version: 3,
  config: {
    name: 'Upload Audit Edit Plan to Google Drive',
    parameters: {
      resource: 'file',
      operation: 'upload',
      inputDataFieldName: 'plan',
      name: '={{$binary.plan.fileName}}',
    },
    credentials: {
      googleDriveOAuth2Api: googleDriveCredential,
    },
  },
  output: [{}],
});

const writeAss = node({
  type: 'n8n-nodes-base.readWriteFile',
  version: 1.1,
  config: {
    name: 'Write ASS Captions to /tmp',
    parameters: {
      operation: 'write',
      fileName: '={{$json.assPath}}',
      dataPropertyName: 'ass',
    },
  },
  output: [{}],
});

const renderImprovedVideo = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Render Improved Video with FFmpeg',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `
const { execFileSync } = require('child_process');
const args = $node['Create FFmpeg Edit Plan'].json.ffmpegArgs;
const stdout = execFileSync('ffmpeg', args, { encoding: 'utf8', maxBuffer: 1024 * 1024 * 20 });
return [{ json: { stdout, outputPath: $node['Create FFmpeg Edit Plan'].json.outputPath } }];
`,
    },
  },
  output: [{}],
});

const readImprovedVideo = node({
  type: 'n8n-nodes-base.readWriteFile',
  version: 1.1,
  config: {
    name: 'Read Improved MP4 from /tmp',
    parameters: {
      operation: 'read',
      fileSelector: '=/tmp/improved-{{$execution.id}}.mp4',
      dataPropertyName: 'data',
    },
  },
  output: [{}],
});

const uploadImprovedVideo = node({
  type: 'n8n-nodes-base.googleDrive',
  version: 3,
  config: {
    name: 'Upload Improved Video to Google Drive',
    parameters: {
      resource: 'file',
      operation: 'upload',
      inputDataFieldName: 'data',
      name: '=improved-heygen-{{$execution.id}}.mp4',
    },
    credentials: {
      googleDriveOAuth2Api: googleDriveCredential,
    },
  },
  output: [{}],
});

export default workflow('thd-daily-viral-script-heygen-video-rework-agent', 'THD Daily Viral Script + HeyGen Video Rework Agent')
  .add(notes)
  .add(dailySchedule)
  .to(fetchTransit)
  .to(buildScriptPrompt)
  .to(geminiScript)
  .to(buildScriptFile)
  .to(uploadScript)
  .add(videoForm)
  .to(downloadVideo)
  .to(writeOriginalVideo)
  .to(ffmpegPreflight)
  .to(extractPreview)
  .to(geminiAudit)
  .to(buildEditPlan)
  .to(writeAss)
  .to(renderImprovedVideo)
  .to(readImprovedVideo)
  .to(uploadImprovedVideo)
  .add(buildEditPlan)
  .to(uploadEditPlan);
