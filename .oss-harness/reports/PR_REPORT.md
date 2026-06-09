# Open Source Contribution Report — @Mr-Neutr0n (Hari)

_Snapshot: 2026-06-01 UTC_  ·  _Source: GitHub Search API + GraphQL_

## 1. The rating — one-line scorecard

| Dimension | Verdict |
|---|---|
| Volume | **373 PRs** across **233 external repos** in a single ~5-day burst (Feb 11–15 2026). |
| Merge rate | **47%** historical (73 merged / 82 closed-not-merged / 155 decided). |
| Open backlog | **218 open, 0 drafted.** 203 untouched for 90+ days. |
| Maintainer engagement | **109 / 218 open PRs have zero comments and zero reviews.** |
| Conflicts | **7 PRs are CONFLICTING** and need a rebase to even evaluate. |
| Wasted effort | **3 PRs in archived repos** (fairseq, Merlion x2). |
| Positive signal | 2 PRs **APPROVED**, 18 PRs have **open review threads** from maintainers, historical merge rate is real. |

**Overall rating: C+ / B-.** Volume is impressive, but the pattern reads as automated/bulk to maintainers. Half the open PRs will likely be auto-closed (stale bot) if you don't act. Real signal underneath — 73 merged and 18-with-thread — but it's drowned out by volume + staleness.

## 2. What the data says about you

- All 218 open PRs and 155 of 166 closed PRs were created in a **5-day window: Feb 11–15, 2026.** That is the signature of an automated/scripted contribution agent, not a human contributing incrementally. Maintainers notice this.
- **Top repos by open PR count** (targets you may have swept systematically):

  - `huggingface/diffusers` — 9 open PRs
  - `microsoft/agent-lightning` — 7 open PRs
  - `public-apis/public-apis` — 7 open PRs
  - `OpenGVLab/InternVL` — 5 open PRs
  - `deepseek-ai/Janus` — 5 open PRs
  - `Stability-AI/generative-models` — 5 open PRs
  - `facebookresearch/detectron2` — 3 open PRs
  - `WongKinYiu/yolov7` — 3 open PRs
  - `hpcaitech/ColossalAI` — 3 open PRs
  - `anthropics/skills` — 3 open PRs

- **Largest stars on repos you touched** (high-value neighborhoods, deduped):

  - `public-apis/public-apis` — 438,371 stars
  - `huggingface/transformers` — 161,151 stars
  - `anthropics/skills` — 145,028 stars
  - `langgenius/dify` — 143,399 stars
  - `Comfy-Org/ComfyUI` — 115,252 stars
  - `openai/whisper` — 101,133 stars
  - `vllm-project/vllm` — 81,577 stars
  - `PaddlePaddle/PaddleOCR` — 79,197 stars
  - `CompVis/stable-diffusion` — 73,064 stars
  - `meta-llama/llama` — 59,435 stars
  - `karpathy/nanoGPT` — 59,068 stars
  - `microsoft/autogen` — 58,602 stars

- The uniformity of titles ("fix mutable default argument", "Fix <X> deprecation") and the 5-day burst pattern strongly suggest an LLM/agent generated the bulk. Maintainers sometimes filter that pattern.

## 3. Tier breakdown of open PRs

| Tier | Count | Meaning |
|---|---:|---|
| S | 0 | Approved + recent, ready to land |
| A | 27 | Approved but stale, or mergeable-able right now |
| B+ | 0 | Active conversation, likely to land with effort |
| B | 155 | Engagement possible, mostly needs a polite nudge |
| B- | 26 | CI/status unknown, needs check |
| C | 7 | Conflicts or changes requested — needs code work first |
| D | 0 | Draft |
| F | 3 | Archived repo, withdraw |

## 4. Priority 1 — Tier A (act on first, winnable)

These are either approved or mergeable + recent. Most valuable first.

