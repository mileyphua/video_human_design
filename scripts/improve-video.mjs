import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { spawn, spawnSync } from 'node:child_process';
import { basename, extname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';

const args = parseArgs(process.argv.slice(2));

if (!args.input || !args.plan || !args.output) {
  console.error(`Usage:
  npm run improve-video -- --input input.mp4 --plan edit-plan.json --output improved.mp4

Required:
  --input    Source HeyGen video file
  --plan     JSON audit/edit plan from Gemini
  --output   Improved video path

Optional:
  --font     Font name for overlays, default Arial
  --duration Video duration in seconds if ffprobe is unavailable, default 45
`);
  process.exit(1);
}

const inputPath = resolve(args.input);
const planPath = resolve(args.plan);
const outputPath = resolve(args.output);
const font = args.font || 'Arial';

checkBinary('ffmpeg');

const plan = await readJson(planPath);
const duration = probeDuration(inputPath, Number.parseFloat(args.duration || '45'));
const tempDir = await mkdtemp(join(tmpdir(), 'thd-video-rework-'));

try {
  const assPath = join(tempDir, 'captions.ass');
  await writeFile(assPath, buildAss(plan, duration), 'utf8');

  const topHook = sanitizeOverlayText(
    plan.topHookText ||
      plan.editPlan?.topHookText ||
      plan.replacementHook ||
      'Do not make a big decision before watching this'
  );
  const bottomCta = sanitizeOverlayText(
    plan.bottomCtaText ||
      'Drop your Human Design type in the comments'
  );

  const vf = [
    'scale=1080:1920:force_original_aspect_ratio=increase',
    'crop=1080:1920',
    'eq=contrast=1.06:saturation=1.08',
    `subtitles=${escapeFilterPath(assPath)}`,
    buildTopTextFilter(topHook, font),
    buildBottomTextFilter(bottomCta, font),
    buildProgressBarFilter(duration)
  ].join(',');

  await run('ffmpeg', [
    '-y',
    '-i',
    inputPath,
    '-vf',
    vf,
    '-af',
    'loudnorm=I=-16:TP=-1.5:LRA=11',
    '-c:v',
    'libx264',
    '-preset',
    'medium',
    '-crf',
    '20',
    '-c:a',
    'aac',
    '-b:a',
    '160k',
    '-movflags',
    '+faststart',
    outputPath
  ]);

  console.log(JSON.stringify({
    ok: true,
    input: inputPath,
    output: outputPath,
    title: plan.title || null,
    hashtags: plan.hashtags || [],
    duration
  }, null, 2));
} finally {
  await rm(tempDir, { recursive: true, force: true });
}

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

function checkBinary(binary) {
  const result = spawnSync(binary, ['-version'], { encoding: 'utf8' });
  if (result.error || result.status !== 0) {
    throw new Error(`${binary} is required but was not found.`);
  }
}

function probeDuration(path, fallbackDuration) {
  const result = spawnSync('ffprobe', [
    '-v',
    'error',
    '-show_entries',
    'format=duration',
    '-of',
    'default=noprint_wrappers=1:nokey=1',
    path
  ], { encoding: 'utf8' });

  if (result.status !== 0) {
    const fallback = Number.isFinite(fallbackDuration) && fallbackDuration > 0
      ? fallbackDuration
      : 45;
    console.warn(`ffprobe is unavailable; using ${fallback} seconds for timeline overlays.`);
    return fallback;
  }

  const duration = Number.parseFloat(result.stdout.trim());
  return Number.isFinite(duration) && duration > 0 ? duration : 45;
}

function buildAss(plan, duration) {
  const overlays = normalizeOverlays(plan.captionOverlays, duration);
  const events = overlays.map((overlay, index) => {
    const text = escapeAssText(overlay.text);
    return `Dialogue: 0,${toAssTime(overlay.start)},${toAssTime(overlay.end)},Caption,,0,0,0,,${text}`;
  });

  return `[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,76,&H0000FFFF,&H00FFFFFF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${events.join('\n')}
`;
}

function normalizeOverlays(overlays, duration) {
  const source = Array.isArray(overlays) && overlays.length > 0
    ? overlays
    : fallbackOverlays(duration);

  return source
    .map((overlay) => ({
      start: parseTimestamp(overlay.start, 0),
      end: parseTimestamp(overlay.end, 3),
      text: sanitizeCaptionText(overlay.text || '')
    }))
    .filter((overlay) => overlay.text && overlay.end > overlay.start)
    .slice(0, 12);
}

function fallbackOverlays(duration) {
  const end = Math.min(duration, 45);
  return [
    { start: 0, end: Math.min(3, end), text: 'Feeling blocked today?' },
    { start: 3, end: Math.min(10, end), text: 'Your energy may be asking for patience.' },
    { start: 10, end: Math.min(20, end), text: 'Watch the Sun and Earth gates.' },
    { start: 20, end: Math.min(35, end), text: 'Use your type before you react.' },
    { start: 35, end, text: 'Drop your Human Design type.' }
  ];
}

function parseTimestamp(value, fallback) {
  if (typeof value === 'number') return value;
  if (typeof value !== 'string') return fallback;
  const parts = value.split(':').map(Number);
  if (parts.length === 3) {
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  if (parts.length === 2) {
    return parts[0] * 60 + parts[1];
  }
  const numeric = Number.parseFloat(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function toAssTime(seconds) {
  const safeSeconds = Math.max(0, seconds);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const secs = Math.floor(safeSeconds % 60);
  const centiseconds = Math.floor((safeSeconds % 1) * 100);
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(centiseconds).padStart(2, '0')}`;
}

function escapeAssText(text) {
  return sanitizeCaptionText(text)
    .replace(/[{}]/g, '')
    .replace(/\n/g, '\\N');
}

function sanitizeCaptionText(text) {
  return String(text)
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 90);
}

function sanitizeOverlayText(text) {
  return String(text)
    .replace(/[':]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 70);
}

function escapeDrawtext(text) {
  return text
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/:/g, '\\:')
    .replace(/%/g, '\\%');
}

function escapeFilterPath(path) {
  return path.replace(/\\/g, '\\\\').replace(/:/g, '\\:').replace(/'/g, "\\'");
}

function buildTopTextFilter(text, font) {
  return [
    'drawtext=',
    `font='${escapeDrawtext(font)}'`,
    `text='${escapeDrawtext(text)}'`,
    'x=(w-text_w)/2',
    'y=130',
    'fontsize=58',
    'fontcolor=white',
    'borderw=5',
    'bordercolor=black',
    'box=1',
    'boxcolor=black@0.45',
    'boxborderw=28',
    "enable='between(t,0,4)'"
  ].join(':');
}

function buildBottomTextFilter(text, font) {
  return [
    'drawtext=',
    `font='${escapeDrawtext(font)}'`,
    `text='${escapeDrawtext(text)}'`,
    'x=(w-text_w)/2',
    'y=h-250',
    'fontsize=44',
    'fontcolor=white',
    'borderw=4',
    'bordercolor=black',
    'box=1',
    'boxcolor=black@0.4',
    'boxborderw=22',
    "enable='gte(t,35)'"
  ].join(':');
}

function buildProgressBarFilter(duration) {
  const safeDuration = Math.max(1, duration);
  return `drawbox=x=0:y=0:w='1080*t/${safeDuration.toFixed(3)}':h=16:color=yellow@0.95:t=fill`;
}

function run(command, args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stdout.on('data', (data) => process.stdout.write(data));
    child.stderr.on('data', (data) => {
      stderr += data.toString();
      process.stderr.write(data);
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolvePromise();
      } else {
        reject(new Error(`${command} exited with ${code}\n${stderr}`));
      }
    });
  });
}
