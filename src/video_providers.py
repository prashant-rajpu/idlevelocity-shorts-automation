"""
Multi-provider AI video generation with automatic fallback.

Chain order (highest quality first, degrades to stock footage last):
  1. fal.ai / MiniMax H3 Max      — 5 free generations / 24h per account
  2. fal.ai / Kling 2.5 Turbo Pro — uses fal signup $10 credit
  3. fal.ai / Wan 2.6             — cheapest fal option
  4. Hugging Face / LTX-Video     — free monthly inference credits
  5. Replicate / LTX-Video        — extremely cheap on signup credit
  6. Pixverse                     — free daily quota
  7. Pexels stock footage         — unlimited free, real footage (last resort)

Quotas that reset daily are tracked in data/provider_quotas.json so the
pipeline skips exhausted providers without wasting an HTTP call.

Env vars checked (missing => provider silently skipped):
  FAL_KEY, HF_TOKEN, REPLICATE_API_TOKEN, PIXVERSE_API_KEY, PEXELS_API_KEY
"""

import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parents[1]
QUOTA_FILE = ROOT / "data" / "provider_quotas.json"


class ProviderError(Exception):
    pass


class QuotaExhausted(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


def _load_quotas() -> dict:
    if not QUOTA_FILE.exists():
        return {}
    try:
        return json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_quotas(data: dict) -> None:
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stream_download(url: str, target_path: Path) -> None:
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with target_path.open("wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                fh.write(chunk)


class VideoProvider(ABC):
    name: str
    daily_free_quota: Optional[int] = None  # None = unlimited / metered

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.overrides = (cfg.get("provider_overrides") or {}).get(self.name, {})

    @abstractmethod
    def has_credentials(self) -> bool: ...

    @abstractmethod
    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path: ...

    def _record_usage(self) -> None:
        if self.daily_free_quota is None:
            return
        quotas = _load_quotas()
        key = f"{self.name}:{_today_utc()}"
        quotas[key] = quotas.get(key, 0) + 1
        _save_quotas(quotas)

    def _mark_exhausted(self) -> None:
        if self.daily_free_quota is None:
            return
        quotas = _load_quotas()
        quotas[f"{self.name}:{_today_utc()}"] = self.daily_free_quota
        _save_quotas(quotas)

    def available(self) -> bool:
        if not self.has_credentials():
            return False
        if self.daily_free_quota is not None:
            used = _load_quotas().get(f"{self.name}:{_today_utc()}", 0)
            if used >= self.daily_free_quota:
                return False
        return True

    def generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        result = self._generate(prompt, target_path, duration)
        self._record_usage()
        return result


class _FalBase(VideoProvider):
    """Shared logic for fal.ai models via their queue REST API."""
    endpoint: str = ""
    poll_seconds: int = 5
    max_poll_attempts: int = 90  # ~7.5 min ceiling

    def has_credentials(self) -> bool:
        return bool(os.getenv("FAL_KEY"))

    def _build_payload(self, prompt: str, duration: float) -> dict:
        return {"prompt": prompt}

    def _extract_video_url(self, result: dict) -> str:
        video = result.get("video") or result.get("output") or result.get("videos")
        if isinstance(video, list) and video:
            video = video[0]
        if isinstance(video, dict):
            for k in ("url", "video_url", "signed_url"):
                if video.get(k):
                    return video[k]
        if isinstance(video, str):
            return video
        raise ProviderError(f"could not find video URL in fal response: {list(result.keys())}")

    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        endpoint = self.overrides.get("endpoint", self.endpoint)
        key = os.environ["FAL_KEY"]
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        payload = self._build_payload(prompt, duration)

        r = requests.post(f"https://queue.fal.run/{endpoint}", headers=headers, json=payload, timeout=60)
        if r.status_code == 429:
            self._mark_exhausted()
            raise RateLimited(f"fal 429: {r.text[:200]}")
        if r.status_code in (402, 403):
            self._mark_exhausted()
            raise QuotaExhausted(f"fal {r.status_code}: {r.text[:200]}")
        r.raise_for_status()

        job = r.json()
        status_url = job.get("status_url") or f"https://queue.fal.run/{endpoint}/requests/{job['request_id']}/status"
        response_url = job.get("response_url") or f"https://queue.fal.run/{endpoint}/requests/{job['request_id']}"

        for _ in range(self.max_poll_attempts):
            time.sleep(self.poll_seconds)
            s = requests.get(status_url, headers=headers, timeout=30)
            if s.status_code != 200:
                continue
            status = (s.json() or {}).get("status", "").upper()
            if status == "COMPLETED":
                break
            if status in ("FAILED", "CANCELLED", "ERROR"):
                raise ProviderError(f"fal job {status}: {s.text[:200]}")
        else:
            raise ProviderError("fal job timed out")

        result = requests.get(response_url, headers=headers, timeout=30).json()
        _stream_download(self._extract_video_url(result), target_path)
        return target_path


class FalMiniMaxH3Max(_FalBase):
    name = "fal_minimax_h3max"
    endpoint = "fal-ai/minimax/hailuo-02/pro/text-to-video"
    daily_free_quota = 5  # fal grants 5 free H3 Max generations / 24h

    def _build_payload(self, prompt: str, duration: float) -> dict:
        return {
            "prompt": prompt,
            "duration": min(max(int(round(duration)), 5), 15),
            "resolution": "768P",
            "aspect_ratio": "9:16",
            "prompt_optimizer": True,
        }


class FalKling25Turbo(_FalBase):
    name = "fal_kling25_turbo"
    endpoint = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"

    def _build_payload(self, prompt: str, duration: float) -> dict:
        return {
            "prompt": prompt,
            "duration": "5",
            "aspect_ratio": "9:16",
        }


class FalWan(_FalBase):
    name = "fal_wan"
    endpoint = "fal-ai/wan/v2.2-a14b/text-to-video"

    def _build_payload(self, prompt: str, duration: float) -> dict:
        return {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        }


class HuggingFaceLTX(VideoProvider):
    """LTX-Video via HF Inference Providers (new router endpoint)."""
    name = "hf_ltx"
    default_model = "Lightricks/LTX-Video"

    def has_credentials(self) -> bool:
        return bool(os.getenv("HF_TOKEN"))

    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        model = self.overrides.get("model", self.default_model)
        token = os.environ["HF_TOKEN"]
        # HF migrated inference off api-inference.huggingface.co to the
        # Inference Providers router. Video generation on the free tier
        # is severely rate-limited or gated to paid providers — expect
        # this to 402/429 without a subscription.
        url = f"https://router.huggingface.co/hf-inference/models/{model}"

        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "video/mp4"},
            json={"inputs": prompt, "parameters": {"num_frames": max(int(duration * 24), 24)}},
            timeout=300,
        )
        if r.status_code == 429:
            raise RateLimited(f"HF 429: {r.text[:200]}")
        if r.status_code in (402, 403):
            raise QuotaExhausted(f"HF {r.status_code}: {r.text[:200]}")
        if r.status_code == 503:
            raise ProviderError(f"HF model warming up: {r.text[:200]}")
        r.raise_for_status()

        content_type = r.headers.get("content-type", "")
        if "video" in content_type or "octet-stream" in content_type:
            target_path.write_bytes(r.content)
            return target_path
        try:
            data = r.json()
            video_url = data.get("output") or data.get("video_url") or (data.get("video") or {}).get("url")
            if video_url:
                _stream_download(video_url, target_path)
                return target_path
        except Exception:
            pass
        raise ProviderError(f"HF returned unexpected content-type: {content_type}")


