"""flow-pilot: YAML-defined AI automation workflows with a safe mock runner."""

from .engine import RunResult, run_workflow
from .workflow import Workflow, load_workflow

__all__ = ["Workflow", "load_workflow", "run_workflow", "RunResult"]
__version__ = "0.1.0"
