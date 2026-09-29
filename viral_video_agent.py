import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


SCRIPT_SYSTEM_PROMPT = (
    "You are an expert short-form viral creator specializing in Human Design and astrology. "
    "Take today's transit data and write a 45-second high-retention script for a talking-head video."
)


VIRAL_RESEARCH_BRIEF = """
Current short-form retention rules to apply:
- Hook within 1 second; the first line must name a specific feeling, pain, identity, or risk.
- Use shock, intrigue, then satisfy: create tension fast, explain the mechanism, give a practical payoff.
- Keep the promise clear; do not hide the point until second 8.
- Burn in readable captions; make key phrases punchy enough to understand with sound off.
- Add a visual reset every 2-4 seconds: caption change, punch-in, overlay, progress bar, CTA, or beat emphasis.
- Keep vertical safe areas in mind; do not put important text in the very bottom UI zone.
- End with a comment-driven CTA or loop cue.
""".strip()


def extract_drive_file_id(value: str) -> Optional[str]:
    value = (value or "").strip()
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/open\?id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", value):
        return value
    return None


def download_drive_file(shared_link_or_id: str, output_path: Path) -> Path:
    file_id = extract_drive_file_id(shared_link_or_id)
    if not file_id:
        raise ValueError("Could not find a Google Drive file ID. Share the file as 'Anyone with the link can view'.")

    session = requests.Session()
    url = "https://drive.google.com/uc"
    response = session.get(url, params={"export": "download", "id": file_id}, timeout=120)
    token = None
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            token = value
            break
    if token:
        response = session.get(url, params={"export": "download", "confirm": token, "id": file_id}, timeout=120)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def fetch_thd_transit(api_key: str, base_url: str = "https://api.totalhumandesign.com") -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    response = requests.get(
        f"{base_url.rstrip('/')}/api/transits/all",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"startDate": today, "endDate": today, "granularity": "daily", "format": "full"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def transit_data_is_empty(transit: Dict[str, Any]) -> bool:
    data = transit.get("data") if isinstance(transit, dict) else None
    return isinstance(data, list) and len(data) == 0


def flatten_transit_values(value: Any, path: str = "") -> List[str]:
    lines: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            lines.extend(flatten_transit_values(item, child_path))
        return lines
    if isinstance(value, list):
        for index, item in enumerate(value[:12]):
            lines.extend(flatten_transit_values(item, f"{path}[{index}]"))
        return lines
    if value not in (None, "", []):
        lines.append(f"{path}: {value}")
    return lines


def transit_fact_summary(transit: Dict[str, Any]) -> str:
    verified = str(transit.get("_verified_transit_facts") or "").strip() if isinstance(transit, dict) else ""
    summary_lines = []
    if verified:
        summary_lines.append("USER-VERIFIED FACTS OVERRIDE API INTERPRETATION:")
        summary_lines.append(verified)
    if isinstance(transit, dict):
        compact_lines = flatten_transit_values({k: v for k, v in transit.items() if k != "_verified_transit_facts"})
        gate_lines = [
            line
            for line in compact_lines
            if re.search(r"(sun|earth|gate|line|太陽|地球|閘門|爻)", line, re.I)
        ][:80]
        if gate_lines:
            summary_lines.append("API FACT LINES:")
            summary_lines.extend(gate_lines)
    return "\n".join(summary_lines).strip() or "No authoritative Sun/Earth gate-line facts were found."


def authoritative_gate_numbers(transit: Dict[str, Any]) -> List[str]:
    facts = transit_fact_summary(transit)
    numbers = set(re.findall(r"Gate\s*(\d{1,2})", facts, re.I))
    numbers.update(re.findall(r"(\d{1,2})\s*號閘門", facts))
    return sorted(numbers, key=lambda item: int(item))


def script_gate_numbers(payload: Dict[str, Any]) -> List[str]:
    fields = [
        str(payload.get("script") or ""),
        str(payload.get("script_zh_hant") or ""),
        str(payload.get("hook") or ""),
        str(payload.get("hook_zh_hant") or ""),
    ]
    for cap_field in ("captions", "captions_zh_hant"):
        for cap in payload.get(cap_field) or []:
            if isinstance(cap, dict):
                fields.append(str(cap.get("text") or ""))
    text = "\n".join(fields)
    numbers = set(re.findall(r"Gate\s*(\d{1,2})", text, re.I))
    numbers.update(re.findall(r"(\d{1,2})\s*號閘門", text))
    return sorted(numbers, key=lambda item: int(item))


def unsupported_script_gates(payload: Dict[str, Any], transit: Dict[str, Any]) -> List[str]:
    allowed = set(authoritative_gate_numbers(transit))
    mentioned = set(script_gate_numbers(payload))
    if not allowed:
        return []
    return sorted(mentioned - allowed, key=lambda item: int(item))


def gemini_generate(prompt: str, api_key: str, model: str = DEFAULT_GEMINI_MODEL, temperature: float = 0.75) -> str:
    if not api_key:
        raise ValueError("Missing GEMINI_API_KEY.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    response = requests.post(url, params={"key": api_key}, json=body, timeout=120)
    response.raise_for_status()
    data = response.json()
    return "\n".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])


