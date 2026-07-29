#!/usr/bin/env python3
"""
Whisper - Streaming voice dictation using faster-whisper.
Press hotkey to start recording, text appears as you speak.
Press again to stop, full text copied to clipboard.
"""

import argparse
import configparser
import queue
import re
import subprocess
import threading
import signal
import sys
import os
import time
import wave
from pathlib import Path

import numpy as np
from pynput import keyboard

from whisper_online import FasterWhisperASR, OnlineASRProcessor

__version__ = "0.2.0"

CONFIG_PATH = Path.home() / ".config" / "whisper" / "config.ini"
CACHE_DIR = Path.home() / ".cache" / "whisper"
LAST_RECORDING_PATH = CACHE_DIR / "last_recording.wav"
SAMPLE_RATE = 16000
CHUNK_BYTES = 3200  # 100ms of s16le mono @ 16kHz
DEVNULL = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
MAX_TYPED_BUFFER = 4096
MAX_OVERLAP_CHARS = 256

COURTESY_START_RE = re.compile(
    r"^\s*(?:thank you(?: for watching)?|thanks(?: for watching)?)(?:[\s,.;:!?\-]+)",
    re.IGNORECASE,
)
COURTESY_END_RE = re.compile(
    r"(?:[\s,.;:!?\-]+)(?:thank you(?: for watching)?|thanks(?: for watching)?)\s*$",
    re.IGNORECASE,
)
COURTESY_ONLY_RE = re.compile(
    r"^\s*(?:thank you(?: for watching)?|thanks(?: for watching)?)\s*$",
    re.IGNORECASE,
)


def _normalize_for_dedupe(text):
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _trim_boundary_courtesy(text, at_start=False, at_end=False):
    out = text
    if at_start:
        previous = None
        while previous != out:
            previous = out
            out = COURTESY_START_RE.sub("", out)
    if at_end:
        previous = None
        while previous != out:
            previous = out
            out = COURTESY_END_RE.sub("", out)
    if COURTESY_ONLY_RE.match(out or ""):
        return ""
    return out


def _looks_non_english_noise(text):
    stripped = text.strip()
    if not stripped:
        return True

    ascii_letters = sum(ch.isascii() and ch.isalpha() for ch in stripped)
    all_letters = sum(ch.isalpha() for ch in stripped)
    non_ascii = sum((not ch.isascii()) and not ch.isspace() for ch in stripped)

    if re.fullmatch(r"[.,!?;:\-_'\"\s]+", stripped):
        return True

    if non_ascii >= 4 and ascii_letters == 0:
        return True

    if all_letters >= 4 and (ascii_letters / all_letters) < 0.35:
        return True

    return False