class ReplicateLTX(VideoProvider):
    """LTX-Video (or configured alt) via Replicate — cheap per generation."""
    name = "replicate_ltx"
    # Official Replicate model path. Override via
    # config.provider_overrides.replicate_ltx.model to swap in a different
    # model (e.g. "bytedance/seedance-1-lite", "fofr/ltx-video") or a
    # pinned "owner/name:version_hash" if you want deterministic output.
    default_model = "lightricks/ltx-video"

    def has_credentials(self) -> bool:
        return bool(os.getenv("REPLICATE_API_TOKEN"))

    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        model = self.overrides.get("model", self.default_model)
        token = os.environ["REPLICATE_API_TOKEN"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # If model contains ":", it's owner/name:version — use /v1/predictions
        # with the version hash. Otherwise use the official-model endpoint.
        input_body = {
            "prompt": prompt,
            "width": 768,
            "height": 1344,
            "num_frames": max(int(duration * 24), 24),
        }
        if ":" in model:
            version = model.split(":", 1)[1]
            r = requests.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers,
                json={"version": version, "input": input_body},
                timeout=60,
            )
        else:
            r = requests.post(
                f"https://api.replicate.com/v1/models/{model}/predictions",
                headers=headers,
                json={"input": input_body},
                timeout=60,
            )
        if r.status_code == 429:
            raise RateLimited(f"replicate 429: {r.text[:200]}")
        if r.status_code in (402, 403):
            raise QuotaExhausted(f"replicate {r.status_code}: {r.text[:200]}")
        r.raise_for_status()

        pred = r.json()
        get_url = pred["urls"]["get"]

        for _ in range(90):
            time.sleep(5)
            s = requests.get(get_url, headers=headers, timeout=30).json()
            status = s.get("status")
            if status == "succeeded":
                output = s["output"]
                video_url = output if isinstance(output, str) else output[0]
                _stream_download(video_url, target_path)
                return target_path
            if status in ("failed", "canceled"):
                raise ProviderError(f"replicate {status}: {s.get('error')}")
        raise ProviderError("replicate timed out")


