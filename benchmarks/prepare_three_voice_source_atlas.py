#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

ROUND_ID = "alexandria_three_voice_source_atlas_v1"

DEFAULT_NARRATOR_CACHE = Path.home() / "Library/Caches/Alexandria/NarratorYouTubeSources"
DEFAULT_CHARACTER_CACHE = Path.home() / "Library/Caches/Alexandria/BennyDoctorYouTubeSources"

SOURCES: dict[str, dict[str, Any]] = {
    "narrator_ultra_deluxe": {
        "target": "narrator",
        "title": "All Narrator Voice Lines from The Stanley Parable: Ultra Deluxe",
        "youtube_id": "zTA3kB9587o",
        "source_kind": "youtube_voice_line_compilation",
        "cache_group": "narrator",
    },
    "narrator_demonstration": {
        "target": "narrator",
        "title": "The Stanley Parable Demonstration — no-commentary playthrough",
        "youtube_id": "gXu9aYwtnCk",
        "source_kind": "youtube_no_commentary_gameplay",
        "cache_group": "narrator",
    },
    "narrator_letters": {
        "target": "narrator",
        "title": "The Stanley Parable Responds to Your Letters and Emails",
        "youtube_id": "8MDu3xocHV0",
        "source_kind": "official_character_performance",
        "cache_group": "narrator",
    },
    "benny_mars": {
        "target": "benny",
        "title": "Transmission from Mars",
        "youtube_id": "6qIUMVFM7pY",
        "source_kind": "official_character_performance",
        "cache_group": "character",
    },
    "benny_retrospective": {
        "target": "benny",
        "title": "Celebrating 25 Years of Bernice Summerfield at Big Finish",
        "youtube_id": "BUCL9eSw5S4",
        "source_kind": "official_retrospective_with_character_excerpts",
        "cache_group": "character",
    },
    "benny_new_adventures_trailer": {
        "target": "benny",
        "title": "The New Adventures of Bernice Summerfield",
        "youtube_id": "JRn0jYY204w",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "benny_dead_and_buried": {
        "target": "benny",
        "title": "Bernice Summerfield: Dead and Buried",
        "youtube_id": "qwbWpBxosvo",
        "source_kind": "official_character_short",
        "cache_group": "character",
    },
    "benny_trailer_003": {
        "target": "benny",
        "title": "The New Adventures of Bernice Summerfield — Trailer 003",
        "youtube_id": "_-PjThyHxXo",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "benny_teaser_002": {
        "target": "benny",
        "title": "The New Adventures of Bernice Summerfield — Teaser 002",
        "youtube_id": "xrAMVeab9TM",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "doctor_blood_and_steel": {
        "target": "doctor",
        "title": "The New Adventures of Bernice Summerfield: Blood and Steel",
        "youtube_id": "HLnWaruOz_Y",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "doctor_last_day": {
        "target": "doctor",
        "title": "The Seventh Doctor Adventures: The Last Day",
        "youtube_id": "Ae1mc8uso3o",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "doctor_happiness_patrol": {
        "target": "doctor",
        "title": "The Doctor's Mind Power — Happiness Patrol",
        "youtube_id": "g6c6AxhAal0",
        "source_kind": "official_television_scene",
        "cache_group": "character",
    },
    "doctor_kingdom_of_silver": {
        "target": "doctor",
        "title": "The Seventh Doctor Meets Temeter — Kingdom of Silver excerpt",
        "youtube_id": "H6LYkhHhlh4",
        "source_kind": "audio_drama_excerpt",
        "cache_group": "character",
    },
    "doctor_silver_and_ice": {
        "target": "doctor",
        "title": "The Seventh Doctor and Mel: Silver and Ice",
        "youtube_id": "jOTc6XiKbuY",
        "source_kind": "official_audio_drama_trailer",
        "cache_group": "character",
    },
    "doctor_time_and_rani": {
        "target": "doctor",
        "title": "The Seventh Doctor is Here — Time and the Rani",
        "youtube_id": "rscdFWDJ3_I",
        "source_kind": "official_television_scene_compilation",
        "cache_group": "character",
    },
}


def spec(
    clip_id: str,
    target: str,
    source: str,
    window: tuple[float, float],
    expected_text: str,
    primary_emotion: str,
    secondary_emotion: str,
    dramatic_function: str,
    intensity: int,
    source_scene: str,
    selection_reason: str,
    *,
    speaker_role: str,
    speaker_certainty: str = "high",
    source_role_warning: str = "",
    coverage_gap: str = "",
    match_floor: float = 0.68,
    verification_floor: float = 0.68,
) -> dict[str, Any]:
    return {
        "clip_id": clip_id,
        "target": target,
        "target_label": {"narrator": "Narrator", "benny": "Benny", "doctor": "Seventh Doctor"}[target],
        "source": source,
        "window": window,
        "expected_text": expected_text,
        "primary_emotion": primary_emotion,
        "secondary_emotion": secondary_emotion,
        "dramatic_function": dramatic_function,
        "intensity_1_to_5": intensity,
        "source_scene": source_scene,
        "selection_reason": selection_reason,
        "speaker_role": speaker_role,
        "speaker_certainty": speaker_certainty,
        "source_role_warning": source_role_warning,
        "coverage_gap": coverage_gap,
        "match_floor": match_floor,
        "verification_floor": verification_floor,
    }


NARRATOR_SPECS: tuple[dict[str, Any], ...] = (
    spec(
        "narrator_ud_ecstatic_bucket_affection", "narrator", "narrator_ultra_deluxe", (14596.0, 14610.5),
        "Finally, yes! The bucket! Yes, yes, yes! I love that bucket.",
        "Ecstatic affection", "Possessive delight", "Overjoyed reunion with a cherished object", 5,
        "Reassurance Bucket pickup", "A rare authentic peak of open joy and attachment, which prior generated joy routes failed to preserve.",
        speaker_role="Narrator character performance", coverage_gap="joy_and_ecstatic_attachment",
    ),
    spec(
        "narrator_ud_manic_victory", "narrator", "narrator_ultra_deluxe", (14152.0, 14164.5),
        "The bucket is the exciting and captivating new content that I promised. I did it! I win! I made a sequel to The Stanley Parable!",
        "Manic triumph", "Grandiose pride", "Unrestrained victory declaration", 5,
        "Bucket New Content ending", "Provides manic success rather than generic happiness.",
        speaker_role="Narrator character performance", coverage_gap="triumph_and_mania",
    ),
    spec(
        "narrator_ud_explosive_indignation", "narrator", "narrator_ultra_deluxe", (14259.0, 14274.5),
        "What quality assurance department signed off on this? I'm infuriated and I'm offended, and I intend to find these people on Twitter and hold them personally accountable.",
        "Explosive indignation", "Personal offense", "Furious public condemnation", 5,
        "New Content disappointment", "Supplies authentic high-intensity anger after weak generated rage results.",
        speaker_role="Narrator character performance", coverage_gap="explosive_anger",
    ),
    spec(
        "narrator_ud_shame_and_guilt", "narrator", "narrator_ultra_deluxe", (14270.0, 14284.5),
        "It's my fault, Stanley. I built up too much anticipation around the new content, I'm afraid. It could never have lived up to such expectations.",
        "Guilt", "Deflated shame", "Taking responsibility after failure", 3,
        "New Content disappointment", "Adds inwardly directed failure rather than anger at another character.",
        speaker_role="Narrator character performance", coverage_gap="shame_and_accountability",
    ),
    spec(
        "narrator_ud_warm_reconciliation", "narrator", "narrator_ultra_deluxe", (14278.0, 14299.5),
        "If you're still with me, why don't we just reset the game, and we'll try to get back to what The Stanley Parable is really about. No frills. No gimmicks. Just you and me having a great time together like always. What do you say, friend?",
        "Hopeful reconciliation", "Warm companionship", "Repairing a relationship after disappointment", 3,
        "New Content disappointment", "Adds relational repair and vulnerable warmth.",
        speaker_role="Narrator character performance", coverage_gap="reconciliation_and_companionship",
    ),
    spec(
        "narrator_ud_creative_insecurity", "narrator", "narrator_ultra_deluxe", (4874.0, 4892.5),
        "Where did I mess up the joke? Should I have paused for longer? Or spoken quicker? Comedic timing is so difficult. I wish I were better at it.",
        "Creative insecurity", "Embarrassment", "Self-conscious post-failure analysis", 3,
        "Comedic Timing ending", "Adds uncertainty, embarrassment, and self-critique.",
        speaker_role="Narrator character performance", coverage_gap="insecurity_and_embarrassment",
    ),
    spec(
        "narrator_ud_petulant_hurt", "narrator", "narrator_ultra_deluxe", (7968.0, 7981.0),
        "Oh, you don't want to see the cool surprise I made for you? Well, fine! You're a dork anyway, so who cares? Oh. Never mind, you're not a dork.",
        "Petulant hurt", "Immediate remorse", "Childish rejection followed by backtracking", 3,
        "Ignoring the Memory Zone vent", "Captures an emotional reversal within one turn.",
        speaker_role="Narrator character performance", coverage_gap="hurt_and_remorse",
    ),
    spec(
        "narrator_ud_contemptuous_disbelief", "narrator", "narrator_ultra_deluxe", (7516.0, 7534.0),
        "Are you hallucinating? This is a tractor! It's an enormous machine that tills the earth! I thought this was a gimme. How on earth did you manage to screw it up? Absolutely incredible!",
        "Contemptuous disbelief", "Irritated astonishment", "Scolding an absurd failure", 4,
        "What Is a Bucket quiz", "Adds incredulous scolding distinct from cold menace.",
        speaker_role="Narrator character performance", coverage_gap="contempt_and_disbelief",
    ),
    spec(
        "narrator_ud_bittersweet_nostalgia", "narrator", "narrator_ultra_deluxe", (9176.0, 9187.0),
        "We were so innocent. We'll never be like that again, Stanley.",
        "Bittersweet nostalgia", "Mourning lost innocence", "Remembering a simpler shared past", 3,
        "Figurines Memory Zone", "Adds quiet loss rather than overt grief.",
        speaker_role="Narrator character performance", coverage_gap="bittersweet_nostalgia",
    ),
    spec(
        "narrator_ud_separation_panic", "narrator", "narrator_ultra_deluxe", (8912.0, 8929.0),
        "No, no, no! I'm not done! I'm not ready to move on! Stop the loading screen! Isn't there some way we can stay here? Keep enjoying these figurines?",
        "Separation panic", "Compulsive attachment", "Refusing an ending and loss", 5,
        "Figurines ending", "Provides authentic frantic resistance to separation.",
        speaker_role="Narrator character performance", coverage_gap="separation_panic",
    ),
    spec(
        "narrator_ud_loneliness_confession", "narrator", "narrator_ultra_deluxe", (9076.0, 9094.0),
        "Why did I invent Stanley? Was I lonely? Yes, perhaps that's it. Perhaps I needed to imagine I had companionship, and Stanley really did make for a wonderful companion.",
        "Loneliness", "Vulnerable affection", "Confessing emotional dependence", 4,
        "Figurines ending self-reflection", "Adds explicit loneliness and dependence.",
        speaker_role="Narrator character performance", coverage_gap="loneliness_and_dependency",
    ),
    spec(
        "narrator_skip_desperate_pleading", "narrator", "narrator_ultra_deluxe", (11018.0, 11037.0),
        "But I will find a way, I promise you. Just need to not do anything. Don't press the skip button. Please, please, please do not press the skip button. Just wait here. Wait here for me.",
        "Desperate pleading", "Panic", "Begging against imminent abandonment", 5,
        "Skip Button ending", "Supplies direct pleading with escalating urgency.",
        speaker_role="Narrator character performance", coverage_gap="desperate_pleading",
    ),
    spec(
        "narrator_skip_abandonment_terror", "narrator", "narrator_ultra_deluxe", (11102.0, 11131.0),
        "Knowing that you're going to do it, and that I'm going to be stuck all alone, and then I had the power to prevent it all from happening, if only I'd held my tongue. It's all out of my control now. Just you. Just your decision as to exactly when you're going to make me suffer, to leave me all alone.",
        "Abandonment terror", "Resentful helplessness", "Anticipating deliberate emotional harm", 5,
        "Skip Button ending", "Adds sustained terror and loss of control.",
        speaker_role="Narrator character performance", coverage_gap="abandonment_terror",
    ),
    spec(
        "narrator_skip_lonely_deprivation", "narrator", "narrator_ultra_deluxe", (11215.0, 11235.0),
        "I've been sitting here all that time. Just sitting here. Not a single person to speak with. And you'd think that that's just how it's always been, right? Me talking, and you saying nothing.",
        "Profound loneliness", "Emotional deprivation", "Naming prolonged isolation", 4,
        "Skip Button ending", "Provides exhausted loneliness without immediate panic.",
        speaker_role="Narrator character performance", coverage_gap="prolonged_isolation",
    ),
    spec(
        "narrator_skip_desperate_surrender", "narrator", "narrator_ultra_deluxe", (11252.0, 11278.0),
        "I needed to know that someone was listening. I needed there to be a vessel through which my words were moving. It was the vessel I needed, Stanley, not the outcomes, not the story. None of that matters anymore. I'll give it all up. I'll give up every branching path. I'll burn my story to the ground.",
        "Desperate surrender", "Dependent pleading", "Abandoning everything to preserve connection", 5,
        "Skip Button ending", "Adds surrender and bargaining at extreme intensity.",
        speaker_role="Narrator character performance", coverage_gap="surrender_and_bargaining",
    ),
    spec(
        "narrator_skip_regret_and_grief", "narrator", "narrator_ultra_deluxe", (11425.0, 11450.0),
        "I felt nothing at all but regret for the longest time. Stanley, days, months, I lost it all in a blur of the deepest longing to undo the past.",
        "Grief-stricken regret", "Longing", "Mourning an irreversible choice", 4,
        "Skip Button ending", "Provides sustained grief rather than merely sad wording.",
        speaker_role="Narrator character performance", coverage_gap="grief_and_regret",
    ),
    spec(
        "narrator_skip_existential_dread", "narrator", "narrator_ultra_deluxe", (11540.0, 11562.0),
        "I wish you to feel afraid, as I do. That perhaps one day this state of mind will consume you as well. Perhaps you will somehow, in some way, have to live as I do now.",
        "Existential dread", "Bitter despair", "Projecting unbearable suffering onto another", 5,
        "Skip Button ending", "Adds quiet but severe existential fear.",
        speaker_role="Narrator character performance", coverage_gap="existential_dread",
    ),
    spec(
        "narrator_demo_theatrical_anticipation", "narrator", "narrator_demonstration", (88.0, 101.0),
        "A tease, just enough to leave you hungry for more! How exciting! Can't you just feel that nervous tension? The looming uncertainty?",
        "Theatrical anticipation", "Playful excitement", "Hyping an audience before a reveal", 4,
        "Demonstration introduction", "Adds audience-facing excitement and suspense.",
        speaker_role="Narrator character performance", coverage_gap="anticipation_and_showmanship",
    ),
    spec(
        "narrator_demo_panicked_failure", "narrator", "narrator_demonstration", (846.0, 870.0),
        "What? No, no, no, no, no! It can't be over yet! You didn't see anything! Everything that was supposed to demonstrate why The Stanley Parable is a quality experience worth your time and money! No, no, no, no, no! We have to get out of here. We have to find something for you to do, anything!",
        "Panicked failure", "Frantic desperation", "Scrambling after a public demonstration collapses", 5,
        "Demonstration ending malfunction", "Adds authentic frantic failure distinct from abandonment panic.",
        speaker_role="Narrator character performance", coverage_gap="public_failure_panic",
    ),
    spec(
        "narrator_demo_warm_nostalgia", "narrator", "narrator_demonstration", (1087.0, 1137.0),
        "That was lovely. No concerns about where it was all going. No confusion. Just a blank slate. Yes, that's what I want. It's all so fresh in my memory. They were such wonderful moments.",
        "Warm nostalgia", "Relieved fondness", "Revisiting an uncomplicated shared experience", 3,
        "Demonstration restart reflection", "Adds gentle pleasure and relief rather than melancholy nostalgia.",
        speaker_role="Narrator character performance", coverage_gap="warm_nostalgia",
    ),
    spec(
        "narrator_demo_bitter_exasperation", "narrator", "narrator_demonstration", (730.0, 745.0),
        "We need to get you out of here before you start forming impressions of The Stanley Parable based on whatever the hell this egg game is. We need to get up. We need to start over.",
        "Bitter exasperation", "Urgent embarrassment", "Rejecting a humiliating derailment", 4,
        "Demonstration egg game", "Adds embarrassed urgency and irritation.",
        speaker_role="Narrator character performance", coverage_gap="exasperation_and_embarrassment",
    ),
    spec(
        "narrator_official_moved_by_vulnerability", "narrator", "narrator_letters", (244.0, 264.0),
        "Wow, I'm actually quite moved by the vulnerability of this letter. To have such naked faith in us to deliver a quality product by not releasing it at all.",
        "Moved tenderness", "Dry irony", "Responding sincerely to exposed vulnerability", 3,
        "Official letters and emails video", "Adds restrained tenderness blended with characteristic irony.",
        speaker_role="Narrator character performance", coverage_gap="tenderness_with_irony",
    ),
    spec(
        "narrator_official_rallying_determination", "narrator", "narrator_letters", (274.0, 307.0),
        "I've never felt so deeply connected to our fan base before. And yes, yes, I too can take a stand. I will give our beloved fans what they've been asking for, what they've demanded. I will be a champion for you, the people.",
        "Rallying determination", "Grandiose solidarity", "Public vow to champion a cause", 4,
        "Official letters and emails video", "Adds public conviction and motivational authority.",
        speaker_role="Narrator character performance", coverage_gap="rallying_determination",
    ),
)

BENNY_SPECS: tuple[dict[str, Any], ...] = (
    spec(
        "benny_mars_emergency_broadcast", "benny", "benny_mars", (0.5, 13.5),
        "People of Mars, this is Bernice Summerfield broadcasting on, hopefully, an emergency frequency. I'm trapped in a pyramid. Yes, a pyramid.",
        "Urgent distress", "Dry disbelief", "Improvised emergency broadcast", 4,
        "Opening transmission", "Authentic Benny under pressure while retaining her dry self-awareness.",
        speaker_role="Benny character performance", coverage_gap="credible_fear_and_emergency_distress",
    ),
    spec(
        "benny_mars_grave_warning", "benny", "benny_mars", (19.5, 35.0),
        "All you need to know is that Egyptian gods were real. They came from Phobos and Osiris, had terrible powers. And Sutekh, the nastiest of the lot, has just come back to life in the middle of your war.",
        "Grave warning", "Controlled fear", "Explaining an existential threat", 4,
        "Emergency warning", "Adds sober danger exposition with genuine stakes.",
        speaker_role="Benny character performance", coverage_gap="grave_warning",
    ),
    spec(
        "benny_mars_desperate_call", "benny", "benny_mars", (35.5, 46.5),
        "Hello? Anyone? God, that's heading your way. Stop fighting. Send help.",
        "Desperate urgency", "Fear", "Calling for immediate aid", 5,
        "Failed emergency contact", "Targets the fear and panic that prior generated Benny routes lacked.",
        speaker_role="Benny character performance", coverage_gap="panic_and_pleading", match_floor=0.62, verification_floor=0.60,
    ),
    spec(
        "benny_diary_buoyant_confidence", "benny", "benny_retrospective", (350.0, 380.0),
        "Dear diary, it's me again, Benny. Have you missed me? I've been so busy recently, you know, saving the world a few times, that sort of thing. But now the world is safe again.",
        "Buoyant confidence", "Intimate playfulness", "Confiding directly to a familiar listener", 3,
        "Retrospective character excerpt", "Adds ordinary charismatic self-possession and diary intimacy.",
        speaker_role="Benny character performance excerpt", source_role_warning="The surrounding source is an interview retrospective; only this isolated audio-drama excerpt is eligible.", coverage_gap="intimate_conversation_and_confidence",
    ),
    spec(
        "benny_shock_grief", "benny", "benny_new_adventures_trailer", (29.5, 40.0),
        "I can't believe it. I can't. I just... I just can't.",
        "Shock", "Emerging grief", "Speechless reaction to devastating news", 4,
        "New Adventures trailer", "Adds broken, stunned grief rather than composed vulnerability.",
        speaker_role="Probable Benny character performance", speaker_certainty="medium", source_role_warning="Trailer mixing makes speaker identity and cleanliness mandatory review items.", coverage_gap="shock_and_grief", match_floor=0.60, verification_floor=0.58,
    ),
    spec(
        "benny_controlled_dread_resolve", "benny", "benny_dead_and_buried", (36.5, 50.5),
        "But we can't keep running and hiding. We decided to fight back. But we have to choose our moment. Wait until Brax shows his hand and makes his move.",
        "Determined resolve", "Controlled dread", "Committing to resist a stronger enemy", 4,
        "Dead and Buried opening", "Adds fear held under disciplined strategic resolve.",
        speaker_role="Benny character performance", coverage_gap="determination_under_fear",
    ),
    spec(
        "benny_comic_panic_pleading", "benny", "benny_dead_and_buried", (262.0, 270.0),
        "Please don't be indestructible. Please don't be indestructible. Huh?",
        "Comic panic", "Pleading fear", "Begging an immediate threat to be vulnerable", 4,
        "Robot pursuit", "Adds frightened pleading without losing Benny's comic timing.",
        speaker_role="Benny character performance", coverage_gap="comic_fear_and_pleading", match_floor=0.58, verification_floor=0.55,
    ),
    spec(
        "benny_emergency_distress", "benny", "benny_dead_and_buried", (479.0, 490.0),
        "This is a general distress call. I'm on the planet Javada. I'm being threatened by a robot, and I'm over thirty miles from my ship.",
        "Emergency distress", "Controlled fear", "Reporting danger while stranded", 4,
        "Robot pursuit distress call", "Adds clear operational urgency suitable for audiodrama action scenes.",
        speaker_role="Benny character performance", coverage_gap="operational_emergency_fear",
    ),
    spec(
        "benny_explosive_frustration", "benny", "benny_trailer_003", (18.0, 27.5),
        "Today I am so sick of this. You never listen. You always think you know best.",
        "Explosive frustration", "Personal betrayal", "Confronting a trusted person who will not listen", 5,
        "New Adventures trailer confrontation", "Targets stronger anger than the prior controlled-anger route.",
        speaker_role="Probable Benny character performance", speaker_certainty="medium", source_role_warning="The trailer contains multiple speakers; confirm this is Benny before approval.", coverage_gap="explosive_anger", match_floor=0.60, verification_floor=0.58,
    ),
    spec(
        "benny_tender_reassurance", "benny", "benny_teaser_002", (34.0, 40.5),
        "You're not alone, you know. I'm still here.",
        "Tender reassurance", "Protective loyalty", "Promising not to abandon someone", 3,
        "New Adventures teaser", "Potentially fills the softer intimacy gap, but the trailer does not make speaker identity explicit.",
        speaker_role="Unconfirmed probable Benny character performance", speaker_certainty="low", source_role_warning="Do not approve unless the voice is clearly Lisa Bowerman performing Benny.", coverage_gap="soft_intimacy_and_reassurance", match_floor=0.58, verification_floor=0.55,
    ),
)

DOCTOR_SPECS: tuple[dict[str, Any], ...] = (
    spec(
        "doctor_wounded_fury", "doctor", "doctor_blood_and_steel", (36.0, 55.0),
        "Oh yes, I'm the Doctor. I'm all bon mots and bonbons, but right now I'm furious. You took away so many people. You promised you'd make their lives better. Liar. I will hurt you until I feel better, and I don't think I ever will.",
        "Wounded fury", "Moral outrage", "Confronting an abuser after mass harm", 5,
        "Blood and Steel confrontation", "Provides a rare explicit Seventh Doctor rage performance with personal pain underneath it.",
        speaker_role="Seventh Doctor character performance", coverage_gap="clean_high_intensity_anger",
    ),
    spec(
        "doctor_weary_mortality", "doctor", "doctor_last_day", (46.0, 59.5),
        "The days tick by. You do all you can because you know your time draws near, because things always end.",
        "Weary mortality", "Quiet resignation", "Acknowledging the approach of an ending", 4,
        "The Last Day monologue", "Adds exhaustion and mortality rather than generic grave warning.",
        speaker_role="Seventh Doctor character performance", coverage_gap="weariness_and_resignation",
    ),
    spec(
        "doctor_indomitable_determination", "doctor", "doctor_last_day", (58.0, 76.5),
        "I'll use everything I've learned, every means at my disposal, to make this cosmos better. I am the Doctor, after all. I won't stop. I can't stop. Not while there's work to do.",
        "Indomitable determination", "Moral conviction", "Vowing to continue despite exhaustion", 5,
        "The Last Day monologue", "Adds clean heroic resolve and authority.",
        speaker_role="Seventh Doctor character performance", coverage_gap="determination_and_moral_authority",
    ),
    spec(
        "doctor_existential_uncertainty", "doctor", "doctor_last_day", (74.0, 82.5),
        "And on the last day, I'll know if I was right.",
        "Existential uncertainty", "Solemn reflection", "Questioning a lifetime of choices", 4,
        "The Last Day monologue", "Adds doubt beneath authority.",
        speaker_role="Seventh Doctor character performance", coverage_gap="uncertainty_and_self_questioning", match_floor=0.58, verification_floor=0.55,
    ),
    spec(
        "doctor_calm_moral_defiance", "doctor", "doctor_happiness_patrol", (74.0, 86.0),
        "Why don't you do it, then? Look me in the eye. Pull the trigger. End my life.",
        "Calm moral defiance", "Controlled menace", "Disarming an armed opponent through psychological pressure", 5,
        "Happiness Patrol gun confrontation", "Adds quiet authority under mortal threat.",
        speaker_role="Seventh Doctor character performance", source_role_warning="The surrounding scene has another speaker; the selected turn must contain only the Doctor.", coverage_gap="calm_defiance",
    ),
    spec(
        "doctor_analytical_authority", "doctor", "doctor_kingdom_of_silver", (110.0, 123.0),
        "I can see from your expression that you've had guns pointed at you before. You're not afraid. Careful, but wary. A career soldier, perhaps. I know your kind quite well.",
        "Analytical authority", "Wary confidence", "Reading and controlling a dangerous stranger", 3,
        "Kingdom of Silver first meeting", "Adds ordinary in-character observation and controlled dominance.",
        speaker_role="Seventh Doctor character performance", source_role_warning="This is a clean scene excerpt but not an official channel upload; audio quality requires review.", coverage_gap="ordinary_conversational_authority",
    ),
    spec(
        "doctor_playful_disarmament", "doctor", "doctor_kingdom_of_silver", (196.0, 208.5),
        "Me? Oh, for the tea. Care for a cup? It's really very good. Best in the quadrant.",
        "Playful disarmament", "Eccentric confidence", "Defusing suspicion with absurd hospitality", 3,
        "Kingdom of Silver first meeting", "Adds clean everyday eccentricity without a generated artifact layer.",
        speaker_role="Seventh Doctor character performance", source_role_warning="This is a clean scene excerpt but not an official channel upload; audio quality requires review.", coverage_gap="playful_conversation",
    ),
    spec(
        "doctor_gentle_contrition", "doctor", "doctor_kingdom_of_silver", (225.5, 233.5),
        "There, now we're being much more civilized. I should apologize, actually.",
        "Gentle contrition", "Social warmth", "Lowering tension and acknowledging manipulation", 2,
        "Kingdom of Silver first meeting", "Adds softer interpersonal repair.",
        speaker_role="Seventh Doctor character performance", source_role_warning="This is a clean scene excerpt but not an official channel upload; audio quality requires review.", coverage_gap="contrition_and_compassion", match_floor=0.60, verification_floor=0.58,
    ),
    spec(
        "doctor_gentle_concern", "doctor", "doctor_silver_and_ice", (9.5, 15.5),
        "Hello? Is someone there? Are you hurt?",
        "Gentle concern", "Cautious attention", "Checking on an unseen injured person", 2,
        "Silver and Ice opening", "Targets clean compassion, which previous generated routes rendered with artifacts.",
        speaker_role="Probable Seventh Doctor character performance", speaker_certainty="medium", source_role_warning="Confirm the speaker before bank approval.", coverage_gap="clean_compassion", match_floor=0.58, verification_floor=0.55,
    ),
    spec(
        "doctor_dry_wit", "doctor", "doctor_silver_and_ice", (56.0, 72.5),
        "It was a while ago now. Twenty years for you. And I looked rather different back then. Taller.",
        "Dry wit", "Fond self-mockery", "Deflating a serious recognition with a joke", 2,
        "Silver and Ice reunion", "Adds an authentic dry-wit route without synthetic music-like artifacts.",
        speaker_role="Seventh Doctor character performance", coverage_gap="clean_dry_wit",
    ),
    spec(
        "doctor_comic_disorientation", "doctor", "doctor_time_and_rani", (106.0, 126.0),
        "That was a nice nap. Now, down to business. I'm a bit worried about the temporal flicker in Sector Thirteen. There's a bicentennial refit of the TARDIS to book in. I must just pop over to Centauri Seven, and then perhaps a quick holiday. Right, that all seems quite clear. Just three small points. Where am I? Who am I? And who are you?",
        "Comic disorientation", "Brisk self-confidence", "Masking confusion with energetic planning", 4,
        "Time and the Rani post-regeneration", "Adds high-energy comic confusion and rapid tonal reversal.",
        speaker_role="Seventh Doctor character performance", coverage_gap="comic_confusion_and_energy", match_floor=0.62, verification_floor=0.60,
    ),
    spec(
        "doctor_firm_refusal", "doctor", "doctor_time_and_rani", (199.0, 208.5),
        "Stay away. Whatever you've brought me here for, I'm having no part of it.",
        "Firm refusal", "Moral disgust", "Rejecting coercion and unethical work", 4,
        "Time and the Rani confrontation", "Adds direct boundary-setting authority.",
        speaker_role="Seventh Doctor character performance", coverage_gap="firm_refusal_and_disgust", match_floor=0.60, verification_floor=0.58,
    ),
)

ALL_SPECS: tuple[dict[str, Any], ...] = NARRATOR_SPECS + BENNY_SPECS + DOCTOR_SPECS


class AtlasError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_audio_path(source_key: str, narrator_cache: Path, character_cache: Path) -> Path:
    metadata = SOURCES[source_key]
    root = narrator_cache if metadata["cache_group"] == "narrator" else character_cache
    return root / f"{metadata['youtube_id']}.m4a"


def download_source(source_key: str, narrator_cache: Path, character_cache: Path) -> Path:
    metadata = SOURCES[source_key]
    root = narrator_cache if metadata["cache_group"] == "narrator" else character_cache
    root.mkdir(parents=True, exist_ok=True)
    expected = source_audio_path(source_key, narrator_cache, character_cache)
    if expected.is_file():
        return expected
    subprocess.run(
        [
            "yt-dlp",
            "--no-playlist",
            "--extract-audio",
            "--audio-format", "m4a",
            "--audio-quality", "0",
            "--output", str(root / "%(id)s.%(ext)s"),
            f"https://www.youtube.com/watch?v={metadata['youtube_id']}",
        ],
        check=True,
    )
    if not expected.is_file():
        alternatives = sorted(root.glob(f"{metadata['youtube_id']}.*"))
        raise AtlasError(f"Download did not create {expected}; found {alternatives}")
    return expected


def download_sources(args: argparse.Namespace) -> dict[str, Any]:
    narrator_cache = Path(args.narrator_cache_root).expanduser().resolve()
    character_cache = Path(args.character_cache_root).expanduser().resolve()
    selected_targets = set(args.target or ("narrator", "benny", "doctor"))
    source_keys = sorted({row["source"] for row in ALL_SPECS if row["target"] in selected_targets})
    rows = []
    for source_key in source_keys:
        path = download_source(source_key, narrator_cache, character_cache)
        rows.append({"source": source_key, "path": str(path), "sha256": sha256_file(path)})
    return {"source_count": len(rows), "sources": rows}


def transcribe_window(audio: Path, start: float, end: float, whisper_model: Path) -> dict[str, Any]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=str(whisper_model),
        language="en",
        word_timestamps=True,
        condition_on_previous_text=False,
        clip_timestamps=f"{start},{end}",
        verbose=False,
    )
    words: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            token = str(word.get("word") or "").strip()
            normalized = normalize_words(token)
            if not normalized:
                continue
            words.append(
                {
                    "word": token,
                    "normalized": normalized[0],
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "probability": float(word.get("probability") or 0.0),
                }
            )
    return {"text": str(result.get("text") or "").strip(), "words": words}


def best_word_span(words: list[dict[str, Any]], expected_text: str, floor: float) -> tuple[int, int, float]:
    expected = normalize_words(expected_text)
    actual = [word["normalized"] for word in words]
    if not expected or not actual:
        raise AtlasError("Expected or transcribed word sequence is empty")
    best = (-1, -1, -1.0)
    low = max(1, int(len(expected) * 0.66))
    high = min(len(actual), int(len(expected) * 1.40) + 3)
    for size in range(low, high + 1):
        for start in range(0, len(actual) - size + 1):
            end = start + size
            ratio = difflib.SequenceMatcher(None, expected, actual[start:end]).ratio()
            ratio -= abs(size - len(expected)) / max(len(expected), 1) * 0.035
            if ratio > best[2]:
                best = (start, end, ratio)
    if best[2] < floor:
        raise AtlasError(f"Transcript match is too weak: {best[2]:.3f} < {floor:.3f} for {expected_text!r}")
    return best


def extract_audio(source: Path, start: float, end: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-ss", f"{max(0.0, start):.3f}",
            "-to", f"{end:.3f}",
            "-i", str(source),
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temporary),
        ],
        check=True,
    )
    audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
    temporary.unlink(missing_ok=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    if mono.size < sample_rate * 0.40:
        raise AtlasError(f"Extracted clip is too short: {output}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono *= min(1.0, 0.78 / peak)
    sf.write(output, mono, 24000, subtype="PCM_16")


def build_row(specification: dict[str, Any], source: Path, whisper_model: Path, output_root: Path) -> dict[str, Any]:
    source_metadata = SOURCES[specification["source"]]
    start, end = specification["window"]
    context = transcribe_window(source, start, end, whisper_model)
    start_index, end_index, match = best_word_span(
        context["words"], specification["expected_text"], float(specification["match_floor"])
    )
    selected = context["words"][start_index:end_index]
    clip_start = max(start, selected[0]["start"] - 0.14)
    clip_end = min(end, selected[-1]["end"] + 0.34)
    output = output_root / "clips" / specification["target"] / f"{specification['clip_id']}.wav"
    extract_audio(source, clip_start, clip_end, output)
    verification = transcribe_window(output, 0.0, clip_end - clip_start, whisper_model)
    verification_ratio = difflib.SequenceMatcher(
        None,
        normalize_words(specification["expected_text"]),
        normalize_words(verification["text"]),
    ).ratio()
    if verification_ratio < float(specification["verification_floor"]):
        raise AtlasError(
            f"Verification transcript is too weak: {verification_ratio:.3f} < "
            f"{float(specification['verification_floor']):.3f}"
        )
    return {
        **{key: value for key, value in specification.items() if key not in {"match_floor", "verification_floor"}},
        "window": list(specification["window"]),
        "source_title": source_metadata["title"],
        "source_kind": source_metadata["source_kind"],
        "youtube_id": source_metadata["youtube_id"],
        "source_audio": str(source),
        "source_audio_sha256": sha256_file(source),
        "context_transcript": context["text"],
        "selected_start_seconds": round(clip_start, 3),
        "selected_end_seconds": round(clip_end, 3),
        "selected_duration_seconds": round(clip_end - clip_start, 3),
        "selection_match": round(match, 6),
        "verification_transcript": verification["text"],
        "verification_similarity": round(verification_ratio, 6),
        "audio_path": str(output),
        "audio_sha256": sha256_file(output),
        "assistant_label_status": "prefilled_for_user_correction",
        "production_promotion_allowed": False,
    }


def selected_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    targets = set(args.target or ("narrator", "benny", "doctor"))
    clip_ids = set(args.clip_id or ())
    rows = [row for row in ALL_SPECS if row["target"] in targets and (not clip_ids or row["clip_id"] in clip_ids)]
    unknown = sorted(clip_ids - {row["clip_id"] for row in ALL_SPECS})
    if unknown:
        raise AtlasError(f"Unknown clip IDs: {unknown}")
    return rows


def persist_receipt(output_root: Path, row: dict[str, Any]) -> None:
    path = output_root / "receipts" / row["target"] / f"{row['clip_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, Any]:
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    narrator_cache = Path(args.narrator_cache_root).expanduser().resolve()
    character_cache = Path(args.character_cache_root).expanduser().resolve()
    if not whisper_model.is_dir():
        raise AtlasError(f"Whisper model is missing: {whisper_model}")
    rows = []
    failures = []
    for specification in selected_specs(args):
        receipt_path = output_root / "receipts" / specification["target"] / f"{specification['clip_id']}.json"
        if receipt_path.is_file() and not args.force:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            audio = Path(receipt.get("audio_path") or "")
            if audio.is_file() and sha256_file(audio) == receipt.get("audio_sha256"):
                rows.append(receipt)
                continue
        try:
            source = source_audio_path(specification["source"], narrator_cache, character_cache)
            if not source.is_file():
                if args.download_missing:
                    source = download_source(specification["source"], narrator_cache, character_cache)
                else:
                    raise AtlasError(f"Source audio is missing: {source}")
            row = build_row(specification, source, whisper_model, output_root)
            persist_receipt(output_root, row)
            rows.append(row)
        except Exception as exc:
            failures.append(
                {
                    "clip_id": specification["clip_id"],
                    "target": specification["target"],
                    "source": specification["source"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    failure_path = output_root / "failures.json"
    output_root.mkdir(parents=True, exist_ok=True)
    if failure_path.is_file():
        persisted = {row["clip_id"]: row for row in json.loads(failure_path.read_text(encoding="utf-8"))}
    else:
        persisted = {}
    for row in failures:
        persisted[row["clip_id"]] = row
    for row in rows:
        persisted.pop(row["clip_id"], None)
    failure_path.write_text(json.dumps(list(persisted.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if failures and not args.allow_failures:
        raise AtlasError(f"{len(failures)} candidate extractions failed; see {failure_path}")
    return {"built_count": len(rows), "failure_count": len(failures), "output_root": str(output_root)}


def recover_narrator(args: argparse.Namespace) -> dict[str, Any]:
    source_root = Path(args.source_atlas_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    specifications = {row["clip_id"]: row for row in NARRATOR_SPECS}
    expected = set(specifications)
    recovered = []
    failures = []
    for clip_id in sorted(expected):
        receipt_path = source_root / "receipts" / f"{clip_id}.json"
        if not receipt_path.is_file():
            failures.append(f"missing receipt:{clip_id}")
            continue
        row = json.loads(receipt_path.read_text(encoding="utf-8"))
        source_audio = Path(row.get("audio_path") or "")
        if not source_audio.is_file():
            source_audio = source_root / "clips" / f"{clip_id}.wav"
        if not source_audio.is_file():
            failures.append(f"missing audio:{clip_id}")
            continue
        if sha256_file(source_audio) != row.get("audio_sha256"):
            failures.append(f"hash mismatch:{clip_id}")
            continue
        destination = output_root / "clips" / "narrator" / f"{clip_id}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_audio, destination)
        current = specifications[clip_id]
        for key in (
            "speaker_role", "speaker_certainty", "source_role_warning", "selection_reason",
            "coverage_gap", "dramatic_function", "primary_emotion", "secondary_emotion",
            "intensity_1_to_5", "source_scene",
        ):
            row[key] = current[key]
        row["target"] = "narrator"
        row["target_label"] = "Narrator"
        row["audio_path"] = str(destination)
        row["audio_sha256"] = sha256_file(destination)
        row["recovered_from"] = str(source_root)
        row["production_promotion_allowed"] = False
        persist_receipt(output_root, row)
        recovered.append(row)
    if failures:
        raise AtlasError(f"Narrator recovery failed: {failures}")
    return {"recovered_count": len(recovered), "output_root": str(output_root)}


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    rows = []
    missing = []
    for specification in ALL_SPECS:
        receipt_path = output_root / "receipts" / specification["target"] / f"{specification['clip_id']}.json"
        if not receipt_path.is_file():
            missing.append(specification["clip_id"])
            continue
        rows.append(json.loads(receipt_path.read_text(encoding="utf-8")))
    failures_path = output_root / "failures.json"
    failures = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.is_file() else []
    counts = Counter(row["target"] for row in rows)
    coverage = Counter(row.get("coverage_gap") or "uncategorized" for row in rows)
    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "candidate_count": len(rows),
        "expected_candidate_count": len(ALL_SPECS),
        "target_counts": dict(sorted(counts.items())),
        "source_count": len({row["source"] for row in rows}),
        "coverage_family_count": len(coverage),
        "coverage_families": dict(sorted(coverage.items())),
        "missing_clip_ids": missing,
        "failure_count": len(failures),
        "failures": failures,
        "rows": rows,
        "selection_policy": {
            "transcript_first": True,
            "complete_utterance_required": True,
            "speaker_identity_requires_user_confirmation": True,
            "assistant_emotion_labels_are_provisional": True,
            "one_click_approval_allowed_only_after_listening": True,
            "production_promotion_allowed": False,
        },
        "production_promotion_allowed": False,
    }
    path = output_root / "three-voice-source-atlas.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if missing and not args.allow_missing:
        raise AtlasError(f"Atlas is missing {len(missing)} clips: {missing}")
    return {"candidate_count": len(rows), "missing_count": len(missing), "atlas": str(path)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    path = output_root / "three-voice-source-atlas.json"
    if not path.is_file():
        raise AtlasError(f"Atlas is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    failures = []
    seen = set()
    for row in rows:
        clip_id = row.get("clip_id")
        if clip_id in seen:
            failures.append(f"duplicate:{clip_id}")
        seen.add(clip_id)
        audio = Path(row.get("audio_path") or "")
        if not audio.is_file():
            failures.append(f"missing:{clip_id}")
            continue
        if sha256_file(audio) != row.get("audio_sha256"):
            failures.append(f"hash:{clip_id}")
        info = sf.info(audio)
        if info.samplerate != 24000 or info.channels != 1 or info.subtype != "PCM_16":
            failures.append(f"format:{clip_id}")
        if float(row.get("verification_similarity") or 0.0) < 0.55:
            failures.append(f"transcript:{clip_id}")
        for key in (
            "target", "source_title", "expected_text", "primary_emotion", "secondary_emotion",
            "dramatic_function", "speaker_role", "selection_reason", "coverage_gap",
        ):
            if not row.get(key):
                failures.append(f"field:{clip_id}:{key}")
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{clip_id}")
    expected_counts = {"narrator": len(NARRATOR_SPECS), "benny": len(BENNY_SPECS), "doctor": len(DOCTOR_SPECS)}
    actual_counts = Counter(row.get("target") for row in rows)
    if dict(actual_counts) != expected_counts:
        failures.append(f"target_counts:{dict(actual_counts)}!={expected_counts}")
    policy = payload.get("selection_policy") or {}
    if policy.get("speaker_identity_requires_user_confirmation") is not True:
        failures.append("policy:speaker_confirmation")
    if payload.get("production_promotion_allowed") is not False:
        failures.append("policy:production_promotion")
    if failures:
        raise AtlasError(f"Atlas validation failed: {failures}")
    return {
        "candidate_count": len(rows),
        "target_counts": dict(actual_counts),
        "source_count": payload.get("source_count"),
        "failure_count": 0,
        "atlas": str(path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a transcript-guided three-character expressive source atlas.")
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download-sources")
    download.add_argument("--narrator-cache-root", default=str(DEFAULT_NARRATOR_CACHE))
    download.add_argument("--character-cache-root", default=str(DEFAULT_CHARACTER_CACHE))
    download.add_argument("--target", action="append", choices=("narrator", "benny", "doctor"))

    build_parser = sub.add_parser("build")
    build_parser.add_argument("--whisper-model", required=True)
    build_parser.add_argument("--output-root", required=True)
    build_parser.add_argument("--narrator-cache-root", default=str(DEFAULT_NARRATOR_CACHE))
    build_parser.add_argument("--character-cache-root", default=str(DEFAULT_CHARACTER_CACHE))
    build_parser.add_argument("--target", action="append", choices=("narrator", "benny", "doctor"))
    build_parser.add_argument("--clip-id", action="append")
    build_parser.add_argument("--download-missing", action="store_true")
    build_parser.add_argument("--force", action="store_true")
    build_parser.add_argument("--allow-failures", action="store_true")

    recover = sub.add_parser("recover-narrator")
    recover.add_argument("--source-atlas-root", required=True)
    recover.add_argument("--output-root", required=True)

    assemble_parser = sub.add_parser("assemble")
    assemble_parser.add_argument("--output-root", required=True)
    assemble_parser.add_argument("--allow-missing", action="store_true")

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "download-sources":
            result = download_sources(args)
        elif args.command == "build":
            result = build(args)
        elif args.command == "recover-narrator":
            result = recover_narrator(args)
        elif args.command == "assemble":
            result = assemble(args)
        else:
            result = validate(args)
    except (AtlasError, subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
