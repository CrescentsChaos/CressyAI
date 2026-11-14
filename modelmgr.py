"""Model management helpers for CressyAI: quantization levels, intent routing and benchmarks.

Pure logic only (no llama.cpp, no server state), so it can be tested on its own:  python modelmgr.py
server.py wires these helpers to the loaded models and the HTTP endpoints.
"""
import json
import math
import re
from pathlib import Path

# ------------------------------------------------------------------ quantization levels

# Typical bits per weight and a one-line quality note for each GGUF quantization type (approximate, model dependent).
QUANTS = {
    "IQ1_S": (1.6, "Extremely small, heavy quality loss"), "IQ2_XXS": (2.1, "Tiny, heavy quality loss"),
    "IQ2_XS": (2.3, "Tiny, heavy quality loss"), "IQ2_S": (2.5, "Tiny, clear quality loss"),
    "IQ2_M": (2.7, "Very small, clear quality loss"), "Q2_K": (3.0, "Very small, clear quality loss"),
    "IQ3_XXS": (3.1, "Small, noticeable loss"), "IQ3_XS": (3.3, "Small, noticeable loss"),
    "Q3_K_S": (3.4, "Small, noticeable loss"), "IQ3_S": (3.4, "Small, noticeable loss"),
    "IQ3_M": (3.7, "Small, some loss"), "Q3_K_M": (3.9, "Small, some loss"),
    "Q3_K_L": (4.3, "Small, mild loss"), "IQ4_XS": (4.3, "Good balance, mild loss"),
    "Q4_0": (4.5, "Older 4-bit format; prefer Q4_K_M"), "IQ4_NL": (4.5, "Good balance, mild loss"),
    "Q4_K_S": (4.6, "Good balance, mild loss"), "Q4_K_M": (4.9, "Best all-round size / quality balance"),
    "Q4_K_L": (5.0, "Q4_K_M with larger embeddings, slightly better"), "Q4_K_XL": (5.1, "Q4_K_M with larger embeddings, slightly better"),
    "Q5_0": (5.5, "Older 5-bit format; prefer Q5_K_M"), "Q5_K_S": (5.5, "Close to the original"),
    "Q5_K_M": (5.7, "Very close to the original"), "Q6_K": (6.6, "Near-lossless"),
    "Q8_0": (8.5, "Practically lossless, large"), "F16": (16.0, "Original precision, very large"),
    "BF16": (16.0, "Original precision, very large"), "F32": (32.0, "Original precision, huge"),
    "MXFP4": (4.3, "Native 4-bit format used by some models"),
}
QUANT_RE = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4))(?![A-Za-z0-9])", re.I)
SHARD_RE = re.compile(r"[-_.]0*\d+-of-0*\d+$", re.I)
REFERENCE_QUANT = 4.9     # bits per weight that auto-picking aims for (Q4_K_M)


def quant_of(name):
    """('Q4_K_M', match) for 'model-Q4_K_M.gguf', else ('', None). The last quant-looking token wins."""
    stem = SHARD_RE.sub("", Path(str(name)).stem)
    ms = list(QUANT_RE.finditer(stem))
    if not ms:
        return "", None
    m = ms[-1]
    return m.group(1).upper().replace("UD-", ""), m


def family_of(name):
    """File name without the quantization (and shard) suffix: 'gemma-4-12b-it-Q4_K_M.gguf' -> 'gemma-4-12b-it'."""
    stem = SHARD_RE.sub("", Path(str(name)).stem)
    q, m = quant_of(name)
    if m:
        stem = stem[:m.start()] + stem[m.end():]
    return re.sub(r"[-_. ]{2,}", "-", stem).strip("-_. ") or Path(str(name)).stem


def quant_info(name):
    q, _ = quant_of(name)
    bpw, note = QUANTS.get(q, (0.0, "")) if q else (0.0, "")
    return {"quant": q, "bpw": bpw, "note": note}


def annotate(m):
    """Add family / quant / bpw / note to a model dict (name, path, size)."""
    return {**m, "family": family_of(m["name"]), **quant_info(m["name"])}


