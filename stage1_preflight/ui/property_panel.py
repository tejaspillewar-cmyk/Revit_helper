"""
Property Panel: Displays and allows editing of a selected structural element's
properties (dimensions, offsets, coordinates).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from stage1_preflight.models.schemas import (
    BeamElement,
    ColumnElement,
    ElementType,
    SlabElement,
    StructuralElementBase,
    WallElement,
)


class PropertyPanel(QWidget):
    """
    Sidebar panel for viewing and editing properties of a selected
    structural element. Updates are propagated via signals.
    """

    element_updated = pyqtSignal(str)   # Emits element ID on change
    verified_toggled = pyqtSignal(str, bool)  # Emits (id, verified)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_element: Optional[StructuralElementBase] = None
        self._spinboxes: dict[str, QDoubleSpinBox] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Header
        header = QLabel("📐 Element Properties")
        header.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: #89B4FA;
                padding: 4px 0;
            }
        """)
        main_layout.addWidget(header)

        # Selected element info
        self._id_label = QLabel("No element selected")
        self._id_label.setStyleSheet(
            "color: #CDD6F4; font-size: 12px; font-weight: bold;"
        )
        main_layout.addWidget(self._id_label)

        self._type_label = QLabel("")
        self._type_label.setStyleSheet("color: #A6ADC8; font-size: 11px;")
        main_layout.addWidget(self._type_label)

        self._conf_label = QLabel("")
        self._conf_label.setStyleSheet("font-size: 11px;")
        main_layout.addWidget(self._conf_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #444;")
        main_layout.addWidget(sep)

        # Scroll area for property fields
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
        """)

        self._form_container = QWidget()
        self._form_layout = QFormLayout(self._form_container)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(6)
        self._form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        scroll.setWidget(self._form_container)
        main_layout.addWidget(scroll, stretch=1)

        # Layer info
        self._layer_label = QLabel("")
        self._layer_label.setStyleSheet("color: #6C7086; font-size: 10px;")
        main_layout.addWidget(self._layer_label)

        # Label text
        self._label_text = QLabel("")
        self._label_text.setStyleSheet("color: #6C7086; font-size: 10px;")
        self._label_text.setWordWrap(True)
        main_layout.addWidget(self._label_text)

        # Verified checkbox
        self._verified_cb = QCheckBox("✓ User Verified")
        self._verified_cb.setStyleSheet("""
            QCheckBox {
                color: #A6E3A1;
                font-size: 11px;
                padding: 4px;
            }
        """)
        self._verified_cb.toggled.connect(self._on_verified_toggled)
        main_layout.addWidget(self._verified_cb)

        # Apply button
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #89B4FA;
                color: #1E1E2E;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #B4D0FB;
            }
            QPushButton:pressed {
                background-color: #74C7EC;
            }
        """)
        self._apply_btn.clicked.connect(self._on_apply)
        self._apply_btn.setEnabled(False)
        main_layout.addWidget(self._apply_btn)

    def show_element(self, element: StructuralElementBase) -> None:
        """Display properties of the given element."""
        self._current_element = element
        self._spinboxes.clear()

        # Update header info
        self._id_label.setText(f"Mark: {element.id}")
        self._type_label.setText(f"Type: {element.element_type.value.capitalize()}")

        conf_color = "#A6E3A1" if element.confidence >= 0.85 else (
            "#F9E2AF" if element.confidence >= 0.70 else "#F38BA8"
        )
        self._conf_label.setText(f"Confidence: {element.confidence:.0%}")
        self._conf_label.setStyleSheet(f"color: {conf_color}; font-size: 11px;")

        self._layer_label.setText(f"Layer: {element.layer}")
        self._label_text.setText(
            f"Label: {element.label_text}" if element.label_text else "Label: —"
        )
        self._verified_cb.blockSignals(True)
        self._verified_cb.setChecked(element.user_verified)
        self._verified_cb.blockSignals(False)

        # Clear form
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build form fields based on element type
        if isinstance(element, BeamElement):
            self._add_field("Width (mm)", element.width, "width")
            self._add_field("Depth (mm)", element.depth, "depth")
            self._add_field("Start X", element.start.x, "start_x")
            self._add_field("Start Y", element.start.y, "start_y")
            self._add_field("Start Z", element.start.z, "start_z")
            self._add_field("End X", element.end.x, "end_x")
            self._add_field("End Y", element.end.y, "end_y")
            self._add_field("End Z", element.end.z, "end_z")
            self._add_readonly("Length (mm)", f"{element.length:.1f}")

        elif isinstance(element, ColumnElement):
            self._add_field("Width (mm)", element.width, "width")
            self._add_field("Depth (mm)", element.depth, "depth")
            self._add_field("Height (mm)", element.height, "height")
            self._add_field("Centroid X", element.centroid.x, "centroid_x")
            self._add_field("Centroid Y", element.centroid.y, "centroid_y")
            self._add_field("Centroid Z", element.centroid.z, "centroid_z")
            if element.is_circular and element.diameter:
                self._add_field("Diameter (mm)", element.diameter, "diameter")

        elif isinstance(element, SlabElement):
            self._add_field("Thickness (mm)", element.thickness, "thickness")
            self._add_readonly("Vertices", str(len(element.boundary)))
            self._add_readonly("Closed", "Yes" if element.is_closed else "No")

        elif isinstance(element, WallElement):
            self._add_field("Thickness (mm)", element.thickness, "thickness")
            self._add_field("Height (mm)", element.height, "height")
            self._add_field("Start X", element.start.x, "start_x")
            self._add_field("Start Y", element.start.y, "start_y")
            self._add_field("End X", element.end.x, "end_x")
            self._add_field("End Y", element.end.y, "end_y")
            self._add_readonly("Length (mm)", f"{element.length:.1f}")

        # Issues
        if element.issues:
            issues_group = QGroupBox("Issues")
            issues_group.setStyleSheet("""
                QGroupBox {
                    color: #FAB387;
                    font-size: 11px;
                    border: 1px solid #45475A;
                    border-radius: 4px;
                    margin-top: 8px;
                    padding: 8px;
                    padding-top: 16px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 8px;
                }
            """)
            issues_layout = QVBoxLayout(issues_group)
            for issue in element.issues:
                lbl = QLabel(f"⚠ {issue}")
                lbl.setStyleSheet("color: #FAB387; font-size: 10px;")
                lbl.setWordWrap(True)
                issues_layout.addWidget(lbl)
            self._form_layout.addRow(issues_group)

        self._apply_btn.setEnabled(True)

    def _add_field(self, label: str, value: float, key: str) -> None:
        """Add an editable numeric field."""
        spinbox = QDoubleSpinBox()
        spinbox.setRange(-1e9, 1e9)
        spinbox.setDecimals(1)
        spinbox.setValue(value)
        spinbox.setSuffix("")
        spinbox.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 3px;
                padding: 3px 6px;
                font-size: 11px;
            }
            QDoubleSpinBox:focus {
                border-color: #89B4FA;
            }
        """)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #BAC2DE; font-size: 11px;")
        self._form_layout.addRow(lbl, spinbox)
        self._spinboxes[key] = spinbox

    def _add_readonly(self, label: str, value: str) -> None:
        """Add a read-only info field."""
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #BAC2DE; font-size: 11px;")
        val = QLabel(value)
        val.setStyleSheet("color: #6C7086; font-size: 11px;")
        self._form_layout.addRow(lbl, val)

    def _on_apply(self) -> None:
        """Apply edited values back to the element."""
        if not self._current_element:
            return

        elem = self._current_element

        if isinstance(elem, BeamElement):
            elem.width = self._get_val("width", elem.width)
            elem.depth = self._get_val("depth", elem.depth)
            elem.start.x = self._get_val("start_x", elem.start.x)
            elem.start.y = self._get_val("start_y", elem.start.y)
            elem.start.z = self._get_val("start_z", elem.start.z)
            elem.end.x = self._get_val("end_x", elem.end.x)
            elem.end.y = self._get_val("end_y", elem.end.y)
            elem.end.z = self._get_val("end_z", elem.end.z)

        elif isinstance(elem, ColumnElement):
            elem.width = self._get_val("width", elem.width)
            elem.depth = self._get_val("depth", elem.depth)
            elem.height = self._get_val("height", elem.height)
            elem.centroid.x = self._get_val("centroid_x", elem.centroid.x)
            elem.centroid.y = self._get_val("centroid_y", elem.centroid.y)
            elem.centroid.z = self._get_val("centroid_z", elem.centroid.z)
            if elem.is_circular and "diameter" in self._spinboxes:
                elem.diameter = self._get_val("diameter", elem.diameter)

        elif isinstance(elem, SlabElement):
            elem.thickness = self._get_val("thickness", elem.thickness)

        elif isinstance(elem, WallElement):
            elem.thickness = self._get_val("thickness", elem.thickness)
            elem.height = self._get_val("height", elem.height)
            elem.start.x = self._get_val("start_x", elem.start.x)
            elem.start.y = self._get_val("start_y", elem.start.y)
            elem.end.x = self._get_val("end_x", elem.end.x)
            elem.end.y = self._get_val("end_y", elem.end.y)

        self.element_updated.emit(elem.id)

    def _get_val(self, key: str, default: float) -> float:
        sb = self._spinboxes.get(key)
        return sb.value() if sb else default

    def _on_verified_toggled(self, checked: bool) -> None:
        if self._current_element:
            self._current_element.user_verified = checked
            self.verified_toggled.emit(self._current_element.id, checked)

    def clear(self) -> None:
        """Reset the panel to empty state."""
        self._current_element = None
        self._id_label.setText("No element selected")
        self._type_label.setText("")
        self._conf_label.setText("")
        self._layer_label.setText("")
        self._label_text.setText("")
        self._apply_btn.setEnabled(False)

        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
