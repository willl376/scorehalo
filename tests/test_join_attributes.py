"""Regression tests for MusicXML <attributes> child ordering.

W3C MusicXML 4.0 models <attributes> as an ORDERED xsd:sequence
(complexType "attributes" in musicxml.xsd), so <staves> must precede
<clef>. Building those blocks with ET.SubElement appends them at the end and
produces a schema-invalid document -- one that MuseScore still imports, so
the corruption is silent. That is exactly how 35 measures of the joined
Carpenters score shipped invalid while all 181 page files validated.

Each test below carries a positive control: a hand-built VALID block must be
accepted, otherwise "nothing fires" would look like a pass.
"""

import unittest
import xml.etree.ElementTree as ET

from scorehalo.join import _ATTR_ORDER, _insert_ordered, _set_staves

# Copied verbatim from musicxml.xsd (complexType "attributes"), lines 2813+.
XSD_ORDER = [
    "footnote",
    "level",
    "divisions",
    "key",
    "time",
    "staves",
    "part-symbol",
    "instruments",
    "clef",
    "staff-details",
    "transpose",
    "directive",
    "measure-style",
]


def _names(block):
    return [c.tag.split("}")[-1] for c in block]


def _ranks(block):
    return [XSD_ORDER.index(n) for n in _names(block)]


def _ordered(block):
    return _ranks(block) == sorted(_ranks(block))


class AttributesOrderTest(unittest.TestCase):
    def test_order_list_matches_the_schema(self):
        """The helper must encode the real XSD sequence, not a guess."""
        self.assertEqual(list(_ATTR_ORDER), XSD_ORDER)

    def test_control_a_hand_built_valid_block_is_accepted(self):
        amp = ET.Element("attributes")
        for name in ("divisions", "key", "time", "staves", "clef"):
            ET.SubElement(amp, name)
        self.assertTrue(_ordered(amp), "control block should already be valid")

    def test_staves_inserted_before_clef(self):
        """The exact defect: <clef> present, <staves> missing."""
        amp = ET.Element("attributes")
        ET.SubElement(amp, "key")
        clef = ET.SubElement(amp, "clef")
        clef.set("number", "2")
        st = ET.Element("staves")
        st.text = "3"
        _insert_ordered(amp, st)
        self.assertEqual(_names(amp), ["key", "staves", "clef"])
        self.assertTrue(_ordered(amp))

    def test_staves_would_be_invalid_if_appended(self):
        """Positive control for the mutation: naive append must be caught."""
        amp = ET.Element("attributes")
        ET.SubElement(amp, "key")
        clef = ET.SubElement(amp, "clef")
        clef.set("number", "2")
        st = ET.SubElement(amp, "staves")  # the bug: appended last
        st.text = "3"
        self.assertFalse(_ordered(amp), "appending should have been invalid")

    def test_staves_lands_after_time_before_part_symbol(self):
        amp = ET.Element("attributes")
        ET.SubElement(amp, "divisions")
        ET.SubElement(amp, "time")
        ET.SubElement(amp, "part-symbol")
        ET.SubElement(amp, "clef")
        st = ET.Element("staves")
        st.text = "2"
        _insert_ordered(amp, st)
        self.assertEqual(_names(amp), ["divisions", "time", "staves", "part-symbol", "clef"])

    def test_clef_appended_to_a_full_block_stays_ordered(self):
        amp = ET.Element("attributes")
        ET.SubElement(amp, "divisions")
        st = ET.SubElement(amp, "staves")
        st.text = "3"
        clef = ET.Element("clef")
        clef.set("number", "3")
        _insert_ordered(amp, clef)
        self.assertTrue(_ordered(amp))
        self.assertEqual(_names(amp), ["divisions", "staves", "clef"])

    def test_set_staves_repairs_an_existing_declaration_in_place(self):
        m = ET.Element("measure")
        amp = ET.SubElement(m, "attributes")
        st = ET.SubElement(amp, "staves")
        st.text = "1"
        ET.SubElement(amp, "clef")
        self.assertTrue(_set_staves(m, 3))
        self.assertEqual(st.text, "3")
        self.assertTrue(_ordered(amp), "repair must not reorder what was there")

    def test_set_staves_inserts_in_schema_position(self):
        m = ET.Element("measure")
        amp = ET.SubElement(m, "attributes")
        ET.SubElement(amp, "clef")
        self.assertTrue(_set_staves(m, 2))
        self.assertEqual(_names(amp), ["staves", "clef"])
        self.assertTrue(_ordered(amp))

    def test_set_staves_ignores_a_measure_without_attributes(self):
        m = ET.Element("measure")
        ET.SubElement(m, "note")
        self.assertFalse(_set_staves(m, 2))

    def test_unknown_child_is_appended_rather_than_dropped(self):
        amp = ET.Element("attributes")
        ET.SubElement(amp, "divisions")
        odd = ET.Element("something-new")
        _insert_ordered(amp, odd)
        self.assertIn(odd, list(amp))


if __name__ == "__main__":
    unittest.main()