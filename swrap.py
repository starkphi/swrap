
import os
import pathlib
import subprocess
import tempfile
import textwrap

import click

class SlurmScript:
    def __init__(self, script_path, backend, is_dryrun):
        self.backend = backend
        self.backend_name, self.backend_xpu = backend.split("_")
        self.script_path = pathlib.Path(script_path)
        assert self.script_path.is_file()
        with open(self.script_path, "r") as file:
            self.raw_script = file.read()
        self.slurm_script = None

        self.jobname_default = self.script_path.stem + "_" + self.backend

    def prep_preamble_and_body(self) -> str:
        sbatch_lines, reg_lines = [],[]
        [(reg_lines, sbatch_lines)[line[:7] == "#SBATCH"].append(line) for line in self.raw_script.split("\n")]
        shebang = "" if reg_lines[0][:3] != "#!/" else reg_lines.pop(0)
        body = "\n".join(reg_lines)
        body = body.replace("${BACKEND_NAME}", "dace").replace("${BACKEND_XPU}", "cpu")

        sbatch_dict = self.dict_from_sbatch_lines(sbatch_lines)
        sbatch_combined = self.set_defaults(sbatch_dict)
        sbatch_block = self.make_sbatch_block(sbatch_combined)
        icon4py_env_vars = self.get_icon4py_env_vars()
        preamble = shebang + "\n" + sbatch_block + "\n"*3 + icon4py_env_vars

        return preamble, body

    def dict_from_sbatch_lines(self, lines):
        return dict(line.split("--")[1].split("=") for line in lines)

    def set_defaults(self, sbatch_dict: dict):
        sbatch_dict.setdefault("uenv", os.environ["SBATCH_UENV"])
        sbatch_dict.setdefault("view", os.environ["SBATCH_UENV_VIEW"])
        sbatch_dict.setdefault("job-name", self.jobname_default)
        sbatch_dict.setdefault("output", sbatch_dict["job-name"] + "_%j.out")
        sbatch_dict.setdefault("error", sbatch_dict["job-name"] + "_%j.err")
        sbatch_dict.setdefault("time", "00:30:00")
        sbatch_dict.setdefault("nodes", 1)
        sbatch_dict.setdefault("ntasks", 1)
        sbatch_dict.setdefault("cpus-per-task", 16 if self.backend_xpu == "gpu" else 64)


        defaultpartitions = {"santis": {"cpu": "debug", "gpu": "debug"},
                             "balfrin": {"cpu": "debug", "gpu": "normal"}}
        host = os.environ["HOSTNAME"].split("-")[0]
        sbatch_dict.setdefault("partition", defaultpartitions[host][self.backend_xpu])
        if self.backend_xpu == "gpu":
            sbatch_dict.setdefault("gres", "gpu:1")
        if host == "santis":
            sbatch_dict.setdefault("account", "cwd01")
        elif self.backend_xpu != "cpu":
            raise ValueError(f"'{self.backend_xpu}' is no valid processing unit.")
        return sbatch_dict

    def make_sbatch_block(self, sbatch_dict: dict) -> str:
        return "\n".join(["#SBATCH --" + key + "=" + str(value) for (key,value) in sbatch_dict.items()])

    def get_icon4py_env_vars(self) -> str:
        lines = [key + "=" + val for key,val in os.environ.items() if "ICON4PY" in key]
        # lines.append(f"\nBACKEND_XPU={self.backend_xpu}")
        # lines.append(f"BACKEND_NAME={self.backend_name}")
        if lines:
            return "\n".join(lines) + "\n"*2
        else:
            ""

    def build_slurm_script(self) -> str:
        print("Build slurm script")
        preamble, body = self.prep_preamble_and_body()

        begin_msg = rf"""printf '%s\n%s\n' "This script was generated from: {self.script_path.name} with backend={self.backend}" "Start script - $(date '+%d.%m.%Y %H:%M:%S')" | tee /dev/stderr    # write to both outputs""" + "\n"*3


        job_summary = textwrap.dedent(r"""
            status=$?

            # errors occured?
            if [[ $status -ne 0 ]]; then
                printf '\n\n\n%s\n\n' '** Exited with error. **' | tee /dev/stderr
            fi

            # job time summary
            if [ -n "$SLURM_JOB_ID" ]; then
                sacct -j $SLURM_JOB_ID --format=JobID,JobName,Elapsed,MaxRSS,State
                echo ""
            fi

            # end time to both outputs
            printf '\n%s\n' "Job completed  - $(date '+%d.%m.%Y %H:%M:%S')" | tee /dev/stderr
        """)

        self.slurm_script = preamble + begin_msg + body + job_summary
        return self.slurm_script

    def submit_script(self) -> None:
        if self.slurm_script is None:
            self.build_slurm_script()
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".slurm") as f:
                f.write(self.slurm_script)
                f.flush()

                result = subprocess.run(
                    ["sbatch", f.name],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            print(result.stdout.strip())

        except subprocess.CalledProcessError as error:
            print("sbatch submission failed.")
            print("Exit code:", error.returncode)
            print("Error:", error.stderr.strip())

@click.command()
@click.argument("scriptname")
@click.option(
    "--backend",
    default="dace_cpu",
    type=click.Choice(["dace_cpu", "dace_gpu", "gtfn_cpu", "gtfn_gpu"]),
    help="The GT4Py backend to use, default=dace_cpu.",
)
@click.option(
    "--dry_run",
    is_flag=True,
    default=False,
    help="With this marker set, the generated script is shown.",
)
@click.option(
    "--save",
    is_flag=True,
    default=False,
    help="With this marker set, the generated script is saved to disk.",
)
def main(
    scriptname: str,
    backend: str,
    dry_run: bool,
    save: bool
):
    sls = SlurmScript(scriptname, backend, dry_run)
    sls.build_slurm_script()

    if dry_run:
        click.echo_via_pager(sls.slurm_script)
    else:
        sls.submit_script()

    if save:
        derived_name = sls.jobname_default + ".slurm"
        with open(derived_name, "w") as f:
            f.write(sls.slurm_script)
        print(f"Saved script to '{derived_name}'")




if __name__ == "__main__":
    main()

