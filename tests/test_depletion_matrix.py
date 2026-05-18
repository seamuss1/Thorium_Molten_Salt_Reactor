from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from thorium_reactor.config import load_case_config
from thorium_reactor.depletion import (
    DepletionChain,
    DepletionNuclide,
    DepletionReaction,
    build_depletion_matrix,
    load_depletion_chain,
    run_depletion_case,
    step_depletion,
)
from thorium_reactor.paths import create_result_bundle


def test_one_nuclide_decay_matches_analytic_solution() -> None:
    chain = DepletionChain(
        name="one_decay",
        source="test",
        source_format="memory",
        nuclides=(DepletionNuclide(name="A", half_life_s=10.0),),
    )
    matrix = build_depletion_matrix(chain)
    final = step_depletion(matrix, np.array([100.0]), 10.0)

    assert final[0] == pytest.approx(50.0, rel=1e-10)


def test_parent_daughter_bateman_solution_conserves_atoms() -> None:
    chain = DepletionChain(
        name="parent_daughter",
        source="test",
        source_format="memory",
        nuclides=(
            DepletionNuclide(
                name="A",
                half_life_s=10.0,
                decay_modes=(DepletionReaction("beta-", target="B"),),
            ),
            DepletionNuclide(name="B"),
        ),
    )
    matrix = build_depletion_matrix(chain)
    final = step_depletion(matrix, np.array([100.0, 0.0]), 10.0)

    assert final[0] == pytest.approx(50.0, rel=1e-10)
    assert final[1] == pytest.approx(50.0, rel=1e-10)
    assert final.sum() == pytest.approx(100.0, abs=1e-8)


def test_removal_feed_operator_preserves_equilibrium_inventory() -> None:
    chain = DepletionChain(
        name="feed_removal",
        source="test",
        source_format="memory",
        nuclides=(DepletionNuclide(name="Xe135"),),
    )
    matrix = build_depletion_matrix(
        chain,
        removal_rates_per_s={"Xe135": 0.1},
        feed_atoms_per_s={"Xe135": 10.0},
    )
    final = step_depletion(matrix, np.array([100.0]), 25.0)

    assert final[0] == pytest.approx(100.0, rel=1e-10)


def test_fission_yield_matrix_accounts_for_closed_tiny_chain() -> None:
    chain = DepletionChain(
        name="fission_yield",
        source="test",
        source_format="memory",
        nuclides=(
            DepletionNuclide(
                name="U233",
                reactions=(
                    DepletionReaction(
                        "fission",
                        default_rate_per_s=0.05,
                        fission_yields={"A": 0.6, "B": 0.4},
                    ),
                ),
            ),
            DepletionNuclide(name="A"),
            DepletionNuclide(name="B"),
        ),
    )
    matrix = build_depletion_matrix(chain)
    final = step_depletion(matrix, np.array([10.0, 0.0, 0.0]), 2.0)

    assert final.sum() == pytest.approx(10.0, abs=1e-8)
    assert final[1] > 0.0
    assert final[2] > 0.0


def test_openmc_xml_mini_chain_import(tmp_path) -> None:
    path = tmp_path / "mini_chain.xml"
    path.write_text(
        """
<depletion_chain name="mini">
  <nuclide name="A" half_life="10.0">
    <decay type="beta-" target="B" branching_ratio="1.0" />
  </nuclide>
  <nuclide name="B" />
</depletion_chain>
""".strip(),
        encoding="utf-8",
    )

    chain = load_depletion_chain(path, source_format="openmc")

    assert chain.source_format == "openmc_xml"
    assert chain.nuclide_names == ["A", "B"]
    assert math.isclose(chain.nuclides[0].half_life_s or 0.0, 10.0)


