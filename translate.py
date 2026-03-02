#!/usr/bin/env python3
import argparse, json, sys, uuid
from pathlib import Path
from datetime import timedelta
from typing import List, Dict, Any
import threading

# Production: Robust imports and dependency checks
import subprocess
def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
for pkg in ["srt", "tqdm", "faster-whisper", "transformers", "sentencepiece"]:
    try:
        __import__(pkg if pkg != "faster-whisper" else "faster_whisper")
    except ImportError:
        print(f"[INFO] Installing missing package: {pkg}")
        install(pkg)

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

import srt
from tqdm import tqdm
from faster_whisper import WhisperModel
from transformers import MarianMTModel, MarianTokenizer

SUPPORTED = {
    "ar": "Helsinki-NLP/opus-mt-ar-en",
    "ru": "Helsinki-NLP/opus-mt-ru-en",
    "zh": "Helsinki-NLP/opus-mt-zh-en",  # works for simplified/traditional
}

def load_mt(src_lang: str):
    """
    Loads the MarianMT translation model and tokenizer for the given language.
    """
    if src_lang not in SUPPORTED:
        raise ValueError(f"No MT model configured for '{src_lang}'. Supported: {list(SUPPORTED)}")
    model_name = SUPPORTED[src_lang]
    try:
        tok = MarianTokenizer.from_pretrained(model_name)
        mdl = MarianMTModel.from_pretrained(model_name)
    except Exception as e:
        logging.error("You need to install 'sentencepiece' for this translation model. Run: pip install sentencepiece")
        raise
    return tok, mdl

def translate_chunks(chunks: List[str], tok, mdl, max_len=512) -> List[str]:
    outputs = []
    total = len(chunks)
    logging.info(f"Starting translation of {total} segments...")
    for idx, ch in enumerate(tqdm(chunks, desc="[TRANSLATING SEGMENTS]", unit="segment", ncols=80)):
        try:
            logging.info(f"Translating segment {idx+1}/{total}")
            inp = tok(ch, return_tensors="pt", truncation=True, max_length=max_len)
            gen = mdl.generate(**inp, max_new_tokens=512)
            translation = tok.batch_decode(gen, skip_special_tokens=True)[0]
            outputs.append(translation)
            logging.info(f"Original: {ch[:60]}{'...' if len(ch)>60 else ''}")
            logging.info(f"English : {translation[:60]}{'...' if len(translation)>60 else ''}")
        except Exception as e:
            logging.error(f"Translation failed for segment {idx+1}: {e}")
            outputs.append("")
    logging.info("Translation complete.")
    return outputs

def translate_chunks_parallel(chunks: List[str], tok, mdl, max_len=512, num_beams=5) -> List[str]:
    # Parallel translation using threads and beam search for accuracy
    import concurrent.futures
    def translate_one(ch):
        inp = tok.prepare_seq2seq_batch([ch], return_tensors="pt", truncation=True, max_length=max_len)
        gen = mdl.generate(**inp, max_new_tokens=512, num_beams=num_beams, early_stopping=True)
        out = tok.batch_decode(gen, skip_special_tokens=True)[0]
        # Post-process: remove extra spaces, fix punctuation spacing
        return " ".join(out.split())
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return list(executor.map(translate_one, chunks))

def secs_to_td(t: float) -> timedelta:
    return timedelta(seconds=max(0.0, t))

def write_srt(segments: List[Dict[str, Any]], out_path: Path, use_translation=False):
    try:
        items = []
        for i, seg in enumerate(segments, 1):
            text = seg["translation"] if (use_translation and seg.get("translation")) else seg["text"]
            items.append(srt.Subtitle(
                index=i,
                start=secs_to_td(seg["start"]),
                end=secs_to_td(seg["end"]),
                content=text.strip()
            ))
        out_path.write_text(srt.compose(items), encoding="utf-8")
        logging.info(f"SRT file written: {out_path}")
    except Exception as e:
        logging.error(f"Failed to write SRT file: {e}")

def write_text(filepath, text, label):
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(f"{label}:\n{text}\n\n")

def write_combined_transcript_multithread(transcript_path, transcript_text, english_translation, lang_label):
    # Clear file before writing
    open(transcript_path, "w", encoding='utf-8').close()
    threads = []
    threads.append(threading.Thread(target=write_text, args=(transcript_path, transcript_text, f"Original {lang_label}")))
    threads.append(threading.Thread(target=write_text, args=(transcript_path, english_translation or "", "English Translation")))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

