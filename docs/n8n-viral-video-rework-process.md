# n8n Process: HeyGen Video Virality Rework Agent

This second process starts after you manually create a HeyGen Creator video and upload it to Google Drive.

## Goal

Turn a finished HeyGen talking-head video into a stronger short-form version by using a free/low-cost AI audit plus a free FFmpeg render pass.

## Recommended Google Drive Folders

```text
THD Daily Videos/
  01_uploaded_heygen/
  02_viral_audit/
  03_reworked_versions/
  04_ready_to_post/
```

## Required n8n Credentials

- Google Drive OAuth2 credential
- Gemini API key stored as `GEMINI_API_KEY`

## Free Render Tool

Use FFmpeg first. It is free and enough for:

- 9:16 resizing
- burned captions
- hook overlay
- CTA overlay
- progress bar
- loudness normalization
- social-ready MP4 export

Use Remotion later only if you want more advanced animated layouts. Remotion is free for individuals and small teams up to 3 people, but it requires a Node render environment.

## Workflow Nodes

1. **Google Drive Trigger**
   - Watch `01_uploaded_heygen`.
   - Trigger when a new video file is added.

2. **Download HeyGen Video**
   - Use Google Drive node.
   - Download the uploaded video as binary.

3. **Upload Video to Gemini File API**
   - HTTP Request.
   - Uses `GEMINI_API_KEY`.
   - Upload the video so Gemini can analyze audio and visuals.

4. **Generate Virality Audit**
   - HTTP Request to Gemini.
   - Use `prompts/virality-audit-prompt.md`.
   - Output strict JSON.

5. **Generate FFmpeg Edit Plan**
   - HTTP Request to Gemini.
   - Use `prompts/rework-plan-prompt.md`.
   - Output strict JSON that includes caption timestamps, hook overlay, CTA overlay, title, and hashtags.

6. **Save Audit JSON to Google Drive**
   - Upload JSON into `02_viral_audit`.
   - Name format: `YYYY-MM-DD-thd-viral-audit.json`.

7. **Render Improved Video**
   - Execute Command node if n8n can run local commands:

   ```sh
   npm run rework-from-feedback -- --input /tmp/input.mp4 --feedback /tmp/viral-audit.json --output /tmp/improved.mp4 --duration 45
   ```

   If your hosted n8n cannot run FFmpeg, run this render script from Codex/local machine after downloading the video and edit plan.

8. **Upload Improved Video**
   - Upload `/tmp/improved.mp4` to `03_reworked_versions`.

9. **Optional Human Review**
   - Move approved video to `04_ready_to_post`.

## Gemini Virality Audit Prompt

Use the file:

```text
prompts/virality-audit-prompt.md
```

## Gemini Edit Plan Prompt

Use the file:

```text
prompts/rework-plan-prompt.md
```

## Local Render Command

Use this when you have Gemini's returned virality feedback JSON and the original HeyGen video:

```sh
npm run rework-from-feedback -- --input ./heygen-original.mp4 --feedback ./viral-audit.json --output ./improved.mp4 --duration 45
```

Use this only when you already have a ready-made edit plan JSON:

```sh
npm run check-video-tools
npm run improve-video -- --input ./input.mp4 --plan ./edit-plan.json --output ./improved.mp4
```

## Example Edit Plan JSON

```json
{
  "title": "Do not force decisions today",
  "fileName": "2026-09-28-thd-transit-reworked.mp4",
  "topHookText": "Feeling blocked today?",
  "bottomCtaText": "Drop your Human Design type",
  "captionOverlays": [
    {
      "start": "00:00:00.000",
      "end": "00:00:03.000",
      "text": "Feeling blocked today?"
    },
    {
      "start": "00:00:03.000",
      "end": "00:00:08.000",
      "text": "Do not force a big decision yet."
    }
  ],
  "style": {
    "captionPrimaryColor": "yellow",
    "captionSecondaryColor": "white",
    "backgroundBlur": true,
    "progressBar": true,
    "safeArea": true
  },
  "hashtags": ["#humandesign", "#dailytransit", "#energyforecast"]
}
```

## Notes

- The AI can improve retention signals, but it cannot guarantee virality.
- Keep the first version simple: audit plus FFmpeg overlay render.
- Add Remotion after the FFmpeg version proves useful.
