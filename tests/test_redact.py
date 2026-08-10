import unittest

from textstrata.redact import PIIFinding, redact, scan_patterns


class PatternTests(unittest.TestCase):
    def test_email(self):
        findings = scan_patterns("Contact me at user@example.com for help.")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "email")
        self.assertEqual(findings[0].raw, "user@example.com")

    def test_email_multiple(self):
        findings = scan_patterns("a@b.com and c@d.org are both valid.")
        self.assertEqual(len(findings), 2)
        cats = [f.category for f in findings]
        self.assertEqual(cats, ["email", "email"])

    def test_email_no_false_positive(self):
        findings = scan_patterns("No email here.")
        self.assertEqual(len(findings), 0)

    def test_phone(self):
        findings = scan_patterns("Call +1 (555) 123-4567 ext 42 for details.")
        self.assertGreaterEqual(len(findings), 1)
        phone = next(f for f in findings if f.category == "phone")
        self.assertIn("555", phone.raw)

    def test_ssn(self):
        findings = scan_patterns("SSN: 123-45-6789 is sensitive.")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "ssn")
        self.assertEqual(findings[0].raw, "123-45-6789")

    def test_ssn_invalid_prefix(self):
        findings = scan_patterns("000-12-3456 should not match.")
        self.assertEqual(len(findings), 0)

    def test_credit_card_visa(self):
        findings = scan_patterns("Card: 4111 1111 1111 1111 expires soon.")
        cc = [f for f in findings if f.category == "credit_card"]
        self.assertEqual(len(cc), 1)
        self.assertIn("4111", cc[0].raw)

    def test_credit_card_mastercard(self):
        findings = scan_patterns("MC: 5500 0000 0000 0004")
        cc = [f for f in findings if f.category == "credit_card"]
        self.assertEqual(len(cc), 1)

    def test_api_key_openai(self):
        findings = scan_patterns("sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "api_key")

    def test_api_key_github(self):
        findings = scan_patterns("ghp_abcdefghijklmnopqrstuvwxyz0123456789abcd")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "api_key")

    def test_api_key_aws(self):
        findings = scan_patterns("AKIAIOSFODNN7EXAMPLE")
        keys = [f for f in findings if f.category == "aws_key"]
        self.assertEqual(len(keys), 1)

    def test_ip_address(self):
        findings = scan_patterns("Server at 192.168.1.1 is internal.")
        ips = [f for f in findings if f.category == "ip_address"]
        self.assertEqual(len(ips), 1)
        self.assertEqual(ips[0].raw, "192.168.1.1")

    def test_ip_address_invalid_octet(self):
        findings = scan_patterns("999.999.999.999 is not a valid IP.")
        ips = [f for f in findings if f.category == "ip_address"]
        self.assertEqual(len(ips), 0)

    def test_url_credential(self):
        findings = scan_patterns("https://user:pass@example.com/path")
        creds = [f for f in findings if f.category == "url_credential"]
        self.assertEqual(len(creds), 1)
        self.assertIn("user:pass", creds[0].raw)

    def test_multiple_pii_types(self):
        body = "User: alice@example.com, Phone: +1-555-0100, IP: 10.0.0.1"
        findings = scan_patterns(body)
        cats = {f.category for f in findings}
        self.assertIn("email", cats)
        self.assertIn("phone", cats)
        self.assertIn("ip_address", cats)

    def test_finding_has_context(self):
        findings = scan_patterns("Please email me at alice@corp.com for access.")
        self.assertGreater(len(findings[0].context), 0)
        self.assertIn("<HIT>", findings[0].context)

    def test_finding_confidence_range(self):
        findings = scan_patterns("a@b.com")
        self.assertGreaterEqual(findings[0].confidence, 0.0)
        self.assertLessEqual(findings[0].confidence, 1.0)

    def test_confidence_higher_for_ssn(self):
        ssn = scan_patterns("123-45-6789")
        email = scan_patterns("a@b.com")
        self.assertGreater(ssn[0].confidence, email[0].confidence)

    def test_empty_text(self):
        findings = scan_patterns("")
        self.assertEqual(len(findings), 0)

    def test_no_false_positives_body(self):
        body = """# Introduction
This is a normal note about software architecture.
The system uses port 8080 for HTTP and 443 for HTTPS.
Memory limit is 1024 MB. Timeout is 30 seconds.
"""
        findings = scan_patterns(body)
        self.assertEqual(len(findings), 0)

    def test_scan_stub(self):
        from textstrata.redact import scan
        findings = scan("hello@world.com", ai_assist=False)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "email")


class RedactTests(unittest.TestCase):
    def assert_redacted(self, original: str, expected: str):
        findings = scan_patterns(original)
        result = redact(original, findings)
        self.assertEqual(result, expected)

    def test_placeholder_email(self):
        self.assert_redacted(
            "Email: user@example.com",
            "Email: [REDACTED_EMAIL]",
        )

    def test_placeholder_multiple(self):
        body = "a@b.com and c@d.org"
        findings = scan_patterns(body)
        result = redact(body, findings)
        self.assertEqual(result, "[REDACTED_EMAIL] and [REDACTED_EMAIL]")

    def test_placeholder_ssn(self):
        self.assert_redacted(
            "SSN: 123-45-6789",
            "SSN: [REDACTED_SSN]",
        )

    def test_placeholder_credit_card(self):
        self.assert_redacted(
            "Card: 4111 1111 1111 1111",
            "Card: [REDACTED_CC]",
        )

    def test_placeholder_ip(self):
        self.assert_redacted(
            "IP: 192.168.1.1",
            "IP: [REDACTED_IP]",
        )

    def test_mask_email(self):
        findings = scan_patterns("user@example.com")
        result = redact("user@example.com", findings, strategy="mask")
        self.assertNotEqual(result, "user@example.com")
        self.assertIn("@", result)
        self.assertIn("*", result)

    def test_mask_phone(self):
        findings = scan_patterns("+1-555-0100")
        result = redact("+1-555-0100", findings, strategy="mask")
        self.assertNotEqual(result, "+1-555-0100")
        self.assertIn("*", result)

    def test_remove_email(self):
        findings = scan_patterns("Email: user@example.com")
        result = redact("Email: user@example.com", findings, strategy="remove")
        self.assertEqual(result, "Email: ")

    def test_no_findings_returns_original(self):
        result = redact("Hello world", [])
        self.assertEqual(result, "Hello world")

    def test_mixed_redact_strategies_not_supported(self):
        body = "Email: a@b.com Phone: 555-0100"
        findings = scan_patterns(body)
        placeholder = redact(body, findings, strategy="placeholder")
        mask = redact(body, findings, strategy="mask")
        remove = redact(body, findings, strategy="remove")
        self.assertIn("[REDACTED", placeholder)
        self.assertIn("*", mask)
        self.assertEqual(remove.replace(" ", ""), "Email:Phone:")

    def test_redact_preserves_surrounding_text(self):
        body = "My email is alice@corp.com and I work remotely."
        findings = scan_patterns(body)
        result = redact(body, findings)
        self.assertIn("My email is", result)
        self.assertIn("and I work remotely.", result)

    def test_deduplicate_overlapping(self):
        text = "use sk-test123 and secret key AKIAIOSFODNN7EXAMPLE"
        findings = scan_patterns(text)
        api_keys = [f for f in findings if f.category in ("api_key", "aws_key")]
        result = redact(text, api_keys)
        self.assertNotIn("sk-test123", result)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", result)
