STRUCTURAL JSON  ->  REVIT   (Stage 2 add-in)
=============================================

What it does
  Reads a structural JSON (made by Stage 1 from a CAD framing plan) and builds native Revit beams, columns,
  slabs (with openings) and walls in the open project. One Ctrl+Z undoes the whole import.

What you need
  - Revit 2022, 2023, 2024, 2025 or 2026
  - A project started from a STRUCTURAL template (or one that already has a Structural Framing family, a
    Structural Column family, a Floor type and a basic Wall type). The add-in copies those to make each size.
  - Nothing else: no admin rights, no Python, no pyRevit, no internet.

Install (about 10 seconds)
  1. Close Revit.
  2. Double-click Install.bat.  It installs for the current Windows user only, for every Revit year it finds.
     (Or run   Install.bat 2024   from a command prompt to force one year.)
  3. Start Revit.  If it asks about the add-in, choose "Always Load".
  4. Open a project, then:  Add-Ins tab  >  External Tools  >  "Import Structural JSON".
Remove: double-click Uninstall.bat.   You can delete this folder after installing; the add-in keeps its own copy.

Use
  1. Click "Import Structural JSON" and pick a .json file (try samples\1_sample_framing.json first).
  2. A summary shows what was built, e.g.  Beams 9 / 9, Columns 7 / 7 ...
  3. A report file is written next to the JSON:  <name>_import_report.txt  (counts, failed elements, notes).
  Elements are created on level "L01" (or the level named in the JSON; an existing level at the same elevation
  is reused, otherwise a level is created). Beams and slabs sit at the top of the storey, columns and walls run up from it.

Samples (samples\ folder, each with a preview PNG to compare against)
  1_sample_framing.json   9 beams, 7 columns (1 round), 2 slabs, 2 walls
  2_FRAMING_OG.json       64 beams, 27 slabs (16 openings), 93 walls (49 non-structural)
  3_Sample-1.json         238 beams, 99 slabs (57 openings), 276 walls (143 non-structural)
  Use a NEW EMPTY project for each, so the counts are easy to check.
  (2_ and 3_ are real-project files and are not published on GitHub: if you got this kit from there, only
   1_sample_framing is included - copy the other two from the original folder.)

If something goes wrong
  - Button is missing: restart Revit; confirm Install.bat said "Installed" for your Revit year.
    If Windows blocked the DLL: right-click the DLL in
    %APPDATA%\Autodesk\Revit\Addins\<year>\StructuralImport\  >  Properties  >  tick "Unblock"  >  OK.
  - "The project has no Structural Framing / Column family": start from the Structural template, or
    Insert > Load Family and load a concrete rectangular beam and column.
  - Some elements failed: they are listed in the report file with the reason. Everything else is still built.
  - Revit shows no dialogs for warnings during import (overlap / join warnings are suppressed on purpose).

What to check in Revit (this is a first-run test; please note anything that looks wrong)
  [ ] Counts in the summary match the sample list above.
  [ ] Click a beam / slab / wall: the type name shows its size (e.g. "Concrete-Rectangular Beam 300x450") and the
      real dimensions agree with the name.
  [ ] Slab openings (stairs, shafts) are cut out.
  [ ] Columns reach the next level, or the storey height if there is no level above; square columns are rotated
      correctly; the round column is round.
  [ ] Non-structural walls are not flagged "Structural".
  [ ] Slabs/beams sit at the top of the storey (3200 mm above the level for the samples), not 3200 mm too high/low.
  [ ] Beam depths: beams the drawing had no size label for use an ASSUMED 600 mm depth (flagged in Stage 1).
