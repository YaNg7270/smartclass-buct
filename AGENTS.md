# Repository Guidelines

## Project Overview

**smartclass-buct** (CLI: `scls`) is a Windows command-line tool that processes lecture recordings from the BUCT (北京化工大学) SmartClass platform (`buct.smartclass.cn`). Given a date + course name, one command produces: compressed lecture video, verbatim transcript (ASR), slide OCR, **homework-requirements summary** (`homework_*.md`), and **class notes** (`notes_*.md`), plus nightly unattended runs via Windows Task Scheduler.

Personal-use scope (owner's own account only, for study). **Login is always manual** — never attempt to auto-fill credentials/CAPTCHA. The platform API requires a page-JS-generated one-time `csrkToken`, so **never call the API directly with curl/fetch**; all site access goes through Playwright XHR interception in `discover.py`/`browser.py`.

## Architecture & Data Flow

Single-user pipeline (`cli.py:_run_pipeline` → `_iter_lectures` → `_process_lecture`):

1. **Discover** — Playwright persistent Chromium context (`browser.py`), intercepts the site's own list XHRs, paginates in-page via `discover.ListPager` (server caps `PageSize` at 50); DOM-scraping fallback.
2. **Group** — `grouping.group_sessions()` merges same-course/same-day 45-min 小节 (sessions, gap ≤ 30 min) into one 整课 (`Lecture`) — the unit of all outputs.
3. **Streams + download** — `streams.fetch_streams()` resolves `VGA.mp4`/`Video1.mp4` (player `<source>`, else reverse-engineered from sniffed VOD URL prefix + HEAD probe); `download.download()` streams with Range-resume `.part` files.
4. **Per-part ASR ∥ OCR** — `audio` (ffmpeg 16k mono mp3, 600 s chunks) + `asr.transcribe` run alongside `vision.scan` (interval frame sampling → 256-bit dHash dedupe → batched base64 OCR) in a 2-worker `ThreadPoolExecutor`.
5. **Merge** — `merge.merge_transcripts`/`merge_slides` concatenate parts, shifting `[mm:ss]` timestamps onto the whole-lecture timeline.
6. **Digest** — `digest.build_homework`/`build_notes` (LLM; homework gets a keyword "证据区" evidence appendix).
7. **Retention** — `videoops.apply_retention`: re-encode via GPU encoder auto-detection (`hwaccel.detect`: QSV→NVENC→AMF, fallback libx264 CRF-33) with ffprobe verify-before-replace, or delete if `keep_video = none`.
8. **Web UI** — `scls serve` (`web/server.py`, Flask + Jinja2, binds 127.0.0.1): read-only browsing, full-text search, media streaming (Range), and run/resume/daily/list task control via whitelisted-argv subprocess; job state in-process, single active task.

**Critical invariants (enforced by design, see `docs/技术开发文档.md` §8.1):**
- Playwright must stay on the **main thread**.
- **Never submit `transcribe()`/`scan()` to the gateway pool** (`scheduler.gateway_map`) — they call it internally and would self-deadlock. The pool runs single HTTP requests only.
- **Idempotency everywhere**: every stage skips existing artifacts unless `--force`. Note `run --force` re-downloads video; `resume --force` only redoes local processing.
- Concurrency is `concurrent.futures` threads only — **no asyncio, no httpx/aiohttp**; all HTTP is synchronous `requests`.

## Key Directories

- `src/smartclass/` — all source (src-layout; imports are always `smartclass.*`).
  - *Pipeline stages*: `discover`, `grouping`, `streams`, `download`, `audio`, `asr`, `vision`, `merge`, `digest`, `videoops` (+ `hwaccel` encoder detection); offline utilities `migrate`, `bench`, `schedule`.
  - *Infrastructure*: `cli` (argparse + orchestration), `config` (dataclasses + TOML), `state` (PID lock, login marker, log tee), `workspace` (`RunDir`/`LectureDir` on-disk model), `scheduler` (thread pools), `browser` (Playwright session).
- `tests/` — 14 flat `test_*.py` + `fixtures/` (plain data dir, e.g. `get_public_videos.json`).
- `docs/` — `项目文档.md` (usage) and `技术开发文档.md` (architecture, module map, extension recipes §20, coverage table §17). Authoritative and in Chinese.
- `skills/smartclass-buct/SKILL.md` — Claude Code skill; operational guardrails (no business logic).
- `data/` — **unversioned** runtime outputs: `<course>/<date>/` packages, `_profile/` (browser login state), `_logs/`, `_state/` (locks, `login-needed.txt`). Reserved `_`-dirs are skipped by `resume`/`prune`/`flatten` scans.

## Development Commands

```powershell
python -m pip install -e .            # editable install (required; src-layout)
python -m playwright install chromium # first time only
python -m pytest -q                   # tests (67 passing, ~7s)
python -m pyflakes src tests          # the ONLY lint gate
scls --help                           # run: login | list | run | resume | daily | bench |
                                      #       regroup | migrate-layout | prune | schedule | asr/slides/digest
```

No CI, no formatter/type-checker config (no ruff/black/mypy), no lockfile, no task runner, no pytest config. `pytest` and `pyflakes` are undeclared dev deps.

## Code Conventions & Common Patterns

- **Language**: docstrings, comments, log lines, error messages, and LLM prompts are Chinese. Match this.
- **Typing/style**: `from __future__ import annotations` in every module; PEP 604 unions (`str | None`); plain `@dataclass` for config sections and domain records (`Session`, `Lecture`, `RunDir`, `PartRef`) — no pydantic.
- **Naming**: lowercase single-word modules; `_`-prefixed private helpers; CLI handlers `cmd_*`; constants `UPPER_SNAKE`; tests `test_<behavior>()` with descriptive names.
- **State management**: file-based, no DB. Per-part `meta.json`, per-lecture `lecture.json` (via `workspace`); operational state in `data/_state/`, logs in `data/_logs/`. Caching = "skip if file exists and not --force".
- **Config**: stdlib `tomllib` → dataclasses (`config.py`). Lookup: `--config` → `SCLS_CONFIG` env → `./smartclass.toml` → repo-root → `~/.smartclass-buct.toml` (first match wins, no section merging); env `SCLS_GATEWAY_KEY`/`SCLS_GATEWAY_URL` override last. To add a field: dataclass field + `_apply` line + update **both** `config.example.toml` and local `smartclass.toml` (never commit the latter — it holds the gateway key).
- **Error handling**: fail-loud `RuntimeError` with Chinese operator-facing messages; `SystemExit` for user-input errors; batch errors collected and re-raised once at the end (`scheduler._run_all`). Exit codes: 0 ok, 1 error/no classes, 2 daily-login-expired (writes `data/_state/login-needed.txt` + Windows toast), 3 lock busy, 130 Ctrl+C.
- **Concurrency**: two-tier `scheduler.py` — global bounded `gateway_map` pool (sized by `[concurrency].gateway`, shared by ASR/OCR/digest, results ordered by index) + ad-hoc `parallel_map` temp pools; pipelining overlaps download of lecture N+1 with processing of N. Retry: ASR model-fallback ladder (`fallback_models` → recursive audio half-split, max 3 levels), jittered backoff on 429/503; the gateway may return HTTP 200 with empty `choices` — treated as failure.
- **Logging**: stdlib `logging` unused; each module defines a private `_log()` printing `[modname] msg` to stderr; `state.tee_log` duplicates output into dated log files for unattended runs.
- **Windows gotchas** (load-bearing): `os.kill(pid, 0)` kills processes on Windows — PID liveness uses ctypes `OpenProcess` (`state.py`); `schtasks` output decoded as GBK; PowerShell treats `( )` as operators → quote course names with parentheses (`scls run --course "物理化学(Ⅰ)"`); `.gitattributes` enforces LF.

## Important Files

- `src/smartclass/cli.py` — entry point (`scls = smartclass.cli:main`) and orchestrator; subcommands registered in `build_parser()`.
- `src/smartclass/config.py` — config dataclasses, TOML/env loading.
- `src/smartclass/discover.py` — site-list XHR interception + `ListPager` pagination (most platform-coupled module; breaks if the platform revamps its API).
- `src/smartclass/asr.py` / `vision.py` / `digest.py` — gateway (OpenAI-compatible, local cc-switch proxy) calls for transcription / frame OCR / summaries.
- `src/smartclass/workspace.py` — on-disk contract: flat `data/<course>/<date>/` with `homework_<date>_<span>.md`, `notes_...`, `transcript.txt`, `slides.txt`, `lecture.json`, `parts/<slot>/` intermediates. Name-collision rule: first lecture of a day owns un-suffixed files, later ones get `_<date>_<span>` suffixes.
- `src/smartclass/scheduler.py` — thread-pool concurrency core.
- `config.example.toml` — committed template mirroring `config.py` sections 1:1 with tuning comments.
- `docs/技术开发文档.md` — architecture reference, extension recipes (new command §20.1, model swap §20.2, layout changes §20.5), tech-debt list §21.

## Runtime/Tooling Preferences

- **Python ≥ 3.11** (uses stdlib `tomllib`), **Windows 10/11 required** (schtasks, GBK decoding, toasts, ctypes process checks).
- **ffmpeg/ffprobe must be on PATH** (configurable in `[paths]`) — audio extraction, chunking, frame sampling, compression, and the compress-verify safety gate (duration deviation > max(5 s, 8%) → keep original).
- Setuptools build via `pyproject.toml`; editable install assumed (`src/smartclass_buct.egg-info/` present).
- Runtime deps are minimal by design: `requests` (all HTTP), `playwright` (browser automation + cookie export to requests), `Pillow` (dHash dedupe, optional).
- Run the `scls` CLI itself rather than reimplementing platform calls; for speed tuning, `scls bench --run <cached-part> --stage asr,ocr` measures the gateway before touching `[concurrency]`.

## Testing & QA

- **Framework**: pytest, function-style only (no classes, no parametrize, no markers, no conftest/pytest.ini). Run: `python -m pytest -q`.
- **Import bootstrap**: every test file starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))` + `# noqa: E402` — replicate this in new test files.
- **Isolation rules** (docs §17): fake **all** network/ffmpeg/model I/O via `monkeypatch.setattr` (patch the aliased module, e.g. `asr_mod.audio_mod.split_chunks`) or fake objects; use `tmp_path` for files — **never touch the real `data/`**; the only real subprocesses/ffmpeg invocations are the deliberate Windows-lock and failure-gate tests (`tests/test_state.py`, `tests/test_retention.py`).
- **Patterns**: Chinese docstrings double as regression tickets (record *why*, e.g. the `os.kill(pid,0)` trap); tiny local helpers (`_cfg()`, `S()` factories); real-clock timing asserts for concurrency tests; `try/finally: scheduler.shutdown()` around any test that calls `scheduler.configure()`.
- Add new files as `tests/test_<topic>.py` and register them in the coverage table at `docs/技术开发文档.md` §17. `pyflakes src tests` must stay clean; quality gate = tests + pyflakes, nothing else.
