# Stage 2: Revit Model Builder

## Overview
This script reads the validated JSON contract exported from the Stage 1 Pre-Flight 
application and constructs native Revit structural elements using the Revit API.

## Requirements
- **Revit 2022+** with pyRevit installed
- The `.json` file exported from Stage 1

## Deployment

### Option A: pyRevit Extension
1. Create a folder in your pyRevit extensions directory:
   ```
   %APPDATA%\pyRevit\Extensions\StructuralJSON.extension\
   ```
2. Create a tab:
   ```
   StructuralJSON.tab\Import.panel\ImportJSON.pushbutton\
   ```
3. Copy `revit_builder.py` into the pushbutton folder as `script.py`
4. In Revit, the button appears in the StructuralJSON tab

### Option B: pyRevit Script Console
1. Open Revit → pyRevit → Script Console
2. Paste and run the script content

## How It Works

1. **JSON Import**: Parses the validated structural JSON contract
2. **Type Mapping**: Queries existing FamilySymbols, auto-creates missing types
3. **Element Generation**: Creates beams, columns, slabs, and walls
4. **Single Transaction**: All operations are grouped for clean undo

## JSON Contract Format
```json
{
  "project_name": "My Project",
  "units": "mm",
  "levels": [
    {
      "level": "L01",
      "base_elevation": 0,
      "floor_height": 3200,
      "beams": [
        {
          "id": "B_01",
          "width": 300,
          "depth": 450,
          "start": {"x": 0, "y": 0, "z": 3200},
          "end": {"x": 5000, "y": 0, "z": 3200}
        }
      ],
      "columns": [
        {
          "id": "C_01",
          "width": 600,
          "depth": 600,
          "centroid": {"x": 0, "y": 0, "z": 0},
          "height": 3200
        }
      ],
      "slabs": [...],
      "walls": [...]
    }
  ]
}
```

## Revit API Methods Used

| Element | API Method |
|---------|-----------|
| Beam | `doc.Create.NewFamilyInstance(line, symbol, level, StructuralType.Beam)` |
| Column | `doc.Create.NewFamilyInstance(point, symbol, level, StructuralType.Column)` |
| Slab | `Floor.Create(doc, curveLoops, floorTypeId, levelId)` |
| Wall | `Wall.Create(doc, line, wallTypeId, levelId, height, offset, flip, structural)` |
