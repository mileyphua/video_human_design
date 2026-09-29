import { spawnSync } from 'node:child_process';

const required = ['ffmpeg'];
const optional = ['ffprobe'];

for (const binary of required) {
  const result = spawnSync(binary, ['-version'], { encoding: 'utf8' });
  if (result.error || result.status !== 0) {
    console.error(`${binary} is not available. Install FFmpeg before rendering.`);
    process.exit(1);
  }

  const firstLine = (result.stdout || result.stderr).split('\n')[0];
  console.log(`${binary}: ${firstLine}`);
}

for (const binary of optional) {
  const result = spawnSync(binary, ['-version'], { encoding: 'utf8' });
  if (result.error || result.status !== 0) {
    console.warn(`${binary}: not found. The renderer will use --duration or default to 45 seconds.`);
    continue;
  }

  const firstLine = (result.stdout || result.stderr).split('\n')[0];
  console.log(`${binary}: ${firstLine}`);
}
