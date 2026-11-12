![Logo](assets/logo.png)

**New UI:** three-pane layout (chats · conversation · Files/Model/Settings tabs), light/dark/auto theme, steady word-by-word streaming with a blinking cursor, a collapsible *Thinking* card for reasoning models, rotating loading texts (Cooking…, Brewing…, Simmering…), and a file browser with **Back / Forward / breadcrumbs** (Backspace or Alt+← goes back).

A private chat UI for a local **GGUF** model. Nothing leaves your machine.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py --model /path/to/model.gguf --root ~/my-project --open
```
Or use `./run.sh model.gguf [project-folder]` (macOS/Linux) / `run.bat model.gguf [project-folder]` (Windows).
Then open http://127.0.0.1:8000 (`--open` does it for you).

| Flag | Default | Meaning |
|---|---|---|
| `--model` | required | path to the `.gguf` file |
| `--root` | `.` | the only folder the app can read (use your project) |
| `--n-ctx` | 4096 | context window; raise to 8192+ for big files |
| `--n-gpu-layers` | 0 | `-1` offloads everything to the GPU |
| `--threads` | auto | CPU threads |
| `--chat-format` | from GGUF | override if the model has no chat template |
| `--host` / `--port` | 127.0.0.1 / 8000 | keep loopback unless you know why |
| `--open` | off | open the browser on start |

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
Alt+N new chat · Alt+L focus input · Alt+R retry · Alt+C copy last reply · Alt+E export · Alt+S side panel · Alt+/ help

## Tips
- Small models forget fast: use *Precise*, give one task per message, attach only the files that matter.
- Slow? Use a smaller or more quantized model (Q4_K_M), or `--n-gpu-layers -1`.
- Out of context? Raise `--n-ctx` (uses more RAM).

## Safety notes
- The server only reads inside `--root` and rejects non-loopback `Host` headers. Don't set `--root` to `~` or `/`, and don't expose it with `--host 0.0.0.0` on an untrusted network.
- Files like `.env` are readable if they're inside `--root`; keep secrets out of the folder or don't attach them.
- Attachments are not restored when you reopen a saved chat (their names are kept, content is not).

## Troubleshooting
- `llama-cpp-python` fails to build: install a C/C++ toolchain (Xcode CLT / build-essential / VS Build Tools) or use a prebuilt wheel.
- Garbled replies: pass `--chat-format` (e.g. `chatml`, `llama-3`).
- "Binary or unreadable file": only text files can be attached.
- Port busy: `--port 8001`.
