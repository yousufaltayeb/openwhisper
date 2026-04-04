#!/usr/bin/env python3
"""
Whisper - Streaming voice dictation using faster-whisper.
Press hotkey to start recording, text appears as you speak.
Press again to stop, full text copied to clipboard.
"""

import argparse
import configparser
import queue
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


def load_config():
    config = configparser.ConfigParser()

    defaults = {
        "model": "base.en",
        "device": "cpu",
        "compute_type": "int8",
        "language": "en",
        "min_chunk_size": "0.3",
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
        "min_chunk_size": config.getfloat(
            "whisper", "min_chunk_size", fallback=float(defaults["min_chunk_size"])
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

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        print(f"Loading Whisper model ({CONFIG['model']})...")
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        try:
            asr = _ASR(lan=CONFIG["language"], modelsize=CONFIG["model"])
            self.online = OnlineASRProcessor(asr)
            self.model_loaded.set()
            print(f"Model loaded. Ready for dictation!")
            print(f"Press [{CONFIG['key']}] to start/stop recording.")
            print("Press Ctrl+C to quit.")
        except Exception as e:
            self.model_error = str(e)
            self.model_loaded.set()
            print(f"Failed to load model: {e}")
            if "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                print("Hint: Try setting device = cpu in config, or install cuDNN.")

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
        if self.recording or self.processing or self.model_error:
            return

        self.model_loaded.wait()
        if self.model_error:
            print("Cannot record: model failed to load")
            return

        self.recording = True
        self._stop_event.clear()
        self._audio_queue = queue.Queue()
        self._raw_audio = bytearray()
        self._full_text = []
        self.online.init()

        self.record_process = subprocess.Popen(
            ["parec", "--raw", "--format=s16le", "--channels=1",
             "--rate=16000", "--latency-msec=10"],
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
                    self._type_text(committed)
                    self._full_text.append(committed)

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
        self.notify("Stopped recording", "Processing...", "audio-input-microphone", 30000)

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
                self._full_text.append(text)
                self._type_text(text)
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
            self.notify("No speech detected", "Try speaking louder", "dialog-warning", 2000)

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
