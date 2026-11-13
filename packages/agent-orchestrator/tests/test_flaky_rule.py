"""A FAIL, then a PASS on the same tree, is flaky (D16) only under the same approval: an amended contract runs other
oracle bytes, so a FAIL under the old digest says nothing about the new one."""

import unittest

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


def receipt(result, digest="new", tree="t1"):
    return {"result": result, "digest": digest, "tree": tree}


class FlakyRuleTests(unittest.TestCase):
    def test_a_pass_after_a_fail_on_the_same_tree_and_digest_is_flaky(self):
        self.assertTrue(app.is_flaky(receipt("PASS"), [receipt("FAIL")]))

    def test_a_fail_under_an_earlier_digest_does_not_make_the_pass_flaky(self):
        self.assertFalse(app.is_flaky(receipt("PASS"), [receipt("FAIL", digest="old")]))

    def test_a_fail_on_another_tree_does_not_make_the_pass_flaky(self):
        self.assertFalse(app.is_flaky(receipt("PASS"), [receipt("FAIL", tree="t0")]))

    def test_a_fail_is_never_flaky(self):
        self.assertFalse(app.is_flaky(receipt("FAIL"), [receipt("FAIL")]))