def group_models(models):
    """Families with their quantization variants, smallest first: [{family, key, variants:[...]}], sorted by family name."""
    fams = {}
    for m in models:
        a = annotate(m)
        fams.setdefault(a["family"].lower(), {"family": a["family"], "key": a["family"].lower(), "variants": []})["variants"].append(a)
    out = []
    for f in fams.values():
        f["variants"].sort(key=lambda v: (v["bpw"] == 0, v["bpw"], v["size"], v["name"].lower()))
        out.append(f)
    out.sort(key=lambda f: f["family"].lower())
    return out


def best_variant(variants, current_quant="", ram_free=0):
    """The quantization to use when a family is chosen without naming one: the user's last choice, else the one closest
    to Q4_K_M that still fits in free memory (if known)."""
    if not variants:
        return None
    for v in variants:
        if current_quant and v["quant"] == current_quant:
            return v
    ok = [v for v in variants if not ram_free or v["size"] * 1.15 <= ram_free] or variants
    return min(ok, key=lambda v: abs((v["bpw"] or REFERENCE_QUANT) - REFERENCE_QUANT))


# ------------------------------------------------------------------ intent routing

CODE_CMDS = {"fix", "explain", "review", "refactor", "test", "doc", "bigo", "regex", "sql", "shell", "convert", "commit", "newfile", "edit", "run"}
REASON_CMDS = {"proscons", "plan", "think", "brainstorm"}
CHAT_CMDS = {"summarize", "tldr", "rewrite", "proofread", "translate", "email", "outline", "quiz", "eli5", "name", "steps", "table"}

CODE_RX = re.compile(
    r"```|\b(?:def|class|function|import|const|var|public static|#include|fn main|console\.log|traceback|stack ?trace|segfault|"
    r"compile[rd]?|syntax|runtime error|exception|null pointer|undefined|npm|pip|cargo|git|docker|regex|sql|query|api|endpoint|json|"
    r"yaml|html|css|react|python|javascript|typescript|rust|golang|java|c\+\+|c#|bash|powershell|unit tests?|refactor\w*|debug\w*|"
    r"bug|bugs|implement\w*|script|snippet|variable|array|loop|recursion|repo|commit|function|method|class|server|frontend|backend)\b"
    r"|\.(?:py|js|ts|tsx|jsx|java|cpp|hpp|rs|go|rb|php|cs|html|css|json|ya?ml|toml|sh|bat|sql|md)\b|(?:^|\s)@[\w./-]+\.\w+", re.I)
REASON_RX = re.compile(
    r"\b(?:solve|calculate|compute|prove|proof|derive|probability|how many|how much|riddle|puzzle|logic\w*|step[- ]by[- ]step|think (?:it )?(?:through|carefully)|"
    r"equation|integral|derivative|theorem|trade-?offs?|pros and cons|analy[sz]e|analysis|compare|why (?:does|do|is|are|would|did)|explain why|"
    r"reason(?:ing)?|deduce|infer|estimate|optimi[sz]e|evaluate|which is (?:better|best)|should i)\b|\d\s*[-+*/^=×÷]\s*\d", re.I)
CHAT_RX = re.compile(r"\b(?:write|story|poem|essay|email|letter|translate|summari[sz]e|rewrite|proofread|paraphrase|brainstorm|ideas?|name|joke|chat|hello|hi|thanks|recommend)\b", re.I)


