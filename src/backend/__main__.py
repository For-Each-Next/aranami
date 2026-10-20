"""Run the shared backend hub for wiki and website updates.

Invoke ``python -m backend`` with sources on PYTHONPATH. Pass ``--dry``
for wiki previews without production website collection.
"""

from backend.hub import main

if __name__ == "__main__":
    main()
