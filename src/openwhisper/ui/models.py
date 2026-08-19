"""Compatibility imports for the temporary Qt parity shell.

New shell-neutral code must import these contracts from :mod:`openwhisper.contracts`.
"""

from openwhisper.contracts import AppController, AppSettings, HistoryRow, ProviderOption

__all__ = ["AppController", "AppSettings", "HistoryRow", "ProviderOption"]
