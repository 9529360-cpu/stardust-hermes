"""Optional native kernels; absence keeps the pure-Python implementation."""
try:
    from stardust_native import assignment_candidate, sequence_ratios
except ImportError:
    assignment_candidate = None
    sequence_ratios = None