def transcribe(
    audio_path: str,
    model_size: str = "medium",
    device: str = "cpu",
    compute_type: str = "float32",
    beam_size: int = 5,
    vad_filter: bool = True
) -> Dict[str, Any]:
    try:
        logging.info(f"Loading WhisperModel (size={model_size}, device={device}, compute_type={compute_type})...")
        model = WhisperModel(model_size, device="cpu", compute_type="float32")
        logging.info(f"Transcribing audio: {audio_path}")
        segments, info = model.transcribe(
            audio_path,
            language=None,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=False
        )
        segments_list = list(segments)  # Fix tqdm progress bar
        segs, full_text = [], []
        total = len(segments_list)
        logging.info(f"Processing {total} segments...")
        for idx, seg in enumerate(tqdm(segments_list, desc="[TRANSCRIBING SEGMENTS]", unit="segment", ncols=80)):
            logging.info(f"Segment {idx+1}/{total}: [{seg.start:.2f}s - {seg.end:.2f}s] {seg.text[:60]}{'...' if len(seg.text)>60 else ''}")
            segs.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()})
            full_text.append(seg.text.strip())
        logging.info(f"Transcription complete. Detected language: {info.language} (probability={info.language_probability:.3f})")
        return {
            "id": str(uuid.uuid4()),
            "detected_language": info.language,
            "language_probability": float(info.language_probability or 0.0),
            "segments": segs,
            "text": " ".join(full_text).strip(),
            "duration": getattr(info, "duration", None)
        }
    except Exception as e:
        logging.error(f"Transcription failed: {e}")
        return {}

