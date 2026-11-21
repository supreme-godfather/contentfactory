from pathlib import Path

from app.config import get_settings
from app.voice_benchmark import run_voice_benchmark


def main() -> None:
    result = run_voice_benchmark(Path(get_settings().media_root))
    print(f"Benchmark created: {result['benchmark_id']}")
    print(f"Private files: {result['output_dir']}")


if __name__ == "__main__":
    main()
