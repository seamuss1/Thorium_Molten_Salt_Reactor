from pathlib import Path

import pytest

from thorium_reactor.msd_tp import clear_database_cache


@pytest.fixture
def synthetic_msd_tp_data_dir(tmp_path: Path) -> str:
    clear_database_cache()

    def direct_record(
        formula: str,
        row_id: str,
        molecular_weight: float,
        composition: str,
        density_a: float,
        density_b: float,
        viscosity_cp: float,
    ) -> str:
        row = ["----"] * 34
        row[0] = formula
        row[1] = row_id
        row[2] = str(molecular_weight)
        row[3] = composition
        row[10] = str(density_a)
        row[11] = str(density_b)
        row[12] = "900-1100"
        row[13] = "5"
        row[14] = "Synthetic public-safe density fixture"
        row[15] = str(viscosity_cp)
        row[16] = "0"
        row[20] = "900-1100"
        row[21] = "10"
        row[22] = "Synthetic public-safe viscosity fixture"
        return ",".join(row)

    direct_rows = [
        ",".join(f"h{index}" for index in range(34)),
        ",".join(f"u{index}" for index in range(34)),
        direct_record("A", "1", 10.0, "1.0", 2.0, 0.0, 1.0),
        direct_record("B", "2", 20.0, "1.0", 4.0, 0.0, 4.0),
        direct_record("A-B", "101", 17.5, "0.25-0.75", 3.0, 0.0005, 2.0),
    ]
    (tmp_path / "Molten_Salt_Thermophysical_Properties.csv").write_text(
        "\n".join(direct_rows),
        encoding="utf-8",
    )
    (tmp_path / "Molten_Salt_Thermophysical_Properties_rho_RK.csv").write_text(
        "\n".join(
            [
                "c1,c2,A1,B1,A2,B2,A3,B3,Tmin,Tmax,reference",
                "A,B,0,0,0,0,0,0,900,1100,Synthetic public-safe RK density fixture",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "Molten_Salt_Thermophysical_Properties_mu_RK.csv").write_text(
        "\n".join(
            [
                "c1,c2,A1,B1,C1,A2,B2,C2,A3,B3,C3,Tmin,Tmax,reference",
                "A,B,0,0,0,0,0,0,0,0,0,900,1100,Synthetic public-safe RK viscosity fixture",
            ]
        ),
        encoding="utf-8",
    )
    return str(tmp_path)
