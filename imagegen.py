#!/usr/bin/env python3
"""Image worker for CressyAI. server.py starts one of these per request:  python imagegen.py job.json

It runs in its own process on purpose: when it exits, every byte of RAM/VRAM the Stable Diffusion model used goes
back to the system, and it cannot clash with llama.cpp's libraries inside the server process.
Progress is reported as lines starting with the marker below on stdout; everything else is native library logging."""
import gc, inspect, json, math, os, random, sys, time
from pathlib import Path

MARK = "@@CRESSY@@ "


def emit(**kw):
    sys.stdout.write("\n" + MARK + json.dumps(kw) + "\n")
    sys.stdout.flush()


def accepted(fn, kw):
    """Keep only the keyword arguments this version of stable-diffusion-cpp-python understands."""
    try:
        names = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return kw
    return {k: v for k, v in kw.items() if k in names}


def snap(v, lo=256, hi=2048, step=64):
    return int(min(hi, max(lo, round(v / step) * step)))


def fit_size(w, h, area):
    """Scale (w, h) to about `area` pixels, keep the aspect ratio, round to multiples of 64."""
    k = math.sqrt(area / max(1, w * h))
    return snap(w * k), snap(h * k)


def inspect_gguf(path):
    """Read just the header of a .gguf: which kinds of tensors it holds. Tells a full SDXL checkpoint from a UNet-only file."""
    import struct
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    with open(path, "rb") as f:
        rd = lambda n: f.read(n)
        if rd(4) != b"GGUF":
            return None
        ver, = struct.unpack("<I", rd(4))
        nt, nk = struct.unpack("<QQ", rd(16))
        s_ = lambda: rd(struct.unpack("<Q", rd(8))[0])

        def val(t):
            if t in sizes:
                rd(sizes[t])
            elif t == 8:
                s_()
            elif t == 9:
                et, n = struct.unpack("<IQ", rd(12))
                for _ in range(n):
                    val(et)
        arch = ""
        for _ in range(nk):
            k = s_().decode("utf-8", "replace")
            t, = struct.unpack("<I", rd(4))
            if t == 8 and k == "general.architecture":
                arch = s_().decode("utf-8", "replace")
            else:
                val(t)
        names = []
        for _ in range(nt):
            names.append(s_().decode("utf-8", "replace"))
            nd, = struct.unpack("<I", rd(4))
            rd(8 * nd + 4 + 8)
    has = lambda *p_: any(n.startswith(p_) or any(x in n for x in p_) for n in names)
    pre = {}
    for n in names:
        k = ".".join(n.split(".")[:2])
        pre[k] = pre.get(k, 0) + 1
    return dict(arch=arch, tensors=len(names), prefixes=sorted(pre.items(), key=lambda kv: -kv[1])[:6], unet=has("model.diffusion_model", "diffusion_model."),
                clip=has("conditioner.embedders", "cond_stage_model", "text_encoders", "text_model"),
                vae=has("first_stage_model", "vae."))


def describe_gguf(path):
    try:
        g = inspect_gguf(path)
    except Exception:
        return ""
    if not g:
        return " The file is not a valid .gguf (bad header): it may be a failed or partial download."
    miss = [n for n, ok in (("UNet", g["unet"]), ("CLIP text encoders", g["clip"]), ("VAE", g["vae"])) if not ok]
    if miss:
        return (f" This file ({g['tensors']} tensors, architecture '{g['arch'] or '?'}') has no {' / '.join(miss)} in it, so it is not "
                "a complete checkpoint. Use a full SDXL .gguf, or put the text encoders next to it (clip_l / clip_g files) and VAE "
                "in the models folder, or pass --image-clip-l / --image-clip-g / --image-vae. Tensor groups: "
                + ", ".join(f"{k} x{v}" for k, v in g["prefixes"]) + ".")
    return (f" The file has UNet, text encoders and VAE ({g['tensors']} tensors, architecture '{g['arch'] or '?'}'), but "
            "stable-diffusion.cpp still could not identify it: it may use tensor names this build of the library does not know. "
            "Tensor groups: " + ", ".join(f"{k} x{v}" for k, v in g["prefixes"]) + ".")


