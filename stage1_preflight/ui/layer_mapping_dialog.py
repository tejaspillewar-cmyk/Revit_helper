import re
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QLabel
)
from PyQt6.QtCore import Qt

from stage1_preflight.models.schemas import ElementType
from stage1_preflight.utils.constants import (
    BEAM_LAYER_PATTERNS, COLUMN_LAYER_PATTERNS,
    SLAB_LAYER_PATTERNS, WALL_LAYER_PATTERNS
)

def guess_layer_type(layer_name: str) -> Optional[ElementType]:
    """Guess the structural element type based on the layer name."""
    for p in BEAM_LAYER_PATTERNS:
        if re.search(p, layer_name): return ElementType.BEAM
    for p in COLUMN_LAYER_PATTERNS:
        if re.search(p, layer_name): return ElementType.COLUMN
    for p in SLAB_LAYER_PATTERNS:
        if re.search(p, layer_name): return ElementType.SLAB
    for p in WALL_LAYER_PATTERNS:
        if re.search(p, layer_name): return ElementType.WALL
    return None

class LayerMappingDialog(QDialog):
    """Dialog to allow users to map CAD layers to ElementTypes."""
    def __init__(self, layers: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map CAD Layers")
        self.setMinimumSize(450, 500)
        self.layers = sorted(layers)
        self.combos = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        lbl = QLabel("Verify or change how CAD layers map to Structural Elements:")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 13px; font-weight: bold; margin-bottom: 8px;")
        layout.addWidget(lbl)
        
        self.table = QTableWidget(len(self.layers), 2)
        self.table.setHorizontalHeaderLabels(["CAD Layer", "Element Type"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1E1E2E;
                alternate-background-color: #252536;
                color: #CDD6F4;
                gridline-color: #383850;
                border: 1px solid #383850;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #CDD6F4;
                padding: 4px;
                border: 1px solid #383850;
                font-weight: bold;
            }
        """)
        
        for row, layer in enumerate(self.layers):
            item = QTableWidgetItem(layer)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            
            combo = QComboBox()
            combo.addItem("Ignore", None)
            combo.addItem("Beam", ElementType.BEAM)
            combo.addItem("Column", ElementType.COLUMN)
            combo.addItem("Slab", ElementType.SLAB)
            combo.addItem("Wall", ElementType.WALL)
            
            guessed = guess_layer_type(layer)
            if guessed == ElementType.BEAM: combo.setCurrentIndex(1)
            elif guessed == ElementType.COLUMN: combo.setCurrentIndex(2)
            elif guessed == ElementType.SLAB: combo.setCurrentIndex(3)
            elif guessed == ElementType.WALL: combo.setCurrentIndex(4)
            else: combo.setCurrentIndex(0)
            
            self.table.setCellWidget(row, 1, combo)
            self.combos[layer] = combo
            
        layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("padding: 6px 12px; background-color: #45475A;")
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setStyleSheet("padding: 6px 12px; background-color: #89B4FA; color: #1E1E2E; font-weight: bold;")
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def get_mapping(self) -> dict[str, Optional[ElementType]]:
        """Return the user-selected mapping."""
        mapping = {}
        for layer, combo in self.combos.items():
            mapping[layer] = combo.currentData()
        return mapping