def load_config():
    config = configparser.ConfigParser()

    defaults = {
        "model": "large-v3-turbo",
        "device": "cpu",
        "compute_type": "int8",
        "language": "en",
        "english_only": "true",
        "min_chunk_size": "0.3",
        "use_vad": "true",
        "condition_on_previous_text": "false",
        "use_init_prompt": "false",
        "no_speech_threshold": "0.45",
        "log_prob_threshold": "-0.8",
        "compression_ratio_threshold": "2.0",
        "repetition_penalty": "1.05",
        "no_repeat_ngram_size": "3",
        "hallucination_silence_threshold": "0.8",
        "vad_threshold": "0.5",
        "vad_min_speech_ms": "200",
        "vad_min_silence_ms": "250",
        "vad_speech_pad_ms": "180",
        "key": "<alt>+o",
        "notifications": "true",
    }

    if CONFIG_PATH.exists():
        config.read(CONFIG_PATH)

    return {
        "model": config.get("whisper", "model", fallback=defaults["model"]),
        "device": config.get("whisper", "device", fallback=defaults["device"]),
        "compute_type": config.get(
            "whisper", "compute_type", fallback=defaults["compute_type"]
        ),
        "language": config.get("whisper", "language", fallback=defaults["language"]),
        "english_only": config.getboolean("whisper", "english_only", fallback=True),
        "min_chunk_size": config.getfloat(
            "whisper", "min_chunk_size", fallback=float(defaults["min_chunk_size"])
        ),
        "use_vad": config.getboolean("whisper", "use_vad", fallback=True),
        "condition_on_previous_text": config.getboolean(
            "whisper", "condition_on_previous_text", fallback=False
        ),
        "use_init_prompt": config.getboolean(
            "whisper", "use_init_prompt", fallback=False
        ),
        "no_speech_threshold": config.getfloat(
            "whisper", "no_speech_threshold", fallback=0.45
        ),
        "log_prob_threshold": config.getfloat(
            "whisper", "log_prob_threshold", fallback=-0.8
        ),
        "compression_ratio_threshold": config.getfloat(
            "whisper", "compression_ratio_threshold", fallback=2.0
        ),
        "repetition_penalty": config.getfloat(
            "whisper", "repetition_penalty", fallback=1.05
        ),
        "no_repeat_ngram_size": config.getint(
            "whisper", "no_repeat_ngram_size", fallback=3
        ),
        "hallucination_silence_threshold": config.getfloat(
            "whisper", "hallucination_silence_threshold", fallback=0.8
        ),
        "vad_threshold": config.getfloat("whisper", "vad_threshold", fallback=0.5),
        "vad_min_speech_ms": config.getint(
            "whisper", "vad_min_speech_ms", fallback=200
        ),
        "vad_min_silence_ms": config.getint(
            "whisper", "vad_min_silence_ms", fallback=250
        ),
        "vad_speech_pad_ms": config.getint(
            "whisper", "vad_speech_pad_ms", fallback=180
        ),
        "key": config.get("hotkey", "key", fallback=defaults["key"]),
        "notifications": config.getboolean("behavior", "notifications", fallback=True),
    }


CONFIG = load_config()


class _ASR(FasterWhisperASR):
    """FasterWhisperASR with device/compute_type from config."""

    _device = CONFIG["device"]
    _compute_type = CONFIG["compute_type"]