class Pixverse(VideoProvider):
    name = "pixverse"
    daily_free_quota = 2

    def has_credentials(self) -> bool:
        return bool(os.getenv("PIXVERSE_API_KEY"))

    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        api_key = os.environ["PIXVERSE_API_KEY"]
        headers = {"API-KEY": api_key, "Content-Type": "application/json"}

        r = requests.post(
            "https://app-api.pixverse.ai/openapi/v2/video/text/generate",
            headers=headers,
            json={
                "prompt": prompt,
                "aspect_ratio": "9:16",
                "duration": 5,
                "quality": "540p",
                "model": "v3.5",
            },
            timeout=60,
        )
        if r.status_code == 429:
            self._mark_exhausted()
            raise RateLimited("pixverse 429")
        r.raise_for_status()

        data = r.json()
        video_id = (data.get("Resp") or data).get("video_id")
        if not video_id:
            raise ProviderError(f"pixverse: no video_id in response ({data})")

        for _ in range(60):
            time.sleep(5)
            s = requests.get(
                f"https://app-api.pixverse.ai/openapi/v2/video/result/{video_id}",
                headers=headers,
                timeout=30,
            ).json()
            resp = s.get("Resp") or s
            status = resp.get("status")
            if status == 1:
                _stream_download(resp["url"], target_path)
                return target_path
            if status in (7, 8):
                raise ProviderError(f"pixverse failed: {resp}")
        raise ProviderError("pixverse timed out")


class PexelsStock(VideoProvider):
    """Non-AI real stock footage. Ultimate fallback — never runs out."""
    name = "pexels"

    def has_credentials(self) -> bool:
        return bool(os.getenv("PEXELS_API_KEY"))

    def _generate(self, prompt: str, target_path: Path, duration: float) -> Path:
        headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
        words = prompt.split()
        queries = [prompt]
        if len(words) > 3:
            queries.append(" ".join(words[:3]))
        queries.extend(["deep focus study", "sunrise motivation", "productive workout", "calm thinking"])

        for q in queries:
            try:
                r = requests.get(
                    "https://api.pexels.com/videos/search",
                    headers=headers,
                    params={"query": q, "orientation": "portrait", "per_page": 8},
                    timeout=45,
                )
            except requests.RequestException:
                continue
            if r.status_code == 429:
                raise RateLimited("pexels 429")
            if not r.ok:
                continue
            videos = r.json().get("videos", [])
            candidates = []
            for video in videos:
                for f in video.get("video_files", []):
                    if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0):
                        candidates.append((abs(f.get("height", 0) - 1920), f["link"]))
            if candidates:
                url = sorted(candidates)[0][1]
                _stream_download(url, target_path)
                return target_path
        raise ProviderError(f"pexels: no portrait results for {prompt!r}")


ALL_PROVIDERS = {
    "fal_minimax_h3max": FalMiniMaxH3Max,
    "fal_kling25_turbo": FalKling25Turbo,
    "fal_wan": FalWan,
    "hf_ltx": HuggingFaceLTX,
    "replicate_ltx": ReplicateLTX,
    "pixverse": Pixverse,
    "pexels": PexelsStock,
}

DEFAULT_CHAIN = [
    "fal_minimax_h3max",
    "fal_kling25_turbo",
    "fal_wan",
    "hf_ltx",
    "replicate_ltx",
    "pixverse",
    "pexels",
]


def load_provider_chain(cfg: dict) -> list[VideoProvider]:
    names = cfg.get("video_providers", DEFAULT_CHAIN)
    return [ALL_PROVIDERS[n](cfg) for n in names if n in ALL_PROVIDERS]


def generate_scene_video(
    scene: dict, target_path: Path, duration: float, cfg: dict
) -> tuple[Path, str]:
    """Try each provider in order. Returns (path, provider_name_used)."""
    style_prefix = cfg.get("style_prefix", "").strip()
    ai_prompt = scene.get("cinematic_prompt") or scene.get("visual_query", "")
    stock_query = scene.get("visual_query") or ai_prompt

    providers = load_provider_chain(cfg)
    last_err: Optional[Exception] = None

    for provider in providers:
        if not provider.available():
            print(f"    ↳ skip {provider.name} (no key or quota exhausted)")
            continue
        prompt = stock_query if provider.name == "pexels" else (
            f"{style_prefix}\n{ai_prompt}".strip() if style_prefix else ai_prompt
        )
        try:
            print(f"    ↳ trying {provider.name}...")
            provider.generate(prompt, target_path, duration)
            print(f"    ✓ {provider.name} produced clip")
            return target_path, provider.name
        except (RateLimited, QuotaExhausted) as e:
            print(f"    ✗ {provider.name} exhausted: {e}")
            last_err = e
        except (ProviderError, requests.RequestException) as e:
            print(f"    ✗ {provider.name} failed: {e}")
            last_err = e
        except Exception as e:  # unknown failure — log and try next
            print(f"    ✗ {provider.name} unexpected error: {e}")
            last_err = e

    raise RuntimeError(f"all video providers exhausted; last error: {last_err}")
