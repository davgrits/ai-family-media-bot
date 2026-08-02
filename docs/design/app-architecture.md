# Application Architecture — FR-1 … FR-6

**Stream:** app-architect
**Inputs:** `PRODUCT-DIRECTION.md`, `SPEC.md` (both at repo root, untracked at `a7c2f29`)
**Baseline read:** `/Users/davgrits/repos/ai-family-media-bot` @ `a7c2f29`
**Date:** 2026-08-02
**Status:** design only — no repo file was created, modified, or deleted.

---

## 0. Summary of the decisions I am making

| # | Decision | Where it bites |
|---|---|---|
| A1 | **The registry is resolved in the worker, not at enqueue.** `Job.prompt` keeps carrying *the family's request*; character text is deployment configuration and never enters the queue. | SPEC §8 open question 1 |
| A2 | **Language is carried on the Job as an additive `language` field.** Per-deployment default, per-message override via a `lang:xx` token. Never per-chat. **This is a deliberate change to contract #1 and needs sign-off.** | SPEC §8 open question 2, contract #1 |
| A3 | **`derive_illustration_prompt` is deleted, not repaired.** In a multi-language product the first-sentence heuristic feeds Russian/Hebrew prose to an English-trained image model — it is not merely weak, it is wrong. | FR-2 |
| A4 | **Registry appearance always wins over the model's `ILLUSTRATION:` hint**, and the conflict is prevented at source by forbidding the model to describe appearance in that line. | FR-2 |
| A5 | **The registry loads once at startup**, both tiers, with Helm forcing a rolling restart on edit via a `checksum/characters` annotation. | FR-1, IR-4 |
| A6 | **`commands.py` stays pure.** `/family list` is answered by a new thin shared `dispatch.py` used by *both* the webhook and the poller — which also removes duplication that already exists today. | FR-3 |
| A7 | **The FR-5 guard is widened: accept only `FinishReason.STOP`**, not just "reject `MAX_TOKENS`". The rolled-back fix (`08b61d4`) is otherwise correct and should be cherry-picked as the base. | FR-5 |
| A8 | **`Mode` is not extended.** `/family` is a query, not a generation job, and must not acquire a `Mode` value. | contract #1 |

Findings that are defects in code I do not own are collected in §8.

---

## 1. Character registry (FR-1)

### 1.1 Location and why

IR-4 requires the registry to reach pods as a **ConfigMap rendered by Helm**, with no image rebuild on edit. Helm's `.Files.Get` cannot traverse outside the chart directory, so the canonical file must live inside the chart. Alternatives and why they lose:

| Option | Verdict |
|---|---|
| `deploy/charts/family-media-bot/files/characters.yaml` + `.Files.Get` | **Chosen.** Single source of truth, no extra CLI flags, packages correctly with `helm package`, renders identically in `helm template` for CI (IR-7). |
| Repo-root `characters.yaml` + `--set-file` | Rejected. Every `helm template`/`helm lint`/`helm install` invocation must remember the flag; CI and laptop drift the moment someone forgets. |
| Characters as structured entries inside `values.yaml` | Rejected. Mixes product data with deploy config, and `make run` (tier 1 of PRODUCT-DIRECTION §3.9 — no Helm, no cluster) then has no registry at all. |

**Cost I accept:** the project's most charming artifact sits four directories deep. This is a real ergonomic loss; I take it because the alternative is a foot-gun in CI.

Paths:

- Canonical source: `deploy/charts/family-media-bot/files/characters.yaml`
- In-cluster mount: `/etc/family-media-bot/characters.yaml` (infra-architect owns the volume + `checksum/characters` pod annotation — see §1.6)
- Local dev: `CHARACTERS_FILE=../deploy/charts/family-media-bot/files/characters.yaml` in `.env.example` (the app process runs with cwd `app/`, per `Makefile` `run:` target)
- Tests: `app/tests/fixtures/characters.yaml` plus deliberately-broken siblings

### 1.2 Format

```yaml
# deploy/charts/family-media-bot/files/characters.yaml
#
# The family's story cast. Edited by pull request — there is no write path from
# Telegram (SPEC D5). `appearance` is English on purpose: image models are
# trained overwhelmingly on English captions, so an English string is both
# better and more *repeatable* than a translated one (PRODUCT-DIRECTION §3.4).
#
# Text descriptions only. Never a photograph of a real child (contract #6).
version: 1
characters:
  - id: mila
    appearance: "5-year-old girl, curly red hair in two short braids, freckles, green hooded cloak, brown boots"
    traits: "curious, brave, loves animals"
    names:
      en: Mila
      ru: Мила
      he: מילה

  - id: dad
    appearance: "tall man in his thirties, short dark beard, round glasses, blue knitted sweater"
    traits: "warm, patient, tells terrible jokes"
    names:
      en: Dad
      ru: Папа
      he: אבא

  - id: nika
    appearance: "small tabby cat, white front paws, one folded ear, red collar with a tiny bell"
    traits: "sleepy, mischievous, always first to the window"
    names:
      en: Nika
```

`nika` shows the legal minimum: `names.en` only. `ru`/`he` are optional per character (FR-1).

### 1.3 Loader module

**New file: `app/family_media_bot/characters.py`.** Stdlib + PyYAML + pydantic only — no cloud SDK, satisfying contract #7. I deliberately do **not** put `Character` in `models.py`: that module is the queue contract (`Job` is frozen there, `models.py:21-37`), and the registry is deployment configuration on the other side of the boundary. Keeping them apart makes A1 legible.

```python
class Character(BaseModel):
    id: str
    appearance: str
    traits: str
    names: dict[str, str]

    def display_name(self, lang: Lang) -> str:
        """names[lang] → names['en'] → id. FR-3/FR-4 fallback chain."""

class CharacterRegistry(BaseModel):
    version: int = 1
    characters: list[Character] = []

    def is_empty(self) -> bool: ...
    def cast(self) -> list[Character]: ...


class RegistryError(ValueError):
    """Malformed registry. Fatal at startup — never degraded silently."""


def load_registry(path: str | None, *, required: bool = False) -> CharacterRegistry:
    """Read and validate the registry. See §1.5 for missing/malformed behaviour."""
```

**Dependency gap (concrete):** PyYAML is *not* declared. `app/requirements.txt` and `app/pyproject.toml` list neither `PyYAML` nor anything that names it. It is only present in `app/.venv` transitively via `uvicorn[standard]` (verified: `PyYAML==6.0.3` in the venv). Relying on an extras-of-an-extra for a core product feature is a latent break the day uvicorn changes its extras. **Add `PyYAML>=6,<7` explicitly to both files.**

### 1.4 Validation rules

Fail the whole load on any violation — partial registries are worse than none, because the failure mode is an invisible missing child.