class Dictation:
    def __init__(self):
        self.recording = False
        self.processing = False
        self.record_process = None
        self.online = None
        self.model_loaded = threading.Event()
        self.model_error = None

        self._stop_event = threading.Event()
        self._audio_thread = None
        self._transcribe_thread = None
        self._audio_queue = queue.Queue()
        self._raw_audio = bytearray()
        self._full_text = []
        self._typed_text = ""
        self._last_chunk_norm = ""
        self._emitted_chunks = 0

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        print(f"Loading Whisper model ({CONFIG['model']})...")
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        try:
            language = "en" if CONFIG["english_only"] else CONFIG["language"]
            asr = _ASR(lan=language, modelsize=CONFIG["model"])
            asr.set_condition_on_previous_text(CONFIG["condition_on_previous_text"])
            asr.set_transcribe_options(
                no_speech_threshold=CONFIG["no_speech_threshold"],
                log_prob_threshold=CONFIG["log_prob_threshold"],
                compression_ratio_threshold=CONFIG["compression_ratio_threshold"],
                repetition_penalty=CONFIG["repetition_penalty"],
                no_repeat_ngram_size=CONFIG["no_repeat_ngram_size"],
                hallucination_silence_threshold=CONFIG[
                    "hallucination_silence_threshold"
                ],
                temperature=0.0,
            )
            if CONFIG["use_vad"]:
                asr.use_vad(
                    {
                        "threshold": CONFIG["vad_threshold"],
                        "min_speech_duration_ms": CONFIG["vad_min_speech_ms"],
                        "min_silence_duration_ms": CONFIG["vad_min_silence_ms"],
                        "speech_pad_ms": CONFIG["vad_speech_pad_ms"],
                    }
                )

            self.online = OnlineASRProcessor(
                asr,
                use_init_prompt=CONFIG["use_init_prompt"],
            )
            self.model_loaded.set()
            print(f"Model loaded. Ready for dictation!")
            print(f"Press [{CONFIG['key']}] to start/stop recording.")
            print("Press Ctrl+C to quit.")
            print(
                "ASR tuning: "
                f"language={language}, "
                f"VAD={'on' if CONFIG['use_vad'] else 'off'}, "
                f"prev_text={'on' if CONFIG['condition_on_previous_text'] else 'off'}, "
                f"init_prompt={'on' if CONFIG['use_init_prompt'] else 'off'}"
            )
        except Exception as e:
            self.model_error = str(e)
            self.model_loaded.set()
            print(f"Failed to load model: {e}")
            if "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                print("Hint: Try setting device = cpu in config, or install cuDNN.")
            self.notify(
                "Whisper model failed",
                str(e)[:180],
                "dialog-error",
                8000,
            )

    def _remove_overlap(self, text):
        if not text or not self._typed_text:
            return text

        tail = self._typed_text[-MAX_OVERLAP_CHARS:]
        max_overlap = min(len(tail), len(text))

        for overlap in range(max_overlap, 0, -1):
            if tail.endswith(text[:overlap]):
                return text[overlap:]
        return text

    def _clean_committed_text(self, text, is_final=False):
        if not text:
            return ""

        out = text.replace("\n", " ").replace("\r", " ")
        out = _trim_boundary_courtesy(
            out,
            at_start=self._emitted_chunks == 0,
            at_end=is_final,
        )

        if not out.strip():
            return ""

        if CONFIG["english_only"] and _looks_non_english_noise(out):
            return ""

        out = self._remove_overlap(out)
        if not out.strip():
            return ""

        norm = _normalize_for_dedupe(out)
        if norm and norm == self._last_chunk_norm:
            return ""

        self._last_chunk_norm = norm
        return out

    def _append_emitted_text(self, text):
        self._full_text.append(text)
        self._typed_text += text
        if len(self._typed_text) > MAX_TYPED_BUFFER:
            self._typed_text = self._typed_text[-MAX_TYPED_BUFFER:]
        self._emitted_chunks += 1

    def notify(self, title, message, icon="dialog-information", timeout=2000):
        if not CONFIG["notifications"]:
            return
        subprocess.Popen(
            [
                "notify-send",
                "-a",
                "Whisper",
                "-i",
                icon,
                "-t",
                str(timeout),
                "-h",
                "string:x-canonical-private-synchronous:whisper",
                title,
                message,
            ],
            **DEVNULL,
        )

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.recording or self.processing:
            return

        self.model_loaded.wait()
        if self.model_error:
            print("Cannot record: model failed to load")
            self.notify(
                "Whisper is not ready",
                self.model_error[:180],
                "dialog-error",
                8000,
            )
            return

        self.recording = True
        self._stop_event.clear()
        self._audio_queue = queue.Queue()
        self._raw_audio = bytearray()
        self._full_text = []
        self._typed_text = ""
        self._last_chunk_norm = ""
        self._emitted_chunks = 0
        self.online.init()

        self.record_process = subprocess.Popen(
            [
                "parec",
                "--raw",
                "--format=s16le",
                "--channels=1",
                "--rate=16000",
                "--latency-msec=10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        self._audio_thread = threading.Thread(target=self._audio_reader, daemon=True)
        self._transcribe_thread = threading.Thread(
            target=self._transcription_loop, daemon=True
        )
        self._audio_thread.start()
        self._transcribe_thread.start()

        print("Recording (streaming)...")
        self.notify(
            "Recording...",
            f"Press {CONFIG['key']} to stop",
            "audio-input-microphone",
            30000,
        )

    def _audio_reader(self):
        """Read raw s16le from parec and queue float32 chunks."""
        stdout = self.record_process.stdout
        while not self._stop_event.is_set():
            data = stdout.read(CHUNK_BYTES)
            if not data:
                break
            self._raw_audio.extend(data)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            samples *= 1.0 / 32768.0
            self._audio_queue.put(samples)

    def _drain_audio_queue(self):
        """Batch-insert all queued audio into the online processor."""
        chunks = []
        try:
            while True:
                chunks.append(self._audio_queue.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self.online.insert_audio_chunk(np.concatenate(chunks))

    def _transcription_loop(self):
        """Poll-based loop: drain audio, transcribe, type confirmed text."""
        min_chunk = CONFIG["min_chunk_size"]
        last_process = time.time()
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=0.1)
            if self._stop_event.is_set():
                break
            self._drain_audio_queue()
            now = time.time()
            if now - last_process < min_chunk:
                continue
            if len(self.online.audio_buffer) / SAMPLE_RATE < 0.3:
                continue

            try:
                result = self.online.process_iter()
                last_process = time.time()
                _beg, _end, committed = result

                if committed:
                    cleaned = self._clean_committed_text(committed, is_final=False)
                    if cleaned:
                        self._type_text(cleaned)
                        self._append_emitted_text(cleaned)

            except Exception as e:
                print(f"Transcription error: {e}")
                last_process = time.time()

    def _type_text(self, text):
        if text:
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", text],
                **DEVNULL,
            )

    def stop_recording(self):
        if not self.recording:
            return

        self.recording = False
        self.processing = True
        self._stop_event.set()
        self.notify(
            "Stopped recording", "Processing...", "audio-input-microphone", 30000
        )

        if self.record_process:
            self.record_process.terminate()
            self.record_process.wait()
            self.record_process = None

        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self):
        """Flush remaining transcription in the background."""
        if self._audio_thread:
            self._audio_thread.join(timeout=2)
        if self._transcribe_thread:
            self._transcribe_thread.join()

        try:
            self._drain_audio_queue()
            _beg, _end, text = self.online.finish()
            if text:
                cleaned = self._clean_committed_text(text, is_final=True)
                if cleaned:
                    self._type_text(cleaned)
                    self._append_emitted_text(cleaned)
        except Exception as e:
            print(f"Final transcription error: {e}")

        full_text = "".join(self._full_text)
        if full_text:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE
            )
            proc.communicate(input=full_text.encode())
            print(f"Copied: {full_text}")
            self.notify(
                "Copied!",
                full_text[:100] + ("..." if len(full_text) > 100 else ""),
                "emblem-ok-symbolic",
                3000,
            )
        else:
            print("No speech detected")
            self.notify(
                "No speech detected", "Try speaking louder", "dialog-warning", 2000
            )

        self._save_wav()
        self.online.init()
        self.processing = False

    def _save_wav(self):
        try:
            with wave.open(str(LAST_RECORDING_PATH), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(self._raw_audio)
        except Exception as e:
            print(f"Failed to save WAV: {e}")

    def stop(self):
        print("\nExiting...")
        os._exit(0)

    def run(self):
        print(f"Listening for hotkey: {CONFIG['key']}")
        with keyboard.GlobalHotKeys({CONFIG["key"]: self.toggle_recording}) as listener:
            listener.join()


def check_dependencies():
    missing = []
    for cmd in ["parec", "xclip", "xdotool"]:
        if subprocess.run(["which", cmd], **DEVNULL).returncode != 0:
            pkg = "pulseaudio-utils" if cmd == "parec" else cmd
            missing.append((cmd, pkg))
    if missing:
        print("Missing dependencies:")
        for cmd, pkg in missing:
            print(f"  {cmd} - install with: sudo apt install {pkg}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Whisper - Streaming voice dictation")
    parser.add_argument(
        "-v", "--version", action="version", version=f"Whisper {__version__}"
    )
    parser.parse_args()

    print(f"Whisper v{__version__}")
    print(f"Config: {CONFIG_PATH}")

    check_dependencies()

    dictation = Dictation()
    signal.signal(signal.SIGINT, lambda *_: dictation.stop())
    dictation.run()


if __name__ == "__main__":
    main()
