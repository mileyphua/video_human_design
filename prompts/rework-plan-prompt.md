# Human Design Video Rework Plan Prompt

You are turning a virality audit into a concrete FFmpeg edit plan.

Inputs:

- The original video
- The virality audit JSON
- The intended 45-second Human Design daily transit script

Return strict JSON only:

```json
{
  "title": "",
  "fileName": "",
  "topHookText": "",
  "bottomCtaText": "",
  "captionOverlays": [
    {
      "start": "00:00:00.000",
      "end": "00:00:03.000",
      "text": ""
    }
  ],
  "style": {
    "captionPrimaryColor": "yellow",
    "captionSecondaryColor": "white",
    "backgroundBlur": true,
    "progressBar": true,
    "safeArea": true
  },
  "hashtags": []
}
```

Rules:

- Keep all overlay text concise.
- Use mobile-safe captions.
- Use emotionally specific language.
- Prefer clarity and retention over decoration.
- The output should be possible with free FFmpeg filters.