| Field | Rule | Rationale |
|---|---|---|
| top level | mapping with `characters:` key; `version` must be `1` | Gives a versioning seam without building one. |
| `characters` | list, length ≤ 8 | Bounds the image prompt. See the budget in §2.4. |
| `id` | required, unique, `^[a-z][a-z0-9_-]{0,31}$` | Stable, slug-safe, and **the only field safe to log** (see §1.7). |
| `appearance` | required, 10–300 chars | Below 10 chars it cannot steer an image model; above 300 the cast block blows the prompt budget. |
| `appearance`, `traits` | must contain **no** Cyrillic, Hebrew, Arabic, or CJK code points | D4 says these are English *for a technical reason*. A Cyrillic `appearance` silently degrades exactly the thing FR-2 exists to guarantee, so it is a hard error, not a warning. Latin-1 accents (`café-au-lait`) stay legal. |
| `traits` | required, 3–200 chars | FR-1 lists it as a carried field. |
| `names` | mapping; `en` **required**; only `en`/`ru`/`he` permitted | Unknown keys are a hard error — catches `heb:`/`rus:` typos that would otherwise fall back to English forever without a signal. |
| `names.*` | non-empty, ≤ 40 chars | Telegram line hygiene. |

Duplicate `id` → error naming the duplicate. Unknown top-level keys → error (pydantic `extra="forbid"`).

### 1.5 Missing vs malformed

These are different failures and must behave differently.

**Malformed (file present, does not validate) → fatal.** Raise `RegistryError` out of the FastAPI lifespan. The pod crashes, `helm upgrade --atomic` (D8) rolls the release back, and the bot that was already running keeps running. A typo'd registry must never reach a child. This is the same principle as FR-5 stated for configuration.

**Absent (file not there at all) → depends on an explicit setting.**

```python
# config.py
characters_file: str = "/etc/family-media-bot/characters.yaml"
characters_required: bool = False    # prod values set this true
```

- `characters_required=False` (default): start with an empty registry, log **one** `WARNING` at startup (`{"event": "character registry absent", "path": ...}`). This is the legitimate shape for `make run` with fake providers, for the kind smoke test (QR-4), and for `helm test`, none of which necessarily mount the ConfigMap.
- `characters_required=True` (set in `deploy/values/prod.yaml`): absent file is fatal, same treatment as malformed.

I chose an explicit boolean over inferring severity from `STORY_PROVIDER == "vertex"`. Inference is one line shorter and one concept harder to explain, and this is a project whose thesis is that decisions are explainable.

**Present but `characters: []`** → legal, `WARNING` logged, `/family list` says so, and `/fairytale` uses the neutral fallback in §2.2. I allow it rather than erroring because an operator emptying the cast is a coherent intent, and a bot that refuses to boot over it violates RR-6.

### 1.6 Startup vs per-request

**Load once, at startup, in both tiers.**

`create_app()` (`app.py:35-86`) gains, alongside the existing swap-point wiring at `app.py:41-46`:

```python
app.state.characters = characters.load_registry(
    settings.characters_file, required=settings.characters_required
)
```

and passes it into `Pipeline(...)` (`app.py:51-53`) and into the dispatcher (§3.3).

Why not per-request? A projected ConfigMap volume *does* refresh on disk without a restart (kubelet sync, ~60 s), so per-request reads would pick edits up "live". I reject that:

1. **Determinism.** With one snapshot per process, every job a pod handles used the same cast. Per-request reads let a story and its illustration disagree if the volume swaps between the two calls — which is precisely the failure FR-2 exists to eliminate.
2. **Torn reads.** kubelet swaps the volume by relinking a directory; reading mid-swap is a real, rare, hard-to-reproduce failure.
3. **Hot path.** No file I/O and no YAML parse per job.
4. **Auditability.** A restart is a visible event; a silent file swap is not.

The acceptance criterion in SPEC §10 is *"`/family list` reflects the registry with no image rebuild after an edit"* — no image rebuild, not no restart. Infra-architect should add the standard `checksum/characters` pod annotation so `helm upgrade` rolls both deployments when the file changes. RR-3 and RR-7 already make that roll safe.

**Coordination item for infra-architect:** ConfigMap from `.Files.Get "files/characters.yaml"`, mounted read-only at `/etc/family-media-bot/`, plus the checksum annotation on both pod templates. Note the existing ConfigMap (`deploy/charts/family-media-bot/templates/configmap.yaml`) is consumed via `envFrom` (`deployment-worker.yaml:38-40`); the registry needs a *volume*, not `envFrom`, so it is a second ConfigMap.

### 1.7 Privacy

Contract #5 names `chat_id`, request text, and raw payloads. Character `names` are children's given names — personal data under the GDPR framing PRODUCT-DIRECTION §8 leans on. **Extend the discipline: log only `id` and counts. Never log `names`, `appearance`, `traits`, or a composed prompt.** Startup logs `{"characters_loaded": 3, "character_ids": ["mila","dad","nika"]}` and nothing more. Validation errors must name the offending *field and index*, never echo the value.

---

## 2. Prompt composition (FR-2)

### 2.1 The split

Today there is one composer (`prompts.compose_prompt`, `prompts.py:42`) called at enqueue, and one heuristic (`prompts.derive_illustration_prompt`, `prompts.py:62`) called in the worker at `pipeline.py:60`. After this change there are three functions with clean ownership:

| Function | Runs | Produces |
|---|---|---|
| `compose_request(mode, args) -> str` | **enqueue** (web tier) | The family's request, framed. Goes into `Job.prompt`. **No character text.** |
| `compose_story_prompt(request, cast, lang) -> str` | **worker** | The user content sent to the story model, with `appearance` verbatim. |
| `compose_image_prompt(hint, cast) -> str` | **worker** | The image-model prompt, with the same `appearance` verbatim. |

`compose_prompt` is renamed to `compose_request` so the diff makes the semantic change loud rather than letting a same-named function quietly mean something else. Call sites: `app.py:142`, `poller.py:83`.

### 2.2 What replaces the hardcoded roles at `prompts.py:48`

```python
roles = args or "mom = kind queen, dad = gentle king, me = brave little knight"
```

is **deleted**. `compose_request` for `Mode.FAIRYTALE` becomes cast-agnostic:

```python
if mode is Mode.FAIRYTALE:
    extra = f" Extra wishes from the family: {args}." if args else ""
    return (
        f"{_BEDTIME_FRAME} A fairytale starring the family as its heroes.{extra}"
    )
```

`args` demotes from "the roles" to "extra wishes", which is what a family actually types after `/fairytale` once the cast is a registry.

`Mode.CUSTOM` and `Mode.RANDOM` (`prompts.py:54-59`) are unchanged.

**Empty-registry fallback:** if the registry is empty, `compose_story_prompt` substitutes a neutral English line — `"a family of gentle heroes: a parent, a child, and their small animal friend"` — rather than resurrecting the mom/dad/me string. The old string was English text injected into a Russian story, which is the bug's ancestor.

### 2.3 Which modes get the cast

**`Mode.FAIRYTALE` only.** `/custom` and `/surprise` produce characterless stories.

The tempting middle option — inject a character when the user's `/custom` text mentions their name — needs case-insensitive matching across `id` plus every `names[*]` value in three scripts, and it fails in both directions (a cat named `Nika` matched inside `Nikaragua`; `Папа` written as `папе` in the instrumental case and missed). It is a fuzzy matcher whose failures are silent. `/fairytale` is the command whose entire meaning is "my family", and D5's `/family list` already tells the user where the cast lives. **Explicitly deferred, not forgotten.**

