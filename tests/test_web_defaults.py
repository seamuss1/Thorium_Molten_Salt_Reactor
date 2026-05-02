from thorium_reactor.transient_sweep import DEFAULT_TRANSIENT_SWEEP_SAMPLES
from thorium_reactor.web.schemas import SimulationDraft


def test_web_simulation_draft_uses_gpu_sized_transient_sweep_defaults() -> None:
    draft = SimulationDraft(case_name="immersed_pool_reference")

    assert draft.sweep_samples == DEFAULT_TRANSIENT_SWEEP_SAMPLES
    assert draft.prefer_gpu is True
