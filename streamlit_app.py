import json
import os
import tempfile
import inspect
from datetime import date
from pathlib import Path

import streamlit as st

from viral_video_agent import (
    b64_json,
    build_edit_plan_from_script,
    clean_script_fields,
    download_drive_file,
    fallback_script,
    fetch_thd_transit,
    ffprobe_duration,
    generate_viral_script,
    render_viral_video,
    save_uploaded,
    script_to_caption_text,
    srt_to_vtt,
    translate_srt_to_zh_hant_taiwan,
    transcribe_video_to_srt,
)


st.set_page_config(page_title="THD Viral Video Agent", page_icon="HD", layout="wide")

st.title("THD Viral Video Agent")
st.caption("Generate and approve the viral Human Design script first, make the HeyGen video manually, then render a script-matched FFmpeg viral edit.")


def setting(name: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or os.getenv(name, default) or "")


def thd_sun_earth_summary(transit: dict) -> dict:
    data = transit.get("data") if isinstance(transit, dict) else None
    first_row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    planets = first_row.get("planets", {}) if isinstance(first_row, dict) else {}
    sun = planets.get("Sun", {}) if isinstance(planets, dict) else {}
    earth = planets.get("Earth", {}) if isinstance(planets, dict) else {}
    return {
        "datetime": first_row.get("datetime"),
        "sun_gate": sun.get("gate"),
        "sun_line": sun.get("line"),
        "earth_gate": earth.get("gate"),
        "earth_line": earth.get("line"),
    }


def script_matches_transit_and_lens(payload: dict, transit: dict, focus_lens: str) -> bool:
    summary = thd_sun_earth_summary(transit)
    text = "\n".join(
        str(payload.get(field, ""))
        for field in ("title", "title_zh_hant", "hook", "hook_zh_hant", "script", "script_zh_hant")
    )
    for value in (summary.get("sun_gate"), summary.get("earth_gate")):
        if value is not None and str(value) not in text:
            return False
    return focus_lens in text


def generate_script_with_lens(transit, gemini_key, gemini_model, lens_context, language_mode, focus_lens):
    params = inspect.signature(generate_viral_script).parameters
    if "focus_lens" in params:
        return generate_viral_script(
            transit,
            gemini_key,
            gemini_model,
            lens_context,
            language_mode,
            focus_lens=focus_lens,
        )
    return generate_viral_script(transit, gemini_key, gemini_model, lens_context, language_mode)


with st.sidebar:
    st.header("Keys")
    saved_gemini_key = setting("GEMINI_API_KEY")
    saved_thd_key = setting("THD_API_KEY")
    if saved_gemini_key:
        st.caption("Gemini API key is configured securely on the server.")
    gemini_key_input = st.text_input("Gemini API key override", value="", type="password", placeholder="Leave blank to use server secret")
    gemini_key = gemini_key_input.strip() or saved_gemini_key
    gemini_model = st.text_input("Gemini model", value=setting("GEMINI_MODEL", "gemini-2.5-flash"))
    if saved_thd_key:
        st.caption("THD API key is configured securely on the server.")
    thd_key_input = st.text_input("THD API key override", value="", type="password", placeholder="Leave blank to use server secret")
    thd_key = thd_key_input.strip() or saved_thd_key
    thd_base = st.text_input("THD base URL", value=setting("THD_BASE_URL", "https://api.totalhumandesign.com"))
    google_client_id = setting("GOOGLE_CLIENT_ID")
    google_client_secret = setting("GOOGLE_CLIENT_SECRET")
    if google_client_id and google_client_secret:
        st.caption("Google OAuth settings are saved locally. This app currently uses public Drive links or direct uploads for video intake.")
    language_mode = st.selectbox("Output language", ["Bilingual", "Traditional Chinese", "English"], index=0)

script_tab, heygen_tab, edit_tab = st.tabs(["1. Script + Virality Check", "2. HeyGen Manual Step", "3. FFmpeg Viral Edit"])

