import subprocess
import sys
from pathlib import Path


def test_smoke_test():
    subprocess.run([sys.executable, "eval/scripts/gencode.py", "--help"], check=True)


def test_run_gencode_dummy_model(tmpdir, monkeypatch):
    sys.path.insert(0, str(Path("eval", "scripts")))
    import gencode

    monkeypatch.setattr(
        gencode,
        "read_from_hf_dataset",
        lambda split: [
            {
                "problem_id": "p-1",
                "required_dependencies": "numpy",
                "sub_steps": [
                    {
                        "step_number": "1",
                        "step_description_prompt": "Return the input.",
                        "step_background": "",
                        "function_header": "def identity(x):",
                        "return_line": "return x",
                    }
                ],
            }
        ],
    )
    output_dir = Path(str(tmpdir))
    prompt_dir = output_dir / "prompts"
    gencode.main(
        model="dummy",
        split="validation",
        output_dir=output_dir,
        prompt_dir=prompt_dir,
        with_background=False,
        temperature=0,
    )
    assert list(output_dir.rglob("*.py"))
