# LocalTTS Studio — Architecture

## Part 1 — Application architecture

### 1.1 Target machine (measured 2026-08-27)

| Component | Value |
|---|---|
| OS | Windows 11 Pro, build 26200 (64-bit) |
| CPU | Intel Core i5-12400F — 6 P-cores / 12 threads |
| RAM | 31.8 GB |
| GPU | NVIDIA GeForce RTX 4060 Ti, **8 GB** VRAM (`nvidia-smi`: 8188 MiB) |
| CUDA | driver 610.47 / CUDA UMD 13.3 — CUDA available |
| Python | 3.12.10 present (also 3.14 — not used; ML wheels lag) |
| Disk | ~90 GB free |
| FFmpeg | system copy present, **not relied on** (bundled via `imageio-ffmpeg`) |

### 1.2 Options compared

| # | Architecture | Startup | Deploy | GPU/CUDA/PyTorch | TTS model compat | Mem | Disk | Portability | Windows | Dev speed | Maintain | Package as EXE | Offline | Move to other PC | Voice-clone ready |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A** | Pure Python desktop (Tkinter/PySide) | fast | medium | native | native | low | low | good | good | slow (UI in Python) | medium | yes (PyInstaller, big) | yes | copy folder | yes |
| **B** | **Python backend + local Web UI** | fast | **simple** | **native** | **native** | low | low | **very good** | **very good** | **fast** | **good** | yes (thin launcher) | **yes** | **copy folder** | **yes** |
| C | Python + React (build step, bundler, node) | fast | heavy | native (py) | native | medium | high | medium | good | slow (2 stacks) | medium | yes | yes | copy folder + rebuild FE | yes |
| D | Electron + Python backend | slow | heavy | native (py) | native | **high (Chromium)** | **high (~200 MB FE)** | medium | good | medium | medium | yes (very big) | yes | copy folder (large) | yes |
| E | Python + C++ inference | medium | heavy | manual | **fragile** (must port each model) | low | medium | poor | medium | **very slow** | poor | partial | yes | recompile per PC | hard |
| F | Rust desktop app | fast | medium | **no PyTorch** → reimplement models | **very poor** | low | low | good | good | **very slow** | medium | yes | yes | copy binary | **very hard** |
| G | Docker-based | **slow** (daemon) | **needs Docker + NVIDIA Container Toolkit** | works but heavy | native | high | **very high** | poor on Windows | poor (WSL2/Docker Desktop) | medium | medium | no | yes | needs Docker on target | yes |
| H | **Local browser + Python server + launcher EXE** | fast | **simple** | **native** | **native** | low | low | **very good** | **very good** | **fast** | **good** | **yes (launcher only)** | **yes** | **copy folder** | **yes** |

### 1.3 Decision

**Chosen: B + H — a Python backend serving a local Web UI in the system browser, started by a thin Windows launcher EXE.**

They are the same architecture; H just adds the double-click launcher on top of B.

Why:

- **PyTorch is mandatory.** Every viable open TTS model (Chatterbox, Fish/OpenAudio, F5, Kokoro, XTTS) ships as PyTorch. That instantly removes **E** and **F** (no PyTorch → you would have to re-implement the acoustic model and vocoder — years of work) and makes **G** heavy for no benefit.
- **The UI is a form + a text editor + an audio player.** It does not need React's component model or Electron's bundled Chromium. HTML/CSS/vanilla-JS served by Flask is enough and keeps the whole front-end to ~3 small files — removing **C** and **D**'s toolchain, disk, and memory cost. Electron alone would add ~150–250 MB and a second runtime.
- **Web UI beats a native Python GUI (A)** for iteration speed (CSS layout, live text highlighting, `<audio>` element for free) and because the browser sandbox keeps the UI process cleanly separate from the model process.
- **Offline is trivial here**: the server binds `127.0.0.1` only, the browser talks only to it, and Python-side network calls are blocked by `app/netguard.py` when `offline_mode` is on. No architecture fights this.
- **Portability is "copy the folder"**: everything (Python `runtime/`, `models/`, `voices/`, `projects/`, `config/`, `output/`) lives under the app root with only relative paths. No registry, no services, no per-user state.
- **Packaging as EXE is cheap**: only the ~200-line launcher is frozen with PyInstaller (`LocalTTS.exe`, a few MB). The heavy dependencies stay as a normal venv in `runtime/`, and **model weights are never embedded** — they live in `models/`.