### 2.4 Composed shape — story prompt

`system_instruction` carries the per-language bedtime contract (§4.2). The cast rides in the user content because it is per-request data:

```
Write a short, gentle, age-appropriate bedtime story for young children
(soothing tone, simple language, happy ending, nothing scary). A fairytale
starring the family as its heroes. Extra wishes from the family: something
with a lighthouse.

The cast, described exactly:
- Call her "Мила" in the story. Appearance: 5-year-old girl, curly red hair in
  two short braids, freckles, green hooded cloak, brown boots. She is curious,
  brave, loves animals.
- Call him "Папа" in the story. Appearance: tall man in his thirties, short
  dark beard, round glasses, blue knitted sweater. He is warm, patient, tells
  terrible jokes.
- Call it "Nika" in the story. Appearance: small tabby cat, white front paws,
  one folded ear, red collar with a tiny bell. It is sleepy, mischievous,
  always first to the window.

Weave them all into one cozy little adventure together.
```

Two properties matter:

- **`appearance` is pasted verbatim.** No summarising, no re-ordering, no translation. FR-2 is satisfied literally: the exact substring in the YAML is the exact substring in the prompt. This is directly assertable in a unit test (`assert character.appearance in composed`) and that assertion is the FR-2 regression test.
- **The name is language-switched, the appearance is not.** `names[lang]` picks the in-story name (§4.3); the appearance stays English even for a Hebrew story. The model reads English appearance and writes Hebrew prose, which is a thing these models do reliably.

### 2.5 Composed shape — image prompt

```
Children's book illustration of this scene: Mila and her father walk a
sleepy tabby cat along a moonlit pier toward a glowing lighthouse.

The characters must look exactly like this, unchanged:
- Mila: 5-year-old girl, curly red hair in two short braids, freckles, green
  hooded cloak, brown boots.
- Dad: tall man in his thirties, short dark beard, round glasses, blue knitted
  sweater.
- Nika: small tabby cat, white front paws, one folded ear, red collar with a
  tiny bell.

Soft watercolor children's book illustration, warm bedtime palette, gentle
lighting, cozy and reassuring. No text or lettering in the image.
```

Deliberate choices:

- **Names in the image prompt are always `names["en"]`**, never `names[lang]`. Hebrew or Cyrillic tokens in an image prompt are noise at best; the name here is only a label binding an appearance to a role in the scene.
- **Appearance comes *after* the scene** and immediately before the style suffix, with an explicit "unchanged" instruction. Later, more specific instructions dominate in practice.
- **`No text or lettering`** is added — image models love to hallucinate storybook captions, and a Hebrew story with garbled Latin text baked into the picture reads as broken.
- The style suffix is lifted verbatim out of the doomed `derive_illustration_prompt` (`prompts.py:74-77`); it is the one part of that function worth keeping.

**Prompt budget.** `image_vertex.py:22` sets `_MAX_PROMPT_CHARS = 2000` and both invoke paths hard-slice with `prompt[:_MAX_PROMPT_CHARS]` (`image_vertex.py:45`, `image_vertex.py:61`). Today that slice is a harmless backstop. With a cast block appended it becomes a **correctness hazard**: an 8-character cast at 300 chars each is 2,400 characters and the slice would silently amputate the last characters and the entire style suffix. Fix in two places:

1. `compose_image_prompt` budgets explicitly — hint capped at 400 chars, cast capped at 8 × 300, style suffix always appended last, total asserted under 2,000. The §1.4 limits (≤ 8 characters, ≤ 300 chars each) exist to make this arithmetic hold: 400 + 8×(300 + ~10) + ~140 ≈ 3,020 — **which does not fit.** Therefore either cap the cast at **5** characters, or raise `_MAX_PROMPT_CHARS`. Gemini accepts far more than 2,000 characters, so **raise `_MAX_PROMPT_CHARS` to 4,000 and keep the 8-character cap.** Recorded here because it is the kind of arithmetic that is wrong in review and only discovered in production.
2. Change the slice to log a `WARNING` when it actually truncates, so the backstop stops being silent.

### 2.6 What replaces `derive_illustration_prompt` — and when the hint is missing

`derive_illustration_prompt` (`prompts.py:62-77`) is **deleted**, and `pipeline.py:60-62` changes from

```python
illustration_prompt = story.illustration_hint or derive_illustration_prompt(story.text)
```

to

```python
illustration_prompt = compose_image_prompt(story.illustration_hint, cast)
```

The strongest argument for deletion is not that the heuristic is crude. It is that FR-4 makes it **wrong**: it takes the first sentence of a story that is now Russian or Hebrew and hands it to an English-trained image model. The function's premise dies with the multi-language requirement.

`split_illustration_hint` (`adapters/story_common.py:6`) returns `""` when the model omits the sentinel line. Handling:

- **Fall back to a fixed English scene**, `"a cozy bedtime scene from this story"`, plus the cast block plus the style suffix. Log a `WARNING` and increment a counter (`fmb_illustration_hint_missing_total`).
- **Do not fail the job.** FR-5's "fail loudly" applies to a *truncated story*, where the product is broken. A missing hint costs scene specificity, not identity — the cast block and style suffix still produce a correct-looking family picture, and the story, which is the actual product, is intact. Failing would discard a good paid-for story and burn a queue retry.
- The counter matters because `ThinkingLevel.MINIMAL` (§5.2) plausibly makes the model likelier to skip a formatting instruction. If that metric climbs, the thinking level is the first thing to revisit.

### 2.7 Conflict: the model's `ILLUSTRATION:` hint vs registry appearance

**The registry wins, unconditionally.** But the better answer is that the conflict should not arise, because the two inputs are given disjoint jobs:

- The hint supplies the **scene** — what is happening, where, in what light.
- The registry supplies **identity** — what each character looks like.

So the fix is at the source. Each per-language `BEDTIME_SYSTEM_PROMPT` (§4.2) gains a clause on the sentinel line:

> `ILLUSTRATION: <one-line English prompt describing the key scene. Refer to characters by name only — do not describe their hair, clothing, age, or face. Their appearance is supplied separately.>`

Note this clause is written in the language of the surrounding system prompt but the sentinel token `ILLUSTRATION:` and the produced line stay **English in all three languages** — contract #2, and `split_illustration_hint` matches on the uppercase English marker at `story_common.py:11`.

If the model disobeys anyway (it will, sometimes), the registry block is appended **after** the hint with `"The characters must look exactly like this, unchanged:"`. Two reasons the registry must win:

1. **Repeatability is the whole point.** A generated hint varies run to run; a git-committed string does not. Consistency across illustrations is FR-2's stated purpose and PRODUCT-DIRECTION §3.6's justification for choosing Gemini Flash Image over Imagen 4.
2. **Provenance.** The registry is reviewed by a human in a pull request. The hint is model output. When the two disagree about a child's appearance, the reviewed artifact wins.

