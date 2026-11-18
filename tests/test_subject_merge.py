"""A long agreement is read in page parts: the land is described in the first part, the unit only in the schedule at the
end. The unit must come from the schedule — never 'The unit is not identified on these pages' (lead 521)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import extract  # noqa: E402

part1 = {"unit_type": "Flat", "unit_no": "", "village": "Ghodbunder",
         "full_description": "Premises in the New Buildings to be constructed on the Tower A, B, C Land (approx. 10,223.13 sq.m.) "
                             "at Village Ghodbunder. The unit is not identified on these pages."}
part4 = {"unit_no": "4015", "floor": "40th", "wing": "B1", "carpet_area": "38.69 sq. m.",
         "building_name": "The Palace - Tower B",
         "full_description": "Flat No. 4015 admeasuring 38.69 sq. m. carpet on the 40th floor of Wing B1 of The Palace - Tower B"}

s = extract.merge_part_subjects([part1, {}, {}, part4])
assert s["unit_no"] == "4015", s
assert "4015" in s["full_description"] and "not identified" not in s["full_description"], s
assert s["village"] == "Ghodbunder"

# the unit fields were read but no description names the unit → the unit clause is put in front of the land description
s = extract.complete_description({"unit_type": "Flat", "unit_no": "4015", "floor": "40th", "wing": "B1",
                                  "building_name": "The Palace - Tower B",
                                  "full_description": "Premises on the Tower A, B, C Land. Unit not identified in these pages."})
assert s["full_description"].startswith("Flat No. 4015 on the 40th floor of Wing B1 of The Palace - Tower B, Premises"), s
assert "not identified" not in s["full_description"], s

# real values are kept
assert extract.clean_value("Flat No. 12, 1st floor, Sai Darshan") == "Flat No. 12, 1st floor, Sai Darshan"
assert extract.clean_value("Not visible in these pages") == ""
print("SUBJECT MERGE TESTS PASSED")