### 1.4 Process model

```
LocalTTS.exe  (frozen launcher)
   │  1. load config/config.json
   │  2. probe hardware  (app/hardware.py)     -> warn on low VRAM / no CUDA
   │  3. verify models   (app/capabilities.py) -> "model not installed" if missing (never downloads)
   │  4. spawn:  runtime/Scripts/python.exe -m app.server
   │  5. wait for  http://127.0.0.1:<port>/api/health
   │  6. open that URL in the default browser
   │  7. on exit / Ctrl+C / window close:
   │        - POST /api/shutdown  -> engine.shutdown() -> torch.cuda.empty_cache()
   │        - terminate the server process, then its children
   ▼
python -m app.server   (Flask + werkzeug, threaded, single worker)
   └── TTSProvider  (loaded once, kept warm)   <- the only GPU user
```

One server process, one model in memory, requests serialised on a per-engine lock.
The launcher owns lifecycle; the server owns inference.

### 1.5 Portable layout

```
LocalTTS/
    LocalTTS.exe          built by build_exe.bat (dev: run.bat)
    run.bat  setup.bat  test.bat  build_exe.bat  LocalTTS.spec
    launcher.py
    app/                  backend code (see Part 3)
    web/                  index.html + static/{app.js,editor.js,studio.css}
    runtime/              the Python venv (created by setup.bat; may be copied or rebuilt)
    models/
        qwen-1.7b/        weights ~4.6 GB (NOT committed, NOT in the EXE)
    voices/               <id>/{voice.json, reference.wav}
    projects/             workspace/{project.json, script.txt, generations/, final/}
    output/               ad-hoc exports (timestamped, never overwritten)
    calibration/          <timestamp>/{*.wav, results.json, README.md}  (calibrate_tts output)
    config/               config.json, config.example.json, model_capabilities.json
    logs/                 rotating logs
    scripts/              download_models.py, make_voices.py, check_env.py, calibrate_tts.py
    tests/
```

No absolute paths, no `%USERNAME%`, no drive letters in code — every path derives from
`app/config.py:APP_ROOT` (the folder containing `app/`). Copy the folder to another
Windows PC and run `setup.bat` (or copy `runtime/` too if the Python versions match).

