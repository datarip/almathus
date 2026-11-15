"""Carry `webrtcvad` into the bundle without its contrib hook.

The module is installed from `webrtcvad-wheels`, whose *distribution* name
differs from the import name. The upstream contrib hook asks PyInstaller's
metadata database for a distribution called `webrtcvad`, does not find one, and
aborts the build. Nothing else in that hook matters here: the wrapper is a small
Python file over the `_webrtcvad` extension, so declaring the extension is the
whole job.
"""

hiddenimports = ["_webrtcvad"]
