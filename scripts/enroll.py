"""
Build embeddings from enrollment_audio/<tech_name>/*.wav and write
to enrolled_voices/embeddings.json.

Run once after dropping new technician audio, or re-run to refresh.
"""

import logging
import sys

from app import enrollment
from app.config import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def main() -> int:
    if not settings.enroll_dir.exists():
        print(f"Enroll dir missing: {settings.enroll_dir}", file=sys.stderr)
        return 1
    profiles = enrollment.build_embeddings()
    if not profiles:
        print("No profiles built. Drop audio in enrollment_audio/<tech_name>/ first.", file=sys.stderr)
        return 1
    print(f"Enrolled {len(profiles)} speakers: {', '.join(profiles)}")
    print(f"Wrote {settings.embeddings_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
