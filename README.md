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
| `--no-web` | off | turn off web search, the page reader and Wikipedia |
| `--no-wiki` | off | turn off only the Wikipedia lookup |
| `--autoswitch` | off | opt in to switching coder models automatically based on message intent |
| `--no-autoswitch` | off | force automatic model switching off |
| `--bench-dir PATH` | `./benchmarks` | folder with extra benchmark datasets (`.jsonl`) |
| `--web-allow-private` | off | let the page reader open links on your own network (blocked by default) |
| `--no-kb` | off | turn off the knowledge base |
| `--kb-dir PATH` | `./knowledge` | where uploaded documents are stored |
| `--kb-max-mb N` | 30 | largest single file the knowledge base accepts |
| `--unsafe-remote-exec` | off | allow terminal and file writes on a non-loopback `--host` (**dangerous**) |

## Conversation history (stored on the server)
Every chat is saved as its own JSON file in `data/chats/` (change the folder with `--data-dir`), so history survives clearing the browser, switching browsers, and opening the app from another device on your network. Nothing is capped at the old 50-chat browser limit.

- Saved after every reply, rename-free: the title is the first message. Pin, delete and *Restore backup* are written through as well.
- Writes are atomic (temp file, then rename), so a crash or power cut can't leave a half-written chat.
- **First start after updating:** chats that were only in your browser are moved to the server automatically (once, then removed from the browser). If the server is unreachable they stay put and move on the next load.
- Switching back to the tab picks up chats saved elsewhere. If a save fails, you get a note and the chat stays in the tab; the next save retries.
- Back up or move your history by copying the `data` folder, or use *Back up all chats*. The folder holds your conversations in plain text, so keep it out of git and off shared drives.
- Settings and Memory are still kept in the browser.

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

**Auto-switch.** Off by default: CressyAI keeps the selected coder model for every message. You can explicitly turn it on in the Model tab or start the server with `--autoswitch`. When enabled, it guesses intent and may switch coder models; each model keeps its own settings. You can pin files to *Code* or *Everything else*, test a sentence without switching, or force switching off with `--no-autoswitch`. The settings are saved in `cressy-state.json`.

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

**New in this version**
- **Preview / Run** buttons on `html`, `svg` and `js` code blocks open a sandboxed live preview (Claude-style artifacts, fully offline).
- **Memory** (Settings tab) plus `/remember <fact>`: standing notes sent with every chat.
- **`/compact`** summarizes the chat to free context; a hint appears when the window is 85% full. **Continue** button when a reply hits the token limit.
- **13 new task commands**: `/summarize` `/tldr` `/rewrite` `/proofread` `/translate` `/email` `/outline` `/plan` `/brainstorm` `/proscons` `/table` `/quiz` `/steps`, and new modes: Writer, Analyst, Study buddy, Brainstorm, Translator.
- **Chats**: search (titles and message text), pin to top, back up / restore all chats as JSON.
- **Speed flags**: `--n-batch 512` and `--flash-attn` for faster prompt processing.