def classify(text, prev=""):
    """Guess what the user wants: 'code', 'reason' or 'chat'. Cheap keyword scoring; short follow-ups keep the previous intent."""
    t = (text or "").strip()
    cmd = re.match(r"^/(\w+)", t)
    if cmd:
        c = cmd.group(1).lower()
        if c in CODE_CMDS:
            return {"intent": "code", "why": f"/{c} command", "scores": {"code": 9, "reason": 0, "chat": 0}}
        if c in REASON_CMDS:
            return {"intent": "reason", "why": f"/{c} command", "scores": {"code": 0, "reason": 9, "chat": 0}}
        if c in CHAT_CMDS:
            return {"intent": "chat", "why": f"/{c} command", "scores": {"code": 0, "reason": 0, "chat": 9}}
    body = re.sub(r"https?://\S+", " ", t)
    code = len({m.group(0).lower() for m in CODE_RX.finditer(body)})
    if "```" in body:
        code += 4
    reason = len({m.group(0).lower() for m in REASON_RX.finditer(body)})
    chat = len({m.group(0).lower() for m in CHAT_RX.finditer(body)})
    words = len(body.split())
    if words >= 60 and not code:
        reason += 1
    scores = {"code": code, "reason": reason, "chat": chat}
    best = max(scores, key=lambda k: (scores[k], k == "code", k == "reason"))
    if scores[best] == 0 or (words <= 6 and scores[best] < 2):
        if prev in scores and words <= 8:
            return {"intent": prev, "why": "short follow-up, keeping the last intent", "scores": scores}
        return {"intent": "chat", "why": "general conversation", "scores": scores}
    if best == "chat" or (best == "reason" and code >= reason):
        best = "code" if code >= max(reason, 1) and code >= chat else best
    return {"intent": best, "why": f"{scores[best]} {best} signal{'s' if scores[best] != 1 else ''}", "scores": scores}


CODE_NAME_RX = re.compile(r"coder|code|codestral|starcoder|devstral|codellama|granite-code|opencoder", re.I)
CHAT_NAME_RX = re.compile(r"instruct|[-_]it\b|chat|assistant|hermes|mistral|llama|gemma|qwen|phi|glm", re.I)
REASON_NAME_RX = re.compile(r"r1|reason|think|qwq|qwen3|magistral|o1|gemma-4|phi-?4-?reason", re.I)
NOT_CHAT_RX = re.compile(r"mmproj|\bvl\b|[-_]vl[-_.]|vision|llava|embed|rerank|bge|nomic|whisper|clip", re.I)


def chat_models(models):
    return [m for m in models if not NOT_CHAT_RX.search(m["name"]) and not m.get("mmproj") and not m.get("image")]


def name_score(intent, name):
    s = 0
    if intent == "code":
        s += 3 if CODE_NAME_RX.search(name) else 0
    elif intent == "reason":
        s += 3 if REASON_NAME_RX.search(name) else 0
    else:
        s += 2 if CHAT_NAME_RX.search(name) else 0
        s -= 3 if CODE_NAME_RX.search(name) else 0
    return s


def pick_for_intent(intent, models, current_path="", chosen="", prefer_quant="", ram_free=0):
    """Which model file should answer this intent? Returns a path, or '' to keep what is loaded.

    chosen: the file the user assigned to this intent (always wins); '' = pick automatically from the file names.
    Automatic picks only switch when another family is clearly better suited than the current one."""
    cands = chat_models(models)
    by_path = {str(Path(m["path"])).lower(): m for m in cands}
    if chosen:
        m = by_path.get(str(Path(chosen)).lower())
        return m["path"] if m and str(Path(m["path"])).lower() != str(Path(current_path)).lower() else ""
    if not cands:
        return ""
    cur = by_path.get(str(Path(current_path)).lower()) if current_path else None
    cur_score = name_score(intent, cur["name"]) if cur else -99
    fams = group_models(cands)
    scored = [(max(name_score(intent, v["name"]) for v in f["variants"][:1]), f) for f in fams]
    top, fam = max(scored, key=lambda x: (x[0], cur is not None and any(v["path"] == cur["path"] for v in x[1]["variants"])))
    if top < 3 and intent != "chat":
        return ""                         # nothing in the folder is clearly built for this: keep what is loaded
    if top <= cur_score:
        return ""
    if cur and any(v["path"] == cur["path"] for v in fam["variants"]):
        return ""
    v = best_variant(fam["variants"], prefer_quant or (cur or {}).get("quant", ""), ram_free)
    return v["path"] if v and (not cur or v["path"] != cur["path"]) else ""


# ------------------------------------------------------------------ benchmarks

