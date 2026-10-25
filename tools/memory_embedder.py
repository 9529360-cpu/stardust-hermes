"""Small, lazy CPU embedder for BAAI/bge-small-zh-v1.5 only.

Design references: fastembed 0.9.1 text/onnx_embedding.py (CLS + L2),
text/onnx_text_model.py (int64 inputs), common/preprocessor_utils.py
(512-token truncation); Hugging Face tokenizers BERT normalizer/WordPiece.
Unlike those general frameworks, this owns a pure-Python tokenizer and uses
stdlib downloads into ordinary files in the existing fastembed cache.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable

# Immutable model revision captured by the fastembed parity baseline.
_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
_REPOSITORY = "Qdrant/bge-small-zh-v1.5"
_FILES = ("model_optimized.onnx", "tokenizer.json")


def _cjk(code: int) -> bool:
    return any(start <= code <= end for start, end in (
        (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF),
        (0x2A700, 0x2B73F), (0x2B740, 0x2B81F), (0x2B820, 0x2CEAF),
        (0xF900, 0xFAFF), (0x2F800, 0x2FA1F),
    ))


class BertWordPiece:
    """Single-sequence, case-sensitive BERT tokenizer from tokenizer.json."""

    def __init__(self, path: Path):
        config = json.loads(path.read_text(encoding="utf-8"))
        model = config["model"]
        normalizer = config["normalizer"]
        if (model["type"] != "WordPiece" or normalizer["type"] != "BertNormalizer"
                or normalizer.get("lowercase") or normalizer.get("strip_accents")):
            raise ValueError("unsupported bge tokenizer configuration")
        self.vocab = model["vocab"]
        self.unknown = self.vocab[model["unk_token"]]
        self.prefix = model["continuing_subword_prefix"]
        self.max_word = model["max_input_chars_per_word"]
        specials = [t["content"] for t in config["added_tokens"] if t.get("special")]
        self.specials = set(specials)
        self.split_specials = re.compile("(" + "|".join(re.escape(t) for t in sorted(specials, key=len, reverse=True)) + ")")
        self.pad = self.vocab["[PAD]"]

    def _pieces(self, word: str) -> list[int]:
        if len(word) > self.max_word:
            return [self.unknown]
        result = []
        start = 0
        while start < len(word):
            end = len(word)
            while end > start:
                piece = (self.prefix if start else "") + word[start:end]
                if piece in self.vocab:
                    break
                end -= 1
            if end == start:
                return [self.unknown]
            result.append(self.vocab[piece])
            start = end
        return result

    def encode(self, text: str) -> list[int]:
        ids = []
        for part in self.split_specials.split(text):
            if part in self.specials:
                ids.append(self.vocab[part])
                continue
            cleaned = []
            for char in part:
                category = unicodedata.category(char)
                if char in "\t\n\r" or category == "Zs":
                    cleaned.append(" ")
                elif ord(char) in (0, 0xFFFD) or category.startswith("C"):
                    continue
                elif _cjk(ord(char)) or category.startswith("P") or (
                    33 <= ord(char) <= 47 or 58 <= ord(char) <= 64
                    or 91 <= ord(char) <= 96 or 123 <= ord(char) <= 126
                ):
                    cleaned.extend((" ", char, " "))
                else:
                    cleaned.append(char)
            for word in "".join(cleaned).split():
                ids.extend(self._pieces(word))
                if len(ids) >= 510:
                    break
            if len(ids) >= 510:
                break
        return [self.vocab["[CLS]"]] + ids[:510] + [self.vocab["[SEP]"]]


def _model_directory(cache: Path) -> Path:
    """Reuse fastembed's HF snapshot (including older cached revisions)."""
    root = cache / "models--Qdrant--bge-small-zh-v1.5"
    preferred = root / "snapshots" / _REVISION
    candidates = [preferred]
    ref = root / "refs" / "main"
    if ref.is_file():
        revision = ref.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", revision):
            candidates.append(root / "snapshots" / revision)
    candidates.extend(sorted((root / "snapshots").glob("*")))
    # fastembed also supported a legacy unpacked model directory.
    candidates.append(cache / "fast-bge-small-zh-v1.5")
    for directory in candidates:
        if all((directory / name).is_file() for name in _FILES):
            return directory
    from urllib.request import urlopen

    preferred.mkdir(parents=True, exist_ok=True)
    for name in _FILES:
        target = preferred / name
        if target.is_file():
            continue
        temporary = target.with_name(name + ".part")
        try:
            with urlopen(f"https://huggingface.co/{_REPOSITORY}/resolve/{_REVISION}/{name}", timeout=60) as response:
                with temporary.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return preferred


class LocalTextEmbedding:
    """Narrow embed/query_embed interface consumed by SemanticTopicIndex."""

    def __init__(self, cache_dir: str):
        import onnxruntime as ort

        directory = _model_directory(Path(cache_dir))
        self.tokenizer = BertWordPiece(directory / "tokenizer.json")
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(8, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / "model_optimized.onnx"),
                                            sess_options=options, providers=["CPUExecutionProvider"])
        self.input_names = {node.name for node in self.session.get_inputs()}

    def embed(self, documents: str | Iterable[str], batch_size: int = 8):
        import numpy as np

        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        source = iter([documents] if isinstance(documents, str) else documents)
        from itertools import islice

        while batch := list(islice(source, batch_size)):
            encoded = [self.tokenizer.encode(text) for text in batch]
            width = max(map(len, encoded))
            ids = np.full((len(batch), width), self.tokenizer.pad, dtype=np.int64)
            mask = np.zeros_like(ids)
            for row, tokens in enumerate(encoded):
                ids[row, :len(tokens)] = tokens
                mask[row, :len(tokens)] = 1
            inputs = {"input_ids": ids, "attention_mask": mask,
                      "token_type_ids": np.zeros_like(ids)}
            output = self.session.run(None, {k: v for k, v in inputs.items() if k in self.input_names})[0]
            vectors = output[:, 0] if output.ndim == 3 else output
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            yield from vectors / np.maximum(norms, np.finfo(vectors.dtype).tiny)

    def query_embed(self, query: str):
        yield from self.embed(query)
