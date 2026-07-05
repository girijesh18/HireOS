"""
Configuration settings for the hiring agent application.
"""

# Global development mode flag.
# HireOS runs this vendored scorer per generated resume — keep it off so it does
# not write cache/ and resume_evaluations.csv into the vendored dir on each run.
DEVELOPMENT_MODE = False