def _mc(q, choices, ans):
    return {"q": q, "choices": choices, "a": ans}


SUITES = {
    "knowledge": {"title": "Knowledge (MMLU-style)", "kind": "mc", "items": [
        _mc("Which organelle is the main site of ATP production in eukaryotic cells?", ["Ribosome", "Mitochondrion", "Golgi apparatus", "Lysosome"], "B"),
        _mc("What is the derivative of sin(x) with respect to x?", ["cos(x)", "-cos(x)", "-sin(x)", "tan(x)"], "A"),
        _mc("Which planet has the shortest orbital period around the Sun?", ["Venus", "Earth", "Mercury", "Mars"], "C"),
        _mc("In economics, what does inflation describe?", ["A general rise in price levels over time", "A fall in unemployment", "A rise in a currency's exchange rate", "A decrease in government spending"], "A"),
        _mc("Which data structure follows last-in, first-out order?", ["Queue", "Stack", "Binary tree", "Hash table"], "B"),
        _mc("Who wrote the novel 'Pride and Prejudice'?", ["Charlotte Bronte", "Jane Austen", "George Eliot", "Mary Shelley"], "B"),
        _mc("What is the chemical symbol for sodium?", ["S", "So", "Na", "Sd"], "C"),
        _mc("The Treaty of Versailles (1919) formally ended which conflict?", ["World War II", "The Franco-Prussian War", "World War I", "The Crimean War"], "C"),
        _mc("Which gas makes up most of Earth's atmosphere?", ["Oxygen", "Carbon dioxide", "Argon", "Nitrogen"], "D"),
        _mc("Assuming the null hypothesis is true, what does a p-value of 0.03 mean?", ["The null hypothesis has a 3% chance of being true", "Data at least this extreme would occur about 3% of the time", "The effect size is 3%", "The test is 97% accurate"], "B"),
        _mc("What is the time complexity of binary search on a sorted array of n elements?", ["O(n)", "O(n log n)", "O(log n)", "O(1)"], "C"),
        _mc("Which of these numbers is prime?", ["51", "57", "87", "89"], "D"),
    ]},
    "reasoning": {"title": "Reasoning (logic / ARC-style)", "kind": "mc", "items": [
        _mc("All bloops are razzies. All razzies are lazzies. Which statement must be true?", ["All lazzies are bloops", "All bloops are lazzies", "Some lazzies are not razzies", "No razzies are bloops"], "B"),
        _mc("If it rains, the ground gets wet. The ground is not wet. What follows?", ["It did not rain", "It rained", "The ground is dry because it rained", "Nothing follows"], "A"),
        _mc("A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?", ["$0.10", "$0.05", "$0.15", "$0.01"], "B"),
        _mc("Sam is taller than Lee. Lee is taller than Pat. Kim is shorter than Pat. Who is the second tallest?", ["Sam", "Lee", "Pat", "Kim"], "B"),
        _mc("Which of these is a chemical change?", ["Ice melting", "Paper burning", "Sugar dissolving in water", "Water evaporating"], "B"),
        _mc("What comes next in the series 2, 6, 12, 20, 30, ...?", ["36", "40", "42", "44"], "C"),
        _mc("A farmer has 17 sheep. All but 9 die. How many sheep are left?", ["8", "9", "17", "0"], "B"),
        _mc("What is the contrapositive of: 'If a number is divisible by 4, then it is even'?", ["If a number is even, then it is divisible by 4", "If a number is not divisible by 4, then it is not even", "If a number is not even, then it is not divisible by 4", "If a number is odd, then it is divisible by 4"], "C"),
    ]},
    "math": {"title": "Math (GSM8K-style)", "kind": "num", "items": [
        {"q": "Maria buys 12 pencils at $0.25 each and pays with a $5 bill. How many dollars of change does she get?", "a": 2},
        {"q": "A train travels at 80 km/h for 2.5 hours, then at 60 km/h for 1.5 hours. How many kilometres does it travel in total?", "a": 290},
        {"q": "Tom has 3 times as many marbles as Ben. Together they have 48 marbles. How many marbles does Tom have?", "a": 36},
        {"q": "A recipe needs 3/4 cup of sugar for 12 cookies. How many cups of sugar are needed for 36 cookies?", "a": 2.25},
        {"q": "A rectangle's length is 3 more than twice its width. Its perimeter is 66 cm. What is its length in cm?", "a": 23},
        {"q": "What is 15% of 240 plus 25% of 80?", "a": 56},
        {"q": "A tank holds 120 liters. A pipe fills it at 8 liters per minute while a drain removes 3 liters per minute at the same time. Starting empty, how many minutes until it is full?", "a": 24},
        {"q": "The sum of three consecutive integers is 108. What is the largest of them?", "a": 37},
    ]},
    "code": {"title": "Code reading (predict the output)", "kind": "mc", "items": [
        _mc("What does this Python code print?\n\nprint(sum(i * i for i in range(4)))", ["6", "14", "30", "9"], "B"),
        _mc("What does this Python code print?\n\nx = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))", ["3", "7", "4", "It raises an error"], "C"),
        _mc("What does this Python code print?\n\nprint('abc'[::-1])", ["abc", "cba", "acb", "bca"], "B"),
        _mc("Which Python operation has O(1) average-case lookup by key?", ["Searching a list with `in`", "Searching a sorted list linearly", "Looking up a key in a dict", "Scanning a tuple"], "C"),
        _mc("What does this Python code print?\n\nprint(7 // 2, 7 % 2)", ["3.5 1", "3 1", "4 1", "3 3"], "B"),
    ]},
    "instruct": {"title": "Instruction following (IFEval-style)", "kind": "ifeval", "items": [
        {"q": "Reply with the single word ready in all lowercase letters and nothing else.", "check": "ready"},
        {"q": "List exactly 4 fruits as a bulleted list. Start every line with '- ' and write nothing else.", "check": "bullets4"},
        {"q": "Write a poem about rain in exactly 3 lines. Do not use any commas.", "check": "lines3_nocomma"},
        {"q": "Answer in ALL CAPITAL LETTERS only: what is the capital of France?", "check": "caps_paris"},
        {"q": "Describe a cat in at most 40 words and include the word 'velvet' at least once.", "check": "velvet40"},
        {"q": "Respond with valid JSON only (no code fences, no commentary): an object with the keys \"name\" (a string) and \"age\" (an integer) for a fictional person.", "check": "json_name_age"},
    ]},
}
SPEED_ONLY = "speed"
THINK_RX = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_think(text):
    """Drop a reasoning model's <think> block (also an unterminated one, which means it was cut off)."""
    t = THINK_RX.sub("", text or "")
    if "<think>" in t.lower():
        t = t[:t.lower().index("<think>")]
    return t.strip()


