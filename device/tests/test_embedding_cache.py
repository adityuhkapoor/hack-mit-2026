"""A saved embedding model must remain usable after reboot without a network lookup."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from nimbus_cam import library


@pytest.mark.parametrize("cold", [False, True])
def test_persistent_model_is_tried_offline_before_download(monkeypatch, tmp_path, cold):
    calls = []

    class Model:
        def __init__(self, name, **options):
            calls.append((name, options))
            if cold and options.get("local_files_only"):
                raise ValueError("model is not cached")

        def embed(self, texts):
            return iter([np.zeros(library.DIMS) for _ in texts])

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Model))
    monkeypatch.setattr(library, "_embedder", None)
    monkeypatch.setattr(library, "_embed_cache", {})
    monkeypatch.setattr(library, "HOME", tmp_path)
    monkeypatch.delenv("NIMBUS_EMBED_CACHE", raising=False)
    assert library.embed(["saved photo"]).shape == (1, library.DIMS)
    assert calls[0][1]["local_files_only"] is True
    assert all(c[1]["cache_dir"] == str(tmp_path / "models") for c in calls)
    assert (tmp_path / "models").is_dir()
    assert len(calls) == (2 if cold else 1)
    library.embed(["another photo"])
    assert len(calls) == (2 if cold else 1)
