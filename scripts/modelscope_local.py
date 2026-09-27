"""
modelscope_local.py — 讓 FunASR / SenseVoice 在 modelscope.cn 掛掉時仍能離線載入。

問題:FunASR 的 AutoModel(model="paraformer-zh") 啟動時會連 modelscope.cn 解析 model id。
modelscope 一旦回 502(對方伺服器當機),即使模型早就下載 cache 在本地,整個 load 還是會炸:
    Download: iic/xxx failed!: ... too many 502 error responses
    AssertionError: iic/xxx is not registered

策略(prefer-local):模型只要已經完整 cache 在本地,就直接把「本地路徑」餵給 AutoModel,
完全不碰網路;沒 cache 時(第一次跑 / 新模型)才回退成原本的線上 model id 讓它去下載。
所以:已下載過的機器 → 永遠不受 modelscope 當機影響;乾淨的新機器 → 行為跟以前一樣(上線下載)。

搭配 AutoModel(..., disable_update=True) 一起用(關掉 funasr 自我版本檢查那一次連線)。

副作用(刻意的取捨):模型一旦 cache 就被「釘」在這個版本,upstream 之後就算發了更新也永遠
不會自動抓。要更新得手動刪掉 ~/.cache/modelscope/hub/models/<repo> 再重跑。對本專案
(ASR 模型固定、重視可重現)這是想要的行為。

對外:
    resolve(model_id)      -> str          # 已 cache 回本地路徑,否則原樣回傳 model id
    all_local(*ids)        -> bool         # 傳入的(非 None)模型是否全部都已本地 cache
    missing_local(*ids)    -> list[str]    # 哪些 id 還沒 cache(會觸發上線下載 → 當機時失敗)
"""
import os
from pathlib import Path

# FunASR / SenseVoice 會用到的 model id(含 alias)→ modelscope repo 目錄名 <namespace>/<name>
# alias 是 funasr 內建的簡寫(對應 funasr 的 name_maps_ms);modelscope 下載後存成右邊的資料夾名。
_ALIAS = {
    "paraformer-zh": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
}

# 判斷一個 repo「完整下載」的標記:configuration.json(早期就落地的小檔)
# 「加上」model.pt(~1GB 權重,最後才落地)。只看 configuration.json 會把中斷的下載
# (小檔在、權重還沒到)誤判成「已 cache」,而那種狀態在 modelscope 當機時無法自我修復。
# 本專案四個模型權重都叫 model.pt(已確認);新模型若權重檔名不同會被當成沒 cache → 安全地回退上線。
_MARKERS = ("configuration.json", "model.pt")


def _cache_roots():
    """可能的 modelscope cache 根目錄。主流版本是 ~/.cache/modelscope/hub/models/;
    其餘候選(.../hub、MODELSCOPE_CACHE 的各種後綴)是對舊版 / 自訂路徑的防禦性涵蓋,不一定存在。"""
    roots = []
    env = os.environ.get("MODELSCOPE_CACHE")
    if env:
        e = Path(env).expanduser()
        roots += [e, e / "models", e / "hub", e / "hub" / "models"]
    home = Path.home() / ".cache" / "modelscope"
    roots += [home / "hub" / "models", home / "hub"]
    return roots


def _complete(d):
    """目錄 d 是否含有「完整下載」的全部標記檔。"""
    return all((d / m).exists() for m in _MARKERS)


def _local_dir(model_id):
    """回傳「已完整 cache」的本地目錄(Path),否則 None。"""
    if not model_id:
        return None
    # 有人直接傳本地模型目錄進來:必須是「目錄 + 標記齊全」才算,避免 CWD 同名資料夾誤判
    p = Path(model_id).expanduser()
    if p.is_dir() and _complete(p):
        return p
    repo = _ALIAS.get(model_id, model_id)  # alias → iic/xxx;其餘原樣(本身可能就是 iic/xxx)
    if "/" in repo:                        # 只有 namespace/name 形式才去 cache 目錄找
        for root in _cache_roots():
            cand = root / repo
            if _complete(cand):
                return cand
    return None


def resolve(model_id):
    """model_id(alias / iic/xxx / 路徑)→ 本地 cache 路徑(若已完整下載)否則原樣回傳 model id。"""
    d = _local_dir(model_id)
    return str(d) if d else model_id


def missing_local(*model_ids):
    """回傳還沒本地 cache 的 model id(這些會觸發上線下載;modelscope 當機時會失敗)。"""
    return [m for m in model_ids if m and _local_dir(m) is None]


def all_local(*model_ids):
    """傳入的模型(略過 None)是否全部都已本地 cache。"""
    return not missing_local(*model_ids)