with script_tab:
    st.subheader("Create today's viral Human Design script")
    creator_context = st.text_area(
        "Audience/context",
        "Audience: Human Design and astrology beginners on TikTok, Instagram Reels, and YouTube Shorts.",
        height=80,
    )
    selected_transit_date = st.date_input(
        "THD transit date",
        value=date.today(),
        min_value=date.today(),
        help="Required. Choose today or a future date. This date is sent to THD and used for script generation.",
    )
    focus_lens = st.selectbox(
        "主題視角（關於）",
        ["關係", "環境", "制約", "溝通"],
        index=0,
        help="Required. The daily transit script will be written through this lens for each Energy Type.",
    )
    col_a, col_b = st.columns(2)
    with col_a:
        fetch = st.button("Fetch THD transit and generate script", type="primary")
    with col_b:
        use_fallback = st.button("Use fallback sample script")

    verified_facts = st.text_area(
        "Corrected / verified transit facts",
        height=120,
        placeholder=(
            "Example: 太陽落在18號閘門4爻，是找出錯誤的閘門；"
            "地球落在17號閘門4爻，讓他人比較願意聽看看下位者的意見。"
        ),
        help="Use this when the API result is empty or you want to override/ground the AI with verified Sun/Earth gate-line facts.",
    )
    transit_json = st.text_area("Optional: paste THD transit JSON manually", height=180)

    if fetch:
        try:
            if transit_json.strip():
                transit = json.loads(transit_json)
            else:
                if not selected_transit_date:
                    st.error("Please choose a THD transit date before generating the script.")
                    st.stop()
                transit = fetch_thd_transit(thd_key, thd_base, selected_transit_date)
            st.session_state["last_thd_transit"] = transit
            if verified_facts.strip():
                transit["_verified_transit_facts"] = verified_facts.strip()
            lens_context = (
                f"{creator_context}\n\n"
                f"Selected required topic lens: {focus_lens}. "
                "Use daily transits through this lens for each Energy Type, especially relational dynamics and interpersonal connections when the lens is 關係."
            )
            script_payload = generate_script_with_lens(transit, gemini_key, gemini_model, lens_context, language_mode, focus_lens)
            if not script_matches_transit_and_lens(script_payload, transit, focus_lens):
                raise ValueError(
                    "Generated script did not clearly match the returned THD Sun/Earth data and selected dropdown lens. "
                    "Please wait for Streamlit to finish redeploying the latest version, then generate again."
                )
            st.session_state["script_payload"] = script_payload
        except Exception as exc:
            st.error(f"Script generation failed: {exc}")

    if use_fallback:
        st.session_state["script_payload"] = fallback_script()

    last_transit = st.session_state.get("last_thd_transit")
    if last_transit:
        st.markdown("#### THD daily returned data response")
        data = last_transit.get("data") if isinstance(last_transit, dict) else None
        count = len(data) if isinstance(data, list) else "unknown"
        if isinstance(data, list) and not data:
            st.warning("THD returned `data: []` for the daily transit request.")
        else:
            st.success(f"THD returned daily transit data count: {count}")
            first_row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
            planets = first_row.get("planets", {}) if isinstance(first_row, dict) else {}
            sun = planets.get("Sun", {}) if isinstance(planets, dict) else {}
            earth = planets.get("Earth", {}) if isinstance(planets, dict) else {}
            if sun or earth:
                st.write(
                    {
                        "datetime": first_row.get("datetime"),
                        "Sun": {"gate": sun.get("gate"), "line": sun.get("line"), "sign": sun.get("sign")},
                        "Earth": {"gate": earth.get("gate"), "line": earth.get("line"), "sign": earth.get("sign")},
                    }
                )
        with st.expander("Raw THD meta and data used for this script", expanded=True):
            if isinstance(last_transit, dict):
                st.markdown("##### meta")
                st.json(last_transit.get("meta", {}))
                st.markdown("##### data")
                st.json(last_transit.get("data", []))
            else:
                st.json(last_transit)

    payload = st.session_state.get("script_payload")
    if payload:
        payload = clean_script_fields(payload)
        st.session_state["script_payload"] = payload
        st.markdown("#### Script")
        st.code(payload.get("script", ""), language="text")
        if payload.get("script_zh_hant"):
            st.markdown("#### 繁體中文腳本")
            st.code(payload.get("script_zh_hant", ""), language="text")
        st.markdown("#### Hook")
        st.write(payload.get("hook", ""))
        if payload.get("hook_zh_hant"):
            st.markdown("#### 繁體中文 Hook")
            st.write(payload.get("hook_zh_hant", ""))
        st.markdown("#### Hashtags")
        all_hashtags = []
        for tag in (payload.get("hashtags", []) or []) + (payload.get("hashtags_zh_hant", []) or []):
            if tag and tag not in all_hashtags:
                all_hashtags.append(tag)
        st.write(" ".join(all_hashtags))
        if payload.get("virality_check"):
            st.markdown("#### Virality check before HeyGen")
            st.json(payload.get("virality_check"))
        if payload.get("avatar_motion_cues"):
            st.markdown("#### HeyGen avatar motion cues")
            st.json(payload.get("avatar_motion_cues"))
        st.download_button(
            "Download script JSON",
            data=b64_json(payload),
            file_name="thd-viral-script.json",
            mime="application/json",
        )

