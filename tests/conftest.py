from pathlib import Path
import os
import sys

# Keep tests fast/deterministic. Production enables ML by installing the service
# dependencies; tests validate the scoring pipeline with keyword/regex fallback.
os.environ.setdefault("POLSTOCK_ENABLE_ML_NLP", "0")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
