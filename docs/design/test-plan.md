# Test Strategy and Test Plan — QA workstream (QR-1 … QR-5)

**Owner:** QA expert
**Inputs:** `PRODUCT-DIRECTION.md`, `SPEC.md`
**Baseline:** repo at `a7c2f29`, clean tree. `make test` → **19 tests, OK, 0.111 s**.
**Scope:** owns QR-1…QR-5; responsible for how FR-1…FR-6, IR-1…IR-7, RR-1…RR-7 get verified.
**Constraint honoured:** nothing in the repo was modified. All findings are read-only observations
with `path:line` citations.

---

## 0. Lead finding — read this first

The truncation bug (PRODUCT-DIRECTION §4) is not an isolated defect. It is one instance of a
**bug class that this codebase systematically produces and this test suite systematically cannot
see: an adapter converts a provider failure into a plausible-looking success, and the pipeline
ships it.**

There are at least five live instances. Only one of them (truncation) is in `SPEC.md`:

| # | Site | Failure | What the user gets | Job outcome | Test today |
|---|---|---|---|---|---|
| 1 | `app/family_media_bot/adapters/story_vertex.py:48-50` | only *empty* text raises; `MAX_TOKENS` partial passes | 122 chars of a story | **acked as success** | none — FR-5 |
| 2 | `app/family_media_bot/adapters/image_vertex.py:85-89` | **every** exception → `_placeholder_png()` returned as a normal `ImageResult` | a blue gradient rectangle | **acked as success** | **none, and not in SPEC.md** |
| 3 | `app/family_media_bot/adapters/story_vertex.py:55-57` | `.get(model_id, (0.0, 0.0))` — unknown model prices at zero | — | cost reported as `$0` | none |
| 4 | `app/family_media_bot/adapters/image_vertex.py:82` | `.get(model_id, 0.0)` — same | — | cost reported as `$0` | none |
| 5 | `app/family_media_bot/pipeline.py:60` | `story.illustration_hint or derive_illustration_prompt(...)` — missing sentinel silently reverts to the first-sentence heuristic | a picture of the wrong-looking child | **acked as success** | none — silently defeats FR-2 |

Instance 2 is the one to act on immediately. It is the same shape as the shipped bug, it is
strictly worse (a child gets a blue rectangle labelled as their illustration, and the pipeline logs
`job completed` with `cost_usd` understated by the whole image cost), and it is **not currently in
scope of any requirement**. Instance 5 is the one that will quietly defeat the headline feature:
FR-2 exists to make characters look consistent, and `pipeline.py:60` provides a silent path back to
the exact heuristic FR-2 is replacing.

The rest of this plan is organised around detecting that class, because coverage of it is worth
more than coverage of anything else here.

---

## 1. Coverage audit of what exists today

`make test` runs 19 tests across four files in 0.111 s. All pass. That number flatters the repo.

### 1.1 What is genuinely covered — and covered well

The Pub/Sub queue adapter is the one component with real, adversarial coverage.
`app/tests/test_gcp_adapters.py:27-201` covers eight distinct behaviours, and they are the right
eight: one-time subscription startup (`:64`), publish-then-ack ordering with an explicit
"not acked before processing" assertion (`:77-101`), dequeue timeout not tearing down the stream
(`:103`), nack releasing the original message object rather than a copy (`:112`), malformed JSON
*and* schema-invalid JSON both nacked (`:128`), flow control matching concurrency (`:144`), and
idempotent shutdown releasing both buffered and in-flight deliveries (`:175-201`). That last one
even asserts `close()` twice is safe. This is the section of the suite that would catch a real
regression, and it directly satisfies AGENTS.md's streaming-queue guidance.

Worker ack/nack semantics are covered adequately at `app/tests/test_worker_delivery.py:26-52`:
ack on success, nack on `False`, nack on unhandled exception. Together with the queue tests this is
a credible proof of contract #4 ("acked only after the pipeline reports success") and of RR-2, at
the unit level.

`app/tests/test_logging_setup.py:10-21` is small but load-bearing: it asserts `httpx`/`httpcore` are
pinned to WARNING, which is the mechanism that keeps the bot token out of logs
(`app/family_media_bot/logging_setup.py:69-72`). Good instinct, correctly targeted.

### 1.2 What only *appears* to be covered

**Contract #5 — "`chat_id` never reaches log records" — is not tested at all.** The one logging
test covers third-party logger levels. Nothing asserts that `JsonFormatter`
(`logging_setup.py:17-44`) does not emit `chat_id`. And `JsonFormatter:40-42` copies *every*
non-reserved key from `record.__dict__` into the payload — so the redaction guarantee is enforced
purely by every call site remembering not to pass `chat_id` in `extra=`. There is no formatter-level
filter and no test. One careless `extra={"chat_id": ...}` silently breaks the GDPR talking point.

**The Vertex story adapter looks tested and is not.** `test_story_generation`
(`test_gcp_adapters.py:225-248`) asserts text, hint, model id, cost, and both token counts. It reads
like coverage. It is not: see §3.2 — the test's own fixture is a truncated story, and the test
asserts that delivering it is correct.

**The Vertex image adapter looks tested and is half-tested.**
`test_gemini_image_generation_uses_production_defaults` (`:251-286`) and
`test_imagen_generation_remains_configurable` (`:289-318`) both cover the happy path only. The
`except Exception` fallback at `image_vertex.py:85-89` — the branch most likely to run in
production — has zero coverage. Neither test would fail if `_invoke` were replaced with
`raise RuntimeError`, because both assert `png_bytes == b"png"`… actually they would fail on the
bytes, but only by luck: had the fixture used a realistic PNG the placeholder would have satisfied
a `len(png_bytes) > 0` assertion. The tests assert *shape*, never *provenance*
(`model_id != "placeholder-fallback"`, `cost_usd > 0`).

**`SqsQueue` and the whole AWS surface are still tested** (`test_queue_adapters.py:31-61`). Under
IR-1 those tests and their subjects get deleted. Worth stating explicitly so the drop from 19 tests
to ~17 is not read as a regression.

**`test_split_illustration_hint` (`test_gcp_adapters.py:18-24`) tests only the happy case.** No test
for the sentinel being absent (which triggers silent-degradation instance 5), appearing mid-story,
appearing twice, or appearing in a translated form. Contract #2 ("the sentinel stays English in all
languages") has no test whatsoever.

### 1.3 Modules with literally zero tests

Confirmed by reading all four test files against the module list:

| Module | Lines | Tested? | What is therefore unverified |
|---|---|---|---|
| `commands.py` | 53 | **no** | `/command@botname` stripping (`:47`), `edited_message` and `caption` fallbacks (`:28-31`), unknown commands → `None`, empty text → `None`. Every one is a parsing edge with a plausible off-by-one. FR-3 adds `/family list` to exactly this untested parser. |
| `prompts.py` | 78 | **no** | `compose_prompt` for all three modes, the bedtime guardrail framing, `derive_illustration_prompt`'s first-sentence extraction and 14-word truncation. FR-2 rewrites this whole module. |
| `app.py` | 159 | **no** | `/healthz` (contract #3, RR-5), `/readyz` aggregation and its 503 (`:100-119`), `/metrics`, `/webhook` secret-token rejection (`:124-127`), malformed-JSON-returns-200 (`:129-134`), unrecognised-update-returns-200 (`:136-139`), enqueue path (`:142-144`). **No FastAPI `TestClient` test exists anywhere in the repo.** |
| `poller.py` | 102 | **no** | plain text → `Mode.CUSTOM` (`:77`), `/start` → greeting (`:72-74`), offset advancement (`:56`), the retry-after-3s loop (`:51-54`), and whether a handler exception advances the offset (it does — `:56` runs before `:58`, so a failing update is skipped, not retried; that is a design choice nobody has asserted). |
| `telegram.py` | 104 | **no** | the 1024-char caption split (`:79-83`) — *the exact boundary implicated in the truncation incident* — the token-disabled no-op paths (`:52-57`, `:70-77`), and `raise_for_status` handling. |
| `pipeline.py` | 116 | **no** | the entire six-step flow, the hint-or-derive fallback (`:60`), cost summation (`:80`), and the failure branch (`:100-115`). |
| `factory.py` | 80 | **no** | every `raise ValueError(f"unknown …")` branch. |
| `config.py` | 79 | **no** | `Literal` validation, `.env` precedence. |
| `models.py` | 48 | indirectly | `new_job` is used by tests; `Job` schema stability (contract #1) is not asserted. |
| `metrics.py`, `telemetry.py` | — | **no** | — |
| `storage_localdir.py`, `story_fake.py`, `image_fake.py`, `story_bedrock.py`, `image_bedrock.py`, `storage_s3.py` | — | **no** | — |

**Summary of the real gap:** the suite covers the queue port thoroughly and covers nothing else
meaningfully. There is not a single test that crosses two components. `Pipeline` is never
instantiated in any test. The HTTP layer is never invoked. Every one of the seven "contracts that
must not break" in SPEC §8 is either untested (#1, #2, #3, #5, #6, #7) or tested only at the unit
level in one direction (#4).

### 1.4 Two environment findings that affect every conclusion above

**The venv is Python 3.14.5; the production image is `python:3.11-slim`** (`app/Dockerfile:2`,
`app/pyproject.toml` `requires-python = ">=3.11"`, `target-version = "py311"`). `make test` green on
3.14 is not evidence about 3.11. CI must run the unit suite on 3.11 to match the artifact.

**`PyYAML 6.0.3` is installed in the venv but is not a declared dependency.** `pip show pyyaml`
reports an empty `Required-by`, and it appears in neither `app/requirements.txt` nor
`app/pyproject.toml`. The Docker image installs *only* `requirements.txt` (`app/Dockerfile:15-16`).
So the moment FR-1's registry loader does `import yaml`, **`make test` passes locally and the
container crashloops on startup in GKE**. See §7 gate G4 for the mechanism that catches this and
§8 risk R2 for the ranking. Alternative that removes the dependency entirely: have Helm read the
registry with `.Files.Get` + `fromYaml | toJson` into the ConfigMap and have the app parse it with
stdlib `json`. Either resolution is fine; silently relying on an orphaned venv package is not.

### 1.5 Correction to PRODUCT-DIRECTION §3.8

The document states there is "one pre-existing 105-character line at
`app/family_media_bot/pipeline.py:73`". Measured by character count (not bytes — several files are
Cyrillic, so `awk length` over-reports), there are **three** violations of the 100-char limit and
the cited line number is off by six:

- `app/family_media_bot/pipeline.py:67` — 105 chars
- `app/family_media_bot/telemetry.py:49` — 101 chars
- `app/family_media_bot/adapters/image_fake.py:19` — 101 chars

`ruff` is not installed in the venv, so this is line-length only; other default rules may add
findings on the first real run. Budget for three-plus fixes, not one.

---

## 2. Test plan for the new work (QR-1)

Conventions, per AGENTS.md §Testing: stdlib `unittest`, `IsolatedAsyncioTestCase` for async, files
`test_<concern>.py` under `app/tests/`, methods `test_<behavior>`, mocks for cloud APIs.

### 2.0 Shared fixtures — build these first

Two small fixture modules pay for themselves across every section below.

**`app/tests/fixtures/vertex.py`** — a factory that builds a **fully-populated** Gemini response,
with every field the real SDK returns, not only the fields the code currently reads. Each test
perturbs exactly one field.

```python
def make_story_response(
    text="Жила-была маленькая звезда, которая училась светить мягко...\nILLUSTRATION: a friendly star",
    finish_reason=FinishReason.STOP,
    prompt_token_count=120,
    candidates_token_count=480,
    thoughts_token_count=64,
    safety_ratings=(),
): ...
```

This is the single highest-leverage item in the plan. §3.3 explains why.

**`app/tests/fixtures/registry.py`** — a canonical two-character registry as both a dict and a
`tmp_path` YAML file, one character with all three languages and one with `en` only:

```yaml
characters:
  - id: mila
    appearance: "5-year-old girl, curly red hair, freckles, green cloak"
    traits: "curious, brave, loves animals"
    names: { en: Mila, ru: Мила, he: מילה }
  - id: pip
    appearance: "small grey rabbit with one folded ear"
    traits: "gentle, sleepy"
    names: { en: Pip }
```

### 2.1 Character registry loader — `app/tests/test_character_registry.py`

| Test | Assertion |
|---|---|
| `test_loads_every_character_from_the_registry_file` | Two entries returned; ids `["mila", "pip"]` in file order; each carries `appearance`, `traits`, `names`. |
| `test_missing_registry_file_raises_at_load_time` | `load_registry("/nonexistent.yaml")` raises; the exception message contains the path. **Must raise, not return `[]`** — an empty registry is the silent-degradation pattern again (the bot would cheerfully generate genericchildren forever). |
| `test_malformed_yaml_raises_with_the_path_in_the_message` | Content `"characters: [{"` raises and the message names the file. |
| `test_registry_without_a_characters_key_raises` | `{"family": []}` raises — a plausible hand-edit typo. |
| `test_entry_missing_a_required_field_raises` | Drop `appearance`; raises naming both the offending `id` and the missing field. Repeat for `id` and `names.en`. |
| `test_duplicate_character_ids_are_rejected` | Two entries with `id: mila` raise. Otherwise last-wins silently and `/family list` shows a duplicate. |
| `test_empty_appearance_string_is_rejected` | `appearance: ""` raises — an empty appearance defeats FR-2 while looking valid. |
| `test_missing_optional_language_falls_back_to_the_english_name` | `pip` resolved with `lang="he"` returns `"Pip"`, not `""` and not `None`. |
| `test_appearance_is_preserved_byte_for_byte` | Loaded `appearance` is `==` the source string — no strip, case-fold, collapse of internal whitespace, or truncation. FR-2 says *verbatim*; this is the test that makes "verbatim" mean something. |
| `test_registry_rejects_any_photo_or_image_reference` | An entry carrying `photo:`, `image:`, `image_url:`, or a value matching a file path/URL is rejected. Enforces SPEC §8 contract #6 ("text descriptions only — never photographs of real children") in code rather than in prose. |
| `test_registry_loads_once_and_is_not_reread_per_request` | Two `get_character()` calls perform one filesystem read (patch `pathlib.Path.read_text`, assert `call_count == 1`). Guards latency and makes the ConfigMap-reload behaviour explicit — see §4 `test_registry_change_rolls_the_pods`. |

### 2.2 Prompt composition — `app/tests/test_prompt_composition.py`

This is where FR-2 lives, and where the product's differentiator either works or does not.

| Test | Assertion |
|---|---|
| `test_appearance_reaches_the_story_prompt_verbatim` | `mila.appearance` is a substring of the composed story prompt. |
| `test_appearance_reaches_the_image_prompt_verbatim` | Same string is a substring of the composed image prompt. |
| `test_both_prompts_carry_the_identical_appearance_substring` | Extract the appearance from each prompt and assert `story_slice == image_slice == registry_value`. **The most important single assertion in the plan.** Two separate substring checks can both pass while a refactor rewords one side; this one cannot. Character consistency across illustrations *is* this assertion. |
| `test_multiple_characters_all_reach_both_prompts` | Two characters requested → both appearance strings present in both prompts, in registry order. |
| `test_image_prompt_does_not_depend_on_the_story_text` | Compose the image prompt twice with two completely different story bodies and the same character; assert byte-identical output. Proves the `prompts.py:62` first-sentence heuristic is genuinely gone rather than merely deprioritised. |
| `test_composition_is_deterministic_for_fairytale_and_custom` | Same inputs → byte-identical prompt across 10 calls. (`Mode.RANDOM` is exempt: `prompts.py:59` uses `random.choice`.) Reproducibility is a precondition for a consistent-looking character. |
| `test_random_mode_is_the_only_nondeterministic_mode` | Seed `random`, assert `Mode.RANDOM` varies and the other two do not. Locks the exemption so nondeterminism cannot leak into the other modes. |
| `test_illustration_sentinel_stays_english_in_every_language` | For `lang in ("en", "ru", "he")`: the system prompt's sentinel line is exactly `ILLUSTRATION:` and the whole line is ASCII. SPEC §8 contract #2, currently untested. |
| `test_illustration_sentinel_is_the_final_line_of_the_instruction` | The sentinel instruction is last, so `split_illustration_hint` can find it. |
| `test_unknown_character_id_raises` | Requesting `id: nobody` raises rather than silently composing a prompt with no character. |
| `test_bedtime_guardrail_is_present_in_every_language_and_mode` | The age-appropriateness framing appears in all 3 × 3 combinations. This is a safety property (`prompts.py:1-6, 17-31`); it must survive the rewrite. |

### 2.3 `/family list` — `app/tests/test_family_list.py`

`commands.py` currently has zero tests (§1.3), so cover the parser and the renderer.

| Test | Assertion |
|---|---|
| `test_family_list_command_is_parsed` | `commands.parse({"message": {...,"text": "/family list"}})` yields a `ParsedCommand` (or the equivalent new type) identifying the list action. |
| `test_family_list_with_botname_suffix_is_parsed` | `/family@ai_family_media_bot list` parses identically — `commands.py:47` already strips `@botname`, and group chats send that form. Untested today. |
| `test_family_list_renders_every_character` | Output contains one line per registry entry. |
| `test_family_list_uses_the_localised_name_when_present` | `lang="ru"` → output contains `Мила`; `lang="he"` → `מילה`. |
| `test_family_list_falls_back_to_the_english_name_when_missing` | `lang="he"` → `Pip` appears (Pip has `en` only) and no empty or `None` placeholder is rendered. |
| `test_family_list_does_not_enqueue_a_job` | Drive the handler with an `AsyncMock` queue; assert `queue.enqueue.assert_not_awaited()`. `/family list` is read-only and must not cost money or occupy the single warm worker. |
| `test_family_add_is_not_a_recognised_command` | `/family add mila ...` does **not** parse into any mutating action — it falls through to the greeting/ignore path. D5 as an executable negative test, so a future contributor cannot half-add a write path. |
| `test_family_list_replies_synchronously_from_the_web_tier` | The reply goes out via `telegram.send_text` in the request path, not through the queue. Keeps the read-only command off the generation pipeline. |

### 2.4 Language selection — `app/tests/test_language_selection.py`

| Test | Assertion |
|---|---|
| `test_default_language_applies_when_none_is_specified` | With `STORY_LANGUAGE` unset, composition uses the configured default. |
| `test_each_supported_language_selects_its_own_system_prompt` | `en`, `ru`, `he` each produce a distinct system prompt; assert three distinct strings and that each contains a language-appropriate marker (e.g. the `ru` prompt contains Cyrillic, the `he` prompt contains Hebrew, the `en` prompt is ASCII apart from the sentinel). |
| `test_unsupported_language_is_rejected_at_configuration_time` | `Settings(story_language="de")` raises `ValidationError`. Implement as `Literal["en","ru","he"]` in `config.py` alongside the existing literals (`config.py:21,28,31-34`). Fail at startup, never at generation time — a misconfigured language must not become a silently-English story. |
| `test_registry_appearance_is_english_in_every_output_language` | For all three languages, the appearance substring in both prompts equals the registry value byte-for-byte. This is the technical justification from PRODUCT-DIRECTION §3.4 turned into a test. |
| `test_hebrew_prompt_contains_no_bidi_control_characters` | Assert none of `U+200E U+200F U+202A-U+202E U+2066-U+2069` appear. Invisible bidi marks pasted from an editor scramble RTL rendering in Telegram and are undiagnosable by eye. |
| `test_language_is_carried_on_the_job_without_changing_the_job_shape` | Whatever the app-architect stream decides for SPEC §8's "how is language carried", assert `set(Job.model_fields) == {"job_id","chat_id","mode","prompt","created_at"}`. SPEC §8 contract #1 says `Job` is frozen; adding a `language` field is allowed only as a called-out decision. This test forces the conversation instead of letting the field appear in a diff. |

### 2.5 Truncation guard — see §3. Cost calculation — `app/tests/test_story_cost.py`

| Test | Assertion |
|---|---|
| `test_cost_includes_thinking_tokens_at_the_output_rate` | `prompt=12`, `candidates=34`, `thoughts=900` → `cost_usd == 0.008424`, i.e. `(12×1.50 + 934×9.00)/1e6`. Compare against today's `0.000324` asserted at `test_gcp_adapters.py:241`: the same call is currently reported **26× cheaper than it is**. |
| `test_thinking_tokens_are_reported_separately_from_output_tokens` | `StoryResult` exposes `tokens_thought` distinctly from `tokens_out`, and the `story done` log record carries both. PRODUCT-DIRECTION §4 fix part 4 — the split must be *visible*, not just summed into the cost. |
| `test_absent_thinking_token_field_is_treated_as_zero_not_as_an_error` | A response whose `usage_metadata` lacks `thoughts_token_count` still produces a cost — 2.5-family models may not populate it. Prevents the fix from breaking a model swap. |
| `test_regional_surcharge_applies_to_thinking_tokens_too` | With `vertex_text_location="us-central1"`, the 1.1 multiplier at `story_vertex.py:58-60` applies to the *combined* output count. Easy to fix the cost bug and forget the surcharge path. |
| `test_unknown_text_model_id_does_not_silently_price_at_zero` | `Settings(vertex_text_model_id="gemini-9-ultra")` → the adapter raises at construction (or emits a `WARNING` and a distinguishable metric). Today `story_vertex.py:55-57` returns `(0.0, 0.0)` and every job reports free. Silent-degradation instance 3. |
| `test_unknown_image_model_id_does_not_silently_price_at_zero` | Same for `image_vertex.py:82`. Instance 4. |
| `test_pipeline_cost_is_the_sum_of_story_and_image_cost` | `Pipeline.process` with stub providers → `JOB_COST` observed value equals `story.cost_usd + image.cost_usd` (`pipeline.py:80`). First test in the repo to instantiate `Pipeline`. |

---

## 3. The truncation regression (QR-2)

### 3.1 The regression test

New file `app/tests/test_story_integrity.py`. The fixture is the **real observed symptom** — a
~122-character Russian fragment ending mid-sentence — so the test reads as the incident it prevents:

```python
# The literal shape of what reached a real chat: a fragment, not a story.
TRUNCATED_RUSSIAN_FRAGMENT = (
    "Жила-была маленькая звезда, которая очень стеснялась темноты и каждый "
    "вечер пряталась за облаком, пока луна не"
)
```

`StoryIntegrityTests(IsolatedAsyncioTestCase)` — adapter level:

| Test | Assertion |
|---|---|
| `test_max_tokens_finish_reason_raises` | `make_story_response(text=TRUNCATED_RUSSIAN_FRAGMENT, finish_reason=MAX_TOKENS)` → `generate()` raises. The core guard, FR-5. |
| `test_stop_finish_reason_is_accepted` | `finish_reason=STOP` with a full story → returns normally. Prevents the guard from being over-tightened into a permanent outage. |
| `test_absent_finish_reason_is_treated_as_a_failure` | `make_story_response(finish_reason=None)` → raises. **This is the test that makes the class of bug impossible to reintroduce.** The original defect existed because a field the code never read was indistinguishable from a field that was absent. If "unknown" is treated as "fine", an SDK rename or a mock that forgets the field restores the bug in silence. Unknown must mean unsafe. |
| `test_empty_candidates_list_is_treated_as_a_failure` | `candidates=[]` → raises, rather than `getattr` chaining to `None` and passing. |
| `test_safety_and_recitation_finish_reasons_also_fail_the_job` | `SAFETY`, `RECITATION`, `OTHER` → raise. Same class: a blocked generation must not be delivered as a story. |
| `test_story_shorter_than_the_minimum_fails_even_when_finish_reason_is_stop` | `text=TRUNCATED_RUSSIAN_FRAGMENT, finish_reason=STOP` → raises. **Provider-independent defence in depth.** `finish_reason` is a Google-specific contract that can change; a length floor cannot. Set the floor low (≈40 words / 200 characters) so it never fires on a legitimately short story, only on a fragment. |
| `test_token_budget_leaves_headroom_beyond_the_word_target` | `max_output_tokens` passed to `generate_content` is `>= story_max_words * 4 + 2048`. Locks PRODUCT-DIRECTION §4 fix part 1 so a future "tidy-up" cannot quietly restore `max(1024, words*4)` (`story_vertex.py:42`). |
| `test_thinking_level_is_constrained` | The `config` carries the minimal-thinking setting. Locks fix part 2. Assert on the field the model actually accepts, resolved by PRODUCT-DIRECTION §9's open item. |

`TruncatedStoryDeliveryTests(IsolatedAsyncioTestCase)` — **pipeline level, and the test that
actually states FR-5**:

| Test | Assertion |
|---|---|
| `test_a_truncated_story_is_never_sent_to_telegram` | Real `Pipeline` (`pipeline.py:26`) wired with a story provider that raises the truncation error, plus `AsyncMock` image/storage/telegram. Assert `telegram.send_story_and_image.assert_not_awaited()`, `storage.save.assert_not_awaited()`, `image.generate.assert_not_awaited()`, and `process() is False` so the worker nacks and the queue retries. This asserts the requirement in the user's own terms: *nothing partial reaches the chat.* |
| `test_a_truncated_story_does_not_bill_an_image_generation` | Failing before the image call means no `$0.039` spent on an illustration for a story that will not ship. |
| `test_repeated_failures_do_not_spam_the_chat_with_apologies` | **Defect the FR-5 fix introduces.** `pipeline.py:104-110` sends "Простите, сказка сейчас не получилась" on *every* failure, and FR-5 deliberately converts truncation into a retryable failure. With the Pub/Sub DLQ at 5 attempts, one `/fairytale` yields **five apology messages** to a child before the job dies. Assert the apology is sent at most once per `job_id` — or, cleaner, only on the terminal attempt. Without this, the FR-5 fix is a worse user experience than the bug it replaces. Flag to the reliability-engineer stream: this needs a delivery-attempt signal from the queue port. |

### 3.2 Why the existing suite missed it — four compounding reasons

**(a) The mock was derived from the implementation, not from the API.** `test_gcp_adapters.py:227-233`
constructs the response as
`SimpleNamespace(text=..., usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=34))`.
That object contains **exactly** the attributes `generate()` reads at `story_vertex.py:48,53,54` and
nothing else. `finish_reason` and `thoughts_token_count` are missing from the fixture *because they
are missing from the code*. A mock built by reading the code under test can only ever confirm that
the code does what the code does. It is structurally incapable of revealing a field the code fails
to read — which is precisely what both bugs were.

**(b) The fixture is itself a truncated story, and the test asserts that shipping it is correct.**
`test_gcp_adapters.py:238` asserts `result.text == "Жила-была звезда."` — **17 characters**. The
suite's own model of a successful story generation is a fragment shorter than the one that reached
production. There is no assertion anywhere about story length, word count, or completeness. The
test did not merely fail to catch the bug; it encoded the bug's output as the expected result.

**(c) No test crosses a component boundary.** All 19 tests stop at a single unit. `Pipeline` is never
instantiated. `TelegramClient` is never exercised. Nothing in the suite asks the question the user
asks — "what arrived in the chat?" — so the only place the defect was observable was production.
The 1024-char caption branch at `telegram.py:79-83`, directly implicated in the initial (wrong)
diagnosis, has no test either.

**(d) Defensive `getattr` defaults erase the difference between "absent" and "zero".**
`story_vertex.py:53-54` uses `int(getattr(usage, "prompt_token_count", 0) or 0)`. Under that idiom a
field the provider never sent, a field the SDK renamed, and a genuine zero are the same value. Code
written this way cannot report its own blind spots, and tests written against it inherit the
blindness.

### 3.3 The generalisation — what else of this class is invisible right now

The class is: **an adapter converts a failure or a missing signal into a plausible success value,
and every layer above treats it as real.** Section 0 lists five live instances with citations;
`worker.py:39-41` (`except: pass` around the depth metric), `logging_setup.py:34` (`except: pass`
around OTel correlation), `telegram.py:26,52-57,70-77` (no token → log "would send" and return
success, so `pipeline.py:77` logs `replied` when nothing was sent), and `app.py:110-113` (a crashed
readiness check is indistinguishable from an unready dependency) are four more in a lower-severity
tier.

Three rules turn that from an anecdote into a policy, each with a mechanical test:

**R1 — An adapter may not return a value it did not obtain from the provider.**
Fallback is a *caller's* decision, because only the caller can log it, count it, and decide whether
the user should be told. Concretely: delete the `except Exception` at `image_vertex.py:85-89` and let
it raise; the pipeline already has a failure path (`pipeline.py:100-115`) and the queue already
retries. Test: `test_image_generation_failure_raises_instead_of_returning_a_placeholder` — patch
`_invoke` to raise, assert `RuntimeError`, and assert no `ImageResult` with
`model_id="placeholder-fallback"` can be constructed by the adapter at all.

**R2 — Assert provenance, not shape.** `assertEqual(result.png_bytes, b"png")` is a shape assertion;
it would pass on a placeholder had the fixture been a realistic PNG. Every adapter happy-path test
gains two lines: `assertEqual(result.model_id, <the configured model id>)` and
`assertGreater(result.cost_usd, 0)`. A degraded result fails both. This is a five-minute change to
`test_gcp_adapters.py:275-278` and `:305-309` that would have caught instances 2, 3, and 4.

**R3 — Mocks are built from the provider's response type, not from the fields the code reads.**
This is §2.0's `make_story_response()` factory: one fully-populated default, each test perturbing a
single field. Where the SDK type is stable enough, prefer
`create_autospec(google.genai.types.GenerateContentResponse, instance=True)` over `SimpleNamespace`,
so a typo'd attribute raises instead of silently returning a `MagicMock` (a `MagicMock` attribute is
truthy, which is its own silent-success hazard). R3 is what makes reason (a) structurally
impossible to repeat.

Add one meta-test to keep the policy honest: `test_no_adapter_swallows_exceptions_silently` in
`app/tests/test_adapter_contracts.py` — walk the AST of `family_media_bot/adapters/*.py`, find every
`except` handler that does not re-raise, and assert the set matches a small explicit allow-list with
a comment justifying each. New silent fallbacks then fail CI by construction rather than by
someone remembering this document.

---

## 4. Helm verification (QR-3)

### 4.1 Mechanism

`helm template` exiting 0 proves the templates parse. It proves nothing about what they render — the
chart today renders a KEDA `ScaledObject`, a `workload=jobs` toleration, and `role:` nodeSelectors,
all of which IR-2/IR-3 delete, and `helm template` will keep exiting 0 after a botched deletion.

**Mechanism: a stdlib `unittest` file, `app/tests/test_helm_render.py`, that shells out to
`helm template` once per test-class and parses the output with `yaml.safe_load_all`.** Rationale:

- It runs inside `make test`, so CI needs no new language, runner, or plugin (no `helm-unittest`,
  no conftest/OPA). One command still means one gate.
- Rendered manifests become ordinary Python dicts, so assertions are readable and diffable.
- It follows AGENTS.md conventions exactly.

Two implementation details that matter:

```python
@classmethod
def setUpClass(cls):
    if shutil.which("helm") is None:
        if os.environ.get("REQUIRE_HELM"):
            raise RuntimeError("helm is required in CI")   # hard fail in CI
        raise unittest.SkipTest("helm not installed")      # graceful skip locally
    out = subprocess.run([...], capture_output=True, check=True, text=True).stdout
    cls.docs = [d for d in yaml.safe_load_all(out) if d]
    cls.raw = out
```

`REQUIRE_HELM=1` in the CI job turns the local convenience-skip into a CI hard failure, so the gate
cannot be silently no-op'd by a runner without helm. **`pyyaml` must be added to a test/dev
requirements file for this (see §1.4) — it is currently an orphan in the venv.**

Render once per class with `--set image.repository=example/repo --set image.tag=test` (IR-6 forbids a
committed tag, and both deployments use `required` at `deployment-web.yaml:30` /
`deployment-worker.yaml:33`).

### 4.2 Concrete assertions

**Decision locks (D2, D3, IR-2, IR-3):**

| Test | Assertion |
|---|---|
| `test_worker_renders_exactly_one_replica` | The worker Deployment's `spec.replicas == 1`. Today it renders `.Values.worker.minReplicaCount` = `0` (`values.yaml:41`, `deployment-worker.yaml:12`) — this test fails at baseline, correctly. |
| `test_no_keda_objects_are_rendered` | No document has `kind` in `{"ScaledObject", "TriggerAuthentication"}`. Baseline renders both (verified: one each). |
| `test_no_node_selectors_are_rendered` | No pod spec has `nodeSelector`. Baseline renders `role: web` and `role: worker`. |
| `test_no_tolerations_are_rendered` | No pod spec has `tolerations`. Baseline renders the `workload=jobs` toleration. |
| `test_exactly_two_deployments_render_named_web_and_worker` | D3 keeps the tier split; this stops a future "simplification" from collapsing them. |

**AWS removal (IR-1, acceptance §10):**

| Test | Assertion |
|---|---|
| `test_no_aws_environment_variables_appear_in_any_manifest` | `cls.raw` contains none of `AWS_REGION`, `SQS_QUEUE_URL`, `S3_BUCKET`, `BEDROCK_`, `eks.amazonaws.com`, `irsa`. Case-insensitive over the whole rendered text, not just the ConfigMap — catches annotations and labels too. |
| `test_the_chart_has_no_cloud_provider_conditional` | `helm template --set cloud.provider=aws` **fails** (schema rejects the key or the value). Proves the conditional at `configmap.yaml:15-32` and `scaledobject.yaml:9-18` is really gone, rather than merely defaulted to `gcp`. |

**Registry delivery — the app/infra seam (IR-4, FR-1, FR-2):**

| Test | Assertion |
|---|---|
| `test_configmap_contains_the_character_registry` | The ConfigMap `data` carries the registry, and a known character id (`mila`) *and its full appearance string* appear verbatim in the rendered value. This is the only assertion in the whole plan that proves the FR-2 appearance string survives the trip from git to the pod. |
| `test_registry_is_sourced_from_the_git_file_not_from_values` | The rendered registry content equals the on-disk registry file (rendered via `.Files.Get`), so the ConfigMap cannot drift from the file characters are edited in. |
| `test_both_deployments_carry_a_config_checksum_annotation` | Both pod templates have `checksum/config` (or `checksum/registry`) in `spec.template.metadata.annotations`. **This is a real defect in the current design:** both deployments consume the ConfigMap via `envFrom` (`deployment-web.yaml:35-37`, `deployment-worker.yaml:38-40`), and `envFrom`-injected environment variables **never refresh in a running pod**. Without a checksum annotation, `helm upgrade` after a character edit updates the ConfigMap and leaves both pods serving the old registry — so IR-4's "changing a character must not require an image rebuild" is true but useless, because it silently requires a manual `kubectl rollout restart`. The test forces the annotation. |

**Image immutability (IR-6):**

| Test | Assertion |
|---|---|
| `test_no_values_file_commits_an_image_tag` | Pure Python, no helm: load every file in `deploy/values/`, assert `image.tag` is absent or `""`. Baseline: `deploy/values/gcp.yaml:6` pins `tag: streaming-pubsub-20260719-111322`. |
| `test_template_fails_when_no_image_tag_is_supplied` | `helm template` without `--set image.tag` exits non-zero. Proves the `required` guard is load-bearing. |
| `test_both_containers_use_the_same_image_reference` | Web and worker render an identical `image:` string — a split-brain deploy where one tier is a release behind is invisible otherwise. |

**Health, reliability, and security (RR-3, RR-5, IR-5, contract #3):**

| Test | Assertion |
|---|---|
| `test_liveness_probes_target_healthz_and_readiness_probes_target_readyz` | Exact paths on both deployments. Swapping them is a one-character edit that causes a cluster-wide restart loop the moment Vertex has a bad minute — contract #3 in manifest form. |
| `test_worker_termination_grace_period_exceeds_the_pipeline_budget` | `terminationGracePeriodSeconds >= 120` on the worker (RR-3, RR-7). |
| `test_helm_test_hook_targets_healthz` | A `Pod` with `helm.sh/hook: test` exists and its command references `/healthz`, not `/readyz` — a test hook that depends on Vertex reachability makes `--atomic` roll back good releases (IR-6, D8). |
| `test_service_account_carries_the_workload_identity_annotation` | `iam.gke.io/gcp-service-account` present (IR-5). |
| `test_no_manifest_mounts_a_service_account_key` | No `secretKeyRef`/volume named like `*-key.json`, no `GOOGLE_APPLICATION_CREDENTIALS` env var. "No keys anywhere" as a test. |
| `test_all_containers_run_as_non_root_with_all_capabilities_dropped` | Locks `deployment-web.yaml:58-66` and `deployment-worker.yaml:61-67`. |

**Blocking prerequisite discovered while auditing:** `grep -rn "kind: Service$" deploy/` returns
nothing — **the chart has no Service template at all**. `helm test` hitting `/healthz` needs a stable
in-cluster address. IR-6 as written cannot be implemented until a Service is added. Add
`test_a_service_targets_the_web_deployment` (correct selector, port 8080, matches the web pod
labels) and treat the Service as part of the IR-6 work item. Flag to the infra-architect stream.

### 4.3 What Helm rendering still cannot prove

Rendered YAML is a static artifact. It cannot show that the image starts, that the ConfigMap keys
match the names `config.py` actually reads (a `VERTEX_TEXT_MODEL_ID` → `VERTEX_TEXT_MODEL` typo in
`configmap.yaml:23` renders perfectly and is silently ignored by `pydantic-settings`
`extra="ignore"`, `config.py:17`), that probes pass, or that the two tiers can reach each other.
That is exactly the gap §5 fills — and the ConfigMap-key-typo case is the single best argument for
having a cluster test at all.

---

## 5. kind smoke test (QR-4)

### 5.1 Identity between laptop and CI

Identity comes from **one script that both callers invoke verbatim**, not from a workflow file that
paraphrases the local steps.

- `scripts/kind-smoke.sh` — the whole test, exit code is the verdict.
- `Makefile`: `kind-smoke: ; ./scripts/kind-smoke.sh` (plus `kind-up` / `kind-down` for iterating).
- GitHub Actions: `- run: ./scripts/kind-smoke.sh`. Nothing else. If the workflow ever contains a
  `kubectl` or `helm` line the script does not, the guarantee is void.

Everything version-sensitive is pinned **in the script**, not in the workflow: the `kind` binary
version, the node image **by digest** (`kindest/node:v1.31.x@sha256:…`), the Helm version, the
Pub/Sub emulator image digest, and every `--timeout`. Unpinned versions are how "runs identically"
quietly stops being true.

### 5.2 Sequence

```
1. kind create cluster --name fmb --config deploy/kind/cluster.yaml   # 1 node, pinned digest
2. docker build -t family-media-bot:smoke app/
3. kind load docker-image family-media-bot:smoke --name fmb           # no registry, no auth
4. kubectl create namespace app
   kubectl -n app create secret generic telegram-bot-token \
       --from-literal=TELEGRAM_BOT_TOKEN=smoke-token-not-a-real-token
5. kubectl -n app apply -f deploy/kind/pubsub-emulator.yaml           # emulator + Service
   kubectl -n app apply -f deploy/kind/telegram-stub.yaml             # HTTP echo + Service
6. helm upgrade --install fmb deploy/charts/family-media-bot -n app \
       -f deploy/values/prod.yaml -f deploy/kind/values.yaml \
       --set image.repository=family-media-bot --set image.tag=smoke \
       --atomic --wait --timeout 3m
7. helm test fmb -n app --logs
8. assertions (§5.3)
9. always: kubectl -n app describe pods; kubectl -n app logs -l app.kubernetes.io/name=... --all-containers --tail=200
   kind export logs ./kind-logs   (uploaded as a CI artifact)
10. kind delete cluster --name fmb   (trap EXIT, so a failed run still cleans up)
```

`--atomic --wait` makes the failure mode a rollback with logs rather than a hang, and it exercises
the same flag the main-branch deploy uses (PRODUCT-DIRECTION §3.7). Total runtime ≈ 3–4 minutes.
Acceptable as a PR gate.

### 5.3 Assertions

| # | Assertion | Requirement |
|---|---|---|
| 1 | `helm upgrade --atomic --wait` succeeds | IR-6, RR-6 |
| 2 | `helm test` passes and its log shows an HTTP 200 from `/healthz` | IR-6, RR-5, contract #3 |
| 3 | Both Deployments report `Available`; worker `.status.replicas == 1` | D2, RR-1 |
| 4 | `kubectl -n app get scaledobject` → `NotFound` **and the install succeeded without the KEDA CRDs installed** | IR-2. `helm template` cannot prove this; a live cluster with no KEDA CRDs can. |
| 5 | `kubectl -n app get cm fmb-config -o jsonpath=…` contains `mila` and its appearance string | IR-4, FR-1 |
| 6 | `kubectl -n app exec deploy/fmb-web -- python -c "…urlopen('http://127.0.0.1:8080/readyz')"` → 200 | RR-5 |
| 7 | POST `docs/sample-update.json` to the web pod's `/webhook` (via `kubectl port-forward`), then poll worker logs for `job completed` with a 90 s budget | **The only end-to-end assertion in the plan**: web → Pub/Sub → worker → pipeline → storage → Telegram, as deployed, across two pods |
| 8 | The telegram-stub's request log shows exactly one `sendPhoto` for that job | contract: the delivery leg actually fires |
| 9 | Worker logs for the run contain no `chat_id` key: `kubectl logs … \| grep -c '"chat_id"'` → 0 | **contract #5**, currently untested anywhere |
| 10 | `kubectl -n app delete pod -l component=worker` mid-job, then assert the job still completes after the replacement pod starts | RR-7, RR-3 — optional stretch; add once 1–9 are green |
| 11 | Restore-the-ConfigMap check: `helm upgrade` with an edited registry, assert both pods roll (new `pod-template-hash`) and `/family list` reflects the edit **with no image rebuild** | IR-4, acceptance §10 bullet 3 |

Assertion 9 is worth calling out: it is the only proposed check anywhere that verifies the PII
contract against real log output rather than against intent.

### 5.4 The fake-provider strategy — and its honest limits

Workload Identity does not exist in kind: there is no GKE metadata server, so
`google.auth.default()` (`story_vertex.py:75-77`, `image_vertex.py:93-95`) fails and the real GCP
adapters cannot authenticate. The strategy is therefore **split by port, not uniformly faked** —
fake the things that need credentials, keep real the things that carry the architecture:

| Port | kind implementation | Why |
|---|---|---|
| Story | `story_provider=fake` (`story_fake.py`) | Needs Vertex credentials. Also makes the run deterministic and free. Model behaviour is unit-test and manual-demo territory, not cluster territory. |
| Image | `image_provider=fake` (`image_fake.py`) | Same. `_placeholder_png()` is a real, valid PNG (`image_fake.py:14-35`), so the storage and Telegram legs get genuine bytes. |
| Storage | `storage=local` | GCS needs credentials. Writes go to the container filesystem; assertion 7 reads the `saved` log line. |
| **Queue** | **real `PubSubQueue` against the `gcloud beta emulators pubsub` container**, via `PUBSUB_EMULATOR_HOST` | **The important one.** `google-cloud-pubsub` reads `PUBSUB_EMULATOR_HOST` natively and skips auth entirely, so **no application code change is needed**. Faking the queue with `inmemory` would break the test: the in-memory queue is per-process, so a web pod's queue is invisible to the worker pod and the two-tier topology — the whole reason to test in a cluster — would not be exercised at all. |
| **Telegram** | **real `TelegramClient` against an in-cluster HTTP stub**, via `TELEGRAM_API_BASE` | Also important. With no token, `telegram.py:26` sets `self._client = None` and every send becomes a logged no-op (`:52-57`, `:70-77`) while `pipeline.py:77` still logs `replied`. The delivery leg would be entirely unexercised. A stub plus a dummy token makes `sendPhoto` a real HTTP request (assertion 8). |

`deploy/kind/values.yaml` therefore overrides: the four adapter selections, `PUBSUB_EMULATOR_HOST`,
`TELEGRAM_API_BASE`, and it drops the `iam.gke.io/gcp-service-account` annotation (harmless in kind,
but leaving it in invites the belief that WI is being tested).

**What kind cannot verify — state this in the README so the artifact is not oversold:**

- **Workload Identity and all GCP IAM.** No metadata server, no GSA binding, no least-privilege
  proof. IR-5 and the "no keys anywhere" claim are verified by Terraform review and by the live
  deploy, never by kind.
- **Real model behaviour.** Truncation, `finish_reason`, safety filtering, token accounting, cost,
  and — critically — **reference-image character consistency (FR-2's actual payoff)**. Covered by
  §2/§3 unit tests and §6 manual checks. kind proves the appearance string reaches the pod; only a
  live run proves it makes the child look the same twice.
- **Real Pub/Sub semantics.** The emulator gives topics, subscriptions, ack/nack and redelivery. It
  does **not** faithfully emulate dead-lettering after 5 attempts or ack-deadline extension, so
  **RR-4 is not proven here** — verify it by Terraform assertion on the subscription config plus one
  live test.
- **GKE specifics:** node pools, taints, Cloud NAT, private nodes, preemption, real scheduling
  pressure. IR-3 is verified by Terraform and by `helm template` (§4), not by kind.
- **Image pull from Artifact Registry** — `kind load` side-loads, so registry auth is untested.
- **Resource realism.** kind nodes share the host kernel. A memory-limit OOM in kind is meaningful;
  CPU throttling and latency numbers are not. Never quote a kind latency figure.
- **Anything about the real dataset or real chats.**

---

## 6. Manual demo checklist (QR-5)

Ordered. Run top to bottom; ~10 minutes. Stop at the first ✗.

**A. Pre-flight (before touching Telegram)**

1. `git status` clean and `git log -1` matches the deployed image digest
   (`kubectl -n app get deploy fmb-worker -o jsonpath='{..image}'`). If the tag is not the commit,
   stop — IR-6 has regressed to hand-editing.
2. `kubectl -n app get pods` → web `1/1`, worker `1/1`, both `Running`, restart count `0`.
   Worker must already be up: RR-1's whole point is that nothing cold-starts while a child waits.
3. `kubectl -n app get cm fmb-config -o yaml | grep -A5 registry` → the characters you expect are
   present, and match `git show HEAD:<registry path>`.
4. **Confirm no second poller is running.** Only one `getUpdates` consumer is allowed per bot token
   (`poller.py:5-7`); a stray local `make demo` makes Telegram return 409 and the cluster bot goes
   deaf. Check no local process is running and `kubectl -n app get pods -l component=web` shows
   exactly one.
5. Trial credit still positive, and `kubectl -n app logs deploy/fmb-worker | tail` shows no
   auth errors.

**B. Read-only path**

6. Send `/family list` → replies within ~2 s, lists every character, names rendered in the active
   language, no `None`/empty entries, no raw dict or YAML leakage.
7. Worker logs show **no** new job for that command (`/family list` must not enqueue — §2.3).

**C. Generation path**

8. Send `/fairytale` naming one character. Expect the ack message within ~2 s, then story + image
   within ~30 s.
9. **Story is complete**: reads as a whole story with a proper ending, not a fragment; roughly the
   target length; correct language throughout; no stray `ILLUSTRATION:` line in the visible text.
10. **The named character's traits are recognisably in the text** — FR-2 on the story side.
11. Worker log line for the job shows: `finish_reason` STOP, `tokens_out` **and** `tokens_thought`
    both present and non-zero, `cost_usd` **> 0** and plausibly ≈ `$0.04–0.05`
    (image `$0.039` + story). `cost_usd` of exactly `0.039` means the story priced at zero — an
    unknown-model-id regression (§2.5).
12. Image log line shows `model: gemini-2.5-flash-image`. **If it shows `placeholder-fallback`, the
    illustration is the synthetic gradient and the demo is broken even though the job succeeded** —
    silent-degradation instance 2. Check this every single time until R1 (§3.3) lands.
13. `kubectl -n app logs deploy/fmb-worker --since=5m | grep -c chat_id` → **0**. Contract #5, and a
    thing worth being able to demonstrate live.

**D. Character consistency — what "good" looks like**

14. Send a **second** `/fairytale` with the **same character** in a clearly different setting
    ("at the seaside", "in a snowy forest"). Put the two illustrations side by side.
15. Score against these five attributes, each judged Same / Different:
    - **hair** — colour *and* style (the single most common drift)
    - **apparent age and build**
    - **skin tone**
    - **the signature garment or prop** from `appearance` (the green cloak, the folded ear)
    - **species and count** for animal characters — one grey rabbit, not two, not a hare
16. **Pass = at least 4 of 5 Same on both images, with the signature item among them.** The
    signature item is non-negotiable: it is the cheapest thing for a child to recognise and the
    thing the `appearance` string exists to pin.
17. **Automatic fail regardless of score:** an extra or missing family member; a face or limb count
    that is wrong; text or letterforms rendered inside the image; a character that has drifted to a
    generic default child; anything that could read as a photograph of a real child rather than an
    illustration (contract #6).
18. If consistency fails, check in this order before blaming the model: (a) does the image prompt
    in the logs contain the `appearance` string verbatim, or did `pipeline.py:60` fall back to the
    first-sentence heuristic; (b) is the reference-image path actually wired (PRODUCT-DIRECTION §9
    open item — it is listed as *unverified*); (c) only then, prompt wording.

**E. Language and resilience**

19. Repeat step 8 in each configured language. Hebrew specifically: the caption renders
    right-to-left with punctuation on the correct side, and no `ILLUSTRATION:` line leaks into the
    visible text (contract #2 — the sentinel stays English and must be stripped in every language).
20. `kubectl -n app delete pod -l app.kubernetes.io/component=worker` and immediately send a
    request. It should complete once the replacement is up, with no duplicate and no lost job
    (RR-7). Confirm exactly **one** reply arrives.
21. Confirm you did not receive multiple apology messages for any single request (§3.1 retry-storm
    defect).

**F. After**

22. Note the observed end-to-end latency and the summed `cost_usd`. Both are talking points, and
    both are only credible if measured on the run you just did.

---

## 7. CI gates

Governing principle: **block on anything deterministic and hermetic; report on anything that depends
on the live cloud, a third-party feed, or a model's output.** A gate that can fail for reasons
unrelated to the diff teaches people to bypass gates, and a bypassed gate is worse than none.

### Blocking on pull request

| # | Gate | Runtime | Rationale |
|---|---|---|---|
| G1 | `ruff check` + `ruff format --check` | ~5 s | Deterministic, zero-judgment. Expect the three fixes from §1.5 in the enabling PR. |
| G2 | `make test` **on Python 3.11**, with `REQUIRE_HELM=1` | ~30 s | The correctness core, including the §4 rendered-manifest tests. 3.11 because that is what `Dockerfile:2` ships — a green 3.14 run is not evidence about the artifact (§1.4). |
| G3 | `helm lint` + `helm template` on `deploy/values/prod.yaml` | ~5 s | Cheap, and gives a clearer error message than a failed assertion when the chart itself is broken. Kept separate from G2 deliberately. |
| G4 | `docker build` **then** `docker run --rm IMAGE python -c "import family_media_bot.app"` | ~60 s | The undeclared-dependency gate. Catches the `pyyaml` trap in §1.4 and every future instance of it, because it imports the app in the *image*, not the venv. Do not settle for `docker build` alone — build succeeds with a missing runtime import. |
| G5 | `./scripts/kind-smoke.sh` | ~4 min | The only pre-merge check that proves the thing starts and the two tiers talk (§5). |
| G6 | `terraform fmt -check -recursive` + `terraform init -backend=false` + `terraform validate` | ~30 s | Hermetic — no credentials, no state, no network beyond provider download. Safe to block. |
| G7 | Secret scan (`gitleaks`) on the diff | ~10 s | The repo is one careless `git add -f` away from committing `app/.env` or `infra/gcp/tfplan`. `.gitignore` covers both today and nothing is tracked (verified), but a gate is cheaper than a token rotation. |

Total ≈ 6 minutes wall clock; G1–G4 and G6–G7 run in parallel with G5.

### Reporting only (visible, never blocking)

| Check | Why not blocking |
|---|---|
| `terraform plan` posted as a PR comment (IR-7) | Requires live GCP auth and reflects the *current* state of a shared project. It can legitimately show drift caused by someone else, or fail because a credential expired. Blocking makes every PR hostage to the environment. Post it, require a human to read it, and gate the *apply* on main instead. |
| Coverage percentage | Report the number; never set a threshold. This plan deliberately prefers a small number of high-value tests (SPEC's own framing). A coverage gate rewards writing tests for `factory.py`'s five `raise ValueError` lines instead of for `pipeline.py`'s failure path. |
| `trivy image` vulnerability scan | Report initially. Promote HIGH/CRITICAL to blocking once the base image is pinned by digest — until then, a `python:3.11-slim` rebuild can turn a green PR red overnight with no diff. |
| Image size delta | Informational. |
| Any story-quality or LLM-as-judge evaluation | Non-deterministic by construction. It must never be able to block a merge. |

### Blocking on push to `main`

| # | Gate |
|---|---|
| M1 | Everything in G1–G7 re-run on the merge commit (no "it passed on the PR" shortcut — the merge result is a different tree). |
| M2 | Build and push to Artifact Registry, tagged with **the commit digest, not a mutable tag**. |
| M3 | `helm upgrade --install --atomic --wait --timeout 5m --set image.tag=<digest>`. `--atomic` supplies RR-6 rollback. |
| M4 | Post-deploy verification: `helm test`, then assert the running image digest equals M2's output. Catches a partially-applied or rolled-back release that still reports success. |
| M5 | On failure of M3/M4: fail the workflow loudly. `--atomic` has already rolled back; the job must not report green. |

**Auth for M2/M3 is Workload Identity Federation (D9, IR-5).** Add a repository-level gate too:
assert no GitHub Actions secret is consumed whose name matches `*_SA_KEY`/`*_JSON`. Cheap grep over
`.github/workflows/*.yml`, and it makes "no keys anywhere" a test rather than a claim.

---

## 8. Risk-ranked gap list

Ranked by expected escape cost = likelihood × user impact × (1 − detection probability today).

| # | Gap | Likelihood | Impact | Detected today | Mitigation |
|---|---|---|---|---|---|
| **R1** | **Silent degradation in adapters** — `image_vertex.py:85-89` returns a placeholder PNG as success; `story_vertex.py:55-57` and `image_vertex.py:82` price unknown models at $0; `pipeline.py:60` silently reverts to the first-sentence heuristic. | **High** — one instance already shipped to real users | **High** — a child gets a blue rectangle or a stranger's face, logged as `job completed` | **None** | §3.3 rules R1/R2/R3 + the AST meta-test; §6 step 12 as the human backstop until R1 lands |
| **R2** | **Undeclared runtime dependency.** `pyyaml` is in the venv but in neither `requirements.txt` nor `pyproject.toml` (verified: `Required-by` empty). The image installs only `requirements.txt`. | **High** the moment FR-1 lands | **Total** — CrashLoopBackOff, whole bot down, and `make test` was green | **None** | CI gate **G4** (import the app inside the built image). Or eliminate: render the registry to JSON in the ConfigMap and parse with stdlib. |
| **R3** | **ConfigMap edits never reach running pods.** Both deployments use `envFrom` only; no `checksum/config` annotation. IR-4's "no image rebuild" is technically true and operationally false. | **High** — will happen the first time a character is edited | Medium — demo looks broken, wrong characters persist | **None** | §4.2 `test_both_deployments_carry_a_config_checksum_annotation`; §5.3 assertion 11 |
| **R4** | **Retry storm of apology messages.** FR-5 makes truncation retryable; `pipeline.py:104-110` apologises on every attempt; DLQ at 5 attempts ⇒ **five apologies per request**. The fix is worse than the bug for the end user. | **High** — it is a direct consequence of the FR-5 design | **High** — this is the demo, in front of a child | **None** | §3.1 `test_repeated_failures_do_not_spam_the_chat_with_apologies`; needs an attempt-count signal from the queue port — raise with the reliability-engineer stream |
| **R5** | **Prompt-composition timing (SPEC §8 open question).** Composing at enqueue (`app.py:142`, `poller.py:83`) bakes the character into `Job.prompt`; a job that outlives a registry edit renders the old character, and after the FR-5 fix a *retry* can render a different character than the first attempt. | Medium | Medium — subtle, demo-visible, hard to reproduce | **None** | Whichever the app-architect picks, add `test_retrying_a_job_composes_the_identical_prompt`; plus §2.4 `test_language_is_carried_on_the_job_without_changing_the_job_shape` guards contract #1 |
| **R6** | **`chat_id` reaching logs.** `JsonFormatter` copies every non-reserved `extra` key (`logging_setup.py:40-42`); redaction is enforced only by call-site discipline. | Medium — one `extra={"chat_id":…}` away | High — GDPR/ISO talking point becomes a liability | **None** | Formatter-level deny-list + `test_json_formatter_drops_identifier_fields`; §5.3 assertion 9 as the live check |
| **R7** | **Hebrew correctness.** RTL caption rendering, sentinel stripping, invisible bidi marks. | Medium | Medium | **None** | §2.4 language tests + §6 step 19; have someone who reads Hebrew look once |
| **R8** | **No Service in the chart.** `grep -rn "kind: Service$" deploy/` → nothing. IR-6's `helm test` hook has nothing to target. | **Certain** | Low — blocks implementation, does not reach production | Would surface at implementation time | §4.2 `test_a_service_targets_the_web_deployment`; flag to infra-architect now |
| **R9** | **Shutdown mid-pipeline drops a job (RR-7).** `worker.stop()` cancels and nacks (`worker.py:47-50, 65-74`) and grace is 120 s, but the only shutdown test (`test_worker_delivery.py:55-71`) covers an **idle** worker. Nothing tests cancellation *during* `pipeline.process`. | Medium — every deploy is a restart | Medium — a lost or duplicated bedtime story | Partial (queue-level only) | `test_shutdown_during_processing_releases_the_delivery`, using an `asyncio.Event` to hold the pipeline open; §5.3 assertion 10 |
| **R10** | **Python version skew.** Tests run on 3.14.5 locally; the image is 3.11. | Medium | Medium | **None** | CI gate G2 pinned to 3.11 |
| **R11** | **Incomplete AWS removal.** Acceptance §10's `git grep` is the test; easy to half-satisfy (`config.py:49-57` AWS fields, `requirements.txt` `boto3`/`botocore[crt]`, `factory.py:20-23,36-39,68-71` branches, `Chart.yaml:3` "Multi-cloud" description, `NOTES.txt:6-8` KEDA text, `AGENTS.md` §Structure). | Medium | Low — readability, not correctness | Acceptance criterion only | `test_no_aws_references_remain` running the acceptance `git grep` with an explicit `docs/` allow-list, so it is a CI gate rather than a manual step |
| **R12** | **Cost-model drift.** Prices are hardcoded literals with a `$0` default; a model-id change in `values.yaml` silently zeroes reporting. | Low | Low operationally, **high** for the "I found the cost undercount" talking point | None | §2.5 unknown-model-id tests |

**If only three things get done:** R1 (rules R1/R2/R3 plus the image-fallback test), R2 (CI gate G4),
and R4 (the apology-storm test). Those three cover the two ways this project has already shipped a
broken experience and the one way the planned fix would ship a new one.

---

## 9. Traceability — every requirement to a verification method

| Req | Verified by |
|---|---|
| FR-1 registry | §2.1 (11 unit tests) + §4.2 ConfigMap assertions + §5.3 assertion 5 |
| FR-2 prompt composition verbatim | §2.2, especially `test_both_prompts_carry_the_identical_appearance_substring`; §4.2 registry-in-ConfigMap; §6 steps 14-18 |
| FR-3 `/family list` | §2.3 (8 unit tests) + §6 steps 6-7 |
| FR-4 language selection | §2.4 + §6 step 19 |
| FR-5 story integrity | §3.1 (adapter + **pipeline** level) — the pipeline-level test is the requirement |
| FR-6 cost accounting | §2.5 + §6 step 11 |
| IR-1 AWS removal | §4.2 `test_no_aws_environment_variables…`, `test_the_chart_has_no_cloud_provider_conditional`, R11's `git grep` gate |
| IR-2 KEDA removal | §4.2 `test_no_keda_objects_are_rendered` + §5.3 assertion 4 (installs with no KEDA CRDs present) |
| IR-3 node pools | §4.2 nodeSelector/toleration tests + Terraform review (not kind) |
| IR-4 registry delivery | §4.2 checksum-annotation test + §5.3 assertions 5 and 11 |
| IR-5 CI identity (WIF) | Terraform review + §7 no-`*_SA_KEY`-secret grep + §4.2 no-mounted-key test. **Not verifiable in kind.** |
| IR-6 Helm chart | §4 in full; blocked on adding a Service (R8) |
| IR-7 pipelines | §7 gate matrix; each gate is itself the test |
| RR-1 warm worker | §4.2 `test_worker_renders_exactly_one_replica`; §5.3 assertion 3; §6 step 2. Latency itself: §6 step 22, measured live |
| RR-2 at-least-once | Already covered — `test_worker_delivery.py:26-52`, `test_gcp_adapters.py:77-142` |
| RR-3 graceful shutdown | `test_gcp_adapters.py:175-201` (queue side) + new `test_shutdown_during_processing_releases_the_delivery` (R9) + §4.2 grace-period test |
| RR-4 ack deadline | **Terraform assertion on the subscription, plus one live test. Explicitly NOT proven in kind** (§5.4) |
| RR-5 `/healthz` touches nothing | §4.2 probe-path test + §5.3 assertions 2 and 6 + a new `TestClient` test that `/healthz` returns 200 with every adapter mocked to raise |
| RR-6 automatic rollback | `--atomic` in §5.2 step 6 and §7 M3; §5 exercises the identical flag pre-merge |
| RR-7 replica replacement | §5.3 assertion 10 + §6 step 20 |
| QR-1…QR-5 | §2, §3, §4, §5, §6 respectively |
| Contract #1 `Job` frozen | §2.4 `test_language_is_carried_on_the_job_without_changing_the_job_shape` |
| Contract #2 English sentinel | §2.2 two sentinel tests + §6 step 19 |
| Contract #3 `/healthz` no cloud calls | RR-5 row |
| Contract #4 ack after success | Already covered (RR-2 row) |
| Contract #5 no `chat_id` in logs | New formatter test (R6) + §5.3 assertion 9 + §6 step 13 |
| Contract #6 text descriptions only | §2.1 `test_registry_rejects_any_photo_or_image_reference` + §6 step 17 |
| Contract #7 no cloud SDK in business logic | New `test_business_logic_imports_no_cloud_sdk` — AST-walk `family_media_bot/*.py` excluding `adapters/`, assert no `google.*`/`boto3` import. Mechanical, permanent, ~15 lines. |

---

## 10. Implementation order

1. **§2.0 fixture factories** — everything else is cheaper afterwards, and `make_story_response` is
   the structural fix for the bug class.
2. **§3.1 truncation tests** — write them **before** the fix, watch them fail, then cherry-pick
   `08b61d4`. The rolled-back fix is worth nothing without the test that pins it.
3. **§3.3 rules R1/R2/R3** applied to the two existing Vertex tests (`test_gcp_adapters.py:275-278`,
   `:305-309`) plus the new image-fallback test. Two lines each, catches three live defects.
4. **CI gate G4** (`docker run … python -c "import family_media_bot.app"`) — one line of YAML,
   removes risk R2 permanently.
5. **§4 `test_helm_render.py`** — write it against today's chart so the IR-1/IR-2/IR-3 deletions have
   a red-to-green target to aim at.
6. **§2.1–§2.5** alongside the FR-1…FR-6 implementation.
7. **§5 kind smoke** last: it depends on IR-2 landing (the KEDA CRDs are not installed in kind) and
   on the Service from R8.
