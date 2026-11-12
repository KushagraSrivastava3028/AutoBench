"""Task specification: parse, hash and validate a task directory.

Autobench's format is a **superset of the Harbor task layout**, read unmodified:

    task.toml  instruction.md  environment/  tests/  solution/

which buys an existing corpus instead of starting from zero tasks. Every key
Autobench adds is optional and defaulted.
"""

from autobench.taskspec.hashing import hash_task_directory
from autobench.taskspec.loader import TaskDirectory, load_task
from autobench.taskspec.schema import TaskSpec

__all__ = ["TaskDirectory", "TaskSpec", "hash_task_directory", "load_task"]