def mc_prompt(item):
    letters = "ABCDEFGH"
    opts = "\n".join(f"{letters[i]}. {c}" for i, c in enumerate(item["choices"]))
    return ("Answer the multiple-choice question. Reply with only the letter of the correct option.\n\n"
            f"Question: {item['q']}\n{opts}\n\nAnswer:")


def parse_letter(text, n=4):
    t = strip_think(text)
    last = "ABCDEFGH"[:n]
    m = re.match(rf"^\W*\(?([{last}])\b", t) or re.search(rf"answer\s*(?:is|:)?\s*\W*\(?([{last}])\b", t, re.I) or None
    if m:
        return m.group(1).upper()
    ms = re.findall(rf"\b([{last}])\b", t)
    return ms[-1] if ms else ""


def num_prompt(item):
    return ("Solve the problem. Keep the working short. Finish with a final line in exactly this form: 'Answer: <number>' "
            f"(digits only, no units).\n\nProblem: {item['q']}")


NUM_RX = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")


def parse_number(text):
    t = strip_think(text)
    m = re.findall(r"answer\s*(?:is|:)?\s*\**\s*\$?\s*(-?\d[\d,]*\.?\d*)", t, re.I)
    cand = m[-1] if m else (NUM_RX.findall(t) or [""])[-1]
    try:
        return float(cand.replace(",", "").rstrip("."))
    except ValueError:
        return None