**Honest residual risk:** ordering plus an override instruction is a strong nudge, not a guarantee. I cannot verify how `gemini-2.5-flash-image` weights a contradictory in-scene description against a later explicit override without live runs. §8 records this. The definitive fix is reference-image input (PRODUCT-DIRECTION §3.6), which is out of this stream's scope — see §8.

---

## 3. `/family list` (FR-3)

### 3.1 Parsing — and why `Mode` must not grow

`/family` is a **query**, answered inline from a ConfigMap. It never enqueues. Adding `Mode.FAMILY` would be the easy path and it is wrong twice over: `Mode` is embedded in the frozen `Job` (`models.py:36`) where every value must denote a generation job, and it labels `metrics.JOBS_ENQUEUED` / `JOBS_PROCESSED` / `JOB_COST` (`metrics.py:14-52`), which would sprout a `mode="family"` series that never records a cost.

`commands.py` gains a second result type:

```python
class Query(str, Enum):
    FAMILY_LIST = "family_list"
    HELP        = "help"       # /start and unknown /commands (today: poller.py:72-74)

@dataclass
class ParsedQuery:
    chat_id: int
    query: Query
    args: str

def parse(update: dict) -> ParsedCommand | ParsedQuery | None: ...
```

Grammar:

- `/family` and `/family list` → `Query.FAMILY_LIST`
- `/family add ...`, `/family remove ...`, any other subcommand → also `Query.FAMILY_LIST`, with `args` preserved so the responder can prepend the read-only notice. Per D5 there is no write path; saying *why* is better than pretending not to understand.
- `@botname` suffix stripping (`commands.py:47`) applies unchanged.

The language token (§4.1) is stripped in `parse()` before mode/query lookup, so it works on every command and on plain text.

### 3.2 Should `commands.py` gain I/O? No.

`commands.py:1-2` states the module is pure functions with no I/O, and that is worth defending: it is the reason `parse()` can be exhaustively unit-tested against `docs/sample-update.json`-shaped dicts with no fixtures. Rendering a list from a registry is also pure; *sending* it is not.

So: **`commands.py` parses. It does not answer.** The renderer is pure and lives with the registry:

```python
# characters.py
def render_family_list(registry: CharacterRegistry, lang: Lang) -> str: ...
```

It imports `i18n.t` for the header, which keeps chat-facing strings in one module (§4.2). The only I/O is the `send_text` call, and that belongs to the dispatcher.

### 3.3 Where the answer is produced — `dispatch.py`

There are two entry points that must behave identically: `POST /webhook` (`app.py:121-152`) and `TelegramPoller._handle` (`poller.py:65-91`). They **already** duplicate parse → compose → `new_job` → `enqueue` → metrics → log. This change would add three more duplicated concerns (query branch, language resolution, registry access) to both.

**Introduce `app/family_media_bot/dispatch.py`** with a single async entry point:

```python
@dataclass
class BotContext:
    settings: Settings
    queue: QueuePort
    telegram: TelegramClient
    characters: CharacterRegistry

async def handle_update(update: dict, ctx: BotContext) -> None:
    """parse → (query: reply inline) | (command: compose, enqueue, ack)."""
```

`app.py`'s webhook shrinks to secret check → JSON parse → `await dispatch.handle_update(update, ctx)` → `{"ok": True}`. `poller.py:65-91` shrinks to the same call plus its plain-text-is-`/custom` rule, which moves into `handle_update` so both paths get it (today the webhook silently ignores plain text — an inconsistency worth deleting).

This is the small version of the `router.py` from `a1e8dd0`; I am not proposing that 250-line conversation engine, because there is no conversation state to run (§2 of SPEC forbids it).

**One consequence to state plainly:** unifying means the webhook now also sends the "✨ writing your story" ack, adding one Telegram round-trip (~100–300 ms) before returning 200. `docs/api-contract.md:51` says "enqueue → return 200 fast". Telegram's webhook timeout is ~60 s, so this is safe, and having the two entry points behave identically is worth more than 200 ms. If reliability-engineer disagrees, the alternative is `asyncio.create_task` for the ack in the webhook path — I do not recommend it, because a fire-and-forget task whose failure is invisible is a worse trade than 200 ms.

### 3.4 Output format

```
Герои ваших сказок:

• Мила — curious, brave, loves animals
  5-year-old girl, curly red hair in two short braids, freckles, green hooded cloak, brown boots

• Папа — warm, patient, tells terrible jokes
  tall man in his thirties, short dark beard, round glasses, blue knitted sweater

• Nika — sleepy, mischievous, always first to the window
  small tabby cat, white front paws, one folded ear, red collar with a tiny bell

Персонажи хранятся в git и меняются через pull request.
```

- Header and footer from `i18n.t(lang, ...)`; name from `display_name(lang)`.
- **`appearance` is shown**, on its own line. It is English inside an otherwise-Russian message, which is slightly odd — and that oddity is the point: it makes the "the registry is English because image models are" decision visible in the product itself, which is a talking point (PRODUCT-DIRECTION §8).
- Empty registry → `i18n.t(lang, "family_empty")`.
- `/family add ...` → the read-only notice, then the list.
- **Length guard:** Telegram caps `sendMessage` at 4,096 characters. Worst case with 8 characters is ~8 × (40 + 200 + 300 + formatting) ≈ 4.5 KB. Cap the rendered output at 3,900 characters and append `…` plus a count of omitted entries. Cheap, and prevents a hard 400 from the Bot API on a demo.

---

## 4. Language selection (FR-4)

### 4.1 Answer to SPEC §8: how is language carried?

**Per-deployment default, overridable per message, carried on the `Job`. Never per-chat.**

| Option | Verdict |
|---|---|
| Per-chat (picker + stored choice) | **Ruled out by SPEC §2** ("no per-chat configuration persistence"). Prior art `a1e8dd0` did exactly this with a `ProfileStore` port backed by S3 — it is the reason that branch is not the baseline. |
| Per-deployment only (`STORY_LANGUAGE` env) | Rejected as the *sole* mechanism. FR-4 says "selectable"; a language you can only change by `helm upgrade` is selectable by an operator, not a family, and all three languages cannot be shown in one demo session. |
| **Per-deployment default + per-message override** | **Chosen.** Fully stateless. ~10 lines in `commands.py`. Makes en/ru/he demonstrable in one Telegram session. |

**Syntax: a leading `lang:xx` token**, consumed by `parse()` before anything else, on commands and on plain text:

```
/fairytale lang:he
/custom lang:ru про дракона и маяк
lang:en a dragon and a lighthouse
```

Rejected alternatives:

- **First bare token is the language** (`/custom he a bear...`) — collides catastrophically with English: `/custom he was a little bear` becomes Hebrew.
- **`/fairytale_he` command variants** — zero ambiguity and discoverable in the BotFather menu, but 3 modes × 3 languages = 9 entries plus `/family` for what is a three-command bot, and `_COMMAND_MODES` (`commands.py:11-15`) triples.
- **Inline keyboard picker** — the honest killer: *a picker implies memory.* Tapping "עברית" and having the next message come back in English is worse UX than no picker at all, and remembering the tap is exactly the state §2 forbids. An explicit per-message token is the only truthful stateless UI. This is the interview answer.

