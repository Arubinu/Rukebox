"""The birthdays of the house (src/birthdays.py): a name and a day of the year, wished on that day."""
import shutil
import tempfile
import unittest
from datetime import date

import _path  # noqa: F401
import birthdays


class BirthdaysTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = _path.document(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_names_of_the_day_and_a_leap_day_in_other_years(self):
        birthdays.add({"name": "Marie", "date": "03-14"}, self.path)
        birthdays.add({"name": "Paul", "date": "03-14"}, self.path)
        birthdays.add({"name": "Léa", "date": "02-29"}, self.path)
        self.assertEqual(birthdays.names_on(date(2027, 3, 14), self.path), ["Marie", "Paul"])
        self.assertEqual(birthdays.names_on(date(2027, 2, 28), self.path), ["Léa"], "no 29th this year")
        self.assertEqual(birthdays.names_on(date(2028, 2, 28), self.path), [], "a leap year has its own")
        self.assertEqual(birthdays.names_on(date(2028, 2, 29), self.path), ["Léa"])
        self.assertEqual([b["name"] for b in birthdays.load(self.path)], ["Léa", "Marie", "Paul"],
                         "in the order of the year")

    def test_a_bad_day_or_name_is_refused_and_the_moment_is_kept(self):
        for bad, code in (({"name": "", "date": "01-01"}, "birthday_name_required"),
                          ({"name": "X", "date": "02-30"}, "birthday_bad_date"),
                          ({"name": "X" * 41, "date": "01-01"}, "birthday_name_too_long")):
            with self.assertRaises(ValueError) as caught:
                birthdays.add(bad, self.path)
            self.assertEqual(str(caught.exception), code)
        self.assertEqual(birthdays.wish(self.path), "start")
        birthdays.set_wish("button", self.path)
        entry = birthdays.add({"name": "Marie", "date": "03-14"}, self.path)
        self.assertEqual(birthdays.wish(self.path), "button", "adding one keeps the moment chosen")
        birthdays.delete(entry["id"], self.path)
        self.assertEqual(birthdays.load(self.path), [])
        with self.assertRaises(ValueError):
            birthdays.set_wish("never", self.path)


if __name__ == "__main__":
    unittest.main()