with heygen_tab:
    st.subheader("Manual HeyGen step")
    st.write("Use the generated script in HeyGen Creator manually. Export the finished MP4, upload it to Google Drive, set sharing to anyone with the link can view, then paste the link in the next tab.")
    payload = st.session_state.get("script_payload") or fallback_script()
    payload = clean_script_fields(payload)
    if "script_payload" in st.session_state:
        st.session_state["script_payload"] = payload
    heygen_script = payload.get("script") if language_mode == "English" else payload.get("script_zh_hant")
    heygen_script = heygen_script or payload.get("script") or payload.get("script_zh_hant", "")
    st.markdown("#### Script to paste into HeyGen")
    st.code(heygen_script, language="text")
    if language_mode == "Bilingual" and payload.get("script"):
        st.markdown("#### English reference")
        st.code(payload.get("script", ""), language="text")
    st.markdown("#### HeyGen checklist")
    for step in payload.get("heygen_manual_steps", []):
        st.checkbox(step, value=False)
    motion_cues = payload.get("avatar_motion_cues", [])
    if motion_cues:
        st.markdown("#### Avatar motion to apply while speaking")
        for cue in motion_cues:
            label = (
                f"{cue.get('start', '?')}-{cue.get('end', '?')}s | "
                f"{cue.get('line_or_beat', 'Beat')}: "
                f"{cue.get('expression', '')}; {cue.get('gesture', '')}; "
                f"{cue.get('delivery', '')}"
            )
            st.checkbox(label, value=False)

