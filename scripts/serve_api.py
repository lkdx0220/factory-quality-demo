"""Launch the demo API on loopback. Paths are local startup configuration."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=ROOT / "data/batches/demo-v1")
    parser.add_argument("--db", type=Path, default=ROOT / "local/demo.sqlite3")
    parser.add_argument("--schema", type=Path, default=ROOT / "schemas/csv-schema.json")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--enable-model", action="store_true", help="Enable DeepSeek planning using server-side DEEPSEEK_API_KEY")
    parser.add_argument("--model", choices=("deepseek-flash", "deepseek-v4-pro"), default="deepseek-flash")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        import uvicorn
        from mdlab_app.api import create_app
        from mdlab_app.model_adapter import ModelConfig
        from mdlab_app.errors import ProcessingError
    except ImportError:
        parser.exit(2, "API dependencies missing. Run: python -m pip install -r requirements-api.txt\n")
    try:
        config = ModelConfig.from_environment(args.model) if args.enable_model else ModelConfig()
    except ProcessingError:
        parser.exit(2, "Model configuration unavailable. Set server-side DEEPSEEK_API_KEY before enabling.\n")
    # No reload/workers: one local process, per-request SQLite connections.
    uvicorn.run(create_app(args.db, args.batch, args.schema, model_config=config), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
