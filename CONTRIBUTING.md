# Contributing

Use a separate environment for each model adapter. Keep changes scoped to their
stage, preserve raw predictions and failure reasons, and document changes to
scoring, source scope, target identity or chassis assumptions.

For a controller change, run the offline example described in the README with a
fresh output directory. Scientific result claims require their actual model and
experimental evidence; a passing synthetic example is not that evidence.

Do not commit credentials, private histories, virtual environments, checkpoints,
third-party database exports or trajectories. Report issues with the command,
software version, sanitized configuration and a small non-sensitive input.
