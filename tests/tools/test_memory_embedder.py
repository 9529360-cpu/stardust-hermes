"""Dependency-free BERT tokenization and local ONNX embedding contracts."""
import json
import sys

import pytest


def _tokenizer(tmp_path):
    from tools.memory_embedder import BertWordPiece

    vocab = {s: i for i, s in enumerate([
        '[PAD]', '[UNK]', '[CLS]', '[SEP]', '[MASK]',
        '中', '文', 'Hello', ',', 'world', '##s', 'é', '!',
    ])}
    path = tmp_path / 'tokenizer.json'
    path.write_text(json.dumps({
        'model': {'type': 'WordPiece', 'vocab': vocab, 'unk_token': '[UNK]',
                  'continuing_subword_prefix': '##', 'max_input_chars_per_word': 100},
        'normalizer': {'type': 'BertNormalizer', 'clean_text': True,
                       'handle_chinese_chars': True, 'strip_accents': None, 'lowercase': False},
        'added_tokens': [{'content': t, 'id': vocab[t], 'special': True}
                         for t in ['[PAD]', '[UNK]', '[CLS]', '[SEP]', '[MASK]']],
    }), encoding='utf-8')
    return BertWordPiece(path), vocab


def test_bert_preserves_case_accents_splits_cjk_punctuation_and_wordpiece(tmp_path):
    tokenizer, v = _tokenizer(tmp_path)
    assert tokenizer.encode('中文 Hello, worlds é!') == [
        v[x] for x in ['[CLS]', '中', '文', 'Hello', ',', 'world', '##s', 'é', '!', '[SEP]']
    ]
    assert tokenizer.encode('hello') == [v['[CLS]'], v['[UNK]'], v['[SEP]']]


def test_special_tokens_controls_unknown_and_truncation(tmp_path):
    tokenizer, v = _tokenizer(tmp_path)
    assert tokenizer.encode('Hello\x00\u200b [MASK]\t中文') == [
        v[x] for x in ['[CLS]', 'Hello', '[MASK]', '中', '文', '[SEP]']
    ]
    assert tokenizer.encode('x' * 101) == [v['[CLS]'], v['[UNK]'], v['[SEP]']]
    ids = tokenizer.encode('中' * 600)
    assert len(ids) == 512
    assert ids[-1] == v['[SEP]']
    assert tokenizer.encode('') == [v['[CLS]'], v['[SEP]']]


def test_import_is_lazy():
    import subprocess
    result = subprocess.run([sys.executable, '-c',
        'import sys; import tools.memory_embedder; '
        'assert not {"onnxruntime", "fastembed", "tokenizers", "numpy"}.intersection(sys.modules)'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

def test_cache_reuses_existing_snapshot_without_network(tmp_path, monkeypatch):
    from tools.memory_embedder import _model_directory
    import urllib.request

    snapshot = tmp_path / 'models--Qdrant--bge-small-zh-v1.5' / 'snapshots' / ('a' * 40)
    snapshot.mkdir(parents=True)
    for name in ('tokenizer.json', 'model_optimized.onnx'):
        (snapshot / name).write_bytes(b'existing')
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('cached model downloaded'))
    assert _model_directory(tmp_path) == snapshot


def test_download_uses_ordinary_files_and_cleans_partial_failure(tmp_path, monkeypatch):
    from tools.memory_embedder import _model_directory
    import io
    import urllib.request

    calls = []
    def download(url, timeout):
        calls.append(url)
        return io.BytesIO(b'model' if url.endswith('.onnx') else b'{}')
    monkeypatch.setattr(urllib.request, 'urlopen', download)
    snapshot = _model_directory(tmp_path)
    assert len(calls) == 2
    assert all(p.is_file() and not p.is_symlink() for p in snapshot.iterdir())
    assert not list(snapshot.glob('*.part'))
    (snapshot / 'tokenizer.json').unlink()
    def fail(*args, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    with pytest.raises(OSError, match='offline'):
        _model_directory(tmp_path)
    assert not list(snapshot.glob('*.part'))


def test_cls_pooling_padding_normalization_and_query_interface(tmp_path):
    import numpy as np
    from tools.memory_embedder import LocalTextEmbedding

    tokenizer, vocab = _tokenizer(tmp_path)
    captured = []

    class Session:
        def run(self, outputs, inputs):
            captured.append(inputs)
            result = np.full((*inputs['input_ids'].shape, 2), 10, dtype=np.float32)
            result[:, 0] = [3, 4]
            return [result]

    model = LocalTextEmbedding.__new__(LocalTextEmbedding)
    model.tokenizer = tokenizer
    model.session = Session()
    model.input_names = {'input_ids', 'attention_mask', 'token_type_ids'}
    vectors = list(model.embed(['中文', 'Hello']))
    assert np.allclose(vectors, [[.6, .8], [.6, .8]])
    assert captured[0]['input_ids'].dtype == np.int64
    assert captured[0]['input_ids'][1, -1] == vocab['[PAD]']
    assert captured[0]['attention_mask'].tolist() == [[1, 1, 1, 1], [1, 1, 1, 0]]
    assert not captured[0]['token_type_ids'].any()
    assert np.allclose(next(model.query_embed('Hello')), [.6, .8])
    assert list(model.embed([])) == []
