"""
Convert PDF geometry to MusicXML for precise layout replication.
"""
from __future__ import annotations

import math
from typing import List, Optional

from . import pdf_geometry


def staff_to_clef(staff: pdf_geometry.Staff) -> str:
    """Guess clef from staff position."""
    if not staff.y_values:
        return "G"
    avg_y = sum(staff.y_values) / len(staff.y_values)
    if avg_y > 400:
        return "F"
    return "G"


def map_y_to_pitch(staff: pdf_geometry.Staff, y: float, clef: str = "G") -> tuple:
    """Map vertical position on staff to pitch. Returns (step, octave)."""
    if not staff.y_values or not staff.interline:
        return ("C", 4)
    
    lines_sorted = sorted(staff.y_values)
    interline = staff.interline
    
    if clef == "G":
        bottom_line = lines_sorted[-1] if len(lines_sorted) > 4 else lines_sorted[0]
        steps_from_bottom = (bottom_line - y) / interline
        idx = round(steps_from_bottom)
        pitches = [("E", 4), ("F", 4), ("G", 4), ("A", 4), ("B", 4), ("C", 5), ("D", 5), ("E", 5), ("F", 5), ("G", 5)]
        if 0 <= idx < len(pitches):
            return pitches[idx]
        return ("C", 4)
    else:
        idx = round((lines_sorted[0] - y) / interline) if lines_sorted else 4
        pitches = [("G", 3), ("A", 3), ("B", 3), ("C", 4), ("D", 4), ("E", 4), ("F", 4), ("G", 4), ("A", 4), ("B", 4)]
        if 0 <= idx < len(pitches):
            return pitches[idx]
        return ("C", 4)


def generate_musicxml_from_geometry(geom: pdf_geometry.PDFGeometry, title: str = "Score") -> str:
    """Generate MusicXML from extracted PDF geometry."""
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')
    lines.append('<score-partwise version="4.0">')
    lines.append('  <work>')
    lines.append(f'    <work-title>{title}</work-title>')
    lines.append('  </work>')
    lines.append('  <identification>')
    lines.append('    <encoding>')
    lines.append('      <encoder>scorehalo pdf-to-musicxml</encoder>')
    lines.append('    </encoding>')
    lines.append('  </identification>')
    lines.append('  <part-list>')
    
    # Group staves into parts
    part_groups = []
    for sys in geom.systems:
        if not sys.staves:
            continue
        sorted_staves = sorted(sys.staves, key=lambda s: s.y_values[0] if s.y_values else 0)
        groups = []
        current_group = [sorted_staves[0]]
        for s in sorted_staves[1:]:
            last = current_group[-1]
            gap = (s.y_values[0] if s.y_values else 0) - (last.y_values[-1] if last.y_values else 0)
            if gap < 40 and len(current_group) < 2:
                current_group.append(s)
            else:
                groups.append(current_group)
                current_group = [s]
        groups.append(current_group)
        for g in groups:
            part_groups.append(g)
    
    if not part_groups:
        part_groups = [[s] for sys in geom.systems for s in sys.staves]
    
    for i, group in enumerate(part_groups):
        lines.append(f'    <score-part id="P{i+1}">')
        if len(group) == 2:
            lines.append(f'      <part-name>Piano</part-name>')
            lines.append(f'      <part-abbreviation>Pno.</part-abbreviation>')
        elif len(group) > 2:
            lines.append(f'      <part-name>Ensemble</part-name>')
        else:
            clef = staff_to_clef(group[0])
            if clef == "F":
                lines.append(f'      <part-name>Bass</part-name>')
            else:
                lines.append(f'      <part-name>Treble</part-name>')
        lines.append('    </score-part>')
    
    lines.append('  </part-list>')
    
    vlines_all = pdf_geometry.find_vertical_lines(geom)
    part_idx = 0
    for sys_idx, system in enumerate(geom.systems):
        for staff_idx, staff in enumerate(system.staves):
            # Skip if part of grand staff already processed? For now process all
            lines.append(f'  <part id="P{part_idx+1}">')
            clef = staff_to_clef(staff)
            barlines = pdf_geometry.detect_barlines_in_system(system, vlines_all)
            
            staff_notes = []
            for sym in geom.symbols:
                if sym.type == 'notehead':
                    if staff.bbox[1] - 40 < sym.center[1] < staff.bbox[3] + 40:
                        if staff.bbox[0] - 100 < sym.center[0] < staff.bbox[2] + 300:
                            staff_notes.append(sym)
            staff_notes.sort(key=lambda n: n.center[0])
            
            measure_boundaries = []
            if barlines:
                for b in barlines:
                    measure_boundaries.append(min(b.x0, b.x1))
                measure_boundaries = sorted(set(measure_boundaries))
            
            num_measures = max(1, len(measure_boundaries)) if measure_boundaries else 1
            for m_idx in range(num_measures):
                lines.append(f'    <measure number="{m_idx+1}">')
                if m_idx == 0:
                    lines.append('      <attributes>')
                    lines.append('        <divisions>1</divisions>')
                    lines.append('        <key><fifths>0</fifths></key>')
                    lines.append('        <time><beats>4</beats><beat-type>4</beat-type></time>')
                    lines.append('        <clef>')
                    if clef == "G":
                        lines.append('          <sign>G</sign><line>2</line>')
                    else:
                        lines.append('          <sign>F</sign><line>4</line>')
                    lines.append('        </clef>')
                    lines.append('        <staff-details><staff-lines>5</staff-lines></staff-details>')
                    lines.append('      </attributes>')
                    if sys_idx == 0 and staff_idx == 0:
                        lines.append('      <print new-system="yes"/>')
                
                if barlines and len(measure_boundaries) > m_idx:
                    m_start = measure_boundaries[m_idx-1] if m_idx > 0 else staff.bbox[0]
                    m_end = measure_boundaries[m_idx]
                else:
                    m_start = staff.bbox[0]
                    m_end = staff.bbox[2]
                
                notes_in_measure = [n for n in staff_notes if m_start - 20 < n.center[0] < m_end + 20]
                
                if notes_in_measure:
                    for note in notes_in_measure[:4]:
                        step, octave = map_y_to_pitch(staff, note.center[1], clef)
                        lines.append('      <note>')
                        lines.append('        <pitch>')
                        lines.append(f'          <step>{step}</step>')
                        lines.append(f'          <octave>{octave}</octave>')
                        lines.append('        </pitch>')
                        lines.append('        <duration>1</duration>')
                        lines.append('        <voice>1</voice>')
                        lines.append('        <type>quarter</type>')
                        lines.append('      </note>')
                else:
                    lines.append('      <note>')
                    lines.append('        <rest measure="yes"/>')
                    lines.append('        <duration>4</duration>')
                    lines.append('        <voice>1</voice>')
                    lines.append('        <type>whole</type>')
                    lines.append('      </note>')
                lines.append('    </measure>')
            lines.append('  </part>')
            part_idx += 1
    
    lines.append('</score-partwise>')
    return '\n'.join(lines)


def pdf_to_musicxml(pdf_path: str, out_path: str, title: Optional[str] = None) -> str:
    """Convert PDF to MusicXML."""
    geom = pdf_geometry.extract_geometry(pdf_path, 0)
    if title is None:
        title = pdf_path.split('/')[-1].replace('.pdf', '')
    xml = generate_musicxml_from_geometry(geom, title)
    with open(out_path, 'w') as f:
        f.write(xml)
    return out_path
