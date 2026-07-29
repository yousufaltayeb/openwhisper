# OpenWhisper notices and attributions

This file records source and release-distribution notices that must accompany
OpenWhisper. It is informational and does not replace the license terms for
OpenWhisper or any dependency.

## OpenWhisper and Soupawhisper

OpenWhisper is a continuation of the Soupawhisper codebase by Kyle
([`ksred/soupawhisper`](https://github.com/ksred/soupawhisper)). The repository
license is MIT and retains the upstream copyright notice in [LICENSE](LICENSE).
Contributors must preserve that notice in source and binary distributions.

## Whisper Streaming provenance

[`whisper_online.py`](whisper_online.py) was vendored from
[`ufal/whisper_streaming`](https://github.com/ufal/whisper_streaming) and then
locally adapted. Its originating commit message identifies that provenance.
The upstream project is MIT-licensed; source or binary distributions that retain
this vendored code must retain the applicable upstream MIT notice and license.

## Runtime dependencies and model assets

OpenWhisper depends on independently licensed packages and model assets,
including Faster Whisper, CTranslate2, PySide6/Qt, pynput, cryptography,
huggingface-hub, platformdirs, and their transitive dependencies. The Flatpak
also builds CPU-only llama.cpp. Optional packs add Cohere,
OpenAI, Groq, Deepgram, PyTorch, Transformers, and associated dependencies.
Model weights are obtained from their upstream publishers at runtime and are not
included in the source tree.

The optional local editing pack downloads the official Qwen3-4B GGUF Q4_K_M
weights, published under Apache-2.0. The model is not bundled in the Flatpak;
users explicitly request its download.

Every release must:

1. Generate an inventory for the exact locked dependency set and include each
   dependency's required license and notice text in the source archive and
   Flatpak repository.
2. Include the license/notice files required by PySide6/Qt and every bundled
   native library; check the precise Qt distribution terms before publishing.
3. Preserve the notices above when `whisper_online.py` is shipped.
4. Link users to the upstream model card and license for every downloadable
   model. Do not imply that a model license applies to OpenWhisper itself.
5. Record the release tag, build environment, package lock, and generated
   notice inventory with the release artifacts.

The release owner is responsible for verifying current upstream licenses. A
package name, model name, or `pip` metadata entry alone is not a sufficient
license review.
