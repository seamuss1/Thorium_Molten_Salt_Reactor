import thorium_reactor.accelerators as accelerators


def test_auto_backend_prefers_torch_xpu_when_available(monkeypatch) -> None:
    calls: list[str] = []

    def fake_create_array_backend(name: str, **_kwargs):
        calls.append(name)
        return object()

    monkeypatch.setattr(accelerators, "create_array_backend", fake_create_array_backend)

    selection = accelerators.resolve_runtime_backend("auto", samples=1)

    assert selection.selected == "torch-xpu"
    assert calls == ["torch-xpu"]


def test_auto_backend_falls_back_to_numpy_when_torch_xpu_is_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    def fake_create_array_backend(name: str, **_kwargs):
        calls.append(name)
        if name == "torch-xpu":
            raise accelerators.BackendUnavailable("no xpu")
        return object()

    monkeypatch.setattr(accelerators, "create_array_backend", fake_create_array_backend)

    selection = accelerators.resolve_runtime_backend("auto", samples=65_536)

    assert selection.selected == "numpy"
    assert calls == ["torch-xpu", "numpy"]
    assert "torch-xpu unavailable" in selection.reason
