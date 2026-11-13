"""Run the shared runtime hub for wiki and website updates.

Invoke ``python -m runtime`` with sources on PYTHONPATH. Pass ``--dry``
for wiki previews without production website collection.
"""

from runtime.hub import main

if __name__ == "__main__":
    main()
