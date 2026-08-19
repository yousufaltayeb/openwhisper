# Flatpak license inventory

This inventory covers code bundled into the OpenWhisper Flatpak and the
top-level packages in its signed optional extension. Generated model weights
are not bundled; their licenses are recorded here so the download UI can link
to the right upstream terms.

| Component | Locked source | License / terms | Distribution |
| --- | --- | --- | --- |
| OpenWhisper | this repository | MIT | Core Flatpak |
| React | `react==19.2.8` / `react-dom==19.2.8` | MIT | Core Flatpak |
| React Aria Components | `react-aria-components==1.19.0` | Apache-2.0 | Core Flatpak |
| Lucide | `lucide-react==1.28.0` | ISC | Core Flatpak |
| Readex Pro | `@fontsource-variable/readex-pro==5.3.0` | OFL-1.1 | Core Flatpak |
| IBM Plex Mono | `@fontsource/ibm-plex-mono==5.3.0` | OFL-1.1 | Core Flatpak |
| Tauri | `tauri==2.11.5` | Apache-2.0 OR MIT | Core Flatpak |
| Tauri single-instance plugin | `tauri-plugin-single-instance==2.4.3` | Apache-2.0 OR MIT | Core Flatpak |
| Tauri notification plugin | `tauri-plugin-notification==2.3.3` | Apache-2.0 OR MIT | Core Flatpak |
| zbus | `zbus==5.18.0` | MIT | Core Flatpak |
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
| CTranslate2 HIP | `OpenNMT/CTranslate2@v4.8.1` | MIT | Optional AMD extension |
| pybind11 | `pybind11==2.13.6` | BSD-3-Clause | Optional AMD extension (build-time) |
| ROCm 6.2.4 user-space libraries | `ROCm/{rocm-core,hip,rocBLAS,hipBLAS,MIOpen}` pinned commits | MIT / Apache-2.0 | Optional AMD extension |
| NVIDIA CUDA redistributables | `nvidia-cublas-cu12==12.6.4.1`, `nvidia-cudnn-cu12==9.5.1.17`, `nvidia-cuda-runtime-cu12==12.6.77`, `nvidia-cuda-nvrtc-cu12==12.6.77` | NVIDIA CUDA EULA | Optional NVIDIA extension |

The optional `tauri-plugin-wdio` and `tauri-plugin-wdio-webdriver` crates and
their JavaScript bridge are development-only test tools. Release builds do not
enable their Cargo feature and do not contain them.

The release job must retain each wheel/native dependency's distributed license
files when assembling the repository. This inventory is a review gate, not a
substitute for those notices or for checking changed upstream terms.
