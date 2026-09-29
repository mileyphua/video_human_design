import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const args = parseArgs(process.argv.slice(2));

if (!args.input || !args.feedback || !args.output) {
  console.error(`Usage:
  npm run rework-from-feedback -- --input heygen.mp4 --feedback viral-audit.json --output improved.mp4

Required:
  --input       Original HeyGen video
  --feedback    Gemini virality audit JSON
  --output      Improved MP4 path

Optional:
  --plan-output Save the generated FFmpeg edit plan JSON
  --duration    Video duration in seconds if ffprobe is unavailable, default 45
`);
  process.exit(1);
}

const inputPath = resolve(args.input);
const feedbackPath = resolve(args.feedback);
const outputPath = resolve(args.output);
const planOutputPath = resolve(args.planOutput || args['plan-output'] || `${stripMp4(outputPath)}.edit-plan.json`);
const duration = Number.parseFloat(args.duration || '45');

const feedback = await readJson(feedbackPath);
const plan = buildEditPlan(feedback, duration);

await writeFile(planOutputPath, `${JSON.stringify(plan, null, 2)}\n`, 'utf8');

await run(process.execPath, [
  'scripts/improve-video.mjs',
  '--input',
  inputPath,
  '--plan',
  planOutputPath,
  '--output',
  outputPath,
  '--duration',
  String(duration)
]);

console.log(JSON.stringify({
  ok: true,
  input: inputPath,
  feedback: feedbackPath,
  generatedPlan: planOutputPath,
  output: outputPath,
  viralScore: feedback.viralScore ?? null,
  title: plan.title,
  hashtags: plan.hashtags
}, null, 2));

function parseArgs(argv) {
  const parsed = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith('--')) continue;
    parsed[key.slice(2)] = argv[i + 1];
    i += 1;
  }
  return parsed;
}

async function readJson(path) {
  const raw = await readFile(path, 'utf8');
  return JSON.parse(raw);
}

function buildEditPlan(feedback, duration) {
  const replacementHook = firstText(
    feedback.replacementHook,
    feedback.editPlan?.topHookText,
    'Feeling weirdly blocked today?'
  );

  const bottomCta = inferCta(feedback);
  const title = firstText(
    feedback.title,
    'Your Human Design transit warning for today'
  );

  const captionOverlays = normalizeCaptionOverlays(feedback, replacementHook, bottomCta, duration);

  return {
    title,
    fileName: safeFileName(feedback.fileName || title),
    topHookText: replacementHook,
    bottomCtaText: bottomCta,
    captionOverlays,
    style: {
      captionPrimaryColor: 'yellow',
      captionSecondaryColor: 'white',
      backgroundBlur: true,
      progressBar: true,
      safeArea: true
    },
    hashtags: normalizeHashtags(feedback.hashtags)
  };
}

function normalizeCaptionOverlays(feedback, hook, cta, duration) {
  const auditOverlays = Array.isArray(feedback.captionOverlays)
    ? feedback.captionOverlays
    : [];

  const normalized = auditOverlays
    .map((overlay) => ({
      start: cleanTimestamp(overlay.start),
      end: cleanTimestamp(overlay.end),
      text: cleanCaption(overlay.text)
    }))
    .filter((overlay) => overlay.start && overlay.end && overlay.text);

  if (normalized.length > 0) {
    return ensureHookAndCta(normalized, hook, cta, duration);
  }

  const midText = firstText(
    feedback.retentionDiagnosis,
    feedback.audiencePromise,
    feedback.biggestReasonItMayFlop,
    'Pause before forcing the next decision.'
  );

  return [
    {
      start: '00:00:00.000',
      end: '00:00:03.000',
      text: cleanCaption(hook)
    },
    {
      start: '00:00:03.000',
      end: '00:00:09.000',
      text: cleanCaption(midText)
    },
    {
      start: '00:00:09.000',
      end: '00:00:18.000',
      text: 'Your energy may be asking for timing, not pressure.'
    },
    {
      start: '00:00:18.000',
      end: '00:00:32.000',
      text: 'Use your type before you react.'
    },
    {
      start: secondsToTimestamp(Math.max(0, Math.min(duration - 8, 35))),
      end: secondsToTimestamp(duration),
      text: cleanCaption(cta)
    }
  ];
}

function ensureHookAndCta(overlays, hook, cta, duration) {
  const result = [...overlays];
  result.unshift({
    start: '00:00:00.000',
    end: '00:00:03.000',
    text: cleanCaption(hook)
  });

  result.push({
    start: secondsToTimestamp(Math.max(0, Math.min(duration - 8, 35))),
    end: secondsToTimestamp(duration),
    text: cleanCaption(cta)
  });

  return dedupeOverlays(result).slice(0, 14);
}

function dedupeOverlays(overlays) {
  const seen = new Set();
  return overlays.filter((overlay) => {
    const key = `${overlay.start}-${overlay.end}-${overlay.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function inferCta(feedback) {
  const revisedScript = typeof feedback.revisedScript === 'string'
    ? feedback.revisedScript
    : '';
  const ctaMatch = revisedScript.match(/(drop .*?(comments?|below)|comment .*?(type|below)|check your chart.*?)/i);
  return cleanCaption(ctaMatch?.[0] || 'Drop your Human Design type');
}

function normalizeHashtags(hashtags) {
  const source = Array.isArray(hashtags) && hashtags.length > 0
    ? hashtags
    : ['#humandesign', '#dailytransit', '#energyforecast'];

  return source
    .map((tag) => String(tag).trim())
    .filter(Boolean)
    .map((tag) => tag.startsWith('#') ? tag : `#${tag}`)
    .slice(0, 8);
}

function firstText(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
}

function cleanCaption(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 88);
}

function cleanTimestamp(value) {
  if (typeof value !== 'string') return '';
  if (/^\d{2}:\d{2}:\d{2}\.\d{3}$/.test(value)) return value;
  if (/^\d{2}:\d{2}$/.test(value)) return `00:${value}.000`;
  if (/^\d{2}:\d{2}:\d{2}$/.test(value)) return `${value}.000`;
  return '';
}

function secondsToTimestamp(totalSeconds) {
  const safe = Math.max(0, Number.isFinite(totalSeconds) ? totalSeconds : 0);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = Math.floor(safe % 60);
  const millis = Math.round((safe % 1) * 1000);
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
}

function safeFileName(value) {
  const base = String(value || 'improved-video')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return `${base || 'improved-video'}.mp4`;
}

function stripMp4(path) {
  return path.toLowerCase().endsWith('.mp4') ? path.slice(0, -4) : path;
}

function run(command, argv) {
  const scriptDir = dirname(fileURLToPath(import.meta.url));
  const projectDir = dirname(scriptDir);

  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, argv, {
      cwd: projectDir,
      stdio: ['ignore', 'inherit', 'inherit']
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolvePromise();
      } else {
        reject(new Error(`${command} exited with ${code}`));
      }
    });
  });
}