def test_openmc_xml_imports_neutron_fission_yield_blocks(tmp_path) -> None:
    path = tmp_path / "fission_chain.xml"
    path.write_text(
        """
<depletion_chain name="mini_fission">
  <nuclide name="U233" reactions="1">
    <reaction type="fission" rate_per_s="0.05" />
    <neutron_fission_yields>
      <energies>0.0253 500000.0</energies>
      <fission_yields energy="0.0253">
        <products>I135 Xe135</products>
        <data>0.063 0.002</data>
      </fission_yields>
      <fission_yields energy="500000.0">
        <products>I135 Xe135</products>
        <data>0.05 0.003</data>
      </fission_yields>
    </neutron_fission_yields>
  </nuclide>
  <nuclide name="I135" />
  <nuclide name="Xe135" />
</depletion_chain>
""".strip(),
        encoding="utf-8",
    )

    chain = load_depletion_chain(path, source_format="openmc")
    fission = chain.nuclides[0].reactions[0]

    assert fission.reaction_type == "fission"
    assert fission.fission_yields == {"I135": 0.063, "Xe135": 0.002}


def test_depletion_case_writes_native_artifacts(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_case_config(repo_root / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    config.data["depletion_solver"] = {
        "chain_path": "resources/depletion/tiny_thorium_chain.yaml",
        "steps": 1,
        "time_step_days": 0.01,
        "zones": ["core"],
    }
    bundle = create_result_bundle(tmp_path, config.name, "depletion-test")
    summary = {"metrics": {}}

    depletion = run_depletion_case(config, bundle, summary)

    assert depletion["status"] == "completed"
    assert depletion["isotope_count"] >= 3
    assert (bundle.root / "depletion_chain.json").exists()
    assert (bundle.root / "depletion_summary.json").exists()
    assert (bundle.root / "depletion_history.json").exists()
    assert (bundle.root / "depletion_matrix.npz").exists()
    assert (bundle.root / "depletion_matrix.schema.json").exists()
    assert (bundle.root / "depletion_matrix.summary.md").exists()
    assert depletion["atom_balance_basis"] in {"closed_chain_total_atoms_conserved", "not_applicable_open_system"}

    schema = json.loads((bundle.root / "depletion_matrix.schema.json").read_text(encoding="utf-8"))
    with np.load(bundle.root / "depletion_matrix.npz") as artifact:
        assert set(artifact.files) == set(schema["arrays"])
        assert schema["arrays"]["shape"]["shape"] == list(artifact["shape"].shape)
    human = (bundle.root / "depletion_matrix.summary.md").read_text(encoding="utf-8")
    assert "Flat inventory order is zone-major" in human
    assert "Closed-chain conservation" in human


def test_depletion_case_uses_chain_initial_atoms_when_material_is_absent(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    chain_path = tmp_path / "synthetic_chain.yaml"
    chain_path.write_text(
        """
name: synthetic_initial_inventory
nuclides:
  - name: Synthetic999
    initial_atoms: 123.0
""".strip(),
        encoding="utf-8",
    )
    config = load_case_config(repo_root / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    config.data["depletion_solver"] = {
        "chain_path": str(chain_path),
        "steps": 1,
        "time_step_s": 1.0,
    }
    bundle = create_result_bundle(tmp_path, config.name, "depletion-initial-test")

    depletion = run_depletion_case(config, bundle, {"metrics": {}})
    history = json.loads((bundle.root / "depletion_history.json").read_text(encoding="utf-8"))

    assert depletion["initial_total_atoms"] == pytest.approx(123.0)
    assert history["records"][0]["zones"]["core"]["inventory_atoms"]["Synthetic999"] == pytest.approx(123.0)


def test_open_depletion_case_marks_atom_balance_not_applicable(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    chain_path = tmp_path / "feed_removal_chain.yaml"
    chain_path.write_text(
        """
name: feed_removal_open_system
nuclides:
  - name: Synthetic999
    initial_atoms: 100.0
""".strip(),
        encoding="utf-8",
    )
    config = load_case_config(repo_root / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    config.data["depletion_solver"] = {
        "chain_path": str(chain_path),
        "steps": 1,
        "time_step_s": 10.0,
        "removal_rates_per_s": {"Synthetic999": 0.1},
        "feed_atoms_per_s": {"Synthetic999": 10.0},
    }
    bundle = create_result_bundle(tmp_path, config.name, "depletion-open-test")

    depletion = run_depletion_case(config, bundle, {"metrics": {}})

    assert depletion["final_total_atoms"] == pytest.approx(100.0)
    assert depletion["atom_balance_residual"] is None
    assert depletion["atom_balance_basis"] == "not_applicable_open_system"
    assert depletion["atom_balance_status"] == "open_system_sources_or_sinks_present"
