# Flatpak license inventory

This inventory covers code bundled into the OpenWhisper Flatpak and the
top-level packages in its signed optional extension. Generated model weights
are not bundled; their licenses are recorded here so the download UI can link
to the right upstream terms.

| Component | Locked source | License / terms | Distribution |
| --- | --- | --- | --- |
| OpenWhisper | this repository | MIT | Core Flatpak |
| Faster Whisper | `faster-whisper==1.2.1` | MIT | Core Flatpak |
| cryptography | `cryptography==48.0.1` | Apache-2.0 OR BSD-3-Clause | Core Flatpak |
| huggingface-hub | `huggingface-hub==1.25.1` | Apache-2.0 | Core Flatpak |
| platformdirs | `platformdirs==4.11.0` | MIT | Core Flatpak |
| pynput | `pynput==1.8.2` | LGPL-3.0 | Core Flatpak |
| PySide6 / Qt | `PySide6==6.11.1` | LGPL-3.0-only OR GPL/commercial | Core Flatpak |
| llama.cpp | commit `11b068d06605288ce7917534b46d52b47823dc13` | MIT | Core Flatpak |
| accelerate | `accelerate==1.14.0` | Apache-2.0 | Optional extension |
| safetensors | `safetensors==0.8.0` | Apache-2.0 | Optional extension |
| PyTorch CPU | `torch==2.13.0+cpu` | BSD-3-Clause | Optional extension |
| Transformers | `transformers==5.14.1` | Apache-2.0 | Optional extension |
| Qwen3-4B GGUF Q4_K_M | on-demand upstream download | Apache-2.0 | Not bundled |

The release job must retain each wheel/native dependency's distributed license
files when assembling the repository. This inventory is a review gate, not a
substitute for those notices or for checking changed upstream terms.
