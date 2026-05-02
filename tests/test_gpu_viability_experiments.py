import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_simulation_class_probe_runner_emits_academic_integrity_metadata(tmp_path: Path) -> None:
    script = REPO_ROOT / "experiments" / "gpu_viability" / "simulation_class_probes.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--backend",
            "numpy",
            "--steps",
            "2",
            "--grid",
            "16",
            "--network-samples",
            "32",
            "--network-branches",
            "2",
            "--network-segments",
            "4",
            "--particles",
            "256",
            "--mc-steps",
            "2",
            "--depletion-samples",
            "64",
            "--depletion-steps",
            "2",
            "--species",
            "4",
            "--output-root",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "wrote" in completed.stdout
    outputs = sorted(tmp_path.glob("simulation_class_probes_numpy_*.json"))
    assert outputs
    payload = json.loads(outputs[-1].read_text(encoding="utf-8"))

    assert "not validated reactor analyses" in payload["academic_integrity"]["summary"]
    assert payload["backend"]["name"] == "numpy"
    assert payload["host"]["environment"]["pytorch_xpu_fallback_enabled"] is False
    assert not payload["failures"]
    assert {result["name"] for result in payload["results"]} == {
        "1d_loop_hydraulic_network_transient",
        "2d_porous_core_ltne_thermal",
        "delayed_neutron_precursor_advection_diffusion_pde",
        "multigroup_neutron_diffusion_proxy",
        "monte_carlo_particle_transport_proxy",
        "depletion_chain_bateman_proxy",
        "local_cfd_convection_diffusion_proxy",
    }
    for result in payload["results"]:
        assert result["numerical_health"] == "ok"
        assert result["invariants"]
        assert not result["invariant_failures"]
        assert all(result["invariants"].values())
        assert result["runtime_environment"]["pytorch_xpu_fallback_enabled"] is False
        assert result["missing_physics"]
        assert "not" in result["validation_status"].lower()
        assert result["throughput"]