Resolution order per message: `lang:xx` token → `settings.story_language` → `en`. Telegram's `message.from.language_code` is deliberately **not** consulted: it would make the output language depend on a per-user client setting that the family cannot see or control, producing non-reproducible demos.

### 4.2 `BEDTIME_SYSTEM_PROMPT` becomes per-language

`prompts.py:17-26` (a single hardcoded Russian string) becomes a dict keyed by `Lang`. The shape from `a1e8dd0:app/family_media_bot/prompts.py:20-55` is correct and should be reused, with the §2.7 clause added:

```python
_ILLUSTRATION_LINE_SPEC = (
    "ILLUSTRATION: <one-line English prompt for a children's book illustration "
    "of this story's key scene. Refer to characters by name only — do not "
    "describe their hair, clothing, age, or face.>"
)

BEDTIME_SYSTEM_PROMPTS: dict[Lang, str] = {Lang.EN: ..., Lang.RU: ..., Lang.HE: ...}

def system_prompt(language: str) -> str:
    return BEDTIME_SYSTEM_PROMPTS[resolve(language)]
```

Each entry embeds `_ILLUSTRATION_LINE_SPEC` verbatim, so **contract #2 holds structurally**: the sentinel exists in exactly one place and is English by construction. A unit test asserting `"ILLUSTRATION:" in prompt` for all three languages makes it a build failure to break it.

The singular `BEDTIME_SYSTEM_PROMPT` is deleted. Its two importers are `story_vertex.py:13,41` and `story_bedrock.py:19,46` — the latter is deleted by IR-1 anyway.

**Port change.** `StoryProvider.generate` (`ports/story.py:28`) becomes:

```python
async def generate(self, mode: Mode, prompt: str, language: str = "en") -> StoryResult:
```

Defaulted, so `FakeStoryProvider` and existing tests keep compiling. `VertexStoryProvider._invoke` then passes `system_instruction=system_prompt(language)` instead of the module constant at `story_vertex.py:41`. I pass a *language code*, not a rendered system prompt, because `system_instruction` is a Gemini-shaped concept and the port should stay in domain terms — the adapter maps domain → provider, which is the pattern already in place.

`FakeStoryProvider` (`adapters/story_fake.py`) should return language-appropriate canned text **and an `illustration_hint`**. Today it returns none (`story_fake.py:31-37`), so the entire hint path — the branch FR-2 depends on — is never exercised by `make run` or by any test that uses the fake. That is a real coverage hole; fixing it is nearly free.

### 4.3 How `names[lang]` is applied

Three call sites, one fallback chain (`names[lang]` → `names["en"]` → `id`):

1. **Story prompt** (§2.4): `Call her "Мила" in the story.` — the model writes the name in the story's script.
2. **`/family list`** (§3.4): the display name.
3. **Never in the image prompt** (§2.5): always `names["en"]`.

Chat-facing strings (`poller.GREETING` at `poller.py:23`, `poller.ACK` at `poller.py:29`, and the hardcoded Russian failure message at `pipeline.py:108-109`) all move to `i18n.t(lang, key)`. **`pipeline.py:108` is the awkward one**: the pipeline apologises in Russian regardless of the story language, and it only knows the language because A2 puts it on the `Job` — `i18n.t(job.language, "error_generation")`. Without A2 there is no correct value available in the worker at all. That is an independent argument for A2.

`i18n.py` is a trimmed version of `a1e8dd0:app/family_media_bot/i18n.py`: keep `Lang`, `resolve`, `t`, and the strings for `menu`, `ack`, `error_generation`, `family_list_header`, `family_empty`, `family_read_only`. Drop everything about photos, `/family add`, and the language picker — those belong to the ruled-out feature set. Keep the completeness test (every key in every language) from `a1e8dd0:app/tests/test_i18n.py`; it turns a missing translation into a test failure instead of a runtime `KeyError` in front of a child.

### 4.4 Hebrew RTL

| Issue | Handling |
|---|---|
| **Story body** | Pure Hebrew; the first strong character is RTL so Telegram renders the whole message RTL. Nothing to do. |
| **Story as photo caption** (`telegram.py:79-80`) | Captions render with the same bidi algorithm. Nothing to do. |
| **`/family list` mixes Hebrew names with English appearance** | This is the real problem: trailing punctuation and the `•` bullet jump to the wrong end. **Wrap every embedded English run in isolates** — `⁨` (FSI) … `⁩` (PDI). Applied around `appearance` and `traits` when `lang is Lang.HE`. This is the correct Unicode-level fix, not a hack. |
| **Leading bullets/emoji are directionally neutral** | Prefix Hebrew lines with `‏` (RLM) so the bullet anchors on the right. |
| **`_CAPTION_LIMIT = 1024`** (`telegram.py:14`) | Telegram counts UTF-16 code units. Hebrew is BMP (1 unit/char) so the Python `len()` check is accurate; emoji count 2, so the check is slightly conservative — safe direction. No change needed. |
| **Digits inside Hebrew** | `appearance` (which contains "5-year-old") never appears in Hebrew story output, only in `/family list`, where the isolates handle it. |
| **Client variance** | RTL bugs are client-specific. Add to QR-5: render a Hebrew story and `/family list` on Telegram Desktop, iOS, and Android before claiming Hebrew works. I cannot verify rendering from the code. |

---

## 5. Story integrity and cost (FR-5, FR-6)

PRODUCT-DIRECTION §4 contains the diagnosis, and the fix exists as a reachable dangling commit: **`git cherry-pick 08b61d4`** ("Stop truncating stories at the Gemini token ceiling"), touching `story_vertex.py`, `pipeline.py`, `ports/story.py`, `test_gcp_adapters.py`. My job was to validate it against the real code, not rediscover it.

### 5.1 Validation against the live SDK

I inspected `google-genai==1.75.0` in `app/.venv`:

- `types.ThinkingConfig` exists with fields `include_thoughts`, `thinking_budget`, `thinking_level`. **Both** knobs from PRODUCT-DIRECTION §9's open question are present in the SDK.
- `types.ThinkingLevel` = `{THINKING_LEVEL_UNSPECIFIED, MINIMAL, LOW, MEDIUM, HIGH}`. `MINIMAL` is valid.
- `types.FinishReason.MAX_TOKENS` exists, alongside `SAFETY`, `RECITATION`, `LANGUAGE`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `OTHER`, and several `IMAGE_*` values.
- `GenerateContentResponseUsageMetadata` has `thoughts_token_count` — confirming the FR-6 diagnosis that reasoning tokens are excluded from `candidates_token_count` (`story_vertex.py:54`).
- `GenerateContentResponse.text` "returns only the text parts" of the first candidate — so a response that is all thinking yields `None`/`""`.

The `08b61d4` diff is **correct as written**. Cherry-pick it as the base, then apply the three amendments below.

### 5.2 Amendment 1 — widen the guard to "only `STOP` is acceptable"

`08b61d4` raises only on `FinishReason.MAX_TOKENS`. That leaves `SAFETY`, `RECITATION`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `LANGUAGE`, `SPII`, and `OTHER` — every one of which can return a partial or empty candidate that the pipeline would ship. A story cut short because it tripped a safety filter is exactly as bad for a child as one cut short by a token ceiling, and the reason it fails is *more* interesting operationally.

