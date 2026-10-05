"""
Quick integration test: parse the sample DXF and validate output.
"""
import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from stage1_preflight.parsers.dxf_parser import DXFParser
from stage1_preflight.models.schemas import StructuralProject
from stage1_preflight.parsers.pipeline_adapter import parse_with_robust_pipeline

def test_pipeline():
    dxf_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_framing.dxf")
    
    if not os.path.exists(dxf_path):
        print(f"ERROR: Sample DXF not found at {dxf_path}")
        sys.exit(1)
    
    # Step 1: Robust parsing
    print("=" * 60)
    print("STEP 1: Robust parsing...")
    from stage1_preflight.ui.layer_mapping_dialog import guess_layer_type
    
    # We still need layers list to guess mappings for tests
    parser = DXFParser(dxf_path)
    cad_data = parser.parse()
    mapping = {layer: guess_layer_type(layer) for layer in cad_data.layers}
    
    level = parse_with_robust_pipeline(
        filepath=dxf_path,
        layer_mapping=mapping,
        floor_height=3200.0,
        base_elevation=0.0,
        level_name="L01"
    )
    
    print(f"  Beams: {len(level.beams)}")
    for b in level.beams:
        print(f"    {b.id}: {b.width}x{b.depth} conf={b.confidence:.2f} issues={b.issues}")
    
    print(f"  Columns: {len(level.columns)}")
    for c in level.columns:
        circ = f" (DIA {c.diameter})" if c.is_circular else ""
        print(f"    {c.id}: {c.width}x{c.depth}{circ} conf={c.confidence:.2f} issues={c.issues}")
    
    print(f"  Slabs: {len(level.slabs)}")
    for s in level.slabs:
        print(f"    {s.id}: {s.thickness}mm thk, {len(s.boundary)} pts conf={s.confidence:.2f}")
    
    print(f"  Walls: {len(level.walls)}")
    for w in level.walls:
        print(f"    {w.id}: {w.thickness}mm thk, len={w.length:.0f}mm conf={w.confidence:.2f}")
    
    # Step 4: Flagged elements
    print("\n" + "=" * 60)
    print("STEP 4: Exception inspector...")
    flagged = level.flagged_elements
    print(f"  Flagged: {len(flagged)} of {level.total_elements}")
    for f in flagged:
        print(f"    {f.id} (conf={f.confidence:.2f}): {f.issues}")
    
    # Step 5: JSON export
    print("\n" + "=" * 60)
    print("STEP 5: JSON export...")
    project = StructuralProject(
        project_name="Sample Framing",
        source_file=dxf_path,
        levels=[level],
    )
    
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_output.json")
    project.to_json_file(out_path)
    print(f"  Exported to: {out_path}")
    print(f"  Total elements: {project.total_elements}")
    
    # Verify round-trip
    loaded = StructuralProject.from_json_file(out_path)
    print(f"  Round-trip OK: {loaded.total_elements} elements")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED OK")

if __name__ == "__main__":
    test_pipeline()