def _lines(t):
    return [l for l in t.strip().splitlines() if l.strip()]


def _json_ok(t):
    t = t.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        o = json.loads(t)
    except ValueError:
        return False
    return isinstance(o, dict) and isinstance(o.get("name"), str) and isinstance(o.get("age"), int) and not isinstance(o.get("age"), bool)


IF_CHECKS = {
    "ready": lambda t: t.strip().strip("'\".") == "ready",
    "bullets4": lambda t: len(_lines(t)) == 4 and all(l.lstrip().startswith("- ") for l in _lines(t)),
    "lines3_nocomma": lambda t: len(_lines(t)) == 3 and "," not in t,
    "caps_paris": lambda t: "PARIS" in t and t == t.upper(),
    "velvet40": lambda t: "velvet" in t.lower() and len(t.split()) <= 40,
    "json_name_age": _json_ok,
}


def score_item(kind, item, output):
    """True / False for one benchmark answer."""
    if kind == "mc":
        return parse_letter(output, len(item["choices"])) == item["a"]
    if kind == "num":
        v = parse_number(output)
        return v is not None and abs(v - float(item["a"])) < 0.01
    if kind == "ifeval":
        return bool(IF_CHECKS[item["check"]](strip_think(output)))
    if kind == "text":
        norm = lambda s: re.sub(r"\W+", " ", str(s).lower()).strip()
        return norm(item["a"]) in norm(strip_think(output))
    return False


def build_messages(kind, item):
    if kind == "mc":
        return [{"role": "user", "content": mc_prompt(item)}]
    if kind == "num":
        return [{"role": "user", "content": num_prompt(item)}]
    if kind == "text":
        return [{"role": "user", "content": item["q"] + "\n\nGive a short, direct answer."}]
    return [{"role": "user", "content": item["q"]}]


def max_tokens_for(kind, thinking):
    base = {"mc": 12, "num": 400, "ifeval": 200, "text": 200}.get(kind, 200)
    return base + (1400 if thinking else 0) if kind != "mc" else (900 if thinking else base)


def load_custom(bench_dir, limit=50):
    """Extra suites from JSONL files in bench_dir. One JSON object per line:
         multiple choice  {"question": "...", "choices": ["a", "b", "c", "d"], "answer": "B"}   (answer: letter or 0-based index)
         number           {"question": "...", "answer": 42}
         short text       {"question": "...", "answer": "Paris"}      (correct if the reply contains it)
       Returns {name: suite}; files that cannot be read are reported under the key '_errors'."""
    out, errs = {}, []
    d = Path(bench_dir) if bench_dir else None
    if not d or not d.is_dir():
        return out, errs
    for f in sorted(d.glob("*.jsonl"))[:20]:
        items, kind, bad = [], None, 0
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if len(items) >= limit:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        bad += 1
                        continue
                    if not isinstance(o, dict):
                        bad += 1
                        continue
                    q = o.get("question") or o.get("prompt") or o.get("q")
                    a = o.get("answer", o.get("a"))
                    if not q or a is None:
                        continue
                    ch = o.get("choices")
                    if isinstance(ch, dict):
                        ch = [ch[k] for k in sorted(ch)]
                    if isinstance(ch, list) and 2 <= len(ch) <= 8:
                        if isinstance(a, int):
                            a = "ABCDEFGH"[a]
                        items.append({"q": str(q), "choices": [str(c) for c in ch], "a": str(a).strip().upper()[:1]})
                        kind = kind or "mc"
                    elif isinstance(a, (int, float)) or re.fullmatch(r"-?[\d,.]+", str(a).strip()):
                        items.append({"q": str(q), "a": float(str(a).replace(",", ""))})
                        kind = kind or "num"
                    else:
                        items.append({"q": str(q), "a": str(a)})
                        kind = kind or "text"
        except (OSError, ValueError) as e:
            errs.append(f"{f.name}: {e}")
            continue
        if bad:
            errs.append(f"{f.name}: skipped {bad} unreadable line{'s' if bad != 1 else ''}")
        if items and kind:
            items = [i for i in items if (kind == "mc") == ("choices" in i)] if kind == "mc" else [i for i in items if "choices" not in i]
            if items:
                out[f.stem] = {"title": f.stem + " (your file)", "kind": kind, "items": items, "custom": True}
    return out, errs


