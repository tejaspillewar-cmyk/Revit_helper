"""
Exception Inspector sidebar: displays flagged elements with low confidence
or issues, allowing the user to click to select them in the viewport.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stage1_preflight.models.schemas import (
    ConfidenceLevel,
    StructuralElementBase,
    StructuralLevel,
)


class ExceptionPanel(QWidget):
    """
    Sidebar panel listing elements with issues or low confidence scores.
    Emitting signals when a row is clicked to highlight the element in 3D.
    """

    element_selected = pyqtSignal(str)  # Emits element ID

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        header = QLabel("⚠ Exception Inspector")
        header.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: #FF8C00;
                padding: 4px 0;
            }
        """)
        layout.addWidget(header)

        # Summary
        self._summary_label = QLabel("No elements loaded")
        self._summary_label.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(self._summary_label)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #444;")
        layout.addWidget(separator)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["ID", "Type", "Confidence", "Issue"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #1E1E2E;
                alternate-background-color: #252536;
                color: #CDD6F4;
                gridline-color: #383850;
                border: 1px solid #383850;
                font-size: 11px;
            }
            QTableWidget::item:selected {
                background-color: #45475A;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #CDD6F4;
                padding: 4px;
                border: 1px solid #383850;
                font-weight: bold;
                font-size: 11px;
            }
        """)
        self._table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self._table, stretch=1)

        # Counters bar
        counter_layout = QHBoxLayout()
        self._high_label = QLabel("✓ High: 0")
        self._high_label.setStyleSheet("color: #A6E3A1; font-size: 11px;")
        self._med_label = QLabel("● Med: 0")
        self._med_label.setStyleSheet("color: #F9E2AF; font-size: 11px;")
        self._low_label = QLabel("⚠ Low: 0")
        self._low_label.setStyleSheet("color: #F38BA8; font-size: 11px;")
        counter_layout.addWidget(self._high_label)
        counter_layout.addWidget(self._med_label)
        counter_layout.addWidget(self._low_label)
        layout.addLayout(counter_layout)

    def update_data(self, level: StructuralLevel) -> None:
        """Populate the table with flagged elements from a StructuralLevel."""
        self._table.setRowCount(0)
        flagged = level.flagged_elements
        all_elements = level.all_elements

        # Count confidence levels
        high = sum(1 for e in all_elements if e.confidence >= 0.85)
        med = sum(1 for e in all_elements if 0.70 <= e.confidence < 0.85)
        low = sum(1 for e in all_elements if e.confidence < 0.70)

        self._high_label.setText(f"✓ High: {high}")
        self._med_label.setText(f"● Med: {med}")
        self._low_label.setText(f"⚠ Low: {low}")

        self._summary_label.setText(
            f"{len(flagged)} flagged of {level.total_elements} elements"
        )

        # Show ALL elements, flagged ones first
        sorted_elements = sorted(
            all_elements, key=lambda e: (e.confidence, e.id)
        )

        self._table.setRowCount(len(sorted_elements))
        for row, elem in enumerate(sorted_elements):
            # ID
            id_item = QTableWidgetItem(elem.id)
            self._table.setItem(row, 0, id_item)

            # Type
            type_item = QTableWidgetItem(elem.element_type.value.capitalize())
            self._table.setItem(row, 1, type_item)

            # Confidence
            conf_text = f"{elem.confidence:.0%}"
            conf_item = QTableWidgetItem(conf_text)
            if elem.confidence < 0.70:
                conf_item.setForeground(QColor("#F38BA8"))
            elif elem.confidence < 0.85:
                conf_item.setForeground(QColor("#F9E2AF"))
            else:
                conf_item.setForeground(QColor("#A6E3A1"))
            self._table.setItem(row, 2, conf_item)

            # Issues
            issue_text = "; ".join(elem.issues) if elem.issues else "—"
            issue_item = QTableWidgetItem(issue_text)
            if elem.issues:
                issue_item.setForeground(QColor("#FAB387"))
            self._table.setItem(row, 3, issue_item)

    def _on_row_clicked(self, row: int, col: int) -> None:
        id_item = self._table.item(row, 0)
        if id_item:
            self.element_selected.emit(id_item.text())
