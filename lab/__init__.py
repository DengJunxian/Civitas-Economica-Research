"""Civitas Lab: course-driven teaching layer over the standard experiment service.

The lab never simulates by itself. It validates teaching scenario documents,
compiles the supported subset into core ``ExperimentConfig`` objects, runs them
through ``ExperimentRunner`` and reads the resulting archives for comparison.
"""