```python
_ACCEPTABLE = {None, FinishReason.FINISH_REASON_UNSPECIFIED, FinishReason.STOP}

reason = self._finish_reason(response)
if reason not in _ACCEPTABLE:
    raise RuntimeError(f"Vertex AI did not complete the story: finish_reason={reason}")
```

Simpler than an exclusion list, covers the class rather than the instance, and satisfies QR-2 (a regression test that fails if a truncated story could ever be delivered) more convincingly than a `MAX_TOKENS`-only check.

### 5.3 Amendment 2 — check `finish_reason` before the empty-text check

`08b61d4` keeps the existing `if not raw: raise RuntimeError("...empty story response")` (`story_vertex.py:48-50`) ahead of the new truncation check. But the canonical failure in the diagnosis — the model spending ~950 of 1024 tokens thinking — can produce **zero** visible text, in which case the operator gets "empty story response" for what is actually a truncation. Both paths fail the job, so behaviour is right and the *diagnostics* are wrong. Reorder: `finish_reason` first, empty-text second.

### 5.4 Amendment 3 — the budget must now cover Hebrew

`08b61d4` sets `max_output_tokens = story_max_words * 4 + 2048` → 220 × 4 + 2048 = **2,928**. With FR-4 the visible-output half must cover the worst-tokenising script, not just Cyrillic:

| Script | ~tokens/word | 220 words | Fits in the 880-token visible allowance? |
|---|---|---|---|
| English | ~1.3 | ~286 | yes |
| Russian | ~2.0–2.5 | 440–550 | yes |
| Hebrew | ~2.5–3.0 | 550–660 | yes, with ~25% headroom |

`_TOKENS_PER_WORD = 4` survives contact with the third language. Recorded so the constant is defensible rather than lucky. And since tokens are billed as generated, not reserved, the headroom is free on stories that end early.

### 5.5 FR-6 — cost

`08b61d4`'s `billed_out = tokens_out + tokens_thought` is the right fix (`story_vertex.py:61`). Two additions:

- **Silent zero-cost on an unpriced model.** `_TEXT_PRICES_PER_MILLION_TOKENS.get(self._model_id, (0.0, 0.0))` (`story_vertex.py:55-57`) means a typo'd or upgraded `VERTEX_TEXT_MODEL_ID` reports **$0.00 per job forever**. For a requirement whose entire subject is cost accuracy, silently reporting zero is the same class of bug as silently shipping a truncated story. Log a `WARNING` at construction when the model id is absent from the table. Same treatment for `_IMAGE_COST_PER_IMAGE_USD.get(..., 0.0)` at `image_vertex.py:82`.
- **Regional surcharge order.** `story_vertex.py:58-60` applies the 10% non-global surcharge to both prices before computing cost. Correct, and it must stay ahead of the `billed_out` multiplication. Preserved by the diff.

`ports/story.py` gains `tokens_thought: int = 0` (defaulted, so the fakes are unaffected), and `pipeline.py`'s "story done" log gains `tokens_out` / `tokens_thought` so the split is visible in Cloud Logging — that is the line that makes the "cost was under-reported and I found it" talking point checkable.

### 5.6 Integration with the rest of this design — the retry-notification problem

Raising on a bad `finish_reason` propagates: `Pipeline.process` catches (`pipeline.py:100`), returns `False`, `Worker` nacks (`worker.py:45`), Pub/Sub redelivers up to 5 attempts, then DLQ (PRODUCT-DIRECTION §2). Correct, and exactly what FR-5 asks for.

**But `pipeline.py:104-110` sends the user an apology on every failed attempt.** A deterministically-truncating job therefore sends "Простите, сказка не получилась" **five times** before the DLQ swallows it. That is worse than the bug it replaces: FR-5 turns a silent partial delivery into a loud quintuple apology.

Recommended fix, flagged as **overlapping reliability-engineer's stream (RR-2)**:

- Add `attempt: int = 1` to `QueueDelivery` (`ports/queue.py:15-20`; the dataclass is `frozen=True`, but a defaulted field is additive and safe).
- `PubSubQueue._accept_message` (`queue_pubsub.py:132`) populates it from `message.delivery_attempt`, which Pub/Sub supplies whenever a dead-letter policy is configured — and one is (5 attempts).
- `Pipeline.process` notifies the chat **only** on the final attempt; earlier attempts fail silently and retry.

Also: that apology becomes `i18n.t(job.language, "error_generation")` (§4.3).

---

## 6. The open design questions (SPEC §8)

### 6.1 Where is the registry resolved?

Today `compose_prompt()` runs at enqueue in the web tier (`app.py:142`, `poller.py:83`) and its output is stored in `Job.prompt`.

#### Option 1 — resolve at enqueue (bake character text into `Job.prompt`)

*For:* the job is self-describing — read a Pub/Sub message and you see exactly what was asked for. The worker needs no registry for the story call. Matches the current shape of `compose_prompt`, so the smallest diff.

*Against:*
1. **It does not actually satisfy FR-2.** FR-2 requires `appearance` verbatim in the **image** prompt too, and the image prompt is necessarily built in the worker (`pipeline.py:60-64`) because it depends on the story's `illustration_hint`. So the worker must obtain `appearance` regardless — either by regex-scraping it back out of `Job.prompt` (grim) or by loading the registry anyway. **Both tiers need the registry either way**, which erases Option 1's main advantage.
2. **Split-brain across a registry edit.** With the registry loaded in both tiers and an edit landing between enqueue and dequeue, the story uses the old cast and the illustration uses the new one. That is a picture that does not match its story — the exact defect FR-2 exists to remove.
3. **Message size.** Eight characters at ~350 bytes is ~3 KB of deployment configuration copied into every queue message.
4. **A job outlives an edit** (SPEC names this): a message sitting in the DLQ carries a cast that no longer exists in git, and replaying it produces a story nobody can reproduce from the repo.

#### Option 2 — resolve in the worker

*For:*
1. **One resolution point, therefore self-consistency.** Story and image are composed from the same in-memory snapshot inside one `Pipeline.process` call. They cannot disagree. This is decisive.
2. **The Job carries the request; the registry is configuration.** `Job.prompt` remains "composed scene description (text)" exactly as `docs/api-contract.md:67-77` defines it — the family's request, framed. Character appearance is not a scene description; it is studio direction supplied by deployment configuration. Contract #1 is honoured in both shape *and* meaning.
3. **Replayability.** A DLQ message replayed next month renders with today's registry — reproducible from git.
4. **Smaller messages, and the web tier is thinner** (it still loads the registry, but only to answer `/family list`).

*Against:*
1. The job is less self-describing — the exact story prompt no longer exists in the queue. Mitigated: it is deterministic from `Job.prompt` + the registry at the pod's `checksum/characters`, and both are in git.
2. `compose_prompt`'s call site moves from web to worker, which is a real (small) refactor.
3. A registry edit changes the output of jobs already in flight. I consider this a *feature*: fixing a typo in a child's cloak colour should apply to the next story, not be pinned to whenever the message was published.

