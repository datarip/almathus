# Flag icons

The SVGs in this directory are copied from [flag-icons](https://github.com/lipis/flag-icons),
version 7.5.0, which is released under the MIT License.

They are bundled (not downloaded at runtime) so the language picker and the
in-app language chip render identically on macOS, Windows and Linux — emoji
flags do not (Windows shows the two-letter code instead of a flag).

Only the flags for the languages in `languages.py` are included. A language
whose `flag` field is empty (Esperanto) has no country flag; its row and chip
fall back to text alone.