def loaded_ok(sd):
    """False when the library handed back an object whose native context is NULL (the model did not load)."""
    o = sd
    for _ in range(3):
        if not hasattr(o, "model"):
            return True
        o = o.model
        if o is None:
            return False
        try:
            if not bool(o):
                return False
        except Exception:
            return True
    return True


def hook_progress(sd, cb):
    """Best effort: different releases of the bindings expose the progress callback differently."""
    try:
        import stable_diffusion_cpp as mod
    except Exception:
        return False
    for owner in (sd, mod):
        f = getattr(owner, "set_progress_callback", None)
        if callable(f):
            try:
                f(cb)
                return True
            except Exception:
                pass
    try:
        from stable_diffusion_cpp import stable_diffusion_cpp as lib
        T, f = getattr(lib, "sd_progress_cb_t"), getattr(lib, "sd_set_progress_callback")
        hook_progress.keep = T(cb)    # must stay alive for the whole run
        f(hook_progress.keep, None)
        return True
    except Exception:
        return False


def main():
    job = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    edit = job["mode"] == "edit"

    try:
        from PIL import Image, PngImagePlugin
    except ImportError:
        emit(type="error", message="Pillow is missing. Run:  pip install -r requirements-image.txt")
        return
    try:
        from stable_diffusion_cpp import StableDiffusion
    except ImportError as e:
        emit(type="error", message="stable-diffusion-cpp-python is not installed (" + str(e) + "). "
             "Run:  pip install -r requirements-image.txt   (see the README for the CUDA / Vulkan build flags)")
        return

    width, height = snap(job["width"]), snap(job["height"])
    init = None
    if edit:
        src = Image.open(job["source"])
        src.load()
        if src.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", src.size, (255, 255, 255))
            src = src.convert("RGBA")
            bg.paste(src, mask=src.split()[-1])
            src = bg
        src = src.convert("RGB")
        width, height = fit_size(src.width, src.height, job["width"] * job["height"])
        init = src.resize((width, height), Image.LANCZOS)
        name = "src-" + time.strftime("%Y%m%d-%H%M%S") + "-" + format(random.randrange(16 ** 4), "04x") + ".png"
        init.save(out_dir / name)
        emit(type="source", name=name, width=width, height=height)

    emit(type="stage", stage="load_image", label="Loading " + os.path.basename(job["model"]) + "…")
    common = dict(n_threads=int(job.get("threads") or -1), vae_decode_only=not edit, vae_tiling=bool(job.get("vae_tiling", True)),
                  diffusion_flash_attn=bool(job.get("flash_attn")), keep_clip_on_cpu=bool(job.get("low_vram")),
                  keep_vae_on_cpu=bool(job.get("low_vram")), verbose=True)
    whole = dict(common, model_path=job["model"])
    if job.get("vae"):
        whole["vae_path"] = job["vae"]
    print("IMAGE JOB COMPONENTS:", flush=True)
    for key in ("model", "clip_l", "clip_g", "vae", "auto_vae"):
        path = job.get(key, "")
    print(f"  {key}: {path!r} | exists: {os.path.isfile(path) if path else False}", flush=True)
    parts = None
    if job.get("clip_l") and job.get("clip_g"):    # a UNet-only checkpoint: the text encoders and VAE come from separate files
        parts = dict(common, diffusion_model_path=job["model"], clip_l_path=job["clip_l"], clip_g_path=job["clip_g"])
        if job.get("vae") or job.get("auto_vae"):
            parts["vae_path"] = job.get("vae") or job.get("auto_vae")
    try:
        info = inspect_gguf(job["model"])
    except Exception:
        info = None
    complete = bool(info and info["unet"] and info["clip"] and info["vae"])
    attempts = [("parts", parts), ("whole", whole)] if (parts and info and not complete) else [("whole", whole)] + ([("parts", parts)] if parts else [])
    t0 = time.time()
    sd, dead, last = None, [], None
    for how, kw in attempts:
        try:
            cand = StableDiffusion(**accepted(StableDiffusion.__init__, kw))
        except BaseException as e:    # noqa: BLE001
            last = e
            continue
        if loaded_ok(cand):
            sd = cand
            if how == "parts":
                emit(type="note", message="Loaded the UNet with separate text encoders and VAE.")
            break
        dead.append(cand)    # never free a context that is NULL: that is what crashes the library
    if sd is None:
        hint = "" if parts else " (no clip_l / clip_g files were found next to the model)"
        raise RuntimeError("The model failed to load" + (": " + (str(last) or last.__class__.__name__)[:160] if last else "") + "." + describe_gguf(job["model"]) + hint)
    emit(type="stage", stage="paint", label="Painting…", load_seconds=round(time.time() - t0, 1))

    count = max(1, min(4, int(job["count"])))
    seed0 = int(job["seed"])
    if seed0 < 0:
        seed0 = random.randrange(2 ** 31 - 8)
    cur = {"image": 0}

    def on_progress(step, steps, t=0.0, data=None):
        emit(type="progress", image=cur["image"], count=count, step=int(step), steps=int(steps))
    hook_progress(sd, on_progress)

    sampler = job.get("sampler") or "euler_a"
    sched = job.get("scheduler") or "default"
    for i in range(count):
        cur["image"] = i
        seed = seed0 + i
        kw = dict(prompt=job["prompt"], negative_prompt=job.get("negative") or "", width=width, height=height,
                  cfg_scale=float(job["cfg"]), sample_steps=int(job["steps"]), seed=seed, batch_count=1,
                  sample_method=sampler, vae_tiling=bool(job.get("vae_tiling", True)))
        if sched != "default":
            kw["scheduler"] = sched
            kw["schedule"] = sched
        if edit:
            kw["strength"] = float(job["strength"])
        fn = getattr(sd, "generate_image", None)
        if fn is not None:
            if edit:
                kw["init_image"] = init
            args_ = accepted(fn, kw)
        else:   # older releases
            fn = sd.img_to_img if edit else sd.txt_to_img
            if edit:
                kw["image"] = init
            args_ = accepted(fn, kw)
        try:
            res = fn(**args_)
        except Exception as e:    # noqa: BLE001
            if "null pointer" in str(e).lower():
                raise RuntimeError("The model did not load (NULL pointer)." + describe_gguf(job["model"])) from e
            if not isinstance(e, (TypeError, ValueError)):
                raise
            # the sampler / scheduler names changed between releases: retry with the library defaults
            args_.pop("sample_method", None)
            args_.pop("scheduler", None)
            args_.pop("schedule", None)
            res = fn(**args_)
        img = res[0] if isinstance(res, (list, tuple)) else res
        if isinstance(img, (str, os.PathLike)):
            img = Image.open(img)
        img = img.convert("RGB")
        black = all(hi == 0 for _, hi in img.getextrema())
        name = "img-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(seed) + ".png"
        meta = PngImagePlugin.PngInfo()
        meta.add_text("parameters", f"{job['prompt']}\nNegative prompt: {job.get('negative') or ''}\n"
                      f"Steps: {job['steps']}, Sampler: {sampler}, CFG scale: {job['cfg']}, Seed: {seed}, "
                      f"Size: {img.width}x{img.height}, Model: {os.path.basename(job['model'])}"
                      + (f", Denoising strength: {job['strength']}" if edit else "") + ", App: CressyAI")
        img.save(out_dir / name, pnginfo=meta)
        emit(type="image", file=name, seed=seed, width=img.width, height=img.height, black=black, index=i)

    del sd
    gc.collect()
    emit(type="done", count=count, seed=seed0)


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:    # noqa: BLE001 - report everything to the server, then exit non-zero
        if not isinstance(e, SystemExit):
            emit(type="error", message=(str(e) or e.__class__.__name__)[:900])
            sys.stdout.flush()
            os._exit(1)    # skip interpreter cleanup: freeing a NULL model context crashes the library
        raise