| Repo | Stars | Stale | Why |
|---|---:|---:|---|
| huggingface/transformers#43775 | 161,151 | 115d | ready to nudge |
| anthropics/skills#363 | 145,028 | 108d | ready to nudge |
| anthropics/skills#362 | 145,028 | 108d | ready to nudge |
| anthropics/skills#361 | 145,028 | 108d | ready to nudge |
| PaddlePaddle/PaddleOCR#17685 | 79,197 | 90d | ready to nudge |
| microsoft/autogen#7211 | 58,602 | 108d | ready to nudge |
| Lightning-AI/pytorch-lightning#21539 | 31,168 | 100d | ready to nudge |
| Stability-AI/generative-models#470 | 27,173 | 108d | ready to nudge |
| usestrix/strix#204 | 25,708 | 10d | ready to nudge |
| pyg-team/pytorch_geometric#10600 | 23,787 | 13d | ready to nudge |
| onnx/onnx#7665 | 20,922 | 106d | ready to nudge |
| meta-llama/llama-cookbook#1021 | 18,341 | 108d | ready to nudge |
| Wan-Video/Wan2.1#574 | 16,144 | 111d | ready to nudge |
| OpenTalker/SadTalker#1031 | 13,863 | 111d | ready to nudge |
| google-research/vision_transformer#320 | 12,555 | 109d | ready to nudge |
| facebookresearch/seamless_communication#572 | 11,786 | 108d | ready to nudge |
| facebookresearch/hydra#3133 | 10,410 | 15d | ready to nudge |
| open-mmlab/mmsegmentation#3869 | 9,824 | 108d | ready to nudge |
| modelscope/facechain#650 | 9,496 | 109d | ready to nudge |
| arrow-py/arrow#1243 | 9,047 | 107d | ready to nudge |
| open-mmlab/mmdetection3d#3140 | 6,432 | 108d | ready to nudge |
| LLaVA-VL/LLaVA-NeXT#505 | 4,682 | 108d | ready to nudge |
| fixie-ai/ultravox#330 | 4,435 | 108d | ready to nudge |
| SysCV/sam-hq#165 | 4,227 | 111d | ready to nudge |
| determined-ai/determined#10265 | 3,225 | 105d | ready to nudge |
| nutonomy/nuscenes-devkit#1191 | 2,752 | 109d | approved but stale |
| Vchitect/LaVie#72 | 950 | 111d | ready to nudge |

## 5. Priority 2 — Tier C (need code work, small list)

Rebase + push + re-request review. 7 PRs only.

| Repo | Issue | Review | Stale |
|---|---|---|---|
| langgenius/dify#32317 | conflicts, rebase needed | None | 102d |
| usestrix/strix#203 | conflicts, rebase needed | REVIEW_REQUIRED | 10d |
| pytorch/vision#9384 | conflicts, rebase needed | None | 97d |
| facebookresearch/xformers#1377 | conflicts, rebase needed | None | 109d |
| lancedb/lancedb#3028 | conflicts, rebase needed | REVIEW_REQUIRED | 107d |
| rapidsai/cudf#21444 | conflicts, rebase needed | CHANGES_REQUESTED | 95d |
| PyGithub/PyGithub#3450 | conflicts, rebase needed | REVIEW_REQUIRED | 105d |

## 6. Priority 3 — Tier F (withdraw, dead work)

These target archived repos. Save your bandwidth.

| Repo | Archived |
|---|---|
| facebookresearch/fairseq#5645 | True |
| salesforce/Merlion#186 | True |
| salesforce/Merlion#185 | True |

## 7. Tier B- (CI status unknown) — check before nudging

| Repo | Stars | Stale |
|---|---:|---:|
| public-apis/public-apis#5088 | 438,371 | 99d |
| public-apis/public-apis#5087 | 438,371 | 99d |
| public-apis/public-apis#5086 | 438,371 | 99d |
| public-apis/public-apis#5085 | 438,371 | 99d |
| public-apis/public-apis#5084 | 438,371 | 99d |
| public-apis/public-apis#5083 | 438,371 | 99d |
| public-apis/public-apis#5082 | 438,371 | 99d |
| huggingface/transformers#43842 | 161,151 | 112d |
| Comfy-Org/ComfyUI#12287 | 115,252 | 104d |
| vllm-project/vllm#34163 | 81,577 | 24d |
| huggingface/diffusers#13102 | 33,748 | 112d |
| huggingface/diffusers#13125 | 33,748 | 109d |
| huggingface/diffusers#13111 | 33,748 | 108d |
| huggingface/diffusers#13110 | 33,748 | 108d |
| huggingface/diffusers#13109 | 33,748 | 108d |
| huggingface/diffusers#13108 | 33,748 | 108d |
| huggingface/diffusers#13103 | 33,748 | 108d |
| huggingface/diffusers#13094 | 33,748 | 107d |
| huggingface/diffusers#13093 | 33,748 | 107d |
| Lightning-AI/pytorch-lightning#21529 | 31,168 | 73d |
| sgl-project/sglang#18397 | 28,874 | 113d |
| speechbrain/speechbrain#3033 | 11,579 | 104d |
| espnet/espnet#6363 | 9,849 | 34d |
| Project-MONAI/MONAI#8739 | 8,229 | 91d |
| huggingface/optimum#2407 | 3,404 | 17d |
| huggingface/optimum#2406 | 3,404 | 17d |

