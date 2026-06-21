"""Agentic coding eval harness (SP-0).

Head-to-head pass@1 scoring of the crowe-logic control loop against a clean
reference plan->act->verify loop on the *same* model. Isolated from the
existing bench/ scoreboard; measurement-only (no changes to the live agent).
"""
