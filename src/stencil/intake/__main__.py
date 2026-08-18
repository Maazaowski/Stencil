"""Entry point for: python -m stencil.intake"""

from stencil.config import settings
from stencil.intake.watcher import start_profile_watcher

if __name__ == "__main__":
    print("Stencil Profile Watcher")
    print(f"  Global inbound: {settings.inbound_dir}")
    print(f"  Profiles dir:   {settings.supplier_profiles_dir}")
    settings.ensure_directories()
    start_profile_watcher(blocking=True)
