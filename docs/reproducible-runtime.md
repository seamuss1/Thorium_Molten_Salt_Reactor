# Reproducible runtime and deployment

`pyproject.toml` declares Python dependencies. `requirements.lock` is the reviewed,
hash-checked resolution for Python 3.11+ on Windows and Linux, including build and
test tools. CI, the CPU image, and the OpenMC image install this lock. The optional
Torch/XPU and Blender runtimes are not required by the CPU image or fast tests.

To update the lock deliberately, use uv 0.10.12:

```text
uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --generate-hashes --cache-dir .pip-cache/uv --output-file requirements.lock
```

For a repo-local Windows interpreter, install without another dependency resolution:

```powershell
.\.runtime-env\python.exe -m pip install --require-hashes -r requirements.lock --cache-dir .pip-cache
.\.runtime-env\python.exe -m pip install --no-deps --no-build-isolation -e .
```

The Conda environment files bootstrap interpreters and consume the same lock.
The optional Conda OpenMC environment is for exploration; its compiled dependency
closure is not locked. Use the Docker solver for reproducible solver execution.

## Release images

```text
docker compose build app web openmc
docker compose run --rm app python -m thorium_reactor.cli run example_pin --no-solver
docker compose up web
```

The release images contain the package, cases, reference data, and documentation.
The app/web image also compiles and includes the UI using `npm ci`. Only
`./results:/workspace/results` is mounted. A source checkout is not required at
runtime. Configure `THORIUM_REACTOR_ADMIN_EMAILS` and
`THORIUM_REACTOR_PROXY_SHARED_SECRET` for the authenticated web deployment; the
proxy must send the verified Access identity and matching shared-secret header.
The host port remains loopback-only unless an operator explicitly configures
remote publishing. No personal address has implicit administrator privileges.

Base images are pinned by digest. The solver base contains OpenMC 0.15.3 and its
bundled NNDC HDF5 data. `build-manifest.json` records installed package versions,
Python, source hash, lock/Dockerfile hashes, and the nuclear cross-section index
hash when present. Run provenance embeds this manifest and hashes current lock
inputs. The pinned base digest identifies the bundled data files; the index hash
alone does not verify externally replaced HDF5 files. Rebuild comparisons should
compare manifests, not expect identical image layers or timestamps.

Thermochimica, SaltProc, and Moltres services use the shared app image as adapters;
their external executables remain optional, separately provisioned integrations.
The CPU image supports the built-in geometry renderers; Blender and FFmpeg are
optional external render tools, not part of its supported dependency closure.

## Development wrappers

Windows `.cmd` wrappers explicitly combine `docker-compose.yml` with
`docker-compose.dev.yml`, which mounts the checkout. `Run-Web.cmd` always runs
`npm ci` and builds before starting Docker, including when `dist` already exists.
`-SkipUiBuild` is an explicit override and requires an existing bundle. Node 22 is
used by CI and the release build.

Both default and custom development ports bind `127.0.0.1`. `-HostName` controls
Uvicorn inside the container; `-PublishAddress` controls the host listener. A
nonloopback publish address requires `-RequireAccessIdentity` and both Access
configuration values above. Local development uses `developer@localhost`.

## Job failures and recovery

Each web process admits at most two active and eight queued jobs. A full queue
returns HTTP 503 with `Retry-After` before charging the daily quota. Invalid drafts
and unknown scenario names also fail before quota admission or bundle creation.
Numeric case inputs must be finite. Transients require a supported timestep of at
least 0.05 seconds, a positive duration covering one step, unique scenario names,
and numeric events within the duration.

An OS-held owner lease distinguishes running servers from abandoned metadata.
On startup, abandoned queued/running runs become `interrupted` once, with a saved
error and terminal event. Live owners are not reconciled. Graceful shutdown stops
phase process trees and finishes queued jobs as interrupted. Windows uses
[kill-on-close Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects);
Unix uses dedicated process groups. Container shutdown removes any remaining
container processes after the grace period.

Runs shows the saved failure and **Retry as new run**. Retrying uses the original
case snapshot and command choices, generates a new ID, and leaves the old bundle
untouched. Retry admission uses the same validation, capacity and daily quota.
CLI solver failures, unavailable solvers, and unexpected solver dry runs exit
nonzero and record a failed stage; explicit `--no-solver` dry runs exit zero.