> **Note:** keep the folder **out of `C:\Users\<you>\Downloads\`** — Windows *Storage
> Sense* can auto-delete Downloads contents. `setup.bat` and the launcher warn if they
> detect this.

---

## Part 2 — Engine & model choice

### 2.1 Options

*(re-checked August 2026 — the open-weights landscape moved fast in H1 2026.)*

| Model | Naturalness | Expressive control | EN / RU | Speed (RTX 4060 Ti) | VRAM (fp16/bf16) | Offline | Windows | Weights licence |
|---|---|---|---|---|---|---|---|---|
| **Chatterbox Multilingual v2** (Resemble AI) | excellent | `exaggeration` + `cfg_weight` scalars | first-class / good | ~1–1.5× RT | ~3 GB | yes | `pip install`, no compiler | **MIT** ✅ commercial |
| **Qwen3-TTS 1.7B** (Alibaba) | excellent | none (reference-driven) + `instruct` in some modes | good / good | ~1× RT | ~8 GB (0.6B ≈ 3 GB) | yes | pure PyTorch, `pip install qwen-tts` (needs `transformers<5`) | **Apache-2.0** ✅ commercial |
| Fish Audio S2 / S2.1 Pro | best-in-class | native free-form tags `[whisper]`, `[super happy]`, `[pitch up]` … | good / Tier-2 | **won't fit** (4.4 B ≈ 9 GB); SGLang, Linux/WSL2 | 12–16 GB | yes | SGLang server (Linux-first) | **"Fish Audio Research License"** — self-host for research free, **commercial use = paid licence** ❌ for YouTube |
| OpenAudio S1-mini (0.5 B) | excellent | native tags | good / good | ~1× RT | ~4 GB | yes | multi-stage | **CC-BY-NC-SA** ❌ non-commercial |
| F5-TTS | very good (ref-dependent) | weak | good / community RU checkpoint | ~1× RT | ~3 GB | yes | flash-attn friction | base weights CC-BY-NC ❌ |
| XTTS-v2 (Coqui) | very good | none | good / good | ~1× RT | ~4 GB | yes | mature | Coqui CPML ❌ non-commercial |
| Kokoro-82M | good | none | good / — | ~10× RT | <1 GB | yes | trivial | Apache-2.0 ✅ |

### 2.2 Decision

**One engine: Qwen3-TTS 1.7B Base** (Alibaba, **Apache-2.0**), behind the
`TTSProvider` abstraction. `mock` remains as a zero-dependency engine for tests /
UI development. Chatterbox, Kokoro and engine-switching were removed.

Why single-engine:

- Qwen3-TTS is current-generation quality with **strong Russian**, zero-shot
  cloning from a reference clip + its transcript (ICL mode: `ref_audio + ref_text`,
  never `x_vector_only`), pure PyTorch, fits the 8 GB card at bf16.
- It is the practical answer to *"something like Fish S2 but local and legal"* —
  Fish S2 Pro is a 4.4 B research-licensed model that needs a **paid commercial
  licence** for monetised video and won't fit 8 GB.
- Keeping two engines meant two `transformers` pins fighting each other
  (Chatterbox `==5.2.0` vs Qwen `==4.57.3` — the `check_model_inputs` ImportError).
  `transformers==4.57.3` is now pinned hard.
- Qwen has **no expressive knob** and **no native seed parameter**. Delivery comes
  from the reference voice + sampling params; determinism comes from
  `torch.manual_seed` + `torch.cuda.manual_seed_all` before each generation
  (HF transformers global RNG).

### 2.3 Delivery model — no inline commands

There are **no `[commands]`** in the text. Any bracketed span is stripped with a
warning. Delivery is entirely preset-driven, and each preset has three decoupled
groups (`config/config.json → presets`):

| Group | Keys | Effect |
|---|---|---|
| `generation` | `temperature`, `top_p`, `repetition_penalty` | passed straight to `generate_voice_clone(**kwargs)` — the only real emotion lever |
| `prosody` | `pause_scale`, `jitter`, `trail_ms` | scales structural silences; adds seeded jitter so pauses aren't mechanical |
| `audio` | `gain_db`, `eq`, `compress` | minimal per-chunk shaping — **no pitch shift, no per-chunk time-stretch** |

`happy` is *not* `pitch +1 semitone`. The only global post step that touches
timing is one optional `time_stretch` for the UI "Скорость" slider, applied once
to the finished master (`audio.global_speed_via_stretch`).

### 2.4 Text normalization (offline)

`app/text_normalizer.py` runs before prosody. It keeps `original_text` (shown in
the editor) and `normalized_text` (fed to Qwen) separate. Boundary-aware, not
global replace:

- **Pronunciation lexicon** (configurable, `normalizer.lexicon`): `C++` → «си плюс
  плюс», `JavaScript` → «джаваскрипт», `PHP` → «пи эйч пи`, `C#` → «си шарп`, …
- Numbers, decimals, percents, money, units, ordinals, ranges → Russian words
  (cardinal / ordinal / genitive as the grammar requires).
- Dates, `2026 год` → genitive ordinal ("две тысячи двадцать шестого года").
- Time `15:30` → "пятнадцать часов тридцать минут".
- Acronyms not in the lexicon → spelled letter-by-letter (policy-gated).
- **English sentences are left alone** (`_is_english_sentence`: needs ≥2 English
  function words and ≤1 Cyrillic word) — Russian prose with a Latin term still
  gets the term from the lexicon.

### 2.5 Segmentation

Qwen is called on **large semantic chunks** — whole paragraphs when they fit
(`chunking.target_chars` 700, `max_chars` 1100) — never sentence-by-sentence, so
prosodic continuity is preserved and `C++ разработчик` / `двадцать три градуса`
never split. Silence is inserted **only** at paragraph / forced-split boundaries;
Qwen's own `. , ; : ? ! … —` handling does everything inside a chunk.

### 2.6 Multi-variant

The "Количество вариантов" slider (1–5) produces N independent stochastic
realizations of the same voice / emotion / text / normalization. Variant *k* uses
`base_seed + k`; the model and the cached clone-prompt are reused across variants.
One variant failing does not abort the others. Output:
`generations/<id>/variant_<n>/{raw,final}.wav + final.mp3` and a shared
`report.json`.

---

## Part 3 — Backend module map

```
app/
    server.py         Flask app + JSON API + lifecycle; /api/generate -> job_id, status polling
    config.py         APP_ROOT, all paths, config load/merge/save, offline_mode
    netguard.py       when offline_mode: monkeypatch socket + set HF_HUB_OFFLINE etc.
    hardware.py       CPU / RAM / GPU / VRAM / CUDA probe -> dict + warnings
    capabilities.py   loads config/model_capabilities.json; model-installed check (importlib.find_spec)
    text_normalizer.py  original_text vs normalized_text; lexicon / numbers / dates / time / acronyms
    prosody.py        normalized text -> stress -> semantic chunks -> context pauses -> [Segment]
    estimate.py       char/word counts + estimated spoken duration (speed is a param)
    analyze.py        text analyzer + auto-fix (thin wrapper over text_normalizer)
    audio.py          silence / gain / EQ / concat / loudnorm / compress / limit / one global time-stretch / WAV / MP3
    pipeline.py       N variants x (chunks -> Qwen ICL -> assemble+pauses -> master -> WAV/MP3) + report.json
    projects.py       project.json CRUD, script.txt, generations/ history, final/ ; atomic writes
    presets.py        nested {generation, prosody, audio} bundles
    voices.py         file-backed voice registry; multi-clip stitching; add/update/delete
    tts/
        base.py       TTSProvider ABC: initialize / list_voices / generate(text,voice,style,seed) / shutdown
        qwen_engine.py     Qwen3TTSModel wrapper; seed via torch.manual_seed; clone-prompt cache
        mock_engine.py     (tests / no-model dev)
        registry.py        engine id -> provider (qwen | mock)
