# GPU Viability Experiments

This folder contains isolated performance probes for deciding whether the
reactor workflows should grow a production GPU backend.

The first target is an Intel Arc Pro B70 class workstation GPU:

- 32 GB GDDR6 VRAM
- 608 GB/s memory bandwidth
- 22.94 TFLOPS FP32
- Intel XMX engines and oneAPI / OpenCL / Intel Extension for PyTorch support

The scripts intentionally keep production code unchanged. They import the same
case configs, transient baseline builders, chemistry assumptions, depletion
assumptions, and precursor-group data used by `reactor transient-sweep`, then
run a dense-array version of the ensemble integrator.

## What This Tests

`gpu_viability_bench.py` focuses on the refactor shape that would make GPU
acceleration realistic:

- dense state arrays instead of Python lists of per-sample dictionaries,
- vectorized thermal, chemistry, depletion, and reactivity updates,
- vectorized six-group delayed-neutron precursor transport,
- backend abstraction for Intel XPU, CUDA/CuPy comparison, DPNP/SYCL, and CPU
  baselines,
- device-side random perturbation generation when the backend supports it,
- percentile/history reduction that transfers only summary data back to Python,
- chunk planning against the Arc Pro B70 32 GB VRAM target.
- raw vector-triad bandwidth and porous-core stencil probes for future spatial
  thermal-hydraulics / precursor-transport refactors.

`opencl_arc_b70_stress.py` is a lower-level device stress test. It talks to
`OpenCL.dll` through `ctypes`, so it can exercise the Intel Arc Pro B70 driver
even when higher-level Python packages such as PyTorch XPU, DPNP, or CuPy are
not installed.

`simulation_class_probes.py` covers the broader simulation roadmap with
explicitly labeled proxy workloads:

- 1D loop hydraulic-network transients
- 2D porous-core local-thermal-nonequilibrium thermal response
- delayed-neutron precursor advection-diffusion PDEs
- coupled multigroup neutron-diffusion-style updates
- Monte Carlo particle-transport-shaped random-walk workloads
- depletion-chain / Bateman-style inventory updates
- local CFD-shaped convection-diffusion flow and temperature updates

These are GPU usability tests, not validated reactor analyses. Each JSON result
states the physical fidelity, production mapping, missing physics, and
validation status for the probe.

## Quick Start

From the repository root:

```cmd
experiments\gpu_viability\Run-GPU-Viability.cmd --help
experiments\gpu_viability\Run-GPU-Viability.cmd --backend auto --case immersed_pool_reference --scenario partial_heat_sink_loss --samples 262144
```

For a broader sweep:

```cmd
experiments\gpu_viability\Run-GPU-Viability.cmd --profile-all --sample-grid 65536,262144,1048576 --rng-mode device
```

The default run also executes two standalone kernel probes. To time only the
transient refactor shape:

```cmd
experiments\gpu_viability\Run-GPU-Viability.cmd --backend torch-xpu --kernel-probes none
```

To confirm the B70 is visible through OpenCL:

```cmd
experiments\gpu_viability\Run-OpenCL-Arc-Stress.cmd --list-devices
```

To make the B70 work hard with multi-GB buffers:

```cmd
experiments\gpu_viability\Run-OpenCL-Arc-Stress.cmd --device-contains "Arc(TM) Pro B70" --ramp-gb 1,4,8,16 --seconds-per-phase 20 --inner-iters 4 --compute-mix
```

To run the simulation-class usability suite on the B70:

```cmd
experiments\gpu_viability\Run-GPU-Simulation-Classes.cmd --backend torch-xpu --steps 32 --grid 192 --network-samples 32768 --particles 1048576 --depletion-samples 262144
```

For a CPU comparison:

```cmd
experiments\gpu_viability\Run-GPU-Simulation-Classes.cmd --backend numpy --steps 32 --grid 192 --network-samples 32768 --particles 1048576 --depletion-samples 262144
```

For a production-faithfulness check before chasing maximum throughput:

```cmd
experiments\gpu_viability\Run-GPU-Viability.cmd --backend numpy --samples 2048 --rng-mode production --validate-production --validation-samples 256
```

Results are written under `.tmp/gpu-viability/` by default.

## Intel Arc Pro B70 Notes

Prefer `--backend torch-xpu` when a PyTorch build with Intel XPU support is
installed. `--backend dpnp` is also useful for oneAPI/SYCL array testing when
available. The script leaves a few GB of the 32 GB card free by default via
`--target-vram-gb 28`.

Useful environment settings for Intel GPU experiments:

```cmd
set ONEAPI_DEVICE_SELECTOR=level_zero:gpu
set SYCL_CACHE_PERSISTENT=1
set ZE_ENABLE_PCI_ID_DEVICE_ORDER=1
```

The wrappers set `PYTORCH_ENABLE_XPU_FALLBACK=0` unless you override it before
launching. Keep that default for GPU-usability measurements so unsupported XPU
operators fail fast instead of silently running on the CPU. If you deliberately
want a compatibility run, set `PYTORCH_ENABLE_XPU_FALLBACK=1`; the JSON output
records the fallback state under `host.environment`.

The wrapper uses the repo-local `.runtime-env` and adds its `Library\bin`
directory to `PATH`, which is required for the bundled numeric DLLs on Windows.
