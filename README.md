# swrap

Turns a plain shell script (`*.sw`) into a complete SLURM batch script and submits it.
Adds the `#SBATCH` preamble, ICON4Py environment variables, start/end timestamps and a
`sacct` job summary — so the template only contains the actual work.

## Usage

```bash
swrap SCRIPT [--backend=...] [--precision=...] [--dry_run] [--save] [-f/--force]
```

| Option | Values | Default |
| --- | --- | --- |
| `--backend` | `dace_cpu`, `dace_gpu`, `gtfn_cpu`, `gtfn_gpu` | `dace_cpu` |
| `--precision` | `single`, `mixed`, `double` | `double` |
| `--dry_run` | show generated script instead of submitting | off |
| `--save` | write the script as `<job-name>.slurm` | off |
| `--force` | skip confirmation prompts | off |

The `swrap` wrapper runs `swrap.py` inside the uenv defined there (`SBATCH_UENV`,
`SBATCH_UENV_VIEW`, `ICON4PY_PATH`).

## Template format

Any line starting with `#SBATCH` or `#SWRAP_` is treated as preamble and moved to the top of
the generated script; everything else is kept verbatim as the job body.

- `#SBATCH --key=value` — overrides a default (see below).
- `#SWRAP_NO_BACKEND` — do not emit `BACKEND*` variables.
- `#SWRAP_NO_PRECISION` — do not emit `ICON4PY_FLOAT_PRECISION`.

## Defaults

Filled in only if not given in the template:

| Key | Default |
| --- | --- |
| `uenv` / `view` | `$SBATCH_UENV` / `$SBATCH_UENV_VIEW` (`none` disables `view`) |
| `job-name` | `<script stem>_<precision>_<backend>` |
| `output` / `error` | `<job-name>_%j.out` / `.err` |
| `time` | `00:30:00` |
| `nodes` / `ntasks` | `1` / `1` |
| `cpus-per-task` | `16` (gpu) / `64` (cpu) |
| `partition` | per host (`santis`, `balfrin`, `linard`) and cpu/gpu |
| `gres` | `gpu:1` for gpu backends |
| `account` | `cwd01` on `santis` |

## Precision resolution

In decreasing priority: `--precision` argument → existing `ICON4PY_FLOAT_PRECISION` env var →
`ICON4PY_FLOAT_PRECISION=...` line in the template (which is removed from the body) →
`double`.

All `ICON4PY_*` environment variables of the calling shell are exported into the job, together
with `BACKEND`, `BACKEND_NAME` and `BACKEND_XPU`.

## Example

```bash
swrap nox.sw --backend=dace_cpu --precision=single --save -f
```
generates and submits `nox_single_dace_cpu.slurm`.


```bash
swrap nox.sw --backend=dace_cpu --precision=single --save --dry_run
```
shows a preview of the slurm script and asks before saving the slurm script.