def overall(suites):
    """Mean accuracy (%) across the quality suites that ran, each suite weighted equally."""
    rates = [s["correct"] / s["total"] * 100 for s in suites.values() if s.get("total")]
    return round(sum(rates) / len(rates), 1) if rates else None


# subprocess body used to create a new quantization with llama.cpp's own quantizer (see server.py)
QUANTIZE_SCRIPT = r"""
import sys, llama_cpp
src, dst, name, requant = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
ft = getattr(llama_cpp, "LLAMA_FTYPE_MOSTLY_" + name, None)
if ft is None:
    sys.exit("this llama-cpp-python build does not know the " + name + " format")
p = llama_cpp.llama_model_quantize_default_params()
p.ftype = ft
p.allow_requantize = requant
p.nthread = 0
rc = llama_cpp.llama_model_quantize(src.encode("utf-8"), dst.encode("utf-8"), p)
sys.exit(0 if rc == 0 else "llama.cpp quantizer returned error code %s" % rc)
"""
QUANTIZE_TARGETS = ["Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S", "Q3_K_M", "Q3_K_S", "Q2_K"]
LOSSLESS_SOURCES = {"F32", "F16", "BF16"}


def quantize_plan(src_name, target):
    """(ok, why, out_file_name, needs_requantize) for turning src into `target`."""
    target = str(target).upper()
    if target not in QUANTIZE_TARGETS:
        return False, "Unsupported target format. Choose one of " + ", ".join(QUANTIZE_TARGETS) + ".", "", False
    q, m = quant_of(src_name)
    if not m:
        return False, "Cannot tell which quantization this file uses (it is not in the file name).", "", False
    if q == target:
        return False, "That file already is " + target + ".", "", False
    src_bpw, dst_bpw = QUANTS.get(q, (0, ""))[0], QUANTS[target][0]
    if src_bpw and src_bpw <= dst_bpw:
        return False, f"{q} is already smaller than {target}; quantizing upward cannot add quality. Start from a larger file.", "", False
    stem = Path(src_name).stem
    out = stem[:m.start()] + target + stem[m.end():] + ".gguf"
    return True, "", out, q not in LOSSLESS_SOURCES