## 8. Tier B+ with open review threads (highest ROI for engagement)

A maintainer is talking to you. Respond, don't script.

| Repo | Stars | Comments | Reviews | Threads | Stale |
|---|---:|---:|---:|---:|---:|
| python-poetry/poetry#10736 | 34,272 | 1 | 3 | 2 | 107d |
| apache/tvm#18774 | 13,404 | 3 | 1 | 2 | 77d |
| chroma-core/chroma#6437 | 28,171 | 2 | 1 | 1 | 107d |
| microsoft/agent-lightning#486 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#485 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#484 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#483 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#481 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#480 | 17,261 | 1 | 1 | 1 | 92d |
| microsoft/agent-lightning#482 | 17,261 | 1 | 2 | 1 | 89d |
| mage-ai/mage-ai#6024 | 8,739 | 0 | 1 | 1 | 107d |

## 9. Historical (closed) PRs

- Merged: **73** (47%)
- Closed without merge: **82** (53%)
- Highest-merged repos:

  - `ultralytics/ultralytics` — 4 merged
  - `aadithva/owly-website` — 3 merged
  - `adinarayanan2003/owly_short_ad` — 3 merged
  - `PaddlePaddle/PaddleOCR` — 3 merged
  - `speechbrain/speechbrain` — 2 merged
  - `recommenders-team/recommenders` — 2 merged
  - `EleutherAI/gpt-neox` — 2 merged
  - `modelscope/DiffSynth-Studio` — 2 merged

## 10. Languages of repos you touched (top 10)

| Lang | Open PRs |
|---|---:|
| Python | 180 |
| Jupyter Notebook | 33 |
| Go | 1 |
| Rust | 1 |
| TypeScript | 1 |
| HTML | 1 |
| C++ | 1 |

## 11. Recommended next actions (in order)

1. **Tier F (3 PRs)** — close with one-liner: "closing — repo is archived, fix no longer needed upstream."
2. **Tier C (7 PRs)** — rebase + push + re-request review. Pay special attention to `rapidsai/cudf#21444` (CHANGES_REQUESTED — read the review).
3. **Tier A (27 PRs)** — pick the top 5 by stars and post a single one-line progress comment. Don't try to be cute: "still interested, happy to update for any feedback."
4. **Tier B+ with threads (18 PRs)** — read each thread, respond, sequentially. Don't script it.
5. **Tier B- (26 PRs)** — check CI / mergeable state, rebase if green.
6. **The other 109 Tier B PRs (zero engagement, 100+ days old)** — long tail. Don't try to save all 109. Pick 20 highest-star ones for a polite nudge, let the rest age out.

## 12. Profile health flags

- ⚠️ **Burst pattern.** ~30+ PRs in 5 days is a yellow flag on your profile. Slow down going forward.
- ⚠️ **Stale bot risk.** Many of these will be auto-closed within 30 days. Be ready to accept that.
- ✅ **Real signal exists.** 73 merged and 18 with open threads is not nothing — your work is landing.
- ✅ **High-value repos.** transformers (161k), diffusers (32k+), pytorch (29k+), lightning (31k+), autogen (58k).
- ⚠️ **Title uniformity.** Many PRs use identical phrasings. Vary them — maintainers read titles first.

---
## Files in this folder

- `data/prs_open.json` — raw search results, open PRs (224)
- `data/prs_closed.json` — raw search results, closed PRs (166)
- `data/oss_open.json` — open PRs filtered to external OSS (218)
- `data/oss_closed.json` — closed PRs filtered to external OSS (155)
- `data/own_open.json` / `own_closed.json` — your own forks (excluded)
- `data/pr_enriched.json` — GraphQL-enriched: review state, CI, comments, threads, labels
- `data/prs_rated.json` — final per-PR rating with tier + label
- `data/PR_REPORT.md` — this report