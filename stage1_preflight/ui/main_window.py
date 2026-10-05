"""
Main Window: Assembles the PyQt6 application with PyVista 3D viewport,
exception inspector, property panel, and toolbar.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pyvista as pv
from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor

from stage1_preflight.models.schemas import (
    BeamElement,
    ColumnElement,
    SlabElement,
    StructuralElementBase,
    StructuralLevel,
    StructuralProject,
    WallElement,
)
from stage1_preflight.parsers.dxf_parser import DXFParser
from stage1_preflight.ui.exception_panel import ExceptionPanel
from stage1_preflight.ui.property_panel import PropertyPanel
from stage1_preflight.utils.constants import COLORS_FLOAT, DEFAULT_FLOOR_HEIGHT
from stage1_preflight.visualization.viewport_3d import StructuralViewport

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window for the Structural BIM Pre-Flight system.

    Layout:
    ┌─────────────────────────────────────────────────────┐
    │  [Toolbar: Load DXF | Settings | Export JSON]       │
    ├───────────────────────────┬─────────────────────────┤
    │                           │  Exception Inspector    │
    │   PyVista 3D Canvas       ├─────────────────────────┤
    │                           │  Element Properties     │
    └───────────────────────────┴─────────────────────────┘
    │  [Status Bar]                                       │
    └─────────────────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Structural BIM Pre-Flight — CAD → Revit")
        self.setMinimumSize(1400, 900)

        # State
        self._project: Optional[StructuralProject] = None
        self._current_level: Optional[StructuralLevel] = None
        self._viewport = StructuralViewport()
        self._selected_id: Optional[str] = None

        # Settings
        self._floor_height = DEFAULT_FLOOR_HEIGHT
        self._base_elevation = 0.0
        self._level_name = "L01"

        self._init_style()
        self._init_ui()
        self._init_menu()
        self._init_toolbar()
        self._init_statusbar()

    def _init_style(self) -> None:
        """Apply dark theme stylesheet."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1E1E2E;
            }
            QWidget {
                background-color: #1E1E2E;
                color: #CDD6F4;
                font-family: 'Segoe UI', 'Inter', sans-serif;
            }
            QMenuBar {
                background-color: #181825;
                color: #CDD6F4;
                border-bottom: 1px solid #313244;
            }
            QMenuBar::item:selected {
                background-color: #45475A;
            }
            QMenu {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
            }
            QMenu::item:selected {
                background-color: #45475A;
            }
            QToolBar {
                background-color: #181825;
                border-bottom: 1px solid #313244;
                spacing: 8px;
                padding: 4px 8px;
            }
            QStatusBar {
                background-color: #181825;
                color: #A6ADC8;
                border-top: 1px solid #313244;
            }
            QDockWidget {
                titlebar-close-icon: none;
                titlebar-normal-icon: none;
                color: #CDD6F4;
            }
            QDockWidget::title {
                background-color: #313244;
                text-align: left;
                padding: 6px;
                border-bottom: 1px solid #45475A;
            }
            QSplitter::handle {
                background-color: #45475A;
                width: 2px;
            }
            QGroupBox {
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 4px;
                margin-top: 8px;
                padding: 8px;
                padding-top: 16px;
                font-size: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
            }
        """)

    def _init_ui(self) -> None:
        """Build the main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main splitter: 3D viewport | sidebar
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 3D PyVista Canvas ---
        self._plotter = QtInteractor(self)
        self._plotter.set_background("#11111B")
        self._plotter.add_axes()

        # Enable mesh picking for interaction
        self._plotter.enable_mesh_picking(
            callback=self._on_mesh_picked,
            left_clicking=True,
            show=False,
            show_message=False,
        )

        splitter.addWidget(self._plotter)

        # --- Right sidebar ---
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Project settings
        settings_group = QGroupBox("Project Settings")
        settings_form = QFormLayout(settings_group)

        self._level_input = QLineEdit(self._level_name)
        self._level_input.setStyleSheet("""
            QLineEdit {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
        """)
        settings_form.addRow("Level:", self._level_input)

        self._elevation_spin = QDoubleSpinBox()
        self._elevation_spin.setRange(-100000, 500000)
        self._elevation_spin.setValue(self._base_elevation)
        self._elevation_spin.setSuffix(" mm")
        self._elevation_spin.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
        """)
        settings_form.addRow("Base Elevation:", self._elevation_spin)

        self._unit_label = QLabel("Unknown")
        self._unit_label.setStyleSheet("color: #A6E3A1; font-size: 11px; font-weight: bold;")
        settings_form.addRow("DXF Units:", self._unit_label)

        self._height_spin = QDoubleSpinBox()
        self._height_spin.setRange(1000, 20000)
        self._height_spin.setValue(self._floor_height)
        self._height_spin.setSuffix(" mm")
        self._height_spin.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
        """)
        settings_form.addRow("Floor Height:", self._height_spin)

        sidebar_layout.addWidget(settings_group)

        # Exception Inspector
        self._exception_panel = ExceptionPanel()
        self._exception_panel.element_selected.connect(self._on_element_selected)
        sidebar_layout.addWidget(self._exception_panel, stretch=1)

        # Property Panel
        self._property_panel = PropertyPanel()
        self._property_panel.element_updated.connect(self._on_element_updated)
        self._property_panel.verified_toggled.connect(self._on_verified_toggled)
        sidebar_layout.addWidget(self._property_panel, stretch=1)

        splitter.addWidget(sidebar)
        splitter.setSizes([900, 450])

        main_layout.addWidget(splitter)

    def _init_menu(self) -> None:
        """Create the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        load_action = QAction("&Load DXF...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self._load_dxf)
        file_menu.addAction(load_action)

        export_action = QAction("&Export JSON...", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._export_json)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        reset_cam = QAction("Reset Camera", self)
        reset_cam.setShortcut(QKeySequence("R"))
        reset_cam.triggered.connect(self._reset_camera)
        view_menu.addAction(reset_cam)

        toggle_edges = QAction("Toggle Edges", self)
        toggle_edges.setShortcut(QKeySequence("E"))
        toggle_edges.triggered.connect(self._toggle_edges)
        view_menu.addAction(toggle_edges)

    def _init_toolbar(self) -> None:
        """Create the main toolbar."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Load DXF
        load_btn = QAction("📂 Load DXF", self)
        load_btn.setToolTip("Load a DXF framing plan")
        load_btn.triggered.connect(self._load_dxf)
        toolbar.addAction(load_btn)

        toolbar.addSeparator()

        # Reprocess
        reprocess_btn = QAction("🔄 Reprocess", self)
        reprocess_btn.setToolTip("Re-run parsing with current settings")
        reprocess_btn.triggered.connect(self._reprocess)
        toolbar.addAction(reprocess_btn)

        toolbar.addSeparator()

        # Export JSON
        export_btn = QAction("💾 Export JSON", self)
        export_btn.setToolTip("Export validated structural data to JSON")
        export_btn.triggered.connect(self._export_json)
        toolbar.addAction(export_btn)

    def _init_statusbar(self) -> None:
        """Create the status bar."""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready — Load a DXF file to begin")

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setMaximumHeight(16)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #45475A;
                border-radius: 3px;
                background-color: #313244;
                text-align: center;
                font-size: 10px;
                color: #CDD6F4;
            }
            QProgressBar::chunk {
                background-color: #89B4FA;
                border-radius: 2px;
            }
        """)
        self._statusbar.addPermanentWidget(self._progress)

    # -------------------------------------------------------------------
    # Core actions
    # -------------------------------------------------------------------

    def _load_dxf(self) -> None:
        """Open a DXF file and process it."""
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open DXF Framing Plan",
            "",
            "DXF Files (*.dxf);;All Files (*)",
        )
        if not filepath:
            return

        self._process_file(filepath)

    def _process_file(self, filepath: str) -> None:
        """Parse, analyze, and visualize a DXF file."""
        self._statusbar.showMessage(f"Loading: {filepath}")
        self._progress.setVisible(True)
        self._progress.setValue(10)

        QApplication.processEvents()

        try:
            # Update settings from UI
            self._level_name = self._level_input.text() or "L01"
            self._base_elevation = self._elevation_spin.value()
            self._floor_height = self._height_spin.value()

            # Stage 1: Parse DXF
            self._progress.setValue(20)
            self._statusbar.showMessage("Parsing DXF...")
            QApplication.processEvents()

            parser = DXFParser(filepath)
            cad_data = parser.parse()

            # Update unit label
            if cad_data.unit_scale == 1.0:
                self._unit_label.setText("mm (1.0)")
            elif cad_data.unit_scale == 25.4:
                self._unit_label.setText("inches (25.4)")
            elif cad_data.unit_scale == 1000.0:
                self._unit_label.setText("metres (1000.0)")
            else:
                self._unit_label.setText(f"scale: {cad_data.unit_scale}")

            # Ask user to map layers
            from stage1_preflight.ui.layer_mapping_dialog import LayerMappingDialog
            dialog = LayerMappingDialog(cad_data.layers, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._progress.setVisible(False)
                self._statusbar.showMessage("Loading cancelled by user.")
                return
            
            layer_mapping = dialog.get_mapping()

            # Stage 2: Robust Pipeline Analysis & Recognition
            self._progress.setValue(50)
            self._statusbar.showMessage("Running robust CAD pipeline...")
            QApplication.processEvents()

            from stage1_preflight.parsers.pipeline_adapter import parse_with_robust_pipeline
            level = parse_with_robust_pipeline(
                filepath=filepath,
                layer_mapping=layer_mapping,
                floor_height=self._floor_height,
                base_elevation=self._base_elevation,
                level_name=self._level_name,
            )
            self._current_level = level

            # Build project
            self._project = StructuralProject(
                project_name=Path(filepath).stem,
                source_file=filepath,
                levels=[level],
            )

            # Stage 4: Build 3D meshes
            self._progress.setValue(80)
            self._statusbar.showMessage("Building 3D model...")
            QApplication.processEvents()

            self._render_level(level)

            # Stage 5: Update UI panels
            self._progress.setValue(90)
            self._exception_panel.update_data(level)
            self._property_panel.clear()

            self._progress.setValue(100)
            self._statusbar.showMessage(
                f"Loaded: {level.total_elements} elements "
                f"({len(level.beams)} beams, {len(level.columns)} columns, "
                f"{len(level.slabs)} slabs, {len(level.walls)} walls) — "
                f"{len(level.flagged_elements)} flagged"
            )

        except Exception as e:
            logger.exception(f"Error processing {filepath}")
            QMessageBox.critical(
                self, "Error", f"Failed to process DXF:\n{e}"
            )
            self._statusbar.showMessage("Error — see details above")

        finally:
            QTimer.singleShot(1500, lambda: self._progress.setVisible(False))

    def _render_level(self, level: StructuralLevel) -> None:
        """Render all elements in the 3D viewport."""
        self._plotter.clear()

        mesh_data = self._viewport.build_meshes(level)

        for elem_id, mesh, color in mesh_data:
            if mesh is not None and mesh.n_points > 0:
                opacity = color[3] if len(color) > 3 else 1.0
                rgb = color[:3]
                try:
                    actor = self._plotter.add_mesh(
                        mesh,
                        color=rgb,
                        opacity=opacity,
                        show_edges=True,
                        edge_color=(0.3, 0.3, 0.4),
                        name=elem_id,
                        pickable=True,
                    )
                except Exception as e:
                    logger.warning(f"Could not render {elem_id}: {e}")

        # Add floor grid
        try:
            grid = pv.Plane(
                center=(0, 0, self._base_elevation),
                direction=(0, 0, 1),
                i_size=50000,
                j_size=50000,
                i_resolution=20,
                j_resolution=20,
            )
            self._plotter.add_mesh(
                grid,
                color=(0.15, 0.15, 0.2),
                opacity=0.3,
                show_edges=True,
                edge_color=(0.2, 0.2, 0.3),
                name="grid",
                pickable=False,
            )
        except Exception:
            pass

        self._plotter.reset_camera()

    def _reprocess(self) -> None:
        """Re-run processing with updated settings."""
        if self._project and self._project.source_file:
            self._process_file(self._project.source_file)

    def _export_json(self) -> None:
        """Export the validated structural data to JSON."""
        if not self._project:
            QMessageBox.warning(
                self, "No Data", "Load a DXF file first."
            )
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export Structural JSON",
            f"{self._project.project_name}_structural.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not filepath:
            return

        try:
            self._project.to_json_file(filepath)
            self._statusbar.showMessage(
                f"Exported {self._project.total_elements} elements to {filepath}"
            )
            QMessageBox.information(
                self,
                "Export Complete",
                f"Structural data exported successfully.\n\n"
                f"File: {filepath}\n"
                f"Elements: {self._project.total_elements}",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export JSON:\n{e}"
            )

    # -------------------------------------------------------------------
    # Interaction handlers
    # -------------------------------------------------------------------

    def _on_mesh_picked(self, mesh: pv.PolyData) -> None:
        """Handle raycasting left-click on the 3D viewport."""
        if mesh is None:
            return

        elem_id = self._viewport.get_element_id_by_mesh(mesh)
        if elem_id:
            self._select_element(elem_id)

    def _on_element_selected(self, elem_id: str) -> None:
        """Handle element selection from the exception panel."""
        self._select_element(elem_id)

    def _select_element(self, elem_id: str) -> None:
        """Select and highlight an element in both viewport and panels."""
        self._selected_id = elem_id
        element = self._viewport.element_map.get(elem_id)

        if element:
            self._property_panel.show_element(element)
            self._statusbar.showMessage(
                f"Selected: {elem_id} ({element.element_type.value})"
            )

            # Re-render with highlight
            if self._current_level:
                self._render_level(self._current_level)
                # Highlight the selected mesh
                mesh = self._viewport.meshes.get(elem_id)
                if mesh and mesh.n_points > 0:
                    try:
                        self._plotter.add_mesh(
                            mesh,
                            color=COLORS_FLOAT["selected"][:3],
                            opacity=0.9,
                            show_edges=True,
                            edge_color=(1.0, 1.0, 0.0),
                            line_width=3,
                            name=f"{elem_id}_highlight",
                            pickable=False,
                        )
                    except Exception:
                        pass

    def _on_element_updated(self, elem_id: str) -> None:
        """Handle element property changes from the property panel."""
        if self._current_level:
            self._render_level(self._current_level)
            self._exception_panel.update_data(self._current_level)
            self._statusbar.showMessage(f"Updated: {elem_id}")

    def _on_verified_toggled(self, elem_id: str, verified: bool) -> None:
        """Handle user verification toggle."""
        status = "verified" if verified else "unverified"
        self._statusbar.showMessage(f"{elem_id}: {status}")
        if self._current_level:
            self._exception_panel.update_data(self._current_level)

    def _reset_camera(self) -> None:
        self._plotter.reset_camera()

    def _toggle_edges(self) -> None:
        """Toggle edge visibility — re-render."""
        if self._current_level:
            self._render_level(self._current_level)