def parse_json_loose(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def strip_timing_markers(text: Any) -> str:
    value = str(text or "")
    value = re.sub(r"(?im)^\s*[-*•]?\s*[（(]?\s*\d+\s*[-–—]\s*\d+\s*(?:s|sec|secs|seconds|秒)\s*[）)]?\s*[:：-]?\s*", "", value)
    value = re.sub(r"(?im)^\s*[-*•]?\s*(?:hook|mechanism|practical hack|call to action|cta)\s*[（(]\s*\d+\s*[-–—]\s*\d+\s*(?:s|sec|secs|seconds|秒)\s*[）)]\s*[:：-]?\s*", "", value)
    value = re.sub(r"[（(]\s*\d+\s*[-–—]\s*\d+\s*(?:s|sec|secs|seconds|秒)\s*[）)]", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def clean_script_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    for field in ("script", "script_zh_hant"):
        if payload.get(field):
            payload[field] = strip_timing_markers(payload[field])
    for field in ("hook", "hook_zh_hant"):
        if payload.get(field):
            payload[field] = strip_timing_markers(payload[field])
    for field in ("captions", "captions_zh_hant"):
        if isinstance(payload.get(field), list):
            for cap in payload[field]:
                if isinstance(cap, dict) and cap.get("text"):
                    cap["text"] = strip_timing_markers(cap["text"])
    return payload


def language_instruction(language_mode: str) -> str:
    if language_mode == "Traditional Chinese":
        return (
            "Write all viewer-facing output in Traditional Chinese (繁體中文). "
            "Keep Human Design terms natural for a Taiwan/Hong Kong audience."
        )
    if language_mode == "Bilingual":
        return (
            "Return both English and Traditional Chinese. Include script and script_zh_hant. "
            "Captions should be Traditional Chinese unless the phrase is a short English hook."
        )
    return "Write viewer-facing output in English."


def build_script_prompt(transit: Dict[str, Any], creator_context: str = "", language_mode: str = "Bilingual") -> str:
    facts = transit_fact_summary(transit)
    return f"""
{SCRIPT_SYSTEM_PROMPT}

Return strict JSON only with:
title, title_zh_hant, hook, hook_zh_hant, script, script_zh_hant, captions, captions_zh_hant, hashtags, hashtags_zh_hant, heygen_manual_steps, avatar_motion_cues, virality_check.

Script Formula:
1. The Pattern-Interrupt Hook (0-3s): Never say "Today's Human Design transit is..." Instead use a line like "If you're feeling weirdly irritable or blocked today, do not make any big decisions. Here's why."
2. The Mechanism (3-20s): Break down the Sun/Earth Gate activation in plain English.
3. The Practical Hack (20-35s): Actionable advice for Manifestors, Generators, Projectors, and Reflectors.
4. Call to Action (35-45s): "Check your chart to see if your throat center is open, and drop your type in the comments."

Important formatting rule:
script and script_zh_hant must be clean narration only for HeyGen lip sync. Do not include timestamps, beat labels, section labels, parentheses like "(0-3s)", or markdown bullets inside script/script_zh_hant. Timing belongs only in captions.start/end and avatar_motion_cues.start/end.

Make it high-retention, direct, emotionally specific, and easy for a HeyGen avatar to speak.
Hashtags must include both English and Traditional Chinese viral discovery tags for Human Design, astrology, energy forecast, and viewer type comments.

Virality research rules:
{VIRAL_RESEARCH_BRIEF}

HeyGen avatar motion requirements:
Include avatar_motion_cues as an array of beat-by-beat directions. Each cue should include start, end, line_or_beat, expression, gesture, camera, and delivery. Use motions a HeyGen avatar can realistically perform: slight lean-in, eyebrow raise, small hand emphasis, calm pause, head nod, half-smile, concerned look, open palm, direct eye contact.

Language:
{language_instruction(language_mode)}

Authoritative transit facts:
{facts}

Factual accuracy rules:
- You must use the Authoritative transit facts above as the source of truth.
- Do not invent Sun/Earth gates, lines, centers, or meanings that are not supported by the facts.
- If user-verified facts are present, they override the raw API response.
- Explicitly name the actual Sun gate/line and Earth gate/line in the mechanism.
- If the facts say 太陽18號閘門4爻 and 地球17號閘門4爻, the script must not mention Sun Gate 16 or Earth Gate 9.

Creator context:
{creator_context or "Audience: Human Design and astrology beginners on TikTok/Reels/Shorts."}

Transit data:
{json.dumps(transit, ensure_ascii=False, indent=2)}
""".strip()


def fallback_script() -> Dict[str, Any]:
    return {
        "title": "Daily Human Design Transit: Pause Before You Decide",
        "title_zh_hant": "今日人類圖流日：先暫停再決定",
        "hook": "If today feels weirdly tense, pause before you make a big decision.",
        "hook_zh_hant": "如果你今天莫名煩躁或卡住，先不要急著做重大決定。",
        "script": (
            "If today feels weirdly tense, pause before you make a big decision. "
            "The transit is showing you the pressure pattern underneath the mood, not a command to act. "
            "Manifestors: inform before you move. Generators: wait for your body response. "
            "Projectors: do not chase the room. Reflectors: give the feeling space before you name it. "
            "Check your chart to see if your throat center is open, and drop your type in the comments."
        ),
        "script_zh_hant": (
            "如果你今天莫名煩躁或卡住，先不要急著做重大決定。"
            "今天的流日不是要你立刻行動，而是在提醒你看見情緒底下的壓力模式。"
            "顯示者：先告知再行動。生產者：等身體回應。"
            "投射者：不要追著別人的注意力跑。反映者：給自己一點時間，不要太快下結論。"
            "去看你的圖，看看喉嚨中心是不是開放，然後在留言區留下你的類型。"
        ),
        "captions": [
            {"start": 0, "end": 4, "text": "Pause before you decide."},
            {"start": 4, "end": 20, "text": "The transit is showing the pressure underneath the mood."},
            {"start": 20, "end": 35, "text": "Use your strategy today, not panic."},
            {"start": 35, "end": 45, "text": "Check your throat center and drop your type."},
        ],
        "captions_zh_hant": [
            {"start": 0, "end": 4, "text": "今天先不要急著做重大決定"},
            {"start": 4, "end": 20, "text": "流日正在放大情緒底下的壓力模式"},
            {"start": 20, "end": 35, "text": "顯示者告知｜生產者等待回應｜投射者等邀請｜反映者給時間"},
            {"start": 35, "end": 45, "text": "看看你的喉嚨中心，留言你的類型"},
        ],
        "hashtags": [
            "#humandesign",
            "#humandesigntransit",
            "#astrology",
            "#spiritualtiktok",
            "#energyforecast",
            "#人類圖",
            "#人類圖流日",
            "#能量提醒",
            "#靈性成長",
            "#顯示者",
            "#生產者",
            "#投射者",
            "#反映者",
        ],
        "hashtags_zh_hant": ["#人類圖", "#人類圖流日", "#能量提醒", "#靈性成長", "#顯示者", "#生產者", "#投射者", "#反映者"],
        "avatar_motion_cues": [
            {
                "start": 0,
                "end": 4,
                "line_or_beat": "Pattern interrupt hook",
                "expression": "concerned but calm",
                "gesture": "slight lean-in with one open palm",
                "camera": "direct eye contact",
                "delivery": "slow first sentence, then tighten the pace",
            },
            {
                "start": 4,
                "end": 20,
                "line_or_beat": "Explain the mechanism",
                "expression": "focused and reassuring",
                "gesture": "small hand emphasis on Gate/Sun/Earth keywords",
                "camera": "subtle nods, stay centered",
                "delivery": "clear, grounded, no rush",
            },
            {
                "start": 20,
                "end": 35,
                "line_or_beat": "Type-specific practical hack",
                "expression": "confident teacher energy",
                "gesture": "count points with fingers or small rhythmic hand beats",
                "camera": "slight forward posture",
                "delivery": "faster list pace",
            },
            {
                "start": 35,
                "end": 45,
                "line_or_beat": "CTA",
                "expression": "inviting half-smile",
                "gesture": "open palm toward viewer, then small nod",
                "camera": "direct eye contact",
                "delivery": "warm and clear",
            },
        ],
        "virality_check": {
            "score": 72,
            "passed": True,
            "notes": [
                "Has a specific emotional hook.",
                "Includes type-specific practical advice.",
                "Needs final captions and overlays in FFmpeg for stronger retention.",
            ],
        },
        "heygen_manual_steps": [
            "Open HeyGen Creator manually.",
            "Create a 9:16 talking-head avatar video.",
            "Paste the script exactly.",
            "Export the MP4.",
            "Upload it to Google Drive and paste the share link in this app.",
        ],
    }


def refine_script_prompt(payload: Dict[str, Any], transit: Dict[str, Any], language_mode: str) -> str:
    facts = transit_fact_summary(transit)
    return f"""
You are Agent 2: a short-form virality QA editor.
Score and improve this Human Design talking-head script before it goes to HeyGen.

Research-backed virality rules:
{VIRAL_RESEARCH_BRIEF}

Language:
{language_instruction(language_mode)}

Return strict JSON only using the same schema:
title, title_zh_hant, hook, hook_zh_hant, script, script_zh_hant, captions, captions_zh_hant, hashtags, hashtags_zh_hant, heygen_manual_steps, avatar_motion_cues, virality_check.

virality_check must include:
score 0-100, passed boolean, notes array, hook_score, clarity_score, retention_score, cta_score.

If score is below 85, rewrite the hook/script/captions until it is at least 85.
Make the HeyGen avatar motion cues specific enough that the user knows what expression, gesture, camera feel, and delivery to choose for each beat.
Make hashtags bilingual: English discovery tags plus Traditional Chinese tags that a Human Design audience would search or comment with.
Keep script and script_zh_hant as clean spoken narration only. Never include timestamps, beat labels, markdown bullets, or labels such as "Hook", "Mechanism", or "CTA" inside either script field.

Authoritative transit facts:
{facts}

Factual QA rules:
- Verify every gate/line/center statement against the Authoritative transit facts.
- Remove or rewrite any unsupported gate, line, center, or type advice.
- If user-verified facts are present, follow them exactly even if the candidate script disagrees.
- The final script must clearly reflect the actual Sun/Earth facts.

Candidate script JSON:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Transit data:
{json.dumps(transit, ensure_ascii=False, indent=2)}
""".strip()


def factual_repair_prompt(payload: Dict[str, Any], transit: Dict[str, Any], language_mode: str, unsupported_gates: List[str]) -> str:
    facts = transit_fact_summary(transit)
    return f"""
You are a Human Design factual QA repair editor.
The current script mentioned unsupported gate numbers: {", ".join(unsupported_gates)}.

Rewrite the JSON so every gate/line/center claim follows ONLY the Authoritative transit facts.
Keep the same strict JSON schema and keep it viral, but factual accuracy is mandatory.

Language:
{language_instruction(language_mode)}

Authoritative transit facts:
{facts}

Rules:
- Remove all unsupported gates: {", ".join(unsupported_gates)}.
- Explicitly use the actual Sun/Earth gate-line facts from the authoritative facts.
- Do not invent new gates, lines, centers, or meanings.
- Keep script and script_zh_hant clean spoken narration only.

Current JSON:
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def generate_viral_script(
    transit: Dict[str, Any],
    api_key: str,
    model: str,
    creator_context: str,
    language_mode: str = "Bilingual",
) -> Dict[str, Any]:
    if not api_key:
        return fallback_script()
    if transit_data_is_empty(transit) and not str(transit.get("_verified_transit_facts") or "").strip():
        raise ValueError(
            "THD returned no daily transit records for today. Paste verified transit facts in the corrected-facts box "
            "or check the THD API date/settings before generating a script."
        )
    data = parse_json_loose(gemini_generate(build_script_prompt(transit, creator_context, language_mode), api_key, model, 0.85))
    base = fallback_script()
    base.update({k: v for k, v in data.items() if v})
    clean_script_fields(base)
    refined = parse_json_loose(gemini_generate(refine_script_prompt(base, transit, language_mode), api_key, model, 0.7))
    base.update({k: v for k, v in refined.items() if v})
    clean_script_fields(base)
    unsupported = unsupported_script_gates(base, transit)
    if unsupported:
        repaired = parse_json_loose(gemini_generate(factual_repair_prompt(base, transit, language_mode, unsupported), api_key, model, 0.35))
        base.update({k: v for k, v in repaired.items() if v})
        clean_script_fields(base)
        unsupported = unsupported_script_gates(base, transit)
        if unsupported:
            raise ValueError(
                "Generated script still mentioned unsupported gate(s): "
                + ", ".join(unsupported)
                + ". Please tighten the corrected transit facts and try again."
            )
    return base


def ffmpeg_info(video_path: Path) -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
    )
    return (result.stdout + "\n" + result.stderr)[-5000:]


def ffprobe_duration(video_path: Path) -> Optional[float]:
    if shutil.which("ffprobe"):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                duration = float(result.stdout.strip())
                if duration > 0:
                    return duration
            except ValueError:
                pass

    if not shutil.which("ffmpeg"):
        return None
    result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video_path)], capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
    if not match:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def srt_time(seconds: Any) -> str:
    seconds = max(0.0, float(seconds or 0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captions_to_srt(captions: List[Dict[str, Any]]) -> str:
    blocks = []
    for index, cap in enumerate(captions, start=1):
        text = strip_timing_markers(cap.get("text", ""))
        if not text:
            continue
        blocks.append(f"{index}\n{srt_time(cap.get('start'))} --> {srt_time(cap.get('end'))}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def compact_caption_text(text: str) -> str:
    text = strip_timing_markers(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def split_word_timestamp_captions(words: List[Any], max_chars: int = 24, max_duration: float = 2.7) -> List[Dict[str, Any]]:
    captions: List[Dict[str, Any]] = []
    current_words: List[str] = []
    start: Optional[float] = None
    end: Optional[float] = None

    def flush() -> None:
        nonlocal current_words, start, end
        if not current_words or start is None or end is None:
            return
        text = compact_caption_text("".join(current_words) if any(contains_cjk(w) for w in current_words) else " ".join(current_words))
        if text:
            captions.append({"start": start, "end": max(start + 0.65, end), "text": text})
        current_words = []
        start = None
        end = None

    for word in words:
        token = compact_caption_text(getattr(word, "word", "") or "")
        word_start = getattr(word, "start", None)
        word_end = getattr(word, "end", None)
        if not token or word_start is None or word_end is None:
            continue
        candidate_words = current_words + [token]
        candidate = "".join(candidate_words) if any(contains_cjk(w) for w in candidate_words) else " ".join(candidate_words)
        too_long = len(candidate) > max_chars
        too_slow = start is not None and (float(word_end) - start) > max_duration
        gap = end is not None and (float(word_start) - end) > 0.7
        if current_words and (too_long or too_slow or gap):
            flush()
        if start is None:
            start = float(word_start)
        current_words.append(token)
        end = float(word_end)
    flush()
    return captions


def vtt_time(seconds: Any) -> str:
    return srt_time(seconds).replace(",", ".")


def srt_to_vtt(srt_text: str) -> str:
    captions = parse_subtitle_captions(srt_text, 24 * 3600)
    lines = ["WEBVTT", ""]
    for cap in captions:
        lines.append(f"{vtt_time(cap.get('start'))} --> {vtt_time(cap.get('end'))}")
        lines.append(strip_timing_markers(cap.get("text", "")))
        lines.append("")
    return "\n".join(lines)


def translate_srt_to_zh_hant_taiwan(srt_text: str, api_key: str, model: str = DEFAULT_GEMINI_MODEL) -> str:
    captions = parse_subtitle_captions(srt_text, 24 * 3600)
    if not captions or not api_key:
        return srt_text
    prompt = f"""
Translate these timed captions into natural Traditional Chinese for Taiwan viewers.
Keep Human Design terms natural in Taiwan usage. Preserve the meaning, keep each caption short, punchy, and spoken-caption friendly.
Do not include Simplified Chinese. Do not add timestamps in the text.
Return strict JSON only as an array of strings with exactly {len(captions)} items.

Captions:
{json.dumps([cap.get("text", "") for cap in captions], ensure_ascii=False, indent=2)}
""".strip()
    raw = gemini_generate(prompt, api_key, model, 0.35)
    values = None
    try:
        values = json.loads(raw)
    except Exception:
        parsed = parse_json_loose(raw)
        if isinstance(parsed, dict):
            values = parsed.get("captions") or parsed.get("items") or parsed.get("translations")
        if values is None:
            match = re.search(r"\[[\s\S]*\]", raw)
            try:
                values = json.loads(match.group(0)) if match else None
            except Exception:
                values = None
    if not isinstance(values, list) or len(values) != len(captions):
        return srt_text
    translated_captions = []
    for cap, text in zip(captions, values):
        translated_captions.append({"start": cap.get("start"), "end": cap.get("end"), "text": strip_timing_markers(text)})
    return captions_to_srt(translated_captions)


def transcribe_video_to_srt(
    video_path: Path,
    model_size: str = "small",
    language: Optional[str] = None,
) -> str:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise RuntimeError(
            "Auto-caption requires faster-whisper. Install it with: python3 -m pip install faster-whisper"
        ) from exc
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to extract audio for auto-captioning.")

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            language=language or None,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        captions = []
        for segment in segments:
            words = getattr(segment, "words", None) or []
            if words:
                captions.extend(split_word_timestamp_captions(words))
            else:
                text = strip_timing_markers(getattr(segment, "text", "")).strip()
                if not text:
                    continue
                captions.append({"start": float(segment.start), "end": float(segment.end), "text": text})
    return captions_to_srt(captions)


def audit_prompt(script: str, notes: str, audience: str, info: str, language_mode: str = "Bilingual") -> str:
    return f"""
You are a short-form viral video strategist and retention editor.
Audit this HeyGen Human Design talking-head video and return strict JSON only:
{{
  "viralityScore": 0-100,
  "regenerateScript": true/false,
  "diagnosis": ["specific issue"],
  "betterHook": "replacement hook if needed",
  "revisedScript": "full revised 45-second script if regenerateScript is true",
  "editPlan": {{
    "hookText": "top overlay 0-4s",
    "ctaText": "bottom overlay after 35s",
    "captions": [{{"start": 0, "end": 4, "text": "caption"}}],
    "emphasisBeats": ["what to punch up visually"],
    "visualStyle": "caption/motion style",
    "musicMood": "background music direction"
  }}
}}

If the script itself is weak, unclear, slow, or not specific enough, set regenerateScript=true and provide a better script for the user to regenerate in HeyGen.
If the script is usable, set regenerateScript=false and make the edit plan strong enough for FFmpeg caption/render changes.

The uploaded HeyGen video is expected to be only an avatar speaking with lip sync, facial expression, and small hand gestures.
Make the edit feel professionally produced by adding retention captions, hook/CTA overlays, subtle color/contrast treatment, pacing graphics, and progress animation.
Do not ask for new filming unless the script itself must be regenerated.

Research-backed edit rules:
{VIRAL_RESEARCH_BRIEF}

FFmpeg edit plan must assume a plain avatar clip and specify:
- short caption lines
- hook overlay text
- CTA overlay text
- beat changes every 2-4 seconds
- progress bar / readability band / punch-in style recommendations
- whether the script must be regenerated before editing

Language requirement:
{language_instruction(language_mode)}

Audience: {audience or "Human Design and astrology beginners"}
Creator notes: {notes or "None"}
Current script:
{script}

FFmpeg/video info:
{info}
""".strip()


def audit_video(
    video_path: Path,
    script: str,
    notes: str,
    audience: str,
    api_key: str,
    model: str,
    language_mode: str = "Bilingual",
) -> Dict[str, Any]:
    info = ffmpeg_info(video_path)
    if not api_key:
        return {
            "viralityScore": 70,
            "regenerateScript": False,
            "diagnosis": ["No Gemini key was provided, so this is a rules-based audit.", "Add larger captions, a faster hook, and a visible CTA."],
            "betterHook": "If today feels emotionally loud, pause before you decide.",
            "revisedScript": "",
            "editPlan": default_edit_plan(),
        }
    data = parse_json_loose(gemini_generate(audit_prompt(script, notes, audience, info, language_mode), api_key, model, 0.75))
    if "editPlan" not in data:
        data["editPlan"] = default_edit_plan()
    return data


def default_edit_plan() -> Dict[str, Any]:
    return {
        "hookText": "如果今天情緒很大，先暫停再決定",
        "ctaText": "看你的喉嚨中心，留言你的類型",
        "captions": [
            {"start": 0, "end": 4, "text": "今天先不要急著做決定"},
            {"start": 4, "end": 20, "text": "流日正在放大你情緒底下的壓力"},
            {"start": 20, "end": 35, "text": "顯示者告知｜生產者等回應｜投射者等邀請｜反映者給時間"},
            {"start": 35, "end": 45, "text": "看看你的喉嚨中心，留言你的類型"},
        ],
        "emphasisBeats": ["0-4s hook overlay", "type-specific hack captions", "CTA in final ten seconds"],
        "visualStyle": "bold yellow/white captions, vertical crop, cyan progress bar",
        "musicMood": "soft energetic pulse under voice",
    }


def selected_language_assets(payload: Dict[str, Any], language_mode: str = "Bilingual") -> Dict[str, Any]:
    use_zh = language_mode in {"Bilingual", "Traditional Chinese"}
    captions = payload.get("captions_zh_hant") if use_zh else payload.get("captions")
    if not captions:
        captions = payload.get("captions") or payload.get("captions_zh_hant") or default_edit_plan()["captions"]

    script = payload.get("script_zh_hant") if use_zh else payload.get("script")
    if not script:
        script = payload.get("script") or payload.get("script_zh_hant") or fallback_script()["script_zh_hant"]

    hook = payload.get("hook_zh_hant") if use_zh else payload.get("hook")
    if not hook:
        hook = payload.get("hook") or payload.get("hook_zh_hant") or default_edit_plan()["hookText"]

    cta = "看你的喉嚨中心，留言你的類型" if use_zh else "Check your throat center and drop your type."

    hashtags = []
    for tag in (payload.get("hashtags") or []) + (payload.get("hashtags_zh_hant") or []):
        if tag and tag not in hashtags:
            hashtags.append(tag)

    return {"captions": captions, "script": script, "hook": hook, "cta": cta, "hashtags": hashtags}


def parse_timecode(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def parse_subtitle_captions(subtitle_text: str, duration: float) -> List[Dict[str, Any]]:
    text = (subtitle_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    text = re.sub(r"^\ufeff?WEBVTT[^\n]*\n+", "", text, flags=re.IGNORECASE)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    captions: List[Dict[str, Any]] = []
    time_pattern = re.compile(
        r"(\d{1,2}(?::\d{1,2}){1,2}(?:[.,]\d+)?)\s*-->\s*(\d{1,2}(?::\d{1,2}){1,2}(?:[.,]\d+)?)"
    )
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and re.fullmatch(r"\d+", lines[0]):
            lines = lines[1:]
        timing_index = None
        match = None
        for index, line in enumerate(lines):
            match = time_pattern.search(line)
            if match:
                timing_index = index
                break
        if match is None or timing_index is None:
            continue
        start = parse_timecode(match.group(1))
        end = parse_timecode(match.group(2))
        if start is None or end is None or end <= start:
            continue
        caption_lines = lines[timing_index + 1 :]
        caption_text = strip_timing_markers(" ".join(caption_lines))
        if not caption_text:
            continue
        captions.append({"start": start, "end": min(float(duration or 45), end), "text": caption_text})
    return normalize_caption_times(captions, duration) if captions else []


def parse_manual_captions(caption_text: str, duration: float) -> List[Dict[str, Any]]:
    text = str(caption_text or "").strip()
    subtitle_captions = parse_subtitle_captions(text, duration)
    if subtitle_captions:
        return subtitle_captions
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    timed: List[Dict[str, Any]] = []
    untimed: List[str] = []
    time_pattern = re.compile(
        r"^\s*(?:\d+\s*)?(?:\[|\()?(\d{1,2}(?::\d{1,2}){0,2}(?:[.,]\d+)?)\s*(?:-->|-|–|—|to|至)\s*(\d{1,2}(?::\d{1,2}){0,2}(?:[.,]\d+)?)(?:\]|\))?\s*[:：-]?\s*(.+)$",
        re.IGNORECASE,
    )
    for line in lines:
        compact = " ".join(part.strip() for part in line.splitlines() if part.strip())
        match = time_pattern.match(compact)
        if match:
            start = parse_timecode(match.group(1))
            end = parse_timecode(match.group(2))
            if start is not None and end is not None and end > start:
                timed.append({"start": start, "end": min(float(duration or 45), end), "text": strip_timing_markers(match.group(3))})
                continue
        untimed.append(strip_timing_markers(compact))

    if timed and not untimed:
        return normalize_caption_times(timed, duration)

    source_lines = [line for line in untimed if line]
    if not source_lines:
        return normalize_caption_times(timed, duration)

    fallback_start = timed[-1]["end"] if timed else 0.0
    remaining = max(1.0, float(duration or 45) - fallback_start)
    step = max(1.4, remaining / len(source_lines))
    for index, line in enumerate(source_lines):
        start = fallback_start + index * step
        end = min(float(duration or 45), fallback_start + (index + 1) * step)
        timed.append({"start": start, "end": max(start + 1.0, end), "text": line})
    return normalize_caption_times(timed, duration)


def script_to_caption_text(payload: Dict[str, Any], language_mode: str = "Bilingual") -> str:
    assets = selected_language_assets(payload or fallback_script(), language_mode)
    captions = assets.get("captions") or []
    if isinstance(captions, list) and captions:
        lines = []
        for cap in captions:
            if isinstance(cap, dict):
                text = strip_timing_markers(cap.get("text", ""))
            else:
                text = strip_timing_markers(cap)
            if text:
                lines.append(text)
        if lines:
            return "\n".join(lines)
    return strip_timing_markers(assets.get("script", ""))


def build_edit_plan_from_script(
    payload: Dict[str, Any],
    language_mode: str = "Bilingual",
    duration: float = 45,
    manual_caption_text: str = "",
    caption_style: str = "Kinetic Yellow",
    caption_offset: float = 0.0,
    video_speed: float = 1.0,
    audio_style: str = "Viral Voice",
    keyword_popups: bool = False,
    motion_graphics: str = "Lively",
) -> Dict[str, Any]:
    assets = selected_language_assets(payload or fallback_script(), language_mode)
    manual_captions = parse_manual_captions(manual_caption_text, duration) if manual_caption_text else []
    captions = apply_caption_offset(manual_captions or normalize_caption_times(assets["captions"], duration), caption_offset, duration)
    return {
        "hookText": assets["hook"],
        "ctaText": assets["cta"],
        "captions": captions,
        "captionStyle": caption_style,
        "captionOffset": caption_offset,
        "videoSpeed": video_speed,
        "audioStyle": audio_style,
        "keywordPopups": keyword_popups,
        "motionGraphics": motion_graphics,
        "hashtags": assets["hashtags"],
        "emphasisBeats": [
            "0-3s: bold hook overlay with quick scale-in",
            "Every caption change: fade and slight punch emphasis",
            "20-35s: practical type advice appears as fast readable captions",
            "Final 10s: CTA overlay plus progress bar completion",
        ],
        "visualStyle": f"FFmpeg-only vertical edit: {caption_style} captions, crisp contrast, subtle sharpening, dark readability bands, top hook, bottom CTA, cyan progress bar",
        "musicMood": "No music added automatically; keep HeyGen voice clear and loudness-normalized.",
        "source": "approved_script_payload",
    }


def apply_caption_offset(captions: List[Dict[str, Any]], offset: float, duration: float) -> List[Dict[str, Any]]:
    try:
        offset_f = float(offset or 0)
    except Exception:
        offset_f = 0.0
    duration_f = float(duration or 45)
    shifted = []
    for cap in captions:
        try:
            start = float(cap.get("start", 0)) + offset_f
            end = float(cap.get("end", start + 1)) + offset_f
        except Exception:
            continue
        start = max(0.0, start)
        end = min(duration_f, max(start + 0.8, end))
        if start >= duration_f:
            continue
        shifted.append({"start": start, "end": end, "text": cap.get("text", "")})
    return shifted


def normalize_caption_times(captions: Any, duration: float) -> List[Dict[str, Any]]:
    if not isinstance(captions, list) or not captions:
        return default_edit_plan()["captions"]
    normalized = []
    fallback_step = max(2.5, float(duration or 45) / max(len(captions), 1))
    for index, cap in enumerate(captions):
        if isinstance(cap, dict):
            text = cap.get("text", "")
            start = cap.get("start", index * fallback_step)
            end = cap.get("end", min(float(duration or 45), (index + 1) * fallback_step))
        else:
            text = str(cap)
            start = index * fallback_step
            end = min(float(duration or 45), (index + 1) * fallback_step)
        try:
            start_f = max(0.0, float(start))
            end_f = min(float(duration or 45), max(start_f + 1.0, float(end)))
        except Exception:
            start_f = index * fallback_step
            end_f = min(float(duration or 45), (index + 1) * fallback_step)
        normalized.append({"start": start_f, "end": end_f, "text": text})
    return normalized


def ass_time(seconds: Any) -> str:
    seconds = max(0.0, float(seconds or 0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return re.sub(r"[\r\n{}]", " ", text).replace("\\", " ")


def wrap_caption_text(text: Any, fallback: str = "", max_chars: int = 18, max_lines: int = 2) -> str:
    raw = ass_escape(str(text or fallback)).strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)[:90]
    if " " not in raw and len(raw) > max_chars:
        chunks = [raw[i : i + max_chars] for i in range(0, len(raw), max_chars)]
        return r"\N".join(chunks[:max_lines])

    words = raw.split(" ")
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".。") + "..."
    return r"\N".join(lines[:max_lines])


KEYWORD_PATTERNS = [
    "顯示者",
    "生產者",
    "投射者",
    "反映者",
    "喉嚨中心",
    "薦骨",
    "情緒中心",
    "開放中心",
    "閘門",
    "流日",
    "壓力",
    "決定",
    "行動",
    "等待回應",
    "告知",
    "邀請",
    "直覺",
    "恐懼",
    "能量",
    "Manifestor",
    "Generator",
    "Projector",
    "Reflector",
    "throat center",
    "pressure",
    "decision",
    "energy",
]


def extract_keyword(text: Any) -> str:
    raw = compact_caption_text(str(text or ""))
    for keyword in KEYWORD_PATTERNS:
        if keyword.lower() in raw.lower():
            return keyword
    cleaned = re.sub(r"[，。！？、,.!?|｜:：]", " ", raw).strip()
    parts = [part for part in cleaned.split() if part]
    if parts:
        return max(parts, key=len)[:12]
    return cleaned[:8]


def caption_style_lines(style_name: str) -> List[str]:
    style_name = style_name or "Viral Pop"
    if style_name == "Viral Pop":
        return [
            "Style: Hook,Arial Unicode MS,66,&H00FFFFFF,&H000000FF,&H00000000,&HDD111111,1,0,0,0,100,100,0,0,1,6,3,8,70,70,135,1",
            "Style: CapA,Arial Unicode MS,72,&H0000FFFF,&H000000FF,&H00000000,&HE0000000,1,0,0,0,100,100,0,0,1,8,4,2,58,58,455,1",
            "Style: CapB,Arial Unicode MS,72,&H00FFFFFF,&H000000FF,&H00005CFF,&HE0000000,1,0,0,0,100,100,0,0,1,8,4,2,58,58,455,1",
            "Style: Pop,Arial Unicode MS,86,&H0000FFFF,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,8,4,5,56,56,0,1",
            "Style: CTA,Arial Unicode MS,54,&H0000FFFF,&H000000FF,&H00000000,&HDD000000,1,0,0,0,100,100,0,0,1,6,3,2,72,72,195,1",
        ]
    if style_name == "Neon Cyan":
        return [
            "Style: Hook,Arial Unicode MS,60,&H00FFFFFF,&H000000FF,&H00FFAA00,&HAA000000,1,0,0,0,100,100,0,0,1,5,2,8,78,78,155,1",
            "Style: CapA,Arial Unicode MS,66,&H00FFFFFF,&H000000FF,&H00FFAA00,&HCC000000,1,0,0,0,100,100,0,0,1,7,3,2,66,66,450,1",
            "Style: CapB,Arial Unicode MS,66,&H00FFFF00,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,7,3,2,66,66,450,1",
            "Style: Pop,Arial Unicode MS,78,&H00FFFF00,&H000000FF,&H00FFAA00,&HCC000000,1,0,0,0,100,100,0,0,1,7,3,5,56,56,0,1",
            "Style: CTA,Arial Unicode MS,52,&H0000FFFF,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,5,2,2,88,88,205,1",
        ]
    if style_name == "Clean White":
        return [
            "Style: Hook,Arial Unicode MS,58,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,4,1,8,86,86,165,1",
            "Style: CapA,Arial Unicode MS,64,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,6,2,2,70,70,450,1",
            "Style: CapB,Arial Unicode MS,64,&H00EAEAEA,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,6,2,2,70,70,450,1",
            "Style: Pop,Arial Unicode MS,72,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,5,2,5,56,56,0,1",
            "Style: CTA,Arial Unicode MS,50,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,4,1,2,92,92,205,1",
        ]
    return [
        "Style: Hook,Arial Unicode MS,60,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,5,2,8,78,78,155,1",
        "Style: CapA,Arial Unicode MS,66,&H00FFFFFF,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,7,3,2,66,66,450,1",
        "Style: CapB,Arial Unicode MS,66,&H0000FFFF,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,7,3,2,66,66,450,1",
        "Style: Pop,Arial Unicode MS,76,&H0000FFFF,&H000000FF,&H00000000,&HCC000000,1,0,0,0,100,100,0,0,1,6,3,5,56,56,0,1",
        "Style: CTA,Arial Unicode MS,52,&H0000FFFF,&H000000FF,&H00000000,&HAA000000,1,0,0,0,100,100,0,0,1,5,2,2,88,88,205,1",
    ]


def caption_effect(style_name: str, index: int) -> str:
    if style_name == "Viral Pop":
        if index % 2:
            return r"{\fad(40,70)\t(0,120,\fscx118\fscy118)\t(120,260,\fscx100\fscy100)\t(0,220,\frz-1)}"
        return r"{\fad(40,70)\t(0,120,\fscx116\fscy116)\t(120,260,\fscx100\fscy100)\t(0,220,\frz1)}"
    if style_name == "Clean White":
        return r"{\fad(90,90)\t(0,220,\fscx103\fscy103)}"
    if style_name == "Neon Cyan":
        return r"{\fad(70,90)\t(0,180,\fscx112\fscy112)\t(180,320,\fscx100\fscy100)}"
    if index % 2:
        return r"{\fad(60,80)\t(0,160,\fscx112\fscy112)\t(160,320,\fscx100\fscy100)}"
    return r"{\fad(60,80)\t(0,180,\fscx108\fscy108)}"


def pop_effect(index: int) -> str:
    angle = -2 if index % 2 else 2
    return rf"{{\pos(540,835)\fad(40,120)\t(0,110,\fscx132\fscy132)\t(110,260,\fscx100\fscy100)\t(0,220,\frz{angle})}}"


def build_ass(edit_plan: Dict[str, Any], duration: float) -> str:
    captions = edit_plan.get("captions") or default_edit_plan()["captions"]
    style_name = edit_plan.get("captionStyle", "Kinetic Yellow")
    try:
        hook_start = max(0.0, float(edit_plan.get("captionOffset", 0) or 0))
    except Exception:
        hook_start = 0.0
    hook_end = min(float(duration or 45), hook_start + 4)
    last_caption_start = max((float(cap.get("start", 0)) for cap in captions), default=0.0)
    cta_start = min(max(float(duration or 45) - 10, last_caption_start), max(0.0, float(duration or 45) - 1))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *caption_style_lines(style_name),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{ass_time(hook_start)},{ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(120,120)\\t(0,250,\\fscx106\\fscy106)}}{wrap_caption_text(edit_plan.get('hookText'), max_chars=16)}",
    ]
    for index, cap in enumerate(captions):
        style = "CapB" if index % 2 else "CapA"
        caption_start = float(cap.get("start", 0) or 0)
        caption_end = float(cap.get("end", 0) or 0)
        keyword = extract_keyword(cap.get("text", ""))
        if edit_plan.get("keywordPopups", True) and keyword and caption_end > caption_start:
            pop_end = min(caption_end, caption_start + 0.95)
            lines.append(
                f"Dialogue: 1,{ass_time(caption_start)},{ass_time(pop_end)},Pop,,0,0,0,,{pop_effect(index)}{wrap_caption_text(keyword, max_chars=8, max_lines=1)}"
            )
        lines.append(
            f"Dialogue: 0,{ass_time(caption_start)},{ass_time(caption_end)},{style},,0,0,0,,{caption_effect(style_name, index)}{wrap_caption_text(cap.get('text'), max_chars=18)}"
        )
    lines.append(
        f"Dialogue: 0,{ass_time(cta_start)},{ass_time(duration)},CTA,,0,0,0,,{{\\fad(120,120)\\t(0,300,\\fscx104\\fscy104)}}{wrap_caption_text(edit_plan.get('ctaText'), max_chars=18)}"
    )
    return "\n".join(lines)


def scaled_edit_plan_timing(edit_plan: Dict[str, Any], speed: float) -> Dict[str, Any]:
    try:
        speed_f = max(0.5, min(1.25, float(speed or 1.0)))
    except Exception:
        speed_f = 1.0
    if abs(speed_f - 1.0) < 0.001:
        return edit_plan
    scaled = dict(edit_plan)
    scaled["captionOffset"] = float(edit_plan.get("captionOffset", 0) or 0) / speed_f
    scaled["captions"] = [
        {
            "start": float(cap.get("start", 0) or 0) / speed_f,
            "end": float(cap.get("end", 0) or 0) / speed_f,
            "text": cap.get("text", ""),
        }
        for cap in edit_plan.get("captions", [])
    ]
    return scaled


def motion_graphics_filters(mode: str, duration: float) -> List[str]:
    mode = mode or "Lively"
    filters = [
        "drawbox=x=0:y=0:w=1080:h=250:color=black@0.30:t=fill",
        "drawbox=x=0:y=1195:w=1080:h=390:color=black@0.24:t=fill",
        "drawbox=x=18:y=18:w=1044:h=1884:color=yellow@0.28:t=8:enable='between(mod(t\\,6)\\,0\\,2)'",
        "drawbox=x=18:y=18:w=1044:h=1884:color=cyan@0.30:t=8:enable='between(mod(t\\,6)\\,2\\,4)'",
        "drawbox=x=18:y=18:w=1044:h=1884:color=white@0.24:t=8:enable='between(mod(t\\,6)\\,4\\,6)'",
        "drawbox=x=0:y=0:w='22+8*sin(t*5)':h=1920:color=cyan@0.62:t=fill",
        "drawbox=x='1058-8*sin(t*5)':y=0:w=22:h=1920:color=yellow@0.62:t=fill",
        "drawbox=x='mod(t*700,1360)-220':y=0:w=190:h=1920:color=white@0.10:t=fill",
        "drawbox=x=54:y=278:w='260+80*sin(t*2.4)':h=12:color=yellow@0.92:t=fill",
        "drawbox=x='766-80*sin(t*2.4)':y=278:w=260:h=12:color=cyan@0.92:t=fill",
        "drawbox=x='300+30*sin(t*2.2)':y=88:w=480:h=82:color=black@0.68:t=fill",
        "drawbox=x='300+30*sin(t*2.2)':y=88:w=480:h=82:color=yellow@0.80:t=4:enable='between(mod(t\\,4)\\,0\\,2)'",
        "drawbox=x='300+30*sin(t*2.2)':y=88:w=480:h=82:color=cyan@0.80:t=4:enable='between(mod(t\\,4)\\,2\\,4)'",
        "drawtext=font='Arial Unicode MS':text='今日類型檢查':x='(w-text_w)/2+30*sin(t*2.2)':y=106:fontsize=38:fontcolor=yellow:borderw=5:bordercolor=black@0.95:shadowx=2:shadowy=2:shadowcolor=cyan@0.55",
        "drawtext=font='Arial Unicode MS':text='留言你的類型':x='w-text_w-96':y=1227:fontsize=30:fontcolor=cyan:borderw=4:bordercolor=black@0.92:shadowx=2:shadowy=2:shadowcolor=yellow@0.50",
    ]
    if mode == "None":
        return filters[:2]
    if mode == "Subtle":
        return filters
    if mode == "Lively":
        return filters + [
            "drawbox=x='52+28*sin(t*3.4)':y=334:w=15:h=210:color=yellow@0.88:t=fill",
            "drawbox=x='1013+28*cos(t*3.1)':y=334:w=15:h=210:color=cyan@0.88:t=fill",
            "drawbox=x=84:y='520+30*sin(t*5.2)':w=34:h=92:color=cyan@0.82:t=fill",
            "drawbox=x=128:y='492+42*cos(t*4.3)':w=34:h=138:color=yellow@0.82:t=fill",
            "drawbox=x=172:y='528+32*sin(t*3.8)':w=34:h=108:color=white@0.62:t=fill",
            "drawbox=x=962:y='520+30*cos(t*5.2)':w=34:h=92:color=yellow@0.82:t=fill",
            "drawbox=x=918:y='492+42*sin(t*4.3)':w=34:h=138:color=cyan@0.82:t=fill",
            "drawbox=x=874:y='528+32*cos(t*3.8)':w=34:h=108:color=white@0.62:t=fill",
            "drawbox=x='80+130*sin(t*1.7)':y=1488:w=250:h=12:color=cyan@0.82:t=fill",
            "drawbox=x='750+130*cos(t*1.7)':y=1488:w=250:h=12:color=yellow@0.82:t=fill",
            "drawbox=x='mod(t*380,1200)-80':y=306:w=150:h=8:color=white@0.62:t=fill",
            "drawbox=x='1160-mod(t*380,1200)':y=1512:w=150:h=8:color=white@0.52:t=fill",
            "drawbox=x='90+18*sin(t*4)':y=1320:w=22:h=22:color=yellow@0.84:t=fill",
            "drawbox=x='972+18*cos(t*4)':y=1320:w=22:h=22:color=cyan@0.84:t=fill",
        ]
    return filters + [
        "drawbox=x='42+36*sin(t*3.8)':y=312:w=18:h=250:color=yellow@0.92:t=fill",
        "drawbox=x='1020+36*cos(t*3.5)':y=312:w=18:h=250:color=cyan@0.92:t=fill",
        "drawbox=x='mod(t*760,1420)-220':y=248:w=250:h=16:color=yellow@0.88:t=fill",
        "drawbox=x='1340-mod(t*760,1420)':y=1512:w=250:h=16:color=cyan@0.88:t=fill",
        "drawbox=x=40:y='760+110*sin(t*2.4)':w=120:h=12:color=white@0.56:t=fill",
        "drawbox=x=920:y='820+110*cos(t*2.4)':w=120:h=12:color=white@0.56:t=fill",
        "drawbox=x='130+220*sin(t*1.9)*sin(t*1.9)':y=385:w=210:h=9:color=cyan@0.72:t=fill",
        "drawbox=x='740-220*sin(t*1.9)*sin(t*1.9)':y=385:w=210:h=9:color=yellow@0.72:t=fill",
        "drawbox=x=132:y=1415:w='816*sin(t*1.2)*sin(t*1.2)':h=12:color=cyan@0.35:t=fill",
        "drawbox=x='948-816*sin(t*1.2)*sin(t*1.2)':y=1436:w='816*sin(t*1.2)*sin(t*1.2)':h=12:color=yellow@0.35:t=fill",
        "drawbox=x=76:y='515+36*sin(t*6.4)':w=38:h=135:color=yellow@0.90:t=fill",
        "drawbox=x=124:y='485+48*cos(t*5.5)':w=38:h=180:color=cyan@0.90:t=fill",
        "drawbox=x=172:y='520+36*sin(t*4.8)':w=38:h=125:color=white@0.62:t=fill",
        "drawbox=x=966:y='515+36*cos(t*6.4)':w=38:h=135:color=cyan@0.90:t=fill",
        "drawbox=x=918:y='485+48*sin(t*5.5)':w=38:h=180:color=yellow@0.90:t=fill",
        "drawbox=x=870:y='520+36*cos(t*4.8)':w=38:h=125:color=white@0.62:t=fill",
    ]


def progress_bar_filters(duration: float) -> List[str]:
    duration = max(1.0, float(duration or 1.0))
    segments = 32
    bottom_x = 54
    bottom_y = 1848
    bottom_w = 972
    gap = 3
    segment_w = (bottom_w - gap * (segments - 1)) / segments
    filters = [
        f"drawbox=x={bottom_x}:y={bottom_y}:w={bottom_w}:h=34:color=black@0.78:t=fill",
        f"drawbox=x={bottom_x}:y={bottom_y}:w={bottom_w}:h=34:color=white@0.70:t=4",
        "drawbox=x=0:y=0:w=1080:h=12:color=black@0.65:t=fill",
    ]
    for index in range(segments):
        threshold = duration * index / segments
        x = bottom_x + index * (segment_w + gap)
        top_x = index * (1080 / segments)
        color = "yellow@1.00" if index % 2 == 0 else "cyan@1.00"
        filters.append(
            f"drawbox=x={x:.1f}:y={bottom_y}:w={segment_w:.1f}:h=34:color={color}:t=fill:enable='gte(t\\,{threshold:.3f})'"
        )
        filters.append(
            f"drawbox=x={top_x:.1f}:y=0:w={1080 / segments:.1f}:h=12:color=cyan@1.00:t=fill:enable='gte(t\\,{threshold:.3f})'"
        )
    return filters


def render_viral_video(video_path: Path, edit_plan: Dict[str, Any], output_path: Path, duration: float = 45) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not on PATH.")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            speed = max(0.5, min(1.25, float(edit_plan.get("videoSpeed", 1.0) or 1.0)))
        except Exception:
            speed = 1.0
        final_duration = max(1.0, float(duration or 45) / speed)
        ass_plan = scaled_edit_plan_timing(edit_plan, speed)
        ass_path = Path(tmp) / "captions.ass"
        ass_path.write_text(build_ass(ass_plan, final_duration), encoding="utf-8")
        vf_parts = [
            f"setpts=PTS/{speed}",
            "scale=1080:1920:force_original_aspect_ratio=increase",
            "crop=1080:1920,setsar=1",
            "eq=contrast=1.12:saturation=1.18:brightness=0.018",
            "unsharp=5:5:0.95:3:3:0.35",
            "vignette=angle=PI/5:eval=frame",
            *motion_graphics_filters(edit_plan.get("motionGraphics", "Lively"), final_duration),
            f"subtitles={str(ass_path)!r}",
            *progress_bar_filters(final_duration),
        ]
        vf = ",".join(vf_parts)
        af = (
            f"atempo={speed},"
            "highpass=f=75,"
            "acompressor=threshold=-18dB:ratio=2.4:attack=8:release=90:makeup=2,"
            "equalizer=f=3200:t=q:w=1.1:g=2.2,"
            "alimiter=limit=0.96,"
            "loudnorm=I=-15:TP=-1.2:LRA=9"
        )
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-af",
            af,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.run(args, check=True, capture_output=True, text=True)
    return output_path


def save_uploaded(uploaded_file: Any, output_path: Path) -> Path:
    output_path.write_bytes(uploaded_file.getbuffer())
    return output_path


def b64_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
