"""Allow ``python -m aria_cli`` as an equivalent to the ``aria`` command."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
