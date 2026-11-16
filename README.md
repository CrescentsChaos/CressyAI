<img src="assets/logo.png" width="200" alt="Logo">

**New UI:** three-pane layout (chats · conversation · Files/Model/Settings tabs), light/dark/auto theme, steady word-by-word streaming with a blinking cursor, a collapsible *Thinking* card for reasoning models, rotating loading texts (Cooking…, Brewing…, Simmering…), and a file browser with **Back / Forward / breadcrumbs** (Backspace or Alt+← goes back).

A private chat UI for a local **GGUF** model. Nothing leaves your machine.
A private chat UI for a local **GGUF** model. The model always runs on your machine; the optional **Web** button looks things up through your own SearXNG (see *Web search*).

## Run it
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py --model /path/to/model.gguf --root ~/my-project --open
```
Or use `./run.sh model.gguf [project-folder]` (macOS/Linux) / `run.bat model.gguf [project-folder] [searxng-url]` (Windows). The optional third Windows argument overrides `SEARXNG_URL`.
Then open http://127.0.0.1:8000 (`--open` does it for you).

| Flag | Default | Meaning |
|---|---|---|
| `--model` | none | path to the coder `.gguf`; leave it out to start empty and load from the *Model* tab |
| `--models-dir` | `E:\AI\Models` | folder(s) scanned for `.gguf` files (repeatable); the coder's own folder is always scanned too |
| `--no-autoload` | off | start with nothing in memory even if `--model` is given |
| `--reasoner` | auto | reasoning `.gguf`; auto-picks `gemma-4-12b-it-Q4_K_M.gguf`, else `Qwen3-8B-Q6_K.gguf`, from the models folder |
| `--reasoner-n-ctx` | 8192 | context while the reasoner is loaded |
| `--no-reasoner` | off | don't look for a reasoner on start (you can still choose one in the app) |
| `--root` | `.` | the only folder the app can read (use your project) |
| `--n-ctx` | 4096 | context window; raise to 8192+ for big files |
| `--n-gpu-layers` | 0 | `-1` offloads everything to the GPU |
| `--threads` | auto | CPU threads |
| `--chat-format` | from GGUF | override if the model has no chat template |
| `--host` / `--port` | 127.0.0.1 / 8000 | keep loopback unless you know why |
| `--open` | off | open the browser on start |
| `--no-terminal` | off | disable the Terminal tab and the *Run* buttons |
| `--read-only` | off | never write into `--root` (downloads still work) |
| `--vision-model` | auto | Qwen2.5-VL `.gguf`; auto-found in `E:\AI\Models` or next to `--model` |
| `--vision-mmproj` | auto | its `mmproj` `.gguf`; auto-found next to the vision model |
| `--vision-n-ctx` | 8192 | context while the vision model is loaded |
| `--vision-max-tokens` | 1024 | max tokens of extracted details per image |
| `--no-vision` | off | disable image attachments |
| `--image-model` | auto | Stable Diffusion `.gguf`; auto-picks `stable-diffusion-xl-base-1.0-Q5_K_M.gguf` from the models folder |
| `--image-vae` | none | optional separate VAE (use `sdxl-vae-fp16-fix.safetensors` if images come out black) |
| `--image-clip-l` / `--image-clip-g` | auto | text encoder files, only needed when the `.gguf` holds just the UNet (auto-found as `clip_l*` / `clip_g*` in the models folder) |
| `--image-dir` | `./generated` | where generated images are saved |
| `--data-dir` | `./data` | where conversation history is stored (`<data-dir>/chats/<id>.json`, one file per chat) |
| `--image-low-vram` | off | keep the text encoder and VAE on the CPU (slower, needs less VRAM) |
| `--image-flash-attn` | off | flash attention for the diffusion model (CUDA builds) |
| `--image-no-vae-tiling` | off | decode the whole image at once (faster, needs more VRAM) |
| `--image-threads` | auto | CPU threads for image generation |
| `--no-image` | off | disable image generation and editing |
| `--searxng-url` | `http://127.0.0.1:8080` | SearXNG address for the Web button (or set `SEARXNG_URL`) |
| `--no-web` | off | turn off web search, the page reader, Wikipedia and GitHub |
| `--no-wiki` | off | turn off only the Wikipedia lookup |
| `--no-github` | off | turn off only the GitHub lookup |
| `--github-token` | `$GITHUB_TOKEN` | optional GitHub token (prefer the environment variable: command lines are visible to other programs) |
| `--no-inat` | off | turn off the iNaturalist nature lookup (also off with `--no-web`) |
| `--inat-token` | `$INAT_TOKEN` | iNaturalist API token (JWT) that unlocks photo identification |
| `--inat-locale` | `en` | language for common names (`en`, `bn`, `es`, …) |
| `--no-geo` | off | turn off Places & weather (Open-Meteo and OpenStreetMap); also off with `--no-web` |
| `--no-weather` / `--no-osm` | off | turn off only the Open-Meteo weather, or only the OpenStreetMap places |
| `--osm-contact` | none | your e-mail or a web address, sent in the User-Agent of OpenStreetMap requests as their usage policy asks (or set `OSM_CONTACT`) |
| `--nominatim-url` / `--overpass-url` | public servers | use your own Nominatim server / another Overpass endpoint |
| `--autoswitch` | off | opt in to picking the slot automatically: the Coder for code questions, the Chat slot for everything else |
| `--no-autoswitch` | off | force automatic slot picking off |
| `--chat-model PATH` | none | model .gguf for the Chat slot (it does all the chatting; code goes to the Coder) |
| `--chat-n-ctx N` | 8192 | context window of the Chat model |
| `--no-tools` | off | turn off tool orchestration (see [Tools](#tool-orchestration-function-calling)) |
| `--tool-steps N` | 4 | most rounds of tool calls the model may make for one answer (1–6) |
| `--no-agent-exec` | off | the model may not write files or run commands through tools (`filesystem.write`, `filesystem.edit`, `terminal.run`); reading and searching stay on |
| `--agent-auto-approve` | off | **dangerous:** do not ask before the model writes a file or runs a command |
| `--bench-dir PATH` | `./benchmarks` | folder with extra benchmark datasets (`.jsonl`) |
| `--web-allow-private` | off | let the page reader open links on your own network (blocked by default) |
| `--no-kb` | off | turn off the knowledge base |
| `--kb-dir PATH` | `./knowledge` | where uploaded documents are stored |
| `--kb-max-mb N` | 30 | largest single file the knowledge base accepts |
| `--embed-model PATH` | auto | small embedding `.gguf` for semantic memory search (see [Memory](#memory)) |
| `--no-embed` | off | turn off semantic search; memory then matches on keywords |
| `--embed-idle N` | 300 | seconds idle before the embedding model is freed from RAM (0 = keep loaded) |
| `--unsafe-remote-exec` | off | allow terminal and file writes on a non-loopback `--host` (**dangerous**) |

## Conversation history (stored on the server)
Every chat is saved as its own JSON file in `data/chats/` (change the folder with `--data-dir`), so history survives clearing the browser, switching browsers, and opening the app from another device on your network. Nothing is capped at the old 50-chat browser limit.

- **Saved the moment you press send.** The chat appears in *Recent chats* immediately, before the first token arrives, and the partial answer (or the agent's progress) is saved every few seconds while it streams, so a refresh never loses the conversation. A reply that was cut off by a reload is marked *Interrupted*; type **continue** and it carries on.
- Saved after every reply, rename-free: the title is the first message. Pin, delete and *Restore backup* are written through as well.
- Writes are atomic (temp file, then rename), so a crash or power cut can't leave a half-written chat.
- **First start after updating:** chats that were only in your browser are moved to the server automatically (once, then removed from the browser). If the server is unreachable they stay put and move on the next load.
- Switching back to the tab picks up chats saved elsewhere. If a save fails, you get a note and the chat stays in the tab; the next save retries.
- Back up or move your history by copying the `data` folder, or use *Back up all chats*. The folder holds your conversations in plain text, so keep it out of git and off shared drives.
- Settings and the *Always included* box are kept in the browser. Notes, tasks and chat hand-offs are stored on the server (see [Memory](#memory)).

## Memory
Memory has four parts, all in **Settings → Memory**. Nothing leaves your machine, and everything fails soft: if any step errors, your message is sent exactly as it would be without memory.

**1. Semantic recall.** Each message is matched against your notes, tasks and earlier chats *by meaning*, so "which db do we use?" finds "We store data in PostgreSQL". Matching combines keywords (BM25 + trigrams) with embeddings.
- Put a small embedding model in your models folder, for example `nomic-embed-text-v1.5.Q8_0.gguf` (about 150 MB). `bge-small`, `e5-small`, `all-MiniLM` and `gte-small` also work and are found automatically; use `--embed-model` to choose one. It is not offered as a chat model.
- It runs on the **CPU only**, is loaded the first time a note is searched and freed after `--embed-idle` seconds, so the "one big model at a time" rule and your VRAM are unaffected.
- No embedding model, or it fails to load? Memory keeps working on keywords, and the panel says why. Swapped the model? Click *Rebuild search index*.
- Related-ness thresholds differ between embedding models. If recall feels too eager or too shy, tune `CRESSY_DENSE_MIN` (default `0.42`) and `CRESSY_DENSE_MARGIN` (`0.07`) in the environment before starting the server.

**2. Project-specific memory.** Notes are *this project* (the folder given by `--root`) or *every project*. The Memory tab shows which, and each note can be switched with one click. Open tasks and chat hand-offs are always per project. A one-line project profile (name, languages, frameworks, first README sentence) is built from files like `requirements.txt` and `package.json`, with no model involved.
- Renamed or moved a folder? *Other projects → Move here* brings its notes, tasks and hand-offs along.
- *Export memory* / *Import* copy a project's memory between machines (no ids or folder paths inside).

**3. Automatic conversation summarization.** When a chat fills about half of the context window (capped at 12k tokens), the oldest turns are replaced **for the model only** by a running summary (goals, decisions, files, open items). The chat on screen stays complete. The most recent turns stay word for word, the summary is saved with the chat, and editing an earlier message discards it. Before this, the server silently dropped the oldest turns once the window was full. `/summary` shows the current summary, `/compact` still works, and *Summarize long chats* turns it off.

**4. Cross-session task continuity.** After a few turns (or sooner when you say things like "remember", "next step", "let's"), and when you leave a chat, the model reads the recent turns and saves durable notes, new tasks, progress on open tasks, finished tasks, and a short **hand-off** (where this chat stands, what comes next). A new chat in the same project then opens with *Pick up where you left off* (last hand-off plus open tasks, one click to continue), and the model is given the last hand-off on your first message. Older chats resurface when they match what you ask. A model-free fallback hand-off is saved after every reply, so closing the tab never loses your place. Turn the learning off with *Learn notes & tasks from chats*.

| Command | What it does |
|---|---|
| `/remember [global:\|project:] text` | save a note (default scope follows the selector in Memory) |
| `/forget words` | find the best-matching note and delete it after a confirmation |
| `/resume` | pre-fill a message that picks up the last chat and your open tasks |
| `/summary` | show this chat's running summary |

Details worth knowing:
- Background work (summaries, learning) only runs when no reply is streaming, and is cancelled the moment you send a message.
- Notes you wrote yourself are never overwritten by learned ones. Likely secrets (API keys, passwords, tokens) are never saved from chats. Deleting a chat also deletes its hand-off. When memory is full, the least-used unpinned note is evicted.
- Files: `data/memory.json` (notes, tasks, hand-offs) and `data/memory.vectors.json` (search index, safe to delete). Both are plain text; keep `data/` out of git.

## Reasoning model (think first, then code)
Press **Reason** next to the paperclip (or start a message with `/think`). Only one model is ever in memory:

1. **Coder unloaded** and the **reasoner loaded** (`gemma-4-12b-it-Q4_K_M.gguf` if found, otherwise `Qwen3-8B-Q6_K.gguf`, both looked up in `E:\AI\Models`).
2. The reasoner works the task through; you watch it live in the collapsible *Thinking* card.
3. **Reasoner unloaded**, **coder reloaded**, and the coder writes the answer using those notes (they're added to that one request only, never shown as your message).

If the message has an image, the order is vision → reasoner → coder, and the coder is only loaded once, at the end. **Stop** at any point puts the coder back. If the reasoner can't load, the coder just answers normally and a note tells you why.
*Settings → Reasoning* has *Every message* vs *Only complex ones* (long messages, code, plan mode, or words like why/fix/design/refactor), the reasoner's temperature / top-p / top-k / repeat penalty, its **token budget**, and an optional replacement for the built-in reasoning instructions. The reasoner's own `<think>` tags are handled; models without them (Gemma) are simply told to reason first.

## Manual model control (Model tab → Models)
Each of **Coder**, **Reasoner** and **Vision** has a card:
- **Load / Switch** (pick any `.gguf` from the scanned folders, only files from those folders are accepted), **Unload**, **Reload**. Loading one unloads whatever else is in memory. A new model starts with its own settings and a context of at most 8192 tokens instead of inheriting the previous model's context; the previous model is restored if the new one cannot load.
- **Tweak settings**: context size, GPU layers, CPU threads, batch size, flash attention, chat format. Settings are kept separately for each model in its Coder or Reasoner slot. *Save* stores them for the next load; *Save & reload* applies them to that same model. A card warns when the loaded model is running older settings.
- Buttons refuse politely while a reply is generating. Chatting with the coder unloaded loads it automatically.
- Starting the server with no `--model` is fine: nothing is loaded until you press **Load**.

## Model management (Model tab)

**Quantization levels.** Files that differ only by quantization (`…-Q4_K_M.gguf`, `…-Q8_0.gguf`, `…-F16.gguf`) are grouped. Each Coder / Reasoner card has a *model* picker and a *quantization* picker showing file size, approximate bits per weight, a one-line quality note and a warning when the file may not fit in free RAM. Pick one and press **Load**. The *Quantization* card can also create a new size from a file you already have (it runs llama.cpp's own quantizer in a separate process and writes the new `.gguf` next to the source): from F16 / BF16 / F32 directly, or from an already-quantized file if you tick the box (that loses a little extra quality). The quantize step needs a `llama-cpp-python` build that exposes `llama_model_quantize`; recent builds do.

**Chat slot and Auto-pick slot.** The Chat slot holds the model that does all the chatting. The Chat model is remembered and loaded when the server starts (unless `--no-autoload`); without one, `--model` loads the Coder as before. Picking a Chat model turns Auto-pick slot on by default (you can switch it off in the Model tab). While it is on, a message about code goes to the Coder, loaded if it is not in memory; every other message goes to the Chat slot, and the Coder is never loaded for it. It never changes which model file a slot holds, so each slot keeps its own context and tweak settings. If the Chat slot is empty, a non-code message is answered only by a model that is already in memory. You can test a sentence without loading anything. Settings are saved in `cressy-state.json`.

**Benchmarks.** *Model tab → Benchmark* runs on the Coder or Reasoner (it loads it first if needed):
- **Speed**: prompt-processing and generation tokens per second, time to first token, memory.
- **Quality**: built-in mini sets in the style of MMLU (knowledge), ARC / logic (reasoning), GSM8K (maths), IFEval (instruction following) and code reading, 39 questions in total, scored automatically at temperature 0.
- **Your own sets**: drop `.jsonl` files into `benchmarks/` (or `--bench-dir`). One JSON object per line: `{"question": "...", "choices": ["a","b","c","d"], "answer": "B"}` for multiple choice, `{"question": "...", "answer": 42}` for a number, or `{"question": "...", "answer": "Paris"}` for a short text answer. This is how you run real MMLU / ARC / GSM8K subsets. Each file is capped at 50 questions to keep a run short.

Runs are saved (newest 40) and grouped by model so you can compare quantizations side by side: score against write speed. The built-in sets are small samples, so use them to compare your own files with each other, not with published leaderboards. Press **Stop** to cancel; the model is released again.

## Images (Qwen2.5-VL, loaded only when needed)
Attach, drop or paste a PNG / JPG / WebP / GIF (up to 4 per message). Only one model is in memory at a time:

1. **Coder unloaded** the moment you send a message that has an image.
2. **Qwen2.5-VL** (`...-Q4_K_M.gguf` + `...-mmproj-f16.gguf`) is loaded and reads the image: it transcribes text/code/errors and describes layout and details, guided by what you typed.
3. **Both vision files are unloaded** as soon as the extraction is done.
4. **Coder reloaded**, and it answers your message using the extracted details, so follow-ups like "write the fix" or "turn this into a table" just work.

The status line and the model pill show which step is running; **Stop** cancels and puts your message back in the box. The extracted text appears under your message (*Image details*) and stays in the chat, so later turns do not re-read the image. Needs a `llama-cpp-python` recent enough to include `Qwen25VLChatHandler` (`pip install -U llama-cpp-python`, same CUDA/Metal build as before). Large images are shrunk to 1280 px in the browser first.

## Image generation and editing (Stable Diffusion XL)
One-time setup (optional, so the app still installs without a compiler):
```bash
pip install -r requirements-image.txt        # GPU builds: see the flags at the top of that file
```
Put `stable-diffusion-xl-base-1.0-Q5_K_M.gguf` in `E:\AI\Models` (or pass `--image-model`). The *Model* tab then shows an **Image** card.

- `/imagine a lighthouse at dawn, oil painting` (also `/img`, `/draw`) makes an image.
- `/imgedit make it night` (also `/retouch`) edits the **attached** image, or the last generated one. Editing is img2img: describe how the picture should look afterwards, and use *Settings → Images → Edit strength* (low keeps the original, high repaints it).
- The **Image** button (Alt+I) turns every message into an image prompt; attach a picture and it edits that instead.
- Under each image: **Enlarge**, **Download**, **Edit**, **Vary** (small img2img change), **Re-roll** (new seed), **Save** (copies it into `generated-images/` in your project folder).
- By default the coder first rewrites your words into a proper Stable Diffusion prompt (you can read it under each image; switch it off in Settings → Images).

How it uses memory: only one model is ever loaded. The coder writes the prompt, is **unloaded**, the image engine runs in a **separate process** (so all RAM/VRAM is released when it exits), then the coder is **reloaded**. **Stop** (Esc) kills the image process and brings the coder back. Images are saved in `generated/` with the settings embedded in the PNG.

Not included: inpainting with a mask, and instruction-style editing ("make the sky red" without describing the whole picture) — SDXL base does prompt-guided img2img only.

## Workspace: files, terminal, plans, charts
The model is taught (via a short system prompt, switch it off under *Settings → Workspace*) to answer in formats the app turns into actions. Small models need `Precise` sampling and one task per message.

| Feature | How it works |
|---|---|
| **Create files** | A ` ```file:src/app.py ` block gets *Save…* and *Download* buttons. *Save…* shows a review dialog (new file, or a diff if it overwrites), then writes inside `--root`. |
| **Edit files** | An ` ```edit:path ` block holds `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` sections. *Review edit* applies them to the current file (project, earlier edits this chat, or an attachment), shows the diff, then *Save to project* or *Download*. Ambiguous or missing matches are reported instead of guessed; CRLF line endings are preserved. Attach the file first (`@path`) so the model can see it. |
| **Changes tab** | Everything you saved or downloaded this session, with *Diff*, *Download*, *Save*, **Download all (.zip)** and *Save all*. |
| **Terminal** | Right-panel tab (Alt+T) streams a command's output live, with Stop, history (↑/↓) and a time limit (Settings). `bash` blocks in a reply get a **Run** button that asks first, shows the output under the block, and **Send output to chat** lets the model read the result. Each command starts fresh in `--root` (no state carried between commands). |
| **Plan mode** | Toggle *Plan* next to the paperclip, or use `/plan <goal>`. The model writes Goal / Approach / Files / Steps / Risks and nothing else. The plan card lets you tick steps, **Build** one step, **Build everything**, or **Revise**. Approving turns plan mode off. Progress is saved with the chat. |
| **Charts & diagrams** | A ` ```chart ` block (JSON: `type`, `title`, `labels`, `datasets`) becomes a chart: bar, line, area, pie, doughnut, radar, polarArea, scatter; *Save PNG* included. A ` ```mermaid ` block becomes a diagram (*Save SVG*). Try `/chart`, `/diagram`. Mermaid 12 supports native UML use-case diagrams with `usecase-beta`, actors, use cases, system boundaries, associations, `include` / `extend`, and generalization. |
| **Other commands** | `/newfile`, `/edit`, `/run` ask for the matching format. |

Example use-case diagram:
```mermaid
usecase-beta
direction LR
actor Customer("Customer")
actor Admin("Store administrator")
systemBoundary "Online Store"
  Browse("Browse catalog")
  Order("Place order")
  Payment("Take payment")
  ManageCatalog("Manage catalog")
end
Customer --> Browse
Customer --> Order
Order ..> : include Payment
Admin --> ManageCatalog
```

## Knowledge base (chat with your documents)
Add `.txt`, `.md` and `.pdf` files in the **Knowledge** tab (drop them on the box or click it to browse). Turn on **Knowledge** next to the paperclip and every question is matched against your documents: the best passages are found, shown in a card above the answer, and handed to the model, which cites them as clickable **[K1] [K2]** chips. Click a chip to jump to its source passage.
- `/kb your question` forces a lookup for one message, even with Knowledge off. Retry repeats the lookup.
- Untick a document to keep it stored but out of the answers; the bin removes it. In *Retrieval* you choose how many passages to fetch and how much text to send.
- Searching is plain keyword ranking (BM25) done in Python on your machine. It loads no model, so it never makes the chat model swap out, and nothing is sent anywhere. The trade-off: it matches words, not meaning, so ask using the document's own terms ("annual leave", not "time off") for the best results.
- Markdown headings and PDF page numbers are kept, so sources read like `manual.pdf, p. 12` or `handbook.md - Leave policy`.
- PDFs need the `pypdf` package: with the app stopped run `.venv\Scripts\pip install pypdf` (Windows) or `.venv/bin/pip install pypdf` (macOS/Linux), then start it again. Scanned PDFs have no text to read and are rejected with a message; run OCR on them first. Password-protected PDFs are rejected too.
- Documents are stored as plain JSON in the `knowledge` folder next to `server.py`. Delete that folder to wipe everything.

## Wikipedia
Gives the model encyclopedic knowledge beyond its training. CressyAI asks Wikipedia's public MediaWiki API for the best matching articles, reads the top one (or more) and hands the text to the model as numbered sources, shown as clickable [1] [2] chips like web results. **No SearXNG, Docker or API key needed**; it only needs an internet connection.

- **When it runs:** in the default *When a question asks for facts* mode, questions like "who was…", "what is…", "when did…", "history of…", "tell me about…" are looked up. Code, attached files, `/commands`, small talk and questions about you or the chat are skipped. *Every message* looks up everything that is not local work. `/wiki your question` forces a lookup for one message.
- **With Web on:** Wikipedia articles and SearXNG results are merged into one numbered list (Wikipedia first, duplicates removed). Without Web it works on its own.
- *Settings → Wikipedia*: mode, language (English, Bengali, Hindi, Arabic, Spanish, French, German and more), articles to find (2-6), articles to read in full (0-3), and a *Test connection* button.
- Article text is trimmed to save context: *References*, *External links*, *See also* and similar tail sections are dropped, disambiguation pages are skipped, and repeated lookups are cached for 10 minutes.
- **Privacy:** your search words go to `<lang>.wikipedia.org`; the footer says so. Only that host can be reached by this feature. Start with `--no-wiki` (or `--no-web`) to switch it off.

## GitHub
Lets the model read GitHub through its official REST API. **No SearXNG or Docker needed.** Works for public data without any login.

- **What it reads:** a repository (description, stars, license, topics, latest release, top-level files and the README), a single file or folder, an issue or pull request with its discussion (and for PRs: branches, lines changed, merged state), a release or the releases list, and a user or organization with their recently active repositories. Repository *search* finds projects by what they do.
- **How to trigger it:** paste a GitHub link (`github.com/...` or `raw.githubusercontent.com/...`), or write `owner/repo` next to the word *github* or *repo*. Requests like "find the best github repos for pdf parsing" search repositories (the word *best/top/popular* sorts by stars). `/gh owner/repo`, `/gh <link>` or `/gh <what to look for>` forces it for one message. GitHub links are read through the API, not as web pages, so you get clean README and file text instead of HTML.
- **Together with other sources:** results are merged into the same numbered list as web and Wikipedia results (GitHub first), with the same clickable [1] [2] chips. If GitHub fails (for example the rate limit), the card says so and the other sources still answer.
- *Settings → GitHub*: mode, repositories to find (3-8), READMEs to read (0-2), and a *Test connection* button that also shows how many requests you have left this hour.
- **Limits and token:** without a token GitHub allows **60 requests per hour**; one repository overview uses about 4 requests, a file or issue 1-3, so lookups are cached for 5 minutes. For 5,000 per hour and for private repositories your token can see, start the server with a token in the environment:
  - Windows: `set GITHUB_TOKEN=ghp_yourtoken` then `run.bat`
  - macOS / Linux: `GITHUB_TOKEN=ghp_yourtoken ./run.sh`

  A fine-grained token with read-only *Contents*, *Issues* and *Pull requests* access is enough. The token stays on the server: it is only sent to `api.github.com`, redirects to other hosts are refused, and the browser only learns whether one is set.
- **Privacy:** your lookups go to `api.github.com`; the footer says so. Files larger than 1 MB and binary files are not read. `--no-github` (or `--no-web`) switches it off.

## Places & weather (Open-Meteo + OpenStreetMap)
Live weather, air quality and place lookups, with no accounts or API keys. **Open-Meteo** supplies the weather, air quality and climate history; **OpenStreetMap** supplies place search (Nominatim) and "what is nearby" (Overpass). Both sit next to the Web and Wikipedia features: off with `--no-web`, or separately with `--no-geo`, `--no-weather` and `--no-osm`.

**Three ways to use it**
- **Places tab** (right panel): search a place or address, or press *Use my location* (your browser asks permission; nothing is sent until you agree). You get the current weather, a 24-hour temperature and rain chart, the daily outlook, air quality with a health note, and a comparison with the normal for those dates. Below that, 24 category buttons (pharmacies, hospitals, restaurants, cafés, ATMs, fuel, EV charging, toilets, drinking water, parks, hotels, sights, transit, mosques/churches/temples and more) list what is nearby, nearest first, with distance, direction, address and opening hours as tagged. Results can be downloaded as a **GPX** file for any map app. *☆ My place* saves a default location (kept in this browser only) so "weather today" works without a name.
- **Commands** (always work, even with the Places button off):

| Command | Does |
|---|---|
| `/weather Dhaka 5 days` | forecast, air quality and (if on) the comparison with normal; the place is optional once you have saved one |
| `/aqi` | the same, focused on air quality |
| `/climate Lisbon` | is the coming week unusually warm or cold for the time of year? (average of the last 10 years for the same dates) |
| `/near pharmacy in Gulshan, Dhaka` | places of one kind near a place; with no place it uses your saved one |
| `/where Eiffel Tower` | coordinates, address, Wikipedia link, local time and the weather there now |
| `/distance Paris to Rome` | straight-line distance and compass direction (not a driving route) |
| `/trip Kyoto 4 days` | forecast + air quality + sights + places to eat, so the model can plan days around the weather and say what to pack |

  Each command is turned into a plain question in the message box (so you can see exactly what is sent), the data is fetched, and it is attached to that one request inside `<live-data>…</live-data>` with an instruction to quote it exactly and say what is missing. The Places tab buttons *Weather brief*, *Is this normal?*, *Plan a trip* and *Tell me about it* fill in the right command for the place you are looking at (with its exact coordinates, so "Springfield" is not guessed twice).
- **Places button** (next to *Knowledge*, off by default): while it is on, ordinary questions such as "will it rain in Dhaka tomorrow?", "nearest pharmacy", "how far is Paris from Rome?" or "plan a 3 day trip to Kyoto" are recognised and answered from live data. The detector is deliberately cautious and runs on this machine: nothing is sent anywhere for code, writing requests ("write a poem about rain in Paris"), messages with attached files or anything that is not clearly a weather or place question. If a lookup fails, the model is told it has no live data and must not guess.

**Settings → Places & weather:** units (metric or imperial), forecast length (3-14 days), search radius (500 m to 5 km), the comparison with normal, and the map style. The default map is an **offline radar** (distance and direction, nothing loaded). Choose *OpenStreetMap tiles* for a real map; that makes your browser fetch tiles from `tile.openstreetmap.org` and the Leaflet library from cdnjs, which tells those servers roughly where you are looking. *Test connection* checks both services.

**Privacy and fair use**
- Only a place name or coordinates go out, to `open-meteo.com` (weather, air quality, history and place names) and to OpenStreetMap's Nominatim and Overpass servers. Coordinates sent to Open-Meteo are rounded to two decimals (about 1 km). Your saved place never leaves the browser except as part of a lookup you triggered. The footer says so whenever the Places button is on.
- OpenStreetMap's usage policy asks for an identifiable client: start with `--osm-contact you@example.com` (or set `OSM_CONTACT`). Nominatim requests are limited to one per second and cached for an hour; Overpass queries are built only from the fixed category list, so nothing you type is ever placed in the query. If Nominatim is busy, place names fall back to Open-Meteo's own geocoder. For heavy use, run your own server and pass `--nominatim-url` / `--overpass-url`.
- Open-Meteo is free for non-commercial use without a key (fair-use limits apply: a request fails with a clear message when the limit is hit). Weather data is CC BY 4.0 and map data is © OpenStreetMap contributors (ODbL); both are credited in the Places tab.
- Forecasts can be wrong, OpenStreetMap is made by volunteers (places can be missing, closed or moved, and opening hours are only what contributors entered), and the model is told both. Distances are straight-line, not routes: OpenStreetMap has no free public routing service, so there is no turn-by-turn or travel-time feature.

## Web search (SearXNG)
Lets the model answer from live results. [SearXNG](https://github.com/searxng/searxng) is a self-hosted meta search engine; CressyAI asks it for results, opens the top pages, and hands the text to the model as numbered sources. The model itself never touches the network.

**Set it up (once)**
1. Install Docker, then in this folder run `docker compose up -d` (or `./searxng.sh` / `searxng.bat`). It listens on `127.0.0.1:8080` only. `searxng/settings.yml` already enables the JSON format and turns the limiter off; if you use your own SearXNG, add `json` under `search: formats:`.
2. Start CressyAI as usual (use `--searxng-url http://host:port` if it is elsewhere). *Settings → Web search → Test connection* tells you what is wrong if it fails.

**Use it**
- **Web is on by default** once SearXNG answers (it switches itself on when SearXNG starts; press **Web** to turn it off for good). In the default *By default* mode every question is searched live unless it is clearly local work: a `/command`, attached files or `@file`, code, small talk ("thanks"), or a request about the chat itself ("shorten that"). *Only fresh-info questions* keeps the older keyword rule (news, prices, versions, "latest…", weather); *Every message* always searches. If the model still answers with "as of my knowledge cutoff…" or "I can't browse", the app searches and answers again automatically (once). `/web …` forces a search.
- `/web your question` (or `/search`) forces a search for one message, even with Web off, and uses your words as the query.
- Paste a link and it is read (up to 3 links) instead of searched.
- A collapsible card above the reply lists the sources (marked *read* when the page text was used); `[1]` `[2]` in the answer become clickable chips. **Retry** repeats the lookup.
- *Settings → Web search*: results (4-10), pages to read (0-4), text budget, freshness, category (web/news/IT/science). The budget is capped by the context window, so small `--n-ctx` values stay safe.
- If search fails, the card shows why and the model is told it has no fresh data, so it should not pretend to have searched.

**Privacy:** your search words go to SearXNG and on to the engines it queries; page text never leaves your machine except as the page request itself. The footer says so while Web is on. The page reader refuses private/local addresses and non-web links, re-checks every redirect, and skips PDFs and binaries.

## Text & style
*Settings → Text & style*: response style (**Balanced / Rich / Plain**, which tells the model how to format), font, size, line spacing, reading width, accent colour, and LaTeX on/off. Replies can show:
- callouts: a quote starting `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]` (also `> **Warning:** …`)
- tables with click-to-sort headers, zebra rows, *Copy table* (pastes into Sheets/Excel) and *Download CSV*
- task lists, `==highlight==`, `<kbd>`, source chips
- math: `$x^2$`, `$$\\int…$$`, `\\( \\)`, `\\[ \\]` (KaTeX loads from cdnjs on first use; currency like $5 or $10 is left alone)
- a **Wrap** button on code blocks

## What you can do
**Slash commands** – paste code after the command, attach a file, or run it bare to use the last code block in the chat.

| Command | Does |
|---|---|
| `/explain` `/fix` `/review` `/refactor` | understand, debug, review, clean up code |
| `/test` `/doc` `/bigo` | unit tests, docstrings, complexity analysis |
| `/commit` | Conventional Commit message from your **staged** diff |
| `/review` (bare) | reviews your **working** diff |
| `/regex` `/sql` `/shell` `/name` `/eli5` | describe what you need in words |
| `/convert` | `/convert Go` + newline + code |
| `/help` | list commands and shortcuts |

**@file mentions** – type `@src/app.py` to attach it (Tab autocompletes from the project).
**PDFs** – attach, drop, or pick a PDF from the project files to extract its selectable text (up to 20 MB and 200,000 characters). Scanned/image-only PDFs need OCR, which is not supported yet.
**Git buttons** (Files panel) – attach working diff, staged diff or last commit.
**Search in files** – substring search across the project, click *Add* to attach a hit.
**Modes** – Code helper, Code reviewer, Debugger, Tutor (set the system prompt) and sampling presets Precise / Balanced / Creative.
**Chats** – auto-saved in your browser (last 50), reopen from the *Chats* list, export as Markdown.
**Message actions** – Copy, Retry (last reply), Edit (rewinds the chat to that message), Delete.
**Code blocks** – *Copy* and *Save* (downloads with the right extension).
**Context guard** – when a chat outgrows the context window the oldest turns are dropped automatically; the reply footer shows how many.
**Settings** are remembered between sessions. Also: drag-and-drop files, live tokens/sec, context bar, RAM/CPU, GGUF metadata.

## Shortcuts
Enter send · Shift+Enter newline · Esc stop/close popup · ↑/↓ recall earlier messages · Tab accept suggestion ·
Alt+N new chat · Alt+L focus input · Alt+R retry · Alt+C copy last reply · Alt+E export · Alt+S side panel · Alt+T terminal · Alt+/ help ·
Alt+I image mode · Alt+G new seed for the last image · Alt+V edit the last image
Alt+N new chat · Alt+L focus input · Alt+R retry · Alt+C copy last reply · Alt+E export · Alt+S side panel · Alt+T terminal · Alt+/ help

## Tips
- Small models forget fast: use *Precise*, give one task per message, attach only the files that matter.
- Slow? Use a smaller or more quantized model (Q4_K_M), or `--n-gpu-layers -1`.
- Out of context? Raise `--n-ctx` (uses more RAM).

## Safety notes
- **Terminal and file writing run with your user permissions.** They only work when the server is bound to loopback (the default), require a custom header and a same-site `Origin` (so other websites cannot call them), and *Run* on a model-suggested command asks first. You are still running commands a language model wrote: read them. Use `--no-terminal` / `--read-only` to turn either off.
- Writes are confined to `--root`, refuse `.git/`, binary files and symlinks that leave the folder, and are atomic. Keep the project under git so you can undo.
- The server only reads inside `--root` and rejects non-loopback `Host` headers. Don't set `--root` to `~` or `/`, and don't expose it with `--host 0.0.0.0` on an untrusted network.
- Files like `.env` are readable if they're inside `--root`; keep secrets out of the folder or don't attach them.
- Attachments are not restored when you reopen a saved chat (their names are kept, content is not).

## Troubleshooting
- `llama-cpp-python` fails to build: install a C/C++ toolchain (Xcode CLT / build-essential / VS Build Tools) or use a prebuilt wheel.
- Images: "stable-diffusion-cpp-python is not installed" → `pip install -r requirements-image.txt` inside the app's `.venv`.
- Images: "get sd version from file failed" / "NULL pointer": stable-diffusion.cpp could not recognise the `.gguf`. The error card lists what the file contains; a UNet-only file needs a full SDXL `.gguf` or `clip_l` / `clip_g` / VAE files in the models folder.
- Images come out completely black: the SDXL VAE overflows in half precision. Download `sdxl-vae-fp16-fix.safetensors` and pass `--image-vae path\to\it`.
- Image engine runs out of memory: pick a smaller size in Settings → Images, or start with `--image-low-vram`.
- Garbled replies: pass `--chat-format` (e.g. `chatml`, `llama-3`).
- "Binary or unreadable file": only text files and PDFs with selectable text can be attached. Scanned PDFs need OCR, which is not supported yet.
- Port busy: `--port 8001`.
- Places: "Cannot reach Open-Meteo / OpenStreetMap": check your internet connection; *Settings → Places & weather → Test connection* says which one is down. Busy Overpass servers answer 429/504; wait a minute or use a smaller radius. A 403 from OpenStreetMap usually means requests are not identifiable: set `--osm-contact`.
- Places: a nearby search finds nothing: the area may not be well mapped in OpenStreetMap; try a bigger radius.

**New in this version**
- **Preview / Run** buttons on `html`, `svg` and `js` code blocks open a sandboxed live preview (Claude-style artifacts, fully offline).
- **Memory** (Settings tab) plus `/remember <fact>`: standing notes sent with every chat.
- **`/compact`** summarizes the chat to free context; a hint appears when the window is 85% full. **Continue** button when a reply hits the token limit.
- **13 new task commands**: `/summarize` `/tldr` `/rewrite` `/proofread` `/translate` `/email` `/outline` `/plan` `/brainstorm` `/proscons` `/table` `/quiz` `/steps`, and new modes: Writer, Analyst, Study buddy, Brainstorm, Translator.
- **Chats**: search (titles and message text), pin to top, back up / restore all chats as JSON.
- **Speed flags**: `--n-batch 512` and `--flash-attn` for faster prompt processing.


## Nature lookup (iNaturalist)
A **🌿 Nature** button (bottom-left) opens the iNaturalist explorer in a new tab. It uses the public iNaturalist API v1; no account or key is needed except for photo identification. Only `api.inaturalist.org` is contacted, results are cached for 10 minutes and requests are spaced out to respect their rate limit.

- **Species**: search by common or scientific name. Each page has a summary, classification breadcrumbs (click to climb the tree), conservation status, photos, a month-by-month *when is it seen* chart, look-alike species and child taxa.
- **Nearby**: what lives around you or inside a named place, filtered by group (birds, plants, insects…), recency, threatened-only and native-only.
- **Sightings**: recent research-grade observations with photos for the chosen place.
- **Identify**: drop a photo for computer-vision suggestions. Needs a token: log in at inaturalist.org, open `https://www.inaturalist.org/users/api_token`, and start the server with `--inat-token <jwt>` (or `INAT_TOKEN`). Tokens last 24 hours.
- In chat, attach an animal photo and use **`/idspecies`** to identify it and add a profile card with iNaturalist and Wikipedia data.
- **Add to chat** returns to the chat page with a compact data block in the composer so the model answers from real records. Shortcut: type `/inat red fox` and press Enter.

Set the location once (search a place, or *Use my location*) and Nearby, Sightings and the seasonality chart all use it. API: `GET /api/inat/status`, `POST /api/inat/{search,taxon,places,nearby,observations,observation,season,observers,identify}`.

## Agent mode (autonomous coding with verification)

Press **Agent** under the message box (or type `/agent <task>`) and the model stops answering and starts *working*: it looks at the project, edits files, runs commands, reads the real results and keeps going until the job is done and checked. It needs file writing (not `--read-only`) and, for tests, the terminal (not `--no-terminal`). The first time you turn it on it explains what it may do.

**Continue, and ticked-off steps.** If a run stops (Stop button, page reload, out of steps, error), type **continue** (or press the *Continue* button on the card). A new run starts from the saved state of the old one: the plan with its ticks, the files already changed and the last steps. It does not re-plan, does not re-run the baseline and does not redo finished work. Plan steps are ticked off automatically: a step that names files is ticked once each of them was written without a syntax error, a test/verify step once the checks pass, and the whole plan when the run finishes. A tick is never lost, even if the model resends the plan without the `[x]`. In *Plan* mode (normal chat) a step is ticked when its reply has finished, not when you click *Build*.

**How a run works**

1. **Orientation.** It gets the project tree and the test command(s), and runs the tests once *before* touching anything, so old failures are not blamed on the task.
2. **Multi-step loop.** Every step the model replies with action blocks: `ls`, `read:PATH`, `grep`, `edit:PATH` (SEARCH/REPLACE), `file:PATH`, `bash`, `test`, `plan`, `done`. The system executes them and sends back the actual output. It keeps a plan, a ledger of changed files and the last verification result in every observation, and when the context window fills up it summarises older steps instead of forgetting the task.
3. **Automatic error detection.** Every written `.py`, `.json`, `.toml`, `.js` and `.sh` file is syntax-checked immediately. Failed commands and tests are parsed into a diagnosis (Python / Node / compiler errors, pytest / unittest / jest / `node:test` / go / cargo results): the exception, the failing tests, the file and line, the code around it, and for a failing test the module it imports. A SEARCH block that does not match comes back with the closest text in the file.
4. **Repair and retry.** If the same failure repeats it gets a warning, and the third time that attempt is **rolled back** (only the files involved) and a new attempt starts with a note of what was already tried. The number of attempts is a setting; when they run out the run ends as *Could not finish*, honestly, with the files left for you to review or revert.
5. **Verify before finishing.** `done` is not taken on trust. The system re-runs the checks itself (syntax of every changed file, then the tests) and sends the model back to work if they fail. If the project has no tests, a code change has to be run successfully (or the result is reported as syntax-checked only). Tests that were already failing before the task are reported separately instead of trapping an unrelated task.

**Safety.** Dangerous commands never run (deleting the project or anything outside it, `sudo`, `git push`, piping a download into a shell, shutdown, disk formatting). Installs, deletes, git changes, network tools and similar **ask you first** (inline Allow / Deny in the card; or set Settings → Agent to *never* or *without asking*). Files stay inside the project folder, `.git` is never written, and every change is recorded in the **Changes** tab. **Revert all changes** undoes a whole run, including files created by commands. Stop (or Esc) cancels at once and kills a running command. It runs real commands on your machine, so keep the project in git as well.

**Tests it can find on its own:** `pytest` (when installed and configured), `unittest`, `npm test`, plain `node tests/*.js` scripts, `go test`, `cargo test`, `make test`, `dotnet test`. To choose them yourself, put the commands in Settings → Agent, or add a `cressy-agent.json` to the project: `{"test": ["python -m pytest -q", "npm test"]}`.

Small local models do much better with `--n-ctx 8192` or more; the agent warns when the window is tiny. Only one run at a time. Agent mode is separate from tool orchestration (`/api/agent`): its own endpoints are under `/api/agentrun`, and it needs the Coder slot, which it loads itself. The code is in `agent.py` (loop, retries, verification gate) and `agenttools.py` (parsing, sandbox, command guard, diagnosis, test detection); both are standard library only.

## Tool orchestration (function calling)
The model can use tools instead of guessing, and chain them for one answer. It works with any GGUF model: tool calls are plain text (`<tool_call>{"name":…,"arguments":{…}}</tool_call>`), so no special chat template is needed.

| Tool | Does | Needs |
|---|---|---|
| `web_search`, `read_url` | live search and page text; open a link | SearXNG (Web on) |
| `wikipedia` | encyclopedia articles | Wikipedia |
| `github` | a repo, file, folder, issue/PR, release or user (link or `owner/repo`), or a repository search, through the GitHub REST API | GitHub (on unless `--no-github`) |
| `geocode`, `weather`, `nearby_places`, `distance` | coordinates, forecast + air quality, places of one kind, straight-line distance | Places & weather |
| `species_lookup`, `wildlife_nearby` | animal/plant facts, species recorded around a place | iNaturalist |
| `knowledge_search`, `memory_recall` | your uploaded documents; your notes, tasks and earlier chats | documents added / memory |
| `read_file`, `search_project` | a text file or matching lines, inside `--root` only | – |
| `filesystem.read`, `filesystem.glob`, `filesystem.grep` | a file with line numbers (or a slice of it), files by glob (`*.py`, `src/**/*.js`), file contents by regular expression; inside `--root` only | – |
| `filesystem.write`, `filesystem.edit` | create or replace a file; replace exact text in a file | not `--read-only`; **you approve each one** |
| `terminal.run` | one shell command in the project folder, for example `docker compose up -d`, `docker ps`, `docker logs web` | Terminal on (not `--no-terminal`); **you approve each one** |
| `calculator`, `datetime` | exact arithmetic (no `eval`); date, time, time zones, days until | – |

- **Selection by intent.** Each message is matched to intents (weather, places, nature, GitHub, fresh facts, documents, files, maths, time…). Only the tools that fit are offered to the model, which keeps the prompt small. Small talk, rewrites of earlier text and code get no tools and cost nothing extra. Obvious calls ("weather in Dhaka", a pasted link, a bare sum) run before the model speaks.
- **Multi-tool workflows.** In one round the model may request several independent calls (they run in parallel); a later round can use earlier results (geocode a place, then ask its weather). `--tool-steps` caps the rounds; a call budget caps the total.
- **Verification.** Arguments are checked against each tool's schema before anything runs (bad ones go back to the model with the reason). Every result then gets a verdict: **ok**, **check** (partial: for example the top search hit does not match the query, a forecast has an implausible temperature, a page had no text), **empty** or **failed**. The model sees the verdict and must not present failed or empty calls as answers; it can fix the arguments and retry. A failed web search falls back to Wikipedia once. After the answer, figures that appear in no tool result, document or earlier message are flagged under the answer.
- **In the app.** A tool card above the answer lists each call with its status, time and notes; web and document sources feed the usual `[1]` / `[K1]` citation chips. *Settings → Tool orchestration* sets mode, rounds and the figure check. `/tools your question` offers every available tool for one message. Explicit commands (`/web`, `/kb`, `/weather`, attachments, plans, the reasoning step) keep their own flow.
- **Not covered:** image generation is not a tool. Writing files and running commands are tools, but they never run without your approval (next section).

### Filesystem and terminal tools (with your approval)
`filesystem.write`, `filesystem.edit` and `terminal.run` let the model change files and run commands, for example to start Docker. Because a model can be steered by text it reads (a web page, a document, a file in your project), these never run on the model's say-so alone:

- **You approve every one.** The tool card stops at *Waiting for your approval…* and shows exactly what will happen: the full command and folder, or a diff of the file change. **Approve** runs it, **Deny** (or no answer for 2 minutes, or **Stop**) refuses it. The model is told it was declined and does not retry.
- **Reading is free, changing is gated.** `filesystem.read`, `filesystem.glob` and `filesystem.grep` need no click. Changes are only offered to the model when your message asks for one (create, edit, fix, run, docker, terminal…), or with `/tools`.
- **Same fences as the rest of the app.** Paths are confined to `--root` (`..` and symlinks cannot escape), nothing inside `.git` is written, binary files are never overwritten, writes are atomic, and CRLF files stay CRLF. `--read-only` removes write and edit, `--no-terminal` removes `terminal.run`, `--no-agent-exec` removes all three for the model while the Terminal tab keeps working for you, and a non-loopback `--host` turns them off unless you pass `--unsafe-remote-exec`.
- **A backstop, not a guarantee.** A short list of plainly destructive commands (`rm -rf /`, formatting a disk, shutdown, `curl … | sh`, `docker run --privileged`, mounting `/` or the Docker socket into a container) is refused even before you are asked. Read each command before you click Approve: that click is the real protection.
- **Commands have no keyboard.** Output is captured, so no `-it` and nothing that waits for input. Commands stop after a timeout (60 s by default, 300 s at most), so use `docker compose up -d` / `docker run -d` for anything that keeps running. A non-zero exit code is reported to the model along with the output so it can explain what went wrong.
- **Docker.** The model runs the same `docker` you would in a terminal, from `--root` (or a folder inside it). Docker (or Docker Desktop on Windows/macOS) must already be running; if it is not, the error text comes back and the model can tell you. This repository's own SearXNG is one example: *"start searxng with docker compose"*. On Windows, Docker Desktop itself can be launched with `start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"` (your install path may differ).
- `--agent-auto-approve` removes the click. Only use it on a machine and a project you are happy to let the model change freely, and never together with web search on pages you do not trust.

## Tests
```
python tests/test_memory.py      # memory.py + embed.py (fake embedding model, no download needed)
node tests/test_memory_js.js     # the pure logic in js/20-memory.js
node tests/test_memory_ui.js     # the whole script against a stubbed page, server and model
python -m unittest discover -s tests -t .   # Places & weather, and tool orchestration: intent selection, schemas, verification, the multi-step loop, the API routes
node tests/test_tools_page.js    # the real page in jsdom (npm i jsdom): tool card, citations, /tools, settings
python -m unittest tests.test_fstools tests.test_toolbox_fs   # filesystem.* and terminal.run: confinement, glob/grep, edit, approval and denial, the command backstop, selection, the loop
python -m unittest tests.test_github   # GitHub: link parsing, the lookup (API faked), the github tool, selection, the API routes
node tests/test_github_page.js   # the real page in jsdom: pasted links, /gh, tool-loop hints, Settings → GitHub
```
The Places & weather tests use a local fake of Open-Meteo, Nominatim and Overpass, so they need no internet and no model. They import the app, so run them inside the app's virtual environment.

### Agent mode: Fast (default) and Strict

Settings, Agent, *Agent mode*. **Fast** just does the task: no planner call, no baseline test run, no rollbacks or retry attempts, no step counter shown to the model, and a bare code block without a `file:` label is saved under a sensible name instead of being rejected. A `done` block is accepted after a quick syntax check (plus the project's own tests when code changed); it is sent back at most twice, then the run ends honestly and keeps your files. **Strict** is the previous plan / verify / roll back / retry behaviour for big repairs.
