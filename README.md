# Whisper

A voice dictation tool for Linux using [Cohere Transcribe](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026), a 2B parameter Conformer-based ASR model.

**Fork Note:** This is an enhanced fork of [soupawhisper](https://github.com/ksred/soupawhisper), expanded to support **Void Linux**, **Runit**, and **PulseAudio/PipeWire**. It also features a **toggle recording** behavior (press to start, press to stop) instead of the original push-to-talk.

## Features

- **Toggle Recording:** Press the hotkey to start recording, press again to stop and transcribe.
- **Cohere Transcribe:** Uses the CohereLabs/cohere-transcribe-03-2026 model for high-quality transcription.
- **Auto-Type:** Automatically copies text to clipboard and types it into the active window.
- **Void Linux Support:** First-class support for Void Linux and Runit service supervision.
- **Notifications:** Desktop notifications for recording status and errors.

## Supported Languages

English, French, German, Italian, Spanish, Portuguese, Greek, Dutch, Polish, Chinese, Japanese, Korean, Vietnamese, Arabic.

## Requirements

- Python 3.10+
- **Audio Backend:** PulseAudio or PipeWire (requires `parecord`)
- **System Tools:** `xclip`, `xdotool`, `libnotify`
- **Linux:** Tested on Void Linux, Ubuntu, Fedora, Arch.

## Installation

### Automatic Installation (Recommended)

The included installer detects your distro and package manager to set everything up, including system dependencies and the Python environment.

```bash
git clone https://github.com/yourusername/whisper.git
cd whisper
chmod +x install.sh
./install.sh
```

### Manual Installation

#### 1. Install System Dependencies

**Void Linux:**
```bash
sudo xbps-install -S pulseaudio-utils xclip xdotool libnotify
```

**Ubuntu / Debian:**
```bash
sudo apt install pulseaudio-utils xclip xdotool libnotify-bin
```

**Fedora:**
```bash
sudo dnf install pulseaudio-utils xclip xdotool libnotify
```

**Arch Linux:**
```bash
sudo pacman -S pulseaudio-utils xclip xdotool libnotify
```

#### 2. Install Python Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. Setup Config

```bash
mkdir -p ~/.config/whisper
cp config.example.ini ~/.config/whisper/config.ini
```

## Configuration

Edit `~/.config/whisper/config.ini`:

```ini
[whisper]
# Model ID from HuggingFace (or local path)
model = CohereLabs/cohere-transcribe-03-2026

# Device: cuda or cpu
device = cuda

# Data type: float16 for GPU, float32 for CPU
dtype = float16

# Language: ISO 639-1 code (en, fr, de, it, es, pt, el, nl, pl, zh, ja, ko, vi, ar)
language = en

[hotkey]
# Hotkey to toggle recording (default: Alt+O)
# Examples: <alt>+o, <ctrl>+space, <f12>
key = <alt>+o

[behavior]
# Type text into active input field
auto_type = true

# Show desktop notification
notifications = true
```

## Usage

Start the application manually:

```bash
source .venv/bin/activate
python dictate.py
```

- **Toggle Recording:** Press **Alt+O** (or your configured key) to start recording.
- **Stop & Transcribe:** Press the key again to stop. The text will be copied to your clipboard and typed into the active window.
- **Quit:** Press **Ctrl+C** in the terminal.

**Note:** The first run will download the model from HuggingFace (~4 GB). Subsequent runs use the cached model.

## Auto-Start with .xinitrc

To start Whisper automatically when you log in, add the following line to your `~/.xinitrc` file (or your window manager's startup script).

Make sure to use the **absolute path** to where you cloned the repository.

```bash
# Start Whisper (adjust path as needed)
/path/to/whisper/start.sh &
```

The `start.sh` script handles:
1.  Activating the virtual environment
2.  Logging output to `whisper.log`
3.  Automatically restarting the application if it crashes

## GPU Support

The model requires ~4 GB VRAM when using `float16`. Any NVIDIA GPU with 4+ GB VRAM should work (e.g. RTX 3050 Mobile).

To use CPU instead (slower):
```ini
device = cpu
dtype = float32
```
