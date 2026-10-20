---
name: workflow
description: "Make 3D models with Tripo from text, a photo or several views, then retexture, rig, animate, retopologize, convert and download them. Use when the user wants a 3D model, game asset, rigged or animated character, AR file (USDZ) or a printable mesh, and the tripo plugin is enabled."
version: 0.2.0
author: Adolanium
license: MIT
metadata:
  hermes:
    tags: [tripo, 3d, text-to-3d, image-to-3d, rigging, animation, game-assets, 3d-printing]
---

# Tripo 3D

The `tripo_*` tools drive Tripo's v3 API. Every call except `tripo_rig_check`, `tripo_task`,
`tripo_download` and `tripo_balance` spends the user's Tripo credits, and Hermes shows an
approval prompt with the estimated cost before each one.

## Ground rules

1. **Agree on the job before paying.** Settle the subject, the target (game engine, web, AR,
   print, render) and the budget. Tell the user which call you will make and its cost from the
   table below, then make it. The Hermes prompt is the second gate, not the first.
2. **Submit once.** A create returns a `task_id` immediately. Keep it; every later step
   (texture, rig, convert) reuses it instead of regenerating.
3. **Wait with `tripo_task`**, not by creating again. Generation usually takes one to three
   minutes. A wait that times out leaves the task running: call `tripo_task` again.
4. **On an ambiguous failure, never resubmit blindly.** If a create times out or the network
   drops, the task may exist and be billed. Check `tripo_balance` (frozen credits) and ask.
5. **"The user did not approve" means stop.** The user declined, or the run is unattended
   (cron, `hermes chat -q`, a gateway with nobody watching) and cannot approve. Do not retry or
   look for a workaround. Say what you would run and what it costs.
6. **Download promptly** with `tripo_download`, passing `save_dir` as an absolute path in the
   user's project (output URLs expire). Report each saved path and size.

## Show the result

Tool results say how to show a model on the surface you are on; follow them.

- **Hermes Desktop:** results carry `show_in_chat`, a line like `::tripo-model{task="<task id>"}`.
  Put it alone on its own line in your reply and Desktop renders an interactive 3D viewer there:
  orbit, auto-spin, wireframe, and playback for every animation clip. Right after a create it shows
  live progress and then the model, so you can reply at once instead of waiting with
  `tripo_task`. It also shows concept images and the preview render of formats it cannot open
  (USDZ, 3MF). Add `label="..."` to name the card. The line works only alone; mid-sentence it
  stays text.
- **Telegram, Discord, Slack and other chats:** after `tripo_download`, the result lists `attach`
  lines (`MEDIA:/path/...`). Put the preview line alone in your reply to post the render, and the
  model line to send the file.
- **CLI and TUI:** give the saved paths in plain text.

## Cost table (credits, estimates; the task reports the real `credits_consumed`)

| Call | Cost |
|---|---|
| Text to 3D, v3.x / v2.5 | 10 untextured, 20 textured |
| Image or multiview to 3D, v3.x / v2.5 | 20 untextured, 30 textured |
| P1 / P2 (clean low-poly) | text 30/40, image 40/50 |
| Add-ons | detailed texture +20, extreme +30, detailed geometry +20, quad +5, smart low-poly +10, parts +20 |
| `tripo_generate_image` | 5 (GPT image models can cost more) |
| `tripo_texture` | 10 / 20 / 30 (standard / detailed / extreme), +5 with style_image |
| `tripo_rig_check` | free |
| `tripo_rig` | 25 |
| `tripo_animate` | 10 per animation (max 5 per task) |
| `tripo_retopology` | v3.0: 35 (40 quad); v1.0/v2.0: 30 |
| `tripo_convert` | 5 format only, 10 with any processing option |

New Tripo accounts come with free credits; `tripo_balance` shows what is left.

## Recipes

**Prop or object from text.** `tripo_generate_model(prompt=...)` (20) → `tripo_task` →
`tripo_download`. Write prompts with shape, material and style: "a weathered oak treasure chest
with iron bands, stylized, hand-painted" beats "a chest". Add `texture=false` for a 10-credit
shape check before paying for texture.

**From a photo.** `tripo_generate_model(image="/abs/path/photo.jpg")` (30). Local paths are
uploaded to Tripo; clean backgrounds and an unobstructed subject work best. Several photos of
the same object: pass `views={"front": ..., "back": ..., "left": ..., "right": ...}` (front is
required, plus at least one more).

**Concept first.** `tripo_generate_image(prompt=..., template="figure")` (5) → show the user the
preview (`tripo_task` returns its image URL) → on approval,
`tripo_generate_model(image="<image task_id>")`. Use `template="t_pose"` for any character
that will be rigged.

**Game-ready asset.** `model="P1-20260311"` with `face_limit` (P1: 50–20,000), or a v3.1 model
with `smart_low_poly=true` or a `face_limit` of 10k–100k. Then `tripo_convert(format="FBX")`
for Unity/Unreal, or keep GLB for Godot, three.js and the web.

**Rigged and animated character.** Generate in a T-pose (concept with `template="t_pose"`) →
`tripo_rig_check` (free; `tripo_task` returns `riggable` and `rig_type`) →
`tripo_rig(rig_type=...)` (25) → `tripo_animate(input=<rig task_id>, animations=[...])`
(10 each). Humanoids: `preset:idle`, `preset:walk`, `preset:run`, `preset:jump`, ... with rig
model v3.0 or v2.5. Rig model v1.0 (Tripo's default for bipeds) uses `preset:biped:*`
names instead (for example `preset:biped:dance_01`). Pass `spec="unity"`, `"unreal"`,
`"mixamo"` or `"vrm"` for the engine's bone naming. Use `animate_in_place=true` for engine
locomotion.

**Apple AR.** Generate, then `tripo_convert(format="USDZ")`. Add `auto_size=true` at
generation so the model is in real-world meters.

**3D print.** Generate untextured (`texture=false`) to save credits, then
`tripo_convert(format="STL" or "3MF", flatten_bottom=true)`. `scale_factor` sets the size.

**Too heavy.** `tripo_retopology(input=..., face_limit=...)` (35) for clean lower-poly
topology, with `smart_uv=true` for fresh UVs.

**Different look, same shape.** `tripo_texture(input=<task_id>, prompt="...")` or with
`image=` for a reference picture. When the source was image-generated, re-supply the image.

## Pitfalls

- `quad=true` always outputs FBX.
- `generate_parts` returns untextured parts; it cannot be combined with texture, PBR or
  smart low-poly.
- `geometry_quality="detailed"`, `quad`, `smart_low_poly`, `generate_parts` and `auto_size`
  do not work on v2.5.
- `tripo_animate` needs the `task_id` of a finished `tripo_rig` task, not the original model.
- Local paths must be absolute. Uploads are capped at 50 MB here; for larger files pass a
  public URL.
- Errors mentioning `TRIPO_API_KEY`, 401 or code 1000 mean the key is missing or wrong: the
  user adds `TRIPO_API_KEY=tsk_...` to the Hermes profile's `.env` and starts a new session.
  Never ask for the key in chat.
- Code 2010 means the account is out of credits.

## Verification

- `tripo_balance` answers with a number: the key works.
- After `tripo_download`, each reported path exists with a non-zero size.
