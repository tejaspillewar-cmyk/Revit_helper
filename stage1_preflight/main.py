"""
Stage 1 Pre-Flight Application Entry Point.

Launches the PyQt6 desktop application for CAD parsing,
3D visualization, and structural element validation.
"""

import logging
import sys
import os

# Ensure the parent directory is in sys.path so stage1_preflight can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from stage1_preflight.ui.main_window import MainWindow


def main():
    """Launch the Structural BIM Pre-Flight application."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Structural BIM Pre-Flight")
    app.setOrganizationName("CAD2Revit")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
