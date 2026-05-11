from pathlib import Path

from setuptools import setup


ROOT = Path(__file__).parent


def read_requirements() -> list[str]:
    requirements_path = ROOT / "requirements.txt"
    if not requirements_path.exists():
        return []

    requirements: list[str] = []
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirements.append(line)
    return requirements


setup(
    name="android-system-analyzer",
    version="0.1.0",
    description="Android UI element extraction and reporting toolkit",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.10",
    install_requires=read_requirements(),
    scripts=[
        "scripts/current_screen_report.py",
        "scripts/diff_captures.py",
        "scripts/run_capture_pipeline.py",
    ],
)