with edit_tab:
    st.subheader("Render uploaded HeyGen video with FFmpeg")
    st.write("Use either a Google Drive share link or upload the MP4 directly.")
    st.info("This step does not rewrite or re-audit the script. It uses the approved Script tab captions exactly, then adds professional short-form treatment: vertical crop, contrast/sharpening, animated-style captions, hook and CTA overlays, dark readability bands, progress bar, and loudness normalization.")
    drive_link = st.text_input("Google Drive video share link or file ID")
    uploaded = st.file_uploader("Or upload HeyGen MP4", type=["mp4", "mov", "m4v"])
    payload = st.session_state.get("script_payload")
    duration = st.number_input("Target duration seconds", min_value=10, max_value=120, value=45)
    use_video_duration = st.checkbox(
        "Use uploaded video duration for caption sync",
        value=True,
        help="Recommended. This prevents captions from finishing too early when the HeyGen video is longer than the estimated script duration.",
    )
    caption_offset = st.slider(
        "Caption delay/sync seconds",
        min_value=-5.0,
        max_value=15.0,
        value=0.0,
        step=0.25,
        help="If captions appear before the avatar says the words, increase this. If captions are late, use a negative value.",
    )
    caption_style = st.selectbox(
        "Caption animation style",
        ["Viral Pop", "Kinetic Yellow", "Neon Cyan", "Clean White"],
        index=0,
        help="All styles are rendered locally with FFmpeg/ASS. Viral Pop is the most energetic short-form style.",
    )
    video_speed = st.slider(
        "Video pacing boost",
        min_value=0.95,
        max_value=1.12,
        value=1.03,
        step=0.01,
        help="Slightly speeds up video and audio together for tighter short-form pacing. Keep near 1.00 if lip sync feels too fast.",
    )
    audio_style = st.selectbox(
        "Sound polish",
        ["Viral Voice"],
        index=0,
        help="Applies voice cleanup, compression, EQ presence boost, limiter, and loudness normalization.",
    )
    motion_graphics = st.selectbox(
        "Motion graphics pack",
        ["Lively", "Energetic", "Subtle", "None"],
        index=0,
        help="Adds animated frame accents, moving light sweeps, progress rails, and lower-third motion without changing caption sync.",
    )
    auto_transcribe = st.checkbox(
        "Auto-generate SRT captions from uploaded HeyGen MP4",
        value=True,
        help="Uses local faster-whisper to listen to the uploaded video and create timed captions automatically.",
    )
    whisper_model_size = st.selectbox(
        "Auto-caption model",
        ["small", "base", "tiny", "medium"],
        index=0,
        help="Small is the recommended balance. Medium is slower but may be more accurate.",
    )
    whisper_language = st.selectbox(
        "Auto-caption spoken language",
        ["zh", "auto", "en"],
        index=0,
        help="Use zh for Chinese narration. Captions can then be converted to Traditional Chinese Taiwan wording.",
    )
    convert_to_zh_tw = st.checkbox(
        "Convert captions to Traditional Chinese (Taiwan)",
        value=True,
        help="Uses Gemini to convert generated captions into natural Traditional Chinese for Taiwan viewers before rendering.",
    )
    if payload:
        payload = clean_script_fields(payload)
        st.session_state["script_payload"] = payload
        caption_text = ""
        if not auto_transcribe:
            default_caption_text = script_to_caption_text(payload, language_mode)
            st.markdown("#### Exact captions/transcript spoken in the uploaded HeyGen video")
            caption_text = st.text_area(
                "Paste the exact words from the HeyGen video here",
                value=default_caption_text,
                height=180,
                help=(
                    "For the best match, paste the same text you used in HeyGen. "
                    "Use one caption per line, or paste timestamps like 0-3 Do not decide yet / 00:03-00:07 The transit is pressurizing you."
                ),
            )
        subtitle_file = st.file_uploader(
            "Best sync: upload timed subtitles from HeyGen or a free transcriber (.srt or .vtt)",
            type=["srt", "vtt"],
            key="subtitle_file",
            help="If uploaded, this overrides the plain transcript box and uses exact subtitle timestamps for sync.",
        )
        st.caption("For exact sync, use SRT/VTT. Plain pasted transcript cannot know HeyGen pauses, so it can only estimate timing across the video.")
        subtitle_text = ""
        if subtitle_file:
            subtitle_text = subtitle_file.getvalue().decode("utf-8-sig", errors="replace")
            st.success("Timed subtitle file loaded. FFmpeg will follow these timestamps exactly.")
        caption_source_text = subtitle_text or caption_text
        if auto_transcribe and not subtitle_text:
            st.caption("Auto-caption is on. Upload/paste only the HeyGen video, and the app will generate Traditional Chinese timed captions before rendering.")
        else:
            st.caption("Accepted: SRT/VTT files, plain lines split across the video, or timestamped lines like `0-3 text`, `00:03-00:07 text`, or `00:00:03 --> 00:00:07 text`.")
            edit_plan_preview = build_edit_plan_from_script(payload, language_mode, float(duration), caption_source_text, caption_style, caption_offset, video_speed, audio_style, False, motion_graphics)
            st.markdown("#### Parsed caption plan used by FFmpeg")
            st.json(edit_plan_preview)
    else:
        st.warning("Generate and approve the script in Tab 1 before rendering, so captions match the HeyGen video.")

    if st.button("Render script-matched FFmpeg viral edit", type="primary"):
        try:
            if not payload:
                st.error("Please generate the script in Tab 1 first. The edit step needs that approved script to keep captions matched.")
                st.stop()
            workdir = Path(tempfile.mkdtemp(prefix="thd-video-agent-"))
            input_path = workdir / "heygen-input.mp4"
            if uploaded:
                save_uploaded(uploaded, input_path)
            elif drive_link.strip():
                download_drive_file(drive_link, input_path)
            else:
                st.error("Upload a video or paste a Google Drive share link first.")
                st.stop()

            video_duration = ffprobe_duration(input_path)
            render_duration = video_duration if (use_video_duration and video_duration) else float(duration)
            if video_duration:
                st.info(f"Detected uploaded video duration: {video_duration:.2f}s. Rendering captions across {render_duration:.2f}s.")
            else:
                st.warning("Could not detect video duration, so the target duration setting will be used.")

            generated_srt = ""
            if auto_transcribe and not subtitle_text:
                with st.spinner("Generating timed SRT captions from the uploaded HeyGen video audio..."):
                    generated_srt = transcribe_video_to_srt(
                        input_path,
                        model_size=whisper_model_size,
                        language=None if whisper_language == "auto" else whisper_language,
                    )
                if generated_srt.strip():
                    if convert_to_zh_tw and gemini_key:
                        with st.spinner("Converting generated captions to Traditional Chinese (Taiwan)..."):
                            generated_srt = translate_srt_to_zh_hant_taiwan(generated_srt, gemini_key, gemini_model)
                    elif convert_to_zh_tw and not gemini_key:
                        st.warning("Gemini API key is missing, so auto captions were not converted to Traditional Chinese Taiwan wording.")
                    st.session_state["last_generated_srt"] = generated_srt
                    st.success("Generated timed SRT captions from the uploaded video.")
                else:
                    st.warning("Auto-captioning returned no captions, so the pasted transcript will be used.")

            if generated_srt:
                caption_source_text = generated_srt

            if subtitle_text and convert_to_zh_tw and gemini_key:
                with st.spinner("Converting uploaded subtitles to Traditional Chinese (Taiwan)..."):
                    caption_source_text = translate_srt_to_zh_hant_taiwan(caption_source_text, gemini_key, gemini_model)

            edit_plan = build_edit_plan_from_script(payload, language_mode, float(render_duration), caption_source_text, caption_style, caption_offset, video_speed, audio_style, False, motion_graphics)
            st.session_state["last_edit_plan"] = edit_plan
            with st.spinner("Rendering script-matched captioned viral edit with FFmpeg..."):
                output_path = workdir / "heygen-viral-edit.mp4"
                render_viral_video(input_path, edit_plan, output_path, float(render_duration))
            st.success("Rendered viral edit.")
            st.video(str(output_path))
            st.download_button(
                "Download viral edit MP4",
                data=output_path.read_bytes(),
                file_name="heygen-viral-edit.mp4",
                mime="video/mp4",
            )
            if generated_srt:
                st.download_button(
                    "Download generated SRT",
                    data=generated_srt.encode("utf-8"),
                    file_name="heygen-auto-captions.srt",
                    mime="text/plain",
                )
                st.download_button(
                    "Download generated VTT",
                    data=srt_to_vtt(generated_srt).encode("utf-8"),
                    file_name="heygen-auto-captions.vtt",
                    mime="text/vtt",
                )
            st.caption("Direct Google Drive upload is not enabled yet because it requires a full OAuth token flow. For now, download this MP4 here and upload it to Drive.")
            st.download_button(
                "Download FFmpeg edit plan JSON",
                data=b64_json(edit_plan),
                file_name="ffmpeg-script-matched-edit-plan.json",
                mime="application/json",
            )
        except Exception as exc:
            st.error(f"Render failed: {exc}")
