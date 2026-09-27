#!/usr/bin/env python3
"""
transcribe_firered.py — 用 FireRedASR2-AED(MLX 版)轉錄音檔。

定位:2026-07-02 加入的 A/B 候選引擎(對戰 SenseVoice / faster,爭「補詞」席位)。
小紅書 FireRedASR2,公開 Mandarin benchmark SOTA(AED 版平均 CER ~3.18%),
跑 mlx-community 移植版,Apple Silicon 原生 Metal。
初測亮點:弱音節保留(「罪與罰」全拼對,Qwen3/多引擎常漏成「罪罰」),
正好補專案已知盲點「三引擎一致仍可能漏中間弱音節」。
2026-07-05 收斂後留任「補詞第三票」(弱音節/陌生專有名詞/主力全崩段)。

限制(跟三主力引擎不同,讀稿時要知道):
    - AED 模型輸入上限 60s → 先用低能量邊界切 50s chunks 再逐段轉
      (切片工具借 mlx-audio 的 split_audio_into_chunks,不是隨便硬切)
    - 無標點 → 每個 chunk 一整行,像 FunASR 原始輸出,只當補詞票
    - 無熱詞 → dictionary.yaml 不加持,專有名詞以 dict + 三引擎為準
    - 簡體輸出 → OpenCC s2twp 簡轉繁(同 SenseVoice 慣例)
    - 有 per-chunk 信心分數(beam search),輸出 .conf.jsonl sidecar

速度優化(2026-09-16):
    mlx-audio 的 beam search 每解一個 token 都把整段 encoder 輸出重新投影成
    16 層 decoder 的 cross-attention K/V(×3 beam),但這些值在一個 chunk 內不變。
    本檔用 monkeypatch 讓它每 chunk 只算一次(見 _apply_kv_cache_patch),
    M4 Pro 實測 63.5 分音檔 約 23 分 → 11.6 分(2.6x → 5.5x 即時),
    全檔 76/76 chunk 逐字相同(0 字差)。
    另測過 8-bit 量化(再快 26%,但 0.13% 字漂移含「侃侃→看看」)、4-bit、beam=1,
    都會動字,對「補詞第三票」不值得,故不採用。
    patch 綁 mlx-audio 內部類別名稱(fireredasr2.DecoderLayer / TransformerDecoder,
    0.4.4 與 0.5.4 相同);結構對不上就自動退回原版,只慢不錯。
    要 A/B 或除錯可設 FIRERED_KV_CACHE=0 關閉。

用法:
    python3 scripts/transcribe_firered.py _inbox/<音檔>
輸出:
    _transcripts_raw/<stem>_firered.txt         簡轉繁純文字(每 chunk 一行)
    _transcripts_raw/<stem>_firered.timed.txt   [HH:MM:SS] 每 chunk 起點(50s 粒度)
    _transcripts_raw/<stem>_firered.conf.jsonl  每 chunk 信心分數

第一次跑會從 HuggingFace 下載 mlx-community/FireRedASR2-AED-mlx(~4.5GB,fp32)。
仍是最慢的引擎(~5.5x 即時),失敗不擋主流程。
長檔驗證:2026-07-02-164530(57 分)69/69 段無崩,結尾段完整(同檔 faster 曾 loop 崩)。
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "_transcripts_raw"
MODEL_ID = "mlx-community/FireRedASR2-AED-mlx"
CHUNK_SECS = 50.0  # AED 上限 60s,留邊際


def _apply_kv_cache_patch() -> bool:
    """把 cross-attention K/V 改成每 chunk 算一次。回傳是否成功啟用。

    數學上跟原版完全相同(只是把不隨 token 變的投影提到迴圈外),
    2026-09-16 全檔驗證 0 字差。任何結構對不上的情況都退回原版實作。
    """
    try:
        import mlx.core as mx
        from mlx_audio.stt.models.fireredasr2 import fireredasr2 as fr

        DecoderLayer, TransformerDecoder = fr.DecoderLayer, fr.TransformerDecoder
        orig_layer_call = DecoderLayer.__call__
        orig_beam_search = TransformerDecoder.beam_search

        # 簽名檢查:mlx-audio 改了介面就不 patch
        layer_params = list(inspect.signature(orig_layer_call).parameters)
        bs_params = list(inspect.signature(orig_beam_search).parameters)
        if layer_params[:5] != ["self", "x", "enc_output", "self_attn_mask", "cache"]:
            raise RuntimeError(f"DecoderLayer.__call__ 簽名不符: {layer_params}")
        if bs_params[:3] != ["self", "enc_output", "beam_size"]:
            raise RuntimeError(f"beam_search 簽名不符: {bs_params}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ K/V 快取 patch 未啟用({e}),走原版速度")
        return False

    def cross_attn_cached(attn, q, k, v):
        n = q.shape[0]
        q = attn.w_qs(q).reshape(n, -1, attn.n_head, attn.d_k).transpose(0, 2, 1, 3)
        out = mx.fast.scaled_dot_product_attention(q, k, v, scale=attn.scale)
        out = out.transpose(0, 2, 1, 3).reshape(n, -1, attn.n_head * attn.d_k)
        return attn.fc(out)

    def patched_layer_call(self, x, enc_output, self_attn_mask=None, cache=None):
        kv = getattr(self, "_enc_kv", None)
        if kv is None:
            return orig_layer_call(self, x, enc_output, self_attn_mask, cache)
        residual = x
        x_norm = self.self_attn_norm(x)
        if cache is not None:
            xq = x_norm[:, -1:, :]
            residual = residual[:, -1:, :]
            if self_attn_mask is not None:
                self_attn_mask = self_attn_mask[:, -1:, :]
        else:
            xq = x_norm
        x = residual + self.self_attn(xq, x_norm, x_norm, self_attn_mask)
        x = x + cross_attn_cached(self.cross_attn, self.cross_attn_norm(x), *kv)
        x = x + self.mlp(self.mlp_norm(x))
        if cache is not None:
            x = mx.concatenate([cache, x], axis=1)
        return x

    def precompute(decoder, enc_output, beam_size):
        for layer in decoder.layer_stack:
            a = layer.cross_attn
            k = a.w_ks(enc_output).reshape(1, -1, a.n_head, a.d_k).transpose(0, 2, 1, 3)
            v = a.w_vs(enc_output).reshape(1, -1, a.n_head, a.d_k).transpose(0, 2, 1, 3)
            k = mx.repeat(k, beam_size, axis=0)
            v = mx.repeat(v, beam_size, axis=0)
            mx.eval(k, v)
            layer._enc_kv = (k, v)

    def clear(decoder):
        for layer in decoder.layer_stack:
            layer._enc_kv = None

    warned = {"done": False}

    def patched_beam_search(self, enc_output, beam_size=3, **kwargs):
        try:
            precompute(self, enc_output, beam_size)
        except Exception as e:  # noqa: BLE001
            clear(self)
            if not warned["done"]:
                print(f"⚠ K/V 快取預算失敗({e}),此後走原版速度")
                warned["done"] = True
            return orig_beam_search(self, enc_output, beam_size=beam_size, **kwargs)
        try:
            return orig_beam_search(self, enc_output, beam_size=beam_size, **kwargs)
        finally:
            clear(self)

    DecoderLayer.__call__ = patched_layer_call
    TransformerDecoder.beam_search = patched_beam_search
    return True


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <音檔路徑>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)

    stem = src.stem
    RAW_DIR.mkdir(exist_ok=True)
    out_txt = RAW_DIR / f"{stem}_firered.txt"
    out_timed = RAW_DIR / f"{stem}_firered.timed.txt"
    out_conf = RAW_DIR / f"{stem}_firered.conf.jsonl"

    # m4a → 16k mono wav(不依賴 mlx-audio 的解碼路徑,行為可控)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "16000", "-ac", "1", wav_path],
        check=True,
    )

    print(f"→ 載入模型 ({MODEL_ID})...")
    from mlx_audio.stt import load
    from mlx_audio.stt.models.qwen3_asr.qwen3_asr import split_audio_into_chunks
    from mlx_audio.stt.utils import load_audio

    if os.environ.get("FIRERED_KV_CACHE", "1") != "0":
        if _apply_kv_cache_patch():
            print("→ K/V 快取 patch 已啟用(約 2 倍速,輸出不變)")
    else:
        print("→ FIRERED_KV_CACHE=0,走原版速度")

    model = load(MODEL_ID)
    wav = load_audio(wav_path, sr=16000)
    chunks = split_audio_into_chunks(wav, 16000, chunk_duration=CHUNK_SECS)
    print(f"→ 低能量邊界切片: {len(chunks)} 段(≤{CHUNK_SECS:.0f}s)")

    records = []
    t0 = time.time()
    for i, (chunk, offset) in enumerate(chunks):
        r = model.generate(chunk)
        text = r.text.strip()
        conf = r.segments[0].get("confidence", 0.0) if r.segments else 0.0
        if text:
            records.append({"start": round(offset, 2), "text": text, "confidence": conf})
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(chunks)} 段,{time.time() - t0:.0f}s")
    print(f"→ 轉錄耗時: {time.time() - t0:.0f} 秒")

    Path(wav_path).unlink(missing_ok=True)

    print("→ 簡轉繁中...")
    from opencc import OpenCC

    cc = OpenCC("s2twp")
    for rec in records:
        rec["text"] = cc.convert(rec["text"])

    lines, timed_lines, conf_lines = [], [], []
    for rec in records:
        s = int(rec["start"])
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        lines.append(rec["text"])
        timed_lines.append(f"[{h:02d}:{m:02d}:{sec:02d}] {rec['text']}")
        conf_lines.append(json.dumps(rec, ensure_ascii=False))

    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_timed.write_text("\n".join(timed_lines) + "\n", encoding="utf-8")
    out_conf.write_text("\n".join(conf_lines) + "\n", encoding="utf-8")
    print(f"✓ 完成: {out_txt}")
    print(f"✓ 時間戳版: {out_timed}")
    print(f"✓ 信心分數版: {out_conf}")


if __name__ == "__main__":
    main()
