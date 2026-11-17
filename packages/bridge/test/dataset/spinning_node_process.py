"""
A process that spins a node for an empty block and then ends.
"""

from __future__ import annotations

from cramera_bridge.ros_node import SpinningNode

# %% Process

if __name__ == "__main__":
    with SpinningNode("cramera_spinning_node_process").running():
        pass
