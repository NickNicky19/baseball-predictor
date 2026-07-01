#!/usr/bin/env python3
"""Launcher for the Baseball Predictor GUI."""

import importlib.util
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load gui.py directly from project root
spec = importlib.util.spec_from_file_location(
    "gui_module", 
    os.path.join(project_root, "gui.py")
)
gui_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui_module)

BaseballPredictorGUI = gui_module.BaseballPredictorGUI


def main():
    import tkinter as tk

    root = tk.Tk()
    app = BaseballPredictorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
