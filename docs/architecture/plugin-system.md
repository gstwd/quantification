# Plugin System

Plugins implement a common contract defined in `apps/api/src/quant_etf_api/plugins/base.py`.

A plugin must declare:
- strategy identity and version
- frequency and asset scope
- required inputs
- factor definitions
- signal definition
- context preparation
- universe execution
- explanation payload

Built-in plugins live under `apps/api/src/quant_etf_api/plugins/builtins/`.