scripts/
    calibrate_tts.py  offline grid-search of generation params; writes calibration/<ts>/
```

Data flow:

```
editor  ─►  original_text  ──text_normalizer──►  normalized_text  (+ replacements[])
                                     │  prosody: stress marks (+vowel -> U+0301)
                                     ▼
                        paragraphs ─► semantic chunks (~700 ch)
                                     │  interleave context silences (seeded jitter)
                                     ▼
              [Segment(speech,text,style) | Segment(silence,ms)]
                                     │
             for k in 1..N:  torch.manual_seed(base_seed + k)
                                     │  per speech chunk: provider.generate(chunk, voice, style, seed=…)
                                     ▼
             raw float32 @ 24 kHz  ─►  variant_k/raw.wav
                                     │  per-chunk EQ + gain only  ▸  master: [compress] ▸ loudnorm(-16) ▸ limiter
                                     │  optional one global time_stretch (speed slider)
                                     ▼
             variant_k/final.wav  ──ffmpeg──►  variant_k/final.mp3
                                     ▼
   projects/workspace/generations/<ts>/{variant_1..N/, report.json}  +  output/<ts>_narration.mp3
```

---

## Part 4 — Staged plan

See [ROADMAP.md](ROADMAP.md). This build delivers the full Qwen-only pipeline, the
text normalizer, semantic chunking, decoupled emotion presets, multi-variant
generation, the calibration CLI, the studio UI, the single workspace project,
history, the audio chain, and the launcher.
