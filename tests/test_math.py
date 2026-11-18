import unittest
from tools.math_check import audit


class MathAuditTests(unittest.TestCase):
    def test_supported_delimiters_and_code_examples(self):
        result = audit("文字 $n_s$\n\n$$\nR_s=\\frac{1}{q n_s\\mu}\n$$\n`$未成對`\n```tex\n$示例\n```\n")
        self.assertEqual((result["inline"], result["display"], result["issues"]), (1, 1, []))

    def test_missing_delimiter(self):
        self.assertIn("unpaired_delimiter", [x["code"] for x in audit("式 $x")['issues']])

    def test_escape_and_braces(self):
        codes = [x["code"] for x in audit(r"$\\mu + {x$")['issues']]
        self.assertIn("double_escaped_command_review", codes)
        self.assertIn("unbalanced_braces", codes)

    def test_markdown_interference(self):
        codes = [x["code"] for x in audit("| $R=[a](L/W)$ |\n")['issues']]
        self.assertIn("math_in_table_review", codes)
        self.assertIn("markdown_link_collision", codes)

    def test_escaped_currency_not_delimiter(self):
        self.assertEqual(audit(r"價格 \$20，式 $x$")["inline"], 1)

    def test_display_own_line(self):
        self.assertIn("display_delimiter_not_own_line", [x["code"] for x in audit("$$x$$")['issues']])


if __name__ == "__main__":
    unittest.main()
