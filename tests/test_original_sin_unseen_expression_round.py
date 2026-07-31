from __future__ import annotations
import json,re,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/"benchmarks/original_sin_unseen_expression_plan_v1.json"
PROJECT=Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")

def norm(s): return " ".join(re.findall(r"[a-z0-9']+",str(s).lower().replace("’","'")))

class UnseenExpressionPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.plan=json.loads(PLAN.read_text())
    def test_round_has_eight_groups_and_twenty_nine_candidates(self):
        self.assertEqual(len(self.plan["groups"]),8)
        self.assertEqual(sum(len(g["routes"]) for g in self.plan["groups"]),29)
        self.assertEqual(self.plan["candidate_count"],29)
    def test_lines_are_not_spoken_in_adaptation(self):
        segs=json.loads((PROJECT/"external_workflows/big_finish_overlap_reference_v1/private/transcript.json").read_text())["segments"]
        full=norm(" ".join(str(s.get("text") or "") for s in segs))
        for group in self.plan["groups"]: self.assertNotIn(norm(group["text"]),full)
    def test_modes_cover_multiple_emotional_families(self):
        modes={g["mode"] for g in self.plan["groups"]}
        self.assertTrue({"urgent concern","dry irony","protective concern","cold authority","controlled anger","existential fear"}.issubset(modes))
    def test_only_approved_anchor_ids_are_used(self):
        allowed={"09dfbec8d8b78cac","1e691578853f9a75","f65ced4c8b19fa45","7d0621147f4f59ce","656021bc660487ba"}
        self.assertEqual({g["anchor_candidate_id"] for g in self.plan["groups"]},allowed)
    def test_every_group_includes_fish_inline_zero_shot(self):
        for group in self.plan["groups"]:
            self.assertIn("fish_inline_adaptation_anchor",group["routes"])
    def test_recurring_characters_include_current_route_control(self):
        recurring={"BERNICE","CHRIS CWEJ","ROZ FORRESTER"}
        for group in self.plan["groups"]:
            if group["book_speaker"] in recurring:
                self.assertIn("current_alexandria_route",group["routes"])
    def test_no_production_change(self): self.assertFalse(self.plan["production_changes"])

if __name__=="__main__": unittest.main()
