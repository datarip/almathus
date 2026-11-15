"""The one place the version number is written.

``pyproject.toml`` reads it from here, so the wheel, the sdist, the macOS
``Info.plist`` and the Windows installer all agree. ``packaging/build.py``
rewrites this file from the git tag before freezing; the committed value is the
version of the working tree.
"""

__version__ = "0.1.2"