# ------------------------------------------------------------------ self-test
if __name__ == "__main__":
    assert quant_of("gemma-4-12b-it-Q4_K_M.gguf")[0] == "Q4_K_M"
    assert quant_of("Qwen3-8B-Q6_K.gguf")[0] == "Q6_K"
    assert quant_of("Qwen3-8B-UD-Q4_K_XL.gguf")[0] == "Q4_K_XL"
    assert quant_of("llama-3.2-3b.F16.gguf")[0] == "F16"
    assert quant_of("model-IQ3_XXS.gguf")[0] == "IQ3_XXS"
    assert quant_of("Qwen3-8B.gguf")[0] == ""
    assert family_of("gemma-4-12b-it-Q4_K_M.gguf") == "gemma-4-12b-it"
    assert family_of("Qwen3-8B-Q6_K-00001-of-00002.gguf") == "Qwen3-8B"
    ms = [{"name": n, "path": "/m/" + n, "size": s} for n, s in [("a-Q8_0.gguf", 8), ("a-Q4_K_M.gguf", 5), ("a-Q2_K.gguf", 3), ("b-Q4_K_M.gguf", 4)]]
    g = group_models(ms)
    assert [v["quant"] for v in g[0]["variants"]] == ["Q2_K", "Q4_K_M", "Q8_0"] and len(g) == 2
    assert best_variant(g[0]["variants"])["quant"] == "Q4_K_M"
    assert best_variant(g[0]["variants"], ram_free=6)["quant"] == "Q4_K_M" and best_variant(g[0]["variants"], ram_free=3.5)["quant"] == "Q2_K"
    assert best_variant(g[0]["variants"], current_quant="Q8_0")["quant"] == "Q8_0"
    assert classify("fix this bug in app.py")["intent"] == "code"
    assert classify("```py\nprint(1)\n```")["intent"] == "code"
    assert classify("Solve: if 3x + 5 = 20, what is x? Think step by step")["intent"] == "reason"
    assert classify("write me a short poem about autumn")["intent"] == "chat"
    assert classify("hello there")["intent"] == "chat"
    assert classify("and in rust?", prev="code")["intent"] == "code"
    assert classify("/regex match emails")["intent"] == "code" and classify("/translate hola")["intent"] == "chat"
    models = [{"name": n, "path": "/m/" + n, "size": 5} for n in ["Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", "gemma-3-12b-it-Q4_K_M.gguf", "Qwen2.5-VL-7B-Q4_K_M.gguf", "mmproj-f16.gguf"]]
    assert pick_for_intent("code", models, "/m/gemma-3-12b-it-Q4_K_M.gguf").endswith("Coder-7B-Instruct-Q4_K_M.gguf")
    assert pick_for_intent("chat", models, "/m/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf").endswith("gemma-3-12b-it-Q4_K_M.gguf")
    assert pick_for_intent("code", models, "/m/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf") == ""
    assert pick_for_intent("chat", models, "/m/gemma-3-12b-it-Q4_K_M.gguf", chosen="/m/gemma-3-12b-it-Q4_K_M.gguf") == ""
    assert pick_for_intent("chat", models, "/m/gemma-3-12b-it-Q4_K_M.gguf", chosen="/m/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf").endswith("Q4_K_M.gguf")
    assert all(m["name"] != "Qwen2.5-VL-7B-Q4_K_M.gguf" for m in chat_models(models))
    # every built-in item scores correct on its own answer key
    for name, s in SUITES.items():
        for it in s["items"]:
            if s["kind"] == "mc":
                assert score_item("mc", it, f"{it['a']}. whatever"), (name, it["q"])
                assert not score_item("mc", it, "Z")
            elif s["kind"] == "num":
                assert score_item("num", it, f"2+2=4\nAnswer: {it['a']}"), (name, it["q"])
    assert parse_letter("<think>maybe A</think>\nThe answer is C") == "C" and parse_letter("B") == "B" and parse_letter("(D) Nitrogen") == "D"
    assert parse_number("so 1,250 total.\nAnswer: 1,250") == 1250 and parse_number("it is 3.5") == 3.5 and parse_number("no digits") is None
    assert IF_CHECKS["ready"]("Ready") is False and IF_CHECKS["ready"]("ready.") and IF_CHECKS["bullets4"]("- a\n- b\n- c\n- d")
    assert IF_CHECKS["lines3_nocomma"]("a\nb\nc") and not IF_CHECKS["lines3_nocomma"]("a, b\nc\nd")
    assert IF_CHECKS["json_name_age"]('```json\n{"name": "Ann", "age": 31}\n```') and not IF_CHECKS["json_name_age"]('{"name": "Ann", "age": "31"}')
    assert IF_CHECKS["caps_paris"]("PARIS.") and not IF_CHECKS["caps_paris"]("Paris")
    assert strip_think("<think>blah</think> B") == "B" and strip_think("<think>unfinished") == ""
    assert quantize_plan("m-F16.gguf", "Q4_K_M") == (True, "", "m-Q4_K_M.gguf", False)
    assert quantize_plan("m-Q8_0.gguf", "Q4_K_M")[3] is True and not quantize_plan("m-Q4_K_M.gguf", "Q8_0")[0] and not quantize_plan("m.gguf", "Q4_K_M")[0]
    assert overall({"a": {"correct": 3, "total": 4}, "b": {"correct": 1, "total": 2}}) == 62.5
    print("modelmgr self-test passed:", sum(len(s["items"]) for s in SUITES.values()), "built-in benchmark items")
