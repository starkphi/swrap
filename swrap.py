
import os
import pathlib
import subprocess
import tempfile
import textwrap

import click

import logging
import io


def get_logger_incl_getter():
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    log.propagate = False

    formatter = logging.Formatter("%(message)s")

    # Print to terminal
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Store in memory
    string_handler = StringHandler()
    string_handler.setFormatter(formatter)

    log.addHandler(console_handler)
    log.addHandler(string_handler)
    return log, string_handler.get_string

class StringHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.output = io.StringIO()

    def emit(self, record):
        message = self.format(record)
        self.output.write(message + "\n")

    def get_string(self):
        return self.output.getvalue()

    def close(self):
        self.output.close()
        super().close()

log, get_log_history = get_logger_incl_getter()

class SlurmScript:
    def __init__(self, script_path, backend, precision, is_dryrun):
        self.backend = backend
        self.backend_name, self.backend_xpu = backend.split("_")
        self.precision = precision
        self.script_path = pathlib.Path(script_path)
        assert self.script_path.is_file()
        with open(self.script_path, "r") as file:
            self.raw_script = file.read()

        self.icon_env_vars: dict = self.decide_precision()
        self.slurm_script = ""
        
        self.jobname_default = self.script_path.stem + "_" + self.precision + "_" + self.backend



    def prep_preamble_and_body(self) -> tuple[str, str]:
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
                             "balfrin": {"cpu": "pp-short", "gpu": "debug"},
                             "linard": {"cpu": "pp-short", "gpu": "debug"}}
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

    def decide_precision(self) -> dict:
        env_var_name = "ICON4PY_FLOAT_PRECISION"
        env_dict = dict((key,val) for key,val in os.environ.items() if "ICON4PY" in key)

        lines = self.raw_script.splitlines()
        float_precision_line = ""
        for i, line in enumerate(lines):
            if env_var_name in line:
                float_precision_line = lines.pop(i)
                self.raw_script = "\n".join(lines)
                break

        if self.precision:
            if env_var_name in env_dict or float_precision_line:
                log.info(f"rem: precision '{self.precision}' from command line argument overwrites existing env var or setting env vars in template script")
        elif env_var_name in env_dict:
            self.precision = env_dict[env_var_name]
            if float_precision_line:
                log.info(f"rem: existing env var ICON4PY_FLOAT_PRECISION={self.precision} from command line argument overwrites vars in template script")
        elif float_precision_line:
            start_prec_str = float_precision_line.find(env_var_name) + len(env_var_name) + 1
            self.precision = float_precision_line[start_prec_str:].replace('"','')
        else:
            self.precision = "double"
            log.info("rem: no precision setting provided nor found in env var or file -> set default: 'double'")
        env_dict[env_var_name] = self.precision
        return env_dict

    def get_icon4py_env_vars(self) -> str:
        # env_dict = os.environ.copy()
        # log.info("####")
        # log.info("\n".join([f"{key}: {val}" for key,val in env_dict.items() if "ICON4PY" in key]))
        # log.info("####")
        # if "ICON4PY_FLOAT_PRECISION" in env_dict.keys() and env_dict['ICON4PY_FLOAT_PRECISION'] != self.precision:
        #     log.info(f"Ignoring the 'ICON4PY_FLOAT_PRECISION={env_dict['ICON4PY_FLOAT_PRECISION']}' environment variable.", end=" ")
        #     env_dict['ICON4PY_FLOAT_PRECISION'] = self.precision
        #     log.info(f"Stick to swrap argument. -> 'ICON4PY_FLOAT_PRECISION={self.precision}'")

        # lines = [key + "=" + val for key,val in env_dict.items() if "ICON4PY" in key]
        lines = [key + "=" + val for key,val in self.icon_env_vars.items() if "ICON4PY" in key]
        if lines:
            return "\n".join(lines) + "\n"*2
        else:
            return ""

    def build_slurm_script(self) -> str:
        log.info("Build slurm script")
        preamble, body = self.prep_preamble_and_body()

        begin_msg = rf"""printf '%s\n%s\n' "This script was generated from: {self.script_path.name} with backend={self.backend} and precision={self.precision}" "Start script - $(date '+%d.%m.%Y %H:%M:%S')" | tee -a /dev/stderr    # write to both outputs""" + "\n"*3


        job_summary = textwrap.dedent(r"""
            status=$?

            # errors occured?
            if [[ $status -ne 0 ]]; then
                printf '\n\n\n%s\n\n' '** Exited with error. **' | tee -a /dev/stderr
            fi

            # job time summary
            if [ -n "$SLURM_JOB_ID" ]; then
                sacct -j $SLURM_JOB_ID --format=JobID,JobName,Elapsed,MaxRSS,State
                echo ""
            fi

            # end time to both outputs
            printf '\n%s\n' "Job completed  - $(date '+%d.%m.%Y %H:%M:%S')" | tee -a /dev/stderr
        """)

        self.slurm_script = preamble + begin_msg + body + job_summary
        return self.slurm_script

    def submit_script(self) -> None:
        if not self.slurm_script:
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

            log.info(result.stdout.strip())

        except subprocess.CalledProcessError as error:
            log.info("sbatch submission failed.")
            log.info("Exit code:", error.returncode)
            log.info("Error:", error.stderr.strip())

@click.command()
@click.argument("scriptname")
@click.option(
    "--precision",
    default="",
    type=click.Choice(["single", "mixed", "double"]),
    help="The floating-point precision to use, default=double.",
)
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
    precision: str,
    dry_run: bool,
    save: bool
):
    sls = SlurmScript(scriptname, backend, precision, dry_run)
    sls.build_slurm_script()

    collected_logs=get_log_history()

    if dry_run:

        str_for_pager = collected_logs + f"{'#'*10} begin slurm script:{'#'*10}\n\n" + sls.slurm_script

        click.echo_via_pager(str_for_pager)
        if save:
            in_str = input("Ok with that? [y]\n")
            if not "y" in in_str and in_str != "":
                log.info("Not saved.")
                return
    else:
        in_str = input("Run script? [y]/p (preview)\n")
        if "y" in in_str or in_str == "":
            sls.submit_script()
        elif "p" in in_str:
            click.echo_via_pager(sls.slurm_script)
        else:
            log.info("Not run.")

    if save:
        derived_name = sls.jobname_default + ".slurm"
        with open(derived_name, "w") as f:
            f.write(sls.slurm_script)
        log.info(f"Saved script to '{derived_name}'")




if __name__ == "__main__":
    main()