#### Recommendation: **Option 2 — resolve in the worker.**

The argument that settles it is not tidiness, it is (1): FR-2's purpose is that the same child looks the same across pictures, and only a single resolution point guarantees the story and its own illustration agree. Option 1 cannot provide that without the worker holding the registry too, at which point Option 1 has all of Option 2's costs plus 3 KB per message and a split-brain window.

Concretely:

- **Web tier:** `compose_request(mode, args)` → framed request, no character text → `Job.prompt`. Loads the registry only to answer `/family list`.
- **Worker:** `Pipeline` receives the registry by constructor injection (`app.py:51-53`) and calls `compose_story_prompt(job.prompt, cast, lang)` and `compose_image_prompt(hint, cast)`.
- **`Job` bytes on the wire are unchanged by this decision.** The only Job change in this design is §6.2's `language` field, and it is separate and separately justified.

### 6.2 How is language carried?

Answered in §4.1: **per-deployment default + per-message `lang:xx` override, carried on the `Job`.**

The carrying mechanism is the part that touches contract #1, so it is stated deliberately here.

**The worker needs the language** to (a) select `system_prompt(lang)`, (b) pick `names[lang]` for the story prompt, and (c) apologise in the right language at `pipeline.py:108`. Three ways to get it there:

1. **Worker reads its own `STORY_LANGUAGE` env var.** Works for per-deployment only; a per-message override is unrepresentable. Rejected — it collapses FR-4 to operator-only selection.
2. **Encode it inside `Job.prompt`.** Zero contract change, but selecting a `system_instruction` by regex over prompt text is exactly the kind of implicit coupling that produces a bug nobody can find. Rejected.
3. **Add `language` to `Job`.** Rejected only if the contract forbids it — and SPEC §8 contract #1 does not forbid it, it says changing it must be "a deliberate, called-out decision — not a side effect". So: **calling it out.**

```python
class Job(BaseModel):
    job_id: str
    chat_id: int
    mode: Mode
    prompt: str
    # Story output language. Defaults to Russian because every job queued
    # before this field existed was generated in Russian — new enqueues always
    # set it explicitly, so this default only ever applies to a legacy message.
    language: str = "ru"
    created_at: str
```

Why this is safe:

