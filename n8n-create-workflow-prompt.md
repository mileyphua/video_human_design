Use the n8n MCP server and create ONE new inactive workflow.

Workflow name: THD Daily Viral Script + HeyGen Video Rework Agent

Use Google Drive credential exactly:
- credential type: googleDriveOAuth2Api
- credential name: Human Design google drive
- credential id: LiPtEJRvSoEwu8RZ

Do not print or hard-code any secrets.

Daily script branch:
1. Schedule Trigger daily at 08:00. If the node has no timezone field, leave a sticky note that this follows the n8n instance timezone.
2. HTTP Request node to Total Human Design all-planets transit endpoint:
   - URL expression: {{$env.THD_BASE_URL || "https://api.totalhumandesign.com"}}/api/transits/all
   - query params: startDate=today YYYY-MM-DD, endDate=today YYYY-MM-DD, granularity=daily, format=full
   - header: Authorization: Bearer {{$env.THD_API_KEY}}
3. Code node normalizes transit data, extracts Sun and Earth gate/line/sign when present, and builds a strict prompt.
4. The prompt must use this system prompt exactly:
   You are an expert short-form viral creator specializing in Human Design and astrology. Take today's transit data and write a 45-second high-retention script for a talking-head video.
5. The prompt must enforce this script formula:
   - Pattern-Interrupt Hook, 0-3 seconds: never say "Today's Human Design transit is..."; use a hook like "If you're feeling weirdly irritable or blocked today, do not make any big decisions. Here's why."
   - Mechanism, 3-20 seconds: break down the Sun/Earth Gate activation in plain English.
   - Practical Hack, 20-35 seconds: actionable advice for Manifestors, Generators, Projectors, and Reflectors.
   - Call to Action, 35-45 seconds: "Check your chart to see if your throat center is open, and drop your type in the comments."
6. HTTP Request node to Gemini free API:
   - Use {{$env.GEMINI_API_KEY}}
   - Use {{$env.GEMINI_MODEL || "gemini-2.5-flash"}}
   - Return JSON with: title, hook, script, captions array, hashtags, heygen_manual_steps.
7. Code node parses Gemini response. If malformed, create a usable fallback script from the transit data.
8. Code node creates a binary JSON file named THD-viral-script-YYYY-MM-DD.json containing transit, script, captions, hashtags, and HeyGen manual steps.
9. Google Drive upload node uploads that JSON file.

Manual finished HeyGen video rework branch:
1. Form Trigger titled "Submit Finished HeyGen Video for Viral Rework".
2. Form fields:
   - googleDriveFileId required text
   - originalScript optional textarea
   - audience optional text
   - feedbackNotes optional textarea
   - durationSeconds optional number
3. Google Drive download node downloads the submitted file ID as binary data.
4. Write the downloaded video to /tmp/heygen-{{$execution.id}}.mp4.
5. Execute Command preflight runs ffmpeg -version.
6. Execute Command extracts simple video info and at least 3 preview frames to /tmp.
7. HTTP Request node to Gemini API creates a virality audit and edit plan JSON from originalScript, feedbackNotes, audience, and available frame/video info. If sending images to Gemini is too complex for n8n, keep this audit text-based but make the edit-plan schema strong.
8. Code node creates or repairs an edit plan JSON with:
   - hookText
   - ctaText
   - captions [{ start, end, text }]
   - emphasisBeats
   - visualStyle
   - musicMood
   - file names
9. Code or Execute Command creates an ASS subtitle file for the captions.
10. Execute Command must use free FFmpeg to actually transform the original downloaded video into /tmp/improved-{{$execution.id}}.mp4:
   - 1080x1920 scale/crop
   - burned captions/subtitles
   - top hook text for 0-4 seconds
   - bottom CTA after 35 seconds
   - progress bar
   - loudness normalization
   - H.264/AAC MP4 output
11. Read the improved MP4 from /tmp.
12. Google Drive upload node uploads the improved MP4.
13. Also upload the audit/edit-plan JSON to Google Drive.

Add sticky notes explaining required environment variables:
- THD_API_KEY
- GEMINI_API_KEY
- optional THD_BASE_URL
- optional GEMINI_MODEL
- optional Google Drive folder IDs if you use them
- FFmpeg must be installed in the n8n runtime for the video branch.

After creating it, validate the workflow. Return only:
- workflow id
- workflow URL if available
- validation status
- any runtime caveats
