# Clef API notes (Phase 0)

Confirmed on 2026-10-09 from the docs listed under [Sources](#sources) and from one real call
(`runs/phase0/response.json`).

## Endpoint and auth

- `POST https://openrouter.ai/api/alpha/decisions` (OpenRouter Decisions API, alpha). It is not the chat-completions endpoint.
- Header: `Authorization: Bearer $OPENROUTER_API_KEY`, `Content-Type: application/json`.
- Model id: `cloudflare/clef` (the small variant is `cloudflare/clef-flash`).

## Request

```json
{
  "model": "cloudflare/clef",
  "state": [
    "<state text>",
    { "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,..." } }
  ],
  "questions": {
    "main_view": {
      "type": "choice",
      "instructions": "What does the main (largest) area of the screen show?",
      "criteria": { "live_camera_rocket": null, "animation_rocket": null }
    }
  },
  "provider": { "only": ["cloudflare"], "allow_fallbacks": false }
}
```

- **Image:** an `image_url` item directly in the top-level `state` array. The URL must be a base64 data URL (`image/png`, `image/jpeg` or `image/webp`); remote URLs are not fetched. A top-level `images` field is rejected with a 400 on OpenRouter (that field is the Workers AI native form).
- **Text:** plain strings in the `state` array, not `{ "type": "text" }` parts.
- **Questions:** an object keyed by question id. Types are `choice`, `noul` (true/false) and `score` (ordered). We use `choice` for every question, including yes/no, so every answer has the same shape. `criteria` maps each option id to a description or `null`.
- **Provider:** OpenRouter lists two providers for `cloudflare/clef`. We pin Cloudflare with no fallback so every frame is answered by the same deployment.

| Provider | Context | Input price |
|---|---|---|
| Cloudflare | 65,536 tokens | $0.24 / M |
| PrimeIntellect | 16,384 tokens | $0.042 / M |

## Limits

- At most 4 images per request; 1 to 64 questions; 2 to 255 options per choice question.
- Question ids: letters, digits, `_`, `.`, `-`, up to 100 characters.
- **Image size:** Workers AI estimates tokens as base64 length / 4 before it reads the image and returns 413 once the estimate passes 65,536. That works out to about 190 KB per image. `clef_client.py` re-encodes to JPEG under `image.max_bytes` (150,000) in `config.yaml`.
- **State text:** only roughly the first 2,000 tokens of text are read; the rest is dropped without an error. Our state is about 440 characters.

## Response

```json
{
  "id": "gen-dec-...",
  "model": "cloudflare/clef",
  "provider": "Cloudflare",
  "answers": {
    "main_view": {
      "type": "choice",
      "choice": "animation_rocket",
      "probabilities": { "live_camera_rocket": 0.0238, "animation_rocket": 0.9184 },
      "confidence": 0.8189
    }
  },
  "usage": { "input_tokens": 2995, "output_tokens": 0, "cost": 0.0007188 }
}
```

- `probabilities` covers every option and sums to 1.
- `usage.cost` is in USD. Output tokens are always 0.
- **The API's `confidence` is not the top probability.** It is lower (0.9184 → 0.8189; 0.5697 → 0.0194) and its formula is not documented. `parse_answers` follows the README and uses `max(probabilities)`; the API value stays in the raw response.

## Measured on the sample frame

- 1920×1080 PNG (1.69 MB), sent as a 126 KB JPEG at quality 90, 19 questions.
- 2,995 input tokens, $0.00072, 2.2 s.

## Schema notes

- No option or question id was rejected. Ids such as `0-10` and `1000+` work.
- PyYAML reads the schema's bare `yes` / `no` options as booleans. `load_schema` maps them back to strings, so the YAML stays verbatim from the README.

## Sources

- OpenRouter, Multimodal Decisions guide: https://openrouter.ai/docs/guides/community/multimodal-decisions
- OpenRouter, Decisions API reference: https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
- OpenRouter OpenAPI spec: https://openrouter.ai/openapi.json
- OpenRouter provider list: https://openrouter.ai/api/v1/models/cloudflare/clef/endpoints
- Cloudflare Workers AI model page: https://developers.cloudflare.com/workers-ai/models/clef/
- Hugging Face model card: https://huggingface.co/Cloudflare/clef

---

# Additions from the NFL probe

## Phase 1 check (2026-10-12)

Three Q4 frames, football schema v1 (23 questions), responses in `runs/phase1/`.

- **61-option questions are accepted.** `jets_score` and `bengals_score` (0-59 plus `not_visible`) returned a full probability table and were right on all three frames (confidence 0.75-0.88).
- **Tokens and cost:** 4,784-5,015 input tokens per frame, $0.00115-0.00120, 1.8-2.6 s. That is about $0.49 for one pass over the 408 frames.
- **Frame size sent:** the 1920x1080 PNGs (2-4 MB) are re-encoded by `encode_image` to stay under 150 KB. Busy frames do not fit at full size and are scaled down: the three test frames went out at 1920x1080, 1632x918 and 1178x663. The scorebug text is small, so the scaled-down frames are a likely cause of scorebug misreads.