def main():
    ap = argparse.ArgumentParser(description="Transcribe & translate Arabic/Russian/Chinese audio to English.")
    ap.add_argument("input", help="Path to audio file or directory of files")
    ap.add_argument("--whisper", default="medium", help="faster-whisper model size (tiny/base/small/medium/large-v3)")
    ap.add_argument("--device", default="auto", help="cpu, cuda, or auto")
    ap.add_argument("--compute-type", default="float16", help="float16 (GPU), int8, or float32 (CPU)")
    ap.add_argument("--outdir", default="nlp_out", help="Output directory")
    ap.add_argument("--srt", action="store_true", help="Also write subtitles (.srt) of transcript")
    ap.add_argument("--srt-translated", action="store_true", help="Also write translated subtitles (.en.srt)")
    ap.add_argument("--json", action="store_true", help="Write JSON with segments & translations")
    ap.add_argument("--translate", action="store_true", help="Translate to English (ar/ru/zh)")
    ap.add_argument("--chunk-join", choices=["none","segment","full"], default="segment",
                    help="How to batch text for translation (segment=default).")
    ap.add_argument("--summary", action="store_true", help="Show transcript and translation summary in console")
    ap.add_argument("--show-lang", action="store_true", help="Print detected language and probability for each file")
    ap.add_argument("--save-original", action="store_true", help="Save only the original transcript (no translation)")
    ap.add_argument("--quiet", action="store_true", help="Suppress info logs except errors")
    ap.add_argument("--skip-existing", action="store_true", help="Skip files if output already exists")
    args = ap.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)

    in_path = Path(args.input)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    files: List[Path] = []
    if in_path.is_dir():
        print(f"[INFO] Searching for audio files in directory: {in_path}")
        for ext in (".mp3",".wav",".m4a",".flac",".ogg",".mp4",".mkv",".webm"):
            found = list(in_path.rglob(f"*{ext}"))
            print(f"[INFO] Found {len(found)} files with extension {ext}")
            files.extend(sorted(found))
    elif in_path.is_file():
        print(f"[INFO] Single audio file provided: {in_path}")
        files = [in_path]
    else:
        print(f"[ERROR] Input not found: {in_path}", file=sys.stderr); sys.exit(1)

    for f in tqdm(files, desc="[PROCESSING FILES]", unit="file"):
        base = outdir / f"{f.stem}"
        transcript_path = base.with_suffix(f".{src}.txt") if 'src' in locals() else None
        if args.skip_existing and transcript_path and transcript_path.exists():
            print(f"[INFO] Skipping {f}, output exists: {transcript_path}")
            continue

        print(f"\n[INFO] Processing file: {f}")

        # Step 1: Transcribe and detect language
        res = transcribe(
            str(f),
            model_size=args.whisper,
            device=args.device,
            compute_type=args.compute_type
        )

        src = res["detected_language"]
        transcript_path = base.with_suffix(f".{src}.txt")
        transcript_text = res["text"]

        if args.show_lang:
            print(f"[LANG] {f.name}: {src} (probability={res['language_probability']:.3f})")

        if args.save_original:
            transcript_path.write_text(transcript_text, encoding="utf-8")
            print(f"[INFO] Saved original transcript to: {transcript_path}")
            continue

        # Step 2: Prepare output paths
        translation_success = False
        print(f"[INFO] Preparing translation model for language: {src}")
        src_mt = "zh" if src.startswith("zh") else src
        translation_path = base.with_suffix(".en.txt")
        english_translation = None
        tok = mdl = None
        if src_mt not in SUPPORTED:
            print(f"[WARN] Skipping translation: unsupported language '{src}'.", file=sys.stderr)
        else:
            print(f"[INFO] Loading MT model: {SUPPORTED[src_mt]}")
            tok, mdl = load_mt(src_mt)
            translation_success = True

        # Prepare outputs for JSON/SRT if needed
        json_obj: Dict[str, Any] = {
            "source_file": str(f),
            "language": res["detected_language"],
            "language_probability": res["language_probability"],
            "segments": res["segments"],
            "text": res["text"],
        }

        # Step 4: Translate transcript to English if possible
        if translation_success and tok and mdl:
            print(f"[INFO] Translating transcript to English...")
            if args.chunk_join == "full":
                full_tr = translate_chunks_parallel([res["text"]], tok, mdl, num_beams=8)[0]
                json_obj["translation_full"] = full_tr
                english_translation = full_tr
                for s in json_obj["segments"]:
                    s["translation"] = None
            else:
                texts = [s["text"] for s in json_obj["segments"]] if args.chunk_join == "segment" else []
                if texts:
                    print("[INFO] Translating segments...")
                    translations = list(tqdm(translate_chunks_parallel(texts, tok, mdl, num_beams=8), desc="[TRANSLATING]", unit="segment", ncols=80))
                else:
                    translations = []
                english_translation = " ".join([t for t in translations if t]).strip() if translations else None
                for s, t in zip(json_obj["segments"], translations):
                    s["translation"] = t

        # Step 5: Write combined transcript and translation using threads
        lang_label = src if src not in ["ar", "ru", "zh"] else {"ar": "Arabic", "ru": "Russian", "zh": "Chinese"}[src]
        write_combined_transcript_multithread(transcript_path, transcript_text, english_translation, lang_label)
        print(f"[INFO] Combined transcript and translation saved to: {transcript_path}")

        # Step 6: Write JSON and SRT outputs if requested
        if args.json:
            print(f"[INFO] Writing JSON output: {base.with_suffix('.json')}")
            (base.with_suffix(".json")).write_text(json.dumps(json_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.srt:
            print(f"[INFO] Writing SRT transcript: {base.with_suffix('.srt')}")
            write_srt(json_obj["segments"], base.with_suffix(".srt"), use_translation=False)
        if args.srt_translated and translation_success:
            print(f"[INFO] Writing translated SRT: {base.with_suffix('.en.srt')}")
            write_srt(json_obj["segments"], base.with_suffix(".en.srt"), use_translation=True)

        # Step 7: Console summary (always show English translation if available)
        print("\n--- TRANSCRIPT (first 800 chars) ---")
        print(json_obj["text"][:800] + ("..." if len(json_obj["text"])>800 else ""))
        if translation_success and tok and mdl:
            if "translation_full" in json_obj:
                print("\n--- ENGLISH TRANSLATION (first 800 chars) ---")
                print(json_obj["translation_full"][:800] + ("..." if len(json_obj["translation_full"])>800 else ""))
            else:
                joined = " ".join([s.get("translation","") for s in json_obj["segments"] if s.get("translation")]).strip()
                print("\n--- ENGLISH TRANSLATION (first 800 chars) ---")
                print(joined[:800] + ("..." if len(joined)>800 else ""))

    print("\n[INFO] All files processed.")

if __name__ == "__main__":
    main()