- **Additive and defaulted.** `Job.model_validate_json` (`queue_pubsub.py:101`) parses an old message that lacks the field; `Job.model_dump_json` (`queue_pubsub.py:91`) simply emits one more key. No coordinated deploy is required and no in-flight message is dropped.
- **The `"ru"` default is a compatibility statement, not the product default.** The product default is `settings.story_language` (`"en"`, per D6's ordering), applied at enqueue. A reader must not confuse the two, hence the comment — this is precisely the framing used in the prior attempt at `a1e8dd0:app/family_media_bot/models.py:39-41`, and it was right.
- **`new_job()` gains `language: str = "ru"`** with the same reasoning (`models.py:40`).

**Required follow-through:** `docs/api-contract.md:67-77` must be updated in the same commit. A frozen contract that changes without its document changing is how the contract stops being real.

**If the reviewer rejects the Job change**, the fallback is per-deployment-only language (option 1 above): `STORY_LANGUAGE` in the ConfigMap, no `lang:xx` token, no Job change, and switching languages costs a `helm upgrade`. FR-4 is arguably still met. I do not recommend it — but it is a clean fallback and it costs one setting.

---

## 7. File-by-file change plan

Paths relative to `/Users/davgrits/repos/ai-family-media-bot`. Files owned by other streams (IR-*) are marked and listed only where this stream touches them.

### Added

| File | Reason |
|---|---|
| `app/family_media_bot/characters.py` | `Character` / `CharacterRegistry` models, `load_registry()` with §1.4 validation, `render_family_list()`. |
| `app/family_media_bot/i18n.py` | `Lang`, `resolve()`, `t()`, en/ru/he chat strings. Trimmed port of `a1e8dd0:app/family_media_bot/i18n.py`. |
| `app/family_media_bot/dispatch.py` | Shared `handle_update()` for webhook and poller; removes existing duplication and hosts the `/family list` branch. |
| `deploy/charts/family-media-bot/files/characters.yaml` | The registry itself, inside the chart so `.Files.Get` can render it (IR-4; infra-architect owns the ConfigMap template + mount + `checksum/characters` annotation). |
| `app/tests/fixtures/characters.yaml` | Valid fixture for loader and prompt tests. |
| `app/tests/fixtures/characters_bad_*.yaml` | Malformed fixtures: duplicate id, Cyrillic appearance, missing `names.en`, unknown `names` key, too many characters. |
| `app/tests/test_characters.py` | QR-1: loader + validation + `render_family_list`. |
| `app/tests/test_prompts.py` | QR-1: `appearance` appears **verbatim** in both composed prompts; sentinel is English in all three languages. |
| `app/tests/test_i18n.py` | QR-1: every key present in every language (port from `a1e8dd0`). |
| `app/tests/test_dispatch.py` | QR-1: `/family list`, `lang:xx` parsing, plain-text-is-custom, query never enqueues. |
| `app/tests/test_story_integrity.py` | QR-2: a non-`STOP` finish reason raises; cost includes `thoughts_token_count`. |

### Modified

| File | Reason |
|---|---|
| `app/family_media_bot/prompts.py` | `BEDTIME_SYSTEM_PROMPT` → `BEDTIME_SYSTEM_PROMPTS` dict + `system_prompt(lang)`; `compose_prompt` → `compose_request` (cast-free, hardcoded roles at `:48` deleted); add `compose_story_prompt` / `compose_image_prompt`; **delete `derive_illustration_prompt` (`:62`)**. |
| `app/family_media_bot/commands.py` | Add `Query` / `ParsedQuery`; `/family` grammar; strip the leading `lang:xx` token. Stays pure — no I/O. |
| `app/family_media_bot/models.py` | **Contract change:** add `language: str = "ru"` to `Job` and to `new_job()`. Called out per contract #1. |
| `app/family_media_bot/config.py` | Add `story_language`, `characters_file`, `characters_required`. (AWS field removal at `:49-57` belongs to IR-1.) |
| `app/family_media_bot/app.py` | Load the registry into `app.state` at startup; inject it into `Pipeline`; webhook delegates to `dispatch.handle_update`. |
| `app/family_media_bot/poller.py` | Delegate to `dispatch.handle_update`; `GREETING` (`:23`) and `ACK` (`:29`) move to `i18n`. |
| `app/family_media_bot/pipeline.py` | Accept the registry; pass `job.language` to the story provider; build the image prompt via `compose_image_prompt`; `i18n` the apology at `:108`; log `tokens_out`/`tokens_thought`; notify only on the final attempt (with reliability-engineer). |
| `app/family_media_bot/ports/story.py` | Add `tokens_thought: int = 0`; `generate(mode, prompt, language="en")`. |
| `app/family_media_bot/ports/queue.py` | Add `QueueDelivery.attempt: int = 1` (final-attempt notification; reliability-engineer's call). |
| `app/family_media_bot/adapters/queue_pubsub.py` | Populate `attempt` from `message.delivery_attempt` at `:132`. |
| `app/family_media_bot/adapters/story_vertex.py` | Cherry-pick `08b61d4` + the three amendments in §5.2–5.5; per-language `system_instruction`; warn on an unpriced model id. |
| `app/family_media_bot/adapters/story_fake.py` | Accept `language`; return language-appropriate canned text **and** an `illustration_hint`, so the hint path is exercised locally. |
| `app/family_media_bot/adapters/image_vertex.py` | Raise `_MAX_PROMPT_CHARS` to 4,000 (§2.5); warn instead of silently slicing; **stop swallowing every exception into a placeholder PNG** (§8, F2); warn on an unpriced model id. |
| `app/requirements.txt`, `app/pyproject.toml` | Add `PyYAML>=6,<7` — currently only present transitively via `uvicorn[standard]`. (boto3 removal is IR-1.) |
| `docs/api-contract.md` | Document `Job.language`, `/family`, the registry, and worker-side prompt resolution. Required by the contract change in §6.2. |
| `.env.example` | `STORY_LANGUAGE`, `CHARACTERS_FILE`, `CHARACTERS_REQUIRED`. |
| `Makefile` | `run:` exports `CHARACTERS_FILE=../deploy/charts/family-media-bot/files/characters.yaml`. (Target cleanup for AWS/KEDA is IR-1/IR-2.) |
| `README.md`, `ROADMAP.md` | Registry, `/family list`, language token. |

### Deleted

| Thing | Reason |
|---|---|
| `prompts.derive_illustration_prompt` (`prompts.py:62-77`) | Replaced by `compose_image_prompt`; the first-sentence heuristic is wrong once stories are not English (FR-2, FR-4). |
| The hardcoded roles string (`prompts.py:48`) | Replaced by the registry cast (FR-2). |
| `prompts.BEDTIME_SYSTEM_PROMPT` (`prompts.py:17`) | Replaced by the per-language dict (FR-4). |
| `app/family_media_bot/adapters/story_bedrock.py` | IR-1 (noted here only because it is the second importer of the deleted constant). |

---

## 8. Risks, findings, and open questions

### Findings — defects in code I do not own

**F1 — `image_vertex.py:85-89` swallows every image failure into a placeholder PNG.** A Vertex outage, a quota rejection, or a safety block returns a blue gradient with `model_id="placeholder-fallback"`, `cost_usd=0.0`, and the pipeline **acks the job as a success**. This is the image-side twin of the truncation bug, and under FR-2 it is worse: the promise is that the family is recognisable, and the delivered artifact is a gradient. It also violates the spirit of contract #4 (ack only after the pipeline reports success — it *reports* success, having failed). Recommend one in-adapter retry, then raise. The extra retry matters because failing the job re-bills the story call on redelivery.

**F2 — Silent $0 cost on an unpriced model id.** `story_vertex.py:55-57` and `image_vertex.py:82` both `.get(..., 0.0)`. FR-6 is a requirement about cost accuracy; the fastest way to violate it is a model-id bump. Log a `WARNING` at construction.

**F3 — The retry apology storm.** With FR-5's guard in place, a deterministically-failing job apologises to the chat five times (`pipeline.py:104-110` × 5 Pub/Sub attempts). Detailed in §5.6. Overlaps reliability-engineer (RR-2).

**F4 — PyYAML is an undeclared dependency** relied upon transitively via `uvicorn[standard]`. §1.3.

**F5 — `_MAX_PROMPT_CHARS = 2000` silently amputates.** Harmless today; a correctness hazard once the cast block is appended. §2.5.

**F6 — `FakeStoryProvider` never returns an `illustration_hint`** (`story_fake.py:31-37`), so the primary FR-2 path is unexercised by `make run` and by every fake-backed test.

**F7 — Character names are personal data and are not covered by contract #5's enumeration.** §1.7 extends the discipline; recommend the contract text be widened to "no personal data in log records" rather than an enumeration that can be outgrown.

### Genuine defect in a fixed decision (SPEC §3)

None. D1–D10 are internally consistent with FR-1…FR-6 as far as I can determine. The nearest thing to a tension is D5 (`/family list` only) combined with PRODUCT-DIRECTION §3.6's reference-image rationale — see below — but that is a scope boundary, not a defect.

### Open questions I could not resolve from the code

1. **Does `gemini-3.5-flash` accept `thinking_level` or only `thinking_budget`?** Both fields exist in `google-genai` 1.75.0 (verified), but which the *model* accepts requires a live call. PRODUCT-DIRECTION §9 flags this too. Mitigation: expose it as `VERTEX_THINKING_LEVEL` in the ConfigMap so a wrong guess is fixed without an image rebuild.

2. **Will `gemini-2.5-flash-image` honour a trailing "unchanged" override against a contradictory in-scene description?** §2.7's design is ordering plus an explicit instruction plus source-side suppression. I cannot verify weighting without live runs. This is the single largest product risk in this stream, because FR-2's success criterion is subjective and only observable on a real model.

3. **Reference-image input.** PRODUCT-DIRECTION §3.6 names reference-image support as *the* reason for choosing Gemini Flash Image over Imagen 4, and §9 flags verifying it. My design achieves consistency with verbatim text only — which is what FR-1/FR-2 ask for, and what is achievable with a git-backed registry. A stronger future step: commit one *generated* canonical portrait per character (`files/portraits/mila.png`, a model output, not a photograph — no tension with contract #6) and pass it as reference input. That adds binary artifacts to the chart and a second ConfigMap/Secret shape, so it is out of scope here. Recorded as the natural next iteration if text-only consistency proves insufficient under (2).

4. **Does the `lang:xx` token read as acceptable to the actual family?** It is the only stateless syntax I can defend (§4.1), but it is a developer-flavoured UI on a bedtime bot. Worth one round of real use before it is called done.

5. **Hebrew rendering across Telegram clients.** §4.4's isolate/RLM handling is correct per the Unicode bidi algorithm, but client behaviour varies. Needs a manual check (QR-5).

### Cross-stream coordination

| With | Item |
|---|---|
| infra-architect | Registry ConfigMap from `.Files.Get "files/characters.yaml"`; read-only mount at `/etc/family-media-bot/`; `checksum/characters` annotation on both pod templates; `CHARACTERS_REQUIRED=true` and `STORY_LANGUAGE` in `deploy/values/prod.yaml`. Note the registry needs a **volume**, not the existing `envFrom` ConfigMap. |
| reliability-engineer | F3 (retry apology storm) and the `QueueDelivery.attempt` addition; F1 (image-failure swallowing) changes the job failure rate and therefore DLQ behaviour; §1.5 fail-fast interacts with RR-6 (`--atomic` rollback). |
| QA | The test list in §7; the FR-2 assertion is `assert character.appearance in composed_prompt` for both prompts — that single assertion is the requirement; QR-2 should assert the §5.2 allow-list, not just `MAX_TOKENS`. |
| Reviewer | **Sign-off required** on the `Job.language` contract change (§6.2) before implementation starts. |
