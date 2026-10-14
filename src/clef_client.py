"""Clef client: ask(image_path, state, questions) -> raw Decisions API response.

Request/response shape is documented in docs/api_notes.md.
"""
import argparse
import base64
import io
import json
import os
import time
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from PIL import Image

try:  # trust the OS certificate store (needed behind corporate TLS-inspecting proxies)
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
CROP_NOTE = ("A second image follows the frame: it is the bottom-centre scorebug area of the same frame, "
             "enlarged. Use it to read the scorebug.")


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_schema(path=None):
    """Load questions.yaml. PyYAML reads bare yes/no as booleans, so map them back."""
    path = Path(path) if path else ROOT / load_config()["schema_path"]
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    for q in schema["questions"]:
        q["options"] = [{True: "yes", False: "no"}.get(o, str(o)) for o in q["options"]]
    schema["state"] = schema["state"].strip()
    return schema


def to_clef_questions(questions):
    """Our YAML question list -> Clef's {id: {type, instructions, criteria}}."""
    return {
        q["id"]: {
            "type": "choice",
            "instructions": q["question"],
            "criteria": {opt: None for opt in q["options"]},
        }
        for q in questions
    }


def encode_image(image_path, max_bytes):
    """Return (jpeg_bytes, width, height), re-encoding until under max_bytes."""
    raw = Path(image_path).read_bytes()
    img = Image.open(io.BytesIO(raw))
    if img.format == "JPEG" and len(raw) <= max_bytes:
        return raw, img.width, img.height
    img = img.convert("RGB")
    quality = 90
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue(), img.width, img.height
        if quality > 70:
            quality -= 10
        else:
            img = img.resize((int(img.width * 0.85), int(img.height * 0.85)), Image.LANCZOS)


def crop_jpeg(image_path, box):
    """The `box` (left, top, right, bottom) region of the frame at full resolution, as JPEG bytes."""
    buf = io.BytesIO()
    Image.open(image_path).convert("RGB").crop(tuple(box)).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def ask(image_path, state, questions, model=None, provider=None, timeout=120, crop=None):
    """One Decisions call on one frame. Returns the raw response JSON plus `_meta`.

    `crop` is an optional (left, top, right, bottom) box: that part of the frame is sent as a second,
    full-resolution image and CROP_NOTE is added to the state.
    """
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    model = model or cfg["model"]["id"]
    provider = provider or cfg["model"].get("provider")
    jpeg, width, height = encode_image(image_path, cfg["image"]["max_bytes"])
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": model,
        "state": [state, {"type": "image_url", "image_url": {"url": data_url}}],
        "questions": to_clef_questions(questions),
    }
    if crop:
        crop_url = "data:image/jpeg;base64," + base64.b64encode(crop_jpeg(image_path, crop)).decode("ascii")
        body["state"] = [state + "\n\n" + CROP_NOTE, body["state"][1], {"type": "image_url", "image_url": {"url": crop_url}}]
    if provider:
        body["provider"] = {"only": [provider], "allow_fallbacks": False}

    start = time.perf_counter()
    resp = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        json=body,
        timeout=timeout,
    )
    latency = time.perf_counter() - start
    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"{resp.status_code}: {resp.text[:2000]}", request=resp.request, response=resp
        )
    out = resp.json()
    out["_meta"] = {
        "latency_s": round(latency, 3),
        "image_bytes_sent": len(jpeg),
        "image_size_sent": [width, height],
    }
    return out


def parse_answers(response):
    """Raw response -> {qid: {answer, confidence, probs}} with confidence = max prob."""
    answers = {}
    for qid, a in response["answers"].items():
        probs = a["probabilities"]
        best = max(probs, key=probs.get)
        answers[qid] = {"answer": best, "confidence": probs[best], "probs": probs}
    return answers


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Single Clef call on one image (Phase 0 check).")
    ap.add_argument("image")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    schema = load_schema()
    response = ask(args.image, schema["state"], schema["questions"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(response, indent=2), encoding="utf-8")
    print(json.dumps(response, indent=2))
