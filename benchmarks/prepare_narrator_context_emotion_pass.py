from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_narrator_context_emotion_v1"
ASSET_ROOT = Path(__file__).with_name("narrator_context_emotion_assets")

CATEGORIES = {
    "neutral": "Neutral / ordinary",
    "reflective_absorbed": "Reflective / intellectually absorbed",
    "dry_sarcastic": "Dry / sarcastic / mock-sincere",
    "baffled_concern": "Baffled / concerned",
    "persuasive_manipulative": "Persuasive / manipulative concern",
    "smug_menace": "Smug / cheerfully menacing",
    "wounded_rage": "Wounded anger / rage",
    "panic_alarm": "Alarm / panic",
    "pleading_desperation": "Pleading / desperate",
    "vulnerable_sad": "Vulnerable / sorrowful",
    "defensive_reassurance": "Defensive self-reassurance",
    "exuberant_joy": "Exuberant joy / triumph",
    "contemptuous": "Contempt / snide exasperation",
    "frustrated_helpless": "Frustrated / helpless",
    "boastful_pride": "Boastful pride",
}

# Only rows where context materially changes or audits the prior training signal.
CORRECTIONS: dict[str, dict[str, Any]] = {
    "4ee56800e27fb05d": {
        "scene": "Confusion Ending — following the Adventure Line",
        "source_reference": "Confusion Ending/Dialogue",
        "context": "The Narrator has become absorbed in an increasingly convoluted philosophical argument about whether a journey creates its own destination.",
        "category": "reflective_absorbed",
        "instruction": "Reflective, intellectually absorbed narration; curious and increasingly carried away by the philosophical question, not emotionally neutral.",
        "reason": "A question is a sentence function, not an emotion. Here it is thoughtful absorption.",
        "action": "replace_metadata",
    },
    "e90923cadfd529bf": {
        "scene": "Employee Lounge detour",
        "source_reference": "2013 HD Remix Dialogue — Employee Lounge",
        "context": "Stanley has ignored the intended route and lingered in an ordinary lounge. The Narrator responds with exaggerated praise and mock gratitude.",
        "category": "dry_sarcastic",
        "instruction": "Deadpan mock sincerity with exaggerated praise and restrained sarcasm.",
        "reason": "The sarcasm is the point of the scene rather than a minor accent on neutral narration.",
        "action": "replace_metadata",
    },
    "49a53fca518f4632": {
        "scene": "Confusion Ending — false victory",
        "source_reference": "Confusion Ending/Dialogue",
        "context": "The Narrator backs away from declaring Stanley the winner and recognizes the result as unfair.",
        "transcript": "Some people win fair and square, and this was not one of those situations.",
        "category": "baffled_concern",
        "instruction": "Uneasy, self-correcting disapproval after a hollow victory; controlled but visibly dissatisfied.",
        "reason": "Corrects the transcript and captures the Narrator revising his own conclusion.",
        "action": "replace_metadata",
    },
    "dfb26913eac3bbba": {
        "scene": "Dream Ending — Stanley tries to wake himself",
        "source_reference": "Dream Ending/Dialogue",
        "context": "This is part of an escalating internal plea to return to an ordinary life as Stanley loses confidence that the world is real.",
        "category": "pleading_desperation",
        "instruction": "Desperate self-soothing and longing for normality, spoken with controlled fear that is beginning to fracture.",
        "reason": "The line is not merely subdued; it belongs to an escalating breakdown.",
        "action": "replace_metadata",
    },
    "02a843d600853f2d": {
        "scene": "Dream Ending — Mariella reassures herself",
        "source_reference": "Dream Ending/Dialogue",
        "context": "The Narrator is voicing Mariella's forceful internal reassurance after she encounters Stanley. The original clip may begin across a boundary.",
        "category": "defensive_reassurance",
        "instruction": "Firm, defensive self-reassurance with deliberate certainty covering underlying unease.",
        "reason": "Role-voiced contextual line and possible clipped boundary. Prefer the clean replacement in the supplement.",
        "action": "replace_audio",
    },
    "5b6d7bfe9539577f": {
        "scene": "Countdown Ending — the Narrator explains the co-workers",
        "source_reference": "Countdown Ending/Dialogue",
        "context": "The Narrator has announced that Stanley will die and offers the answer as a moment of amusement before obliteration.",
        "category": "smug_menace",
        "instruction": "Calmly sadistic, smug revelation delivered with cheerful superiority and no sympathy.",
        "reason": "The surrounding taunt makes this gleeful menace, not bitterness or generic emphasis.",
        "action": "replace_metadata",
    },
    "ff219fc5a4780a70": {
        "scene": "Playtest Ending — rating of three",
        "source_reference": "Playtest Ending/Dialogue",
        "context": "The Narrator reacts with contemptuous disappointment to a middling rating and immediately challenges Stanley to form a real opinion.",
        "category": "contemptuous",
        "instruction": "Snide, contemptuous exasperation with clipped disbelief and a dismissive rhetorical edge.",
        "reason": "The old clip begins with a stray word. Replace it with the clean scene recut in the supplement.",
        "action": "replace_audio",
    },
    "6d652c6a6aad892d": {
        "scene": "Incorrect Ending — destroyed game",
        "source_reference": "Incorrect Ending/Dialogue",
        "context": "After shutting the broken game down, the Narrator finds himself trapped in its ruins with the player who destroyed the only thing that belonged to him.",
        "category": "wounded_rage",
        "instruction": "Wounded rage and spite, beginning in stunned disbelief and hardening into a personal accusation.",
        "reason": "Your 'upset and spiteful' reading was correct; the scene makes the underlying grief explicit.",
        "action": "replace_metadata",
    },
    "26d2a3c73903abd2": {
        "scene": "Incorrect Ending — destroyed game",
        "source_reference": "Incorrect Ending/Dialogue",
        "context": "The Narrator has just said his entire game was destroyed and that the player ruined the only thing in the world that was his.",
        "category": "wounded_rage",
        "instruction": "Accusatory outrage rooted in personal hurt; venomous disbelief rather than ordinary urgency.",
        "reason": "Possible leading artifact. Prefer the clean replacement in the supplement.",
        "action": "replace_audio",
    },
    "bab15047e707e43c": {
        "scene": "Confusion Ending — failed restart",
        "source_reference": "Confusion Ending/Dialogue",
        "context": "The Narrator has restarted the game but the expected story is missing, leading him to suspect Stanley changed something.",
        "category": "baffled_concern",
        "instruction": "Baffled alarm and suspicious concern, searching for an explanation rather than rushing in panic.",
        "reason": "Concern is correct; 'urgent' overstates the performance.",
        "action": "replace_metadata",
    },
    "ff3107a98817296b": {
        "scene": "Playtest Ending — Stanley jumps out of reach",
        "source_reference": "Playtest Ending/Dialogue",
        "context": "The Narrator suddenly loses access to Stanley and realizes he cannot follow or help him.",
        "category": "panic_alarm",
        "instruction": "Immediate alarm and panicked pleading, shouted with tightening urgency and genuine fear of losing Stanley.",
        "reason": "The surrounding lines confirm this is a sudden panic response.",
        "action": "replace_metadata",
    },
    "2f04ec3946bc49af": {
        "scene": "Playtest Ending — response to a rating",
        "source_reference": "Playtest Ending/Dialogue",
        "context": "After a less-hostile rating, the Narrator cautiously decides there may be something in the game that appeals to Stanley.",
        "category": "baffled_concern",
        "instruction": "Cautiously encouraged and analytically curious, with guarded optimism rather than sarcasm.",
        "reason": "The line is a tentative positive inference, not a dry joke.",
        "action": "replace_metadata",
    },
    "299969c06b09eaa5": {
        "scene": "Warehouse cargo lift — the Narrator tries to regain control",
        "source_reference": "Warehouse/Dialogue",
        "context": "The Narrator attempts to coax Stanley back into the intended story while presenting control as concern for Stanley.",
        "category": "persuasive_manipulative",
        "instruction": "Softly manipulative concern; patient persuasion that masks a need to regain control of the story.",
        "reason": "The request is intentionally reassuring and controlling, not neutral narration.",
        "action": "replace_metadata",
    },
    "720076ad15419800": {
        "scene": "Warehouse cargo lift — conciliatory appeal",
        "source_reference": "Warehouse/Dialogue",
        "context": "The Narrator reframes the conflict as a misunderstanding and tries to re-establish cooperation.",
        "category": "persuasive_manipulative",
        "instruction": "Conciliatory, carefully persuasive reassurance with an undercurrent of manipulation.",
        "reason": "This is controlled persuasion rather than urgency.",
        "action": "replace_metadata",
    },
    "f31f22da07365e86": {
        "scene": "Narrator challenges Stanley's deviation",
        "source_reference": "2013 HD Remix Dialogue",
        "context": "The Narrator is confident he can absorb Stanley's attempt to derail the story and turns it into a challenge.",
        "category": "smug_menace",
        "instruction": "Composed, condescending challenge with confident menace and no visible concern.",
        "reason": "The delivery is threatening through confidence, not tension.",
        "action": "replace_metadata",
    },
    "3f2b9720ef82cf40": {
        "scene": "Countdown Ending — false generosity",
        "source_reference": "Countdown Ending/Dialogue",
        "context": "The Narrator cheerfully offers information because Stanley is going to die regardless.",
        "category": "smug_menace",
        "instruction": "Cheerful menace and sadistic amusement, bright in tone while calmly announcing death.",
        "reason": "Your understated-threat reading is correct; scene context strengthens the sadistic amusement.",
        "action": "replace_metadata",
    },
    "fe5488b31d3eff96": {
        "scene": "Zending — repeated self-destruction",
        "source_reference": "Zending/Dialogue",
        "context": "The Narrator has begged Stanley to remain in the happy place and is watching him repeatedly throw himself from the stairs.",
        "category": "pleading_desperation",
        "instruction": "Fragile, frightened pleading with helpless desperation and emotional exhaustion.",
        "reason": "This is more desperate and personal than a slightly concerned statement.",
        "action": "replace_metadata",
    },
    "7cb2e1f2f5d30ba1": {
        "scene": "Cold Feet Ending — jump encouragement and Choice PSA",
        "source_reference": "Cold Feet Ending/Dialogue",
        "context": "The extracted clip crosses two separate beats and appears to include a transition into another voice or presentation.",
        "category": "exuberant_joy",
        "instruction": "Taunting encouragement followed by detached surprise.",
        "reason": "Mixed boundaries make this unsafe training data in its current form.",
        "action": "exclude",
    },
    "898a17d77b75d9ef": {
        "scene": "Office achievement — escalating door clicks",
        "source_reference": "Office/Dialogue",
        "context": "The Narrator and Stanley have finally completed a deliberately absurd click sequence and the Narrator celebrates the payoff.",
        "category": "exuberant_joy",
        "instruction": "Unrestrained, delighted exhilaration with a spontaneous burst of triumph.",
        "reason": "This is substantially stronger than 'pleasantly surprised'.",
        "action": "replace_metadata",
    },
    "96775abe7a0eee0e": {
        "scene": "Apartment Ending — Stanley ignores the Narrator",
        "source_reference": "Apartment Ending/Dialogue",
        "context": "The Narrator watches Stanley remain trapped in a repetitive game despite warnings that it is slowly killing him.",
        "category": "frustrated_helpless",
        "instruction": "Sorrowful frustration and helpless concern, spoken as the Narrator realizes Stanley will not listen.",
        "reason": "Your helpless/sad interpretation is supported by the surrounding moral plea.",
        "action": "replace_metadata",
    },
    "f70e019f3c6ebfba": {
        "scene": "Office achievement — escalating door clicks",
        "source_reference": "Office/Dialogue",
        "context": "The Narrator congratulates Stanley after the absurd click challenge succeeds.",
        "category": "exuberant_joy",
        "instruction": "Effusive, ecstatic praise with playful triumph and energetic emphasis.",
        "reason": "The performance is celebratory and heightened, not ordinary happiness.",
        "action": "replace_metadata",
    },
    "77661320980ba40a": {
        "scene": "Empty office discovery",
        "source_reference": "Office/Dialogue",
        "context": "Stanley has found the office abandoned and confronts the possibility that no one else remains.",
        "category": "baffled_concern",
        "instruction": "Fearful uncertainty and dawning isolation, restrained but unsettled rather than overtly sad.",
        "reason": "The question expresses apprehension, not grief.",
        "action": "replace_metadata",
    },
    "a1ad4ce97174e279": {
        "scene": "Employee Lounge detour",
        "source_reference": "2013 HD Remix Dialogue — Employee Lounge",
        "context": "The Narrator performs mock-poetic analysis of an unremarkable room before abruptly dismissing each imagined quality.",
        "category": "dry_sarcastic",
        "instruction": "Mock-poetic admiration turning into a dry, dismissive punchline.",
        "reason": "The rhetorical questions are part of the sarcasm, not neutral description.",
        "action": "replace_metadata",
    },
    "ce87f001771f4e05": {
        "scene": "Confusion Ending — Adventure Line introduced",
        "source_reference": "Confusion Ending/Dialogue",
        "context": "The Narrator believes he has found an elegant solution to repeatedly losing the story.",
        "category": "boastful_pride",
        "instruction": "Upbeat confidence and showman-like pride while presenting an ingenious solution.",
        "reason": "The line has deliberate flourish and optimism, not neutral exposition.",
        "action": "replace_metadata",
    },
    "ce07cc750653d3d8": {
        "scene": "Comedic gag sequence",
        "source_reference": "2013 HD Remix Dialogue",
        "context": "The Narrator drops the bit and checks whether Stanley is as tired of it as he is.",
        "category": "dry_sarcastic",
        "instruction": "Breezy impatience and playful dismissal, conversationally moving on from a worn-out gag.",
        "reason": "The performance is casual comic fatigue rather than urgency.",
        "action": "replace_metadata",
    },
}

# Scene-sized recuts selected for emotional range. Exact words remain reviewer-confirmed.
SUPPLEMENT = [
    ("zending_happiness", 164.78, 178.68, "Zending — Happy Place", "The Narrator discovers a place where he and Stanley could stop the conflict and remain together.", "Oh, it's beautiful, isn't it? If we just stay right here, right in this moment with this place, Stanley, I think I feel happy. I actually feel happy.", "exuberant_joy", "Soft wonder blossoming into genuine, vulnerable happiness; intimate and increasingly delighted."),
    ("zending_relief", 219.82, 225.94, "Zending — Stanley survives a fall", "After fearing Stanley has died and reset the game, the Narrator hears him return.", "Oh, thank God you lived. You had me worried there for a moment.", "pleading_desperation", "Shocked, breathless relief after genuine fear, with affection breaking through immediately."),
    ("zending_hurt", 315.72, 329.96, "Zending — repeated stair jumps", "Stanley repeatedly rejects the happy place by throwing himself from the stairs.", "My God, is this really how much you dislike my game? That you'll throw yourself from this platform over and over to be rid of it? You were literally willing to kill yourself to keep me from being happy. Am I reading the situation correctly?", "wounded_rage", "Deeply hurt disbelief turning accusatory, personal, and emotionally raw without losing intelligibility."),
    ("zending_plea", 333.64, 346.64, "Zending — plea to stop", "The Narrator admits he only wanted Stanley and himself to be happy together.", "I wanted us to be happy here, Stanley. Maybe you're just getting a kick out of it. I don't know anymore. I just wanted us to get along.", "pleading_desperation", "Wounded, exhausted pleading; sadness and confusion under a fading attempt to reason."),
    ("zending_resignation", 347.10, 358.74, "Zending — final resignation", "The Narrator realizes Stanley has chosen another fall and anticipates the reset.", "But I guess that was too much to ask. It looks like you wanted to make a choice after all. Well, this one is yours. Is it over? It's going to restart, isn't it?", "vulnerable_sad", "Defeated resignation and quiet heartbreak, ending in fearful certainty about the coming reset."),
    ("achievement_triumph", 379.16, 387.32, "Office achievement — completed click challenge", "An absurdly elaborate click sequence finally succeeds.", "Yes! We did it! Oh, wow! That felt amazing! Oh! You really earned it, Stanley!", "exuberant_joy", "Unrestrained celebration, exhilaration, and playful praise with genuine triumphant energy."),
    ("apartment_helpless", 490.60, 502.90, "Apartment Ending — Stanley will not stop", "The Narrator explains that Stanley is slowly destroying himself by remaining an observer.", "And I'm trying to tell him this, that in this world he can never be anything but an observer, that as long as he remains here, he's slowly killing himself. But he won't listen to me. He won't stop.", "frustrated_helpless", "Sorrowful moral urgency giving way to helpless frustration and grief."),
    ("apartment_frustration", 511.14, 526.24, "Apartment Ending — failed warning", "Stanley continues pressing buttons despite the Narrator's direct warning.", "Can he just not hear me? That every second he remains here, he's electing to kill himself? How can I get him to see what I see? I suppose I can't, not in the way I want him to.", "frustrated_helpless", "Frustrated disbelief, pleading concern, and eventual helpless resignation."),
    ("confusion_fluster", 1630.48, 1640.06, "Confusion Ending — lost directions", "The Narrator realizes his directions are wrong and tries to recover his place in the script.", "Oh, no. No, it's to the right, my mistake. No! No, no, no! Not the right! Why would I have ever said it was to the right? What was I thinking?", "panic_alarm", "Rapidly escalating fluster and self-directed panic, with repeated corrections and loss of composure."),
    ("confusion_spoiler_panic", 1664.54, 1675.44, "Confusion Ending — accidental spoiler", "Stanley reaches the monitor room too early and the Narrator realizes the story has become unusable.", "This is all a spoiler. Quick, Stanley, close your eyes. We just, we just have to get back to— oh. Who am I kidding? It's all rubbish now.", "panic_alarm", "Urgent spoiler panic collapsing abruptly into defeated hopelessness."),
    ("confusion_failed_restart", 1694.78, 1709.22, "Confusion Ending — failed restart", "The Narrator restarted the game, but the expected story is still missing.", "I swear, I definitely restarted the game over completely fresh. Everything should be— Stanley, did you change anything when we were back in that room with all the monitors? Did you move the story somewhere, or...", "baffled_concern", "Baffled alarm and suspicious concern, stumbling as confidence gives way to confusion."),
    ("confusion_schedule_discovery", 2050.60, 2058.94, "Confusion Ending — schedule discovered", "The Narrator encounters a board describing the entire ending and realizes the situation itself has been predetermined.", "Oh, hold up, what's this? You're telling me that's what this is?", "baffled_concern", "Startled discovery and wary disbelief as the implications begin to register."),
    ("confusion_schedule_horror", 2062.00, 2071.66, "Confusion Ending — schedule discovered", "The schedule says the game will restart eight times and predetermines everything that follows.", "I'm supposed to restart the game eight, eight times? That's really how all this goes? It's all determined?", "panic_alarm", "Horrified disbelief, stammering on the number as loss of control becomes clear."),
    ("confusion_loss_of_agency", 2086.86, 2097.96, "Confusion Ending — rebellion against the schedule", "The Narrator rejects a schedule that dictates his actions and memories.", "Well, who consulted me? Why don't I get to decide? Why don't I get a say in all of this? Is it really— No, it can't be. I don't want it to be.", "wounded_rage", "Indignant protest escalating into frightened denial as personal agency disappears."),
    ("confusion_trapped", 2097.80, 2109.42, "Confusion Ending — rebellion against the schedule", "The Narrator fears endless resets will erase his memory and trap him in the loop.", "I don't want the game to keep restarting. I don't want to forget what's going on. I don't want to be trapped like this. I won't restart the game. I won't do it! I won't do it! I won't do it.", "panic_alarm", "Genuine fear of erasure escalating into frantic, repeated refusal."),
    ("confusion_tentative_hope", 2110.26, 2126.72, "Confusion Ending — timer stops", "The timer unexpectedly stops after the Narrator refuses to restart.", "And the timer... uh, stopped? Does that mean... did we do it? Did we break the cycle? The, um... whatever it is that made this schedule? How would we even know? Will someone come for us? Will something happen?", "baffled_concern", "Tentative hope mixed with anxious uncertainty, halting and afraid to trust the apparent escape."),
    ("countdown_false_kindness", 2327.24, 2338.36, "Countdown Ending — co-worker revelation", "With Stanley's death imminent, the Narrator offers an answer as entertainment.", "You'd like to know where your co-workers are? A moment of solace before you're obliterated. All right, I'm in a good mood. You're going to die anyway. I'll tell you exactly what happened to them.", "smug_menace", "Bright, falsely generous menace; casual sadistic amusement under polished narration."),
    ("countdown_erased_them", 2338.50, 2346.30, "Countdown Ending — co-worker revelation", "The Narrator reveals that he erased Stanley's co-workers and controls alternate versions of the story.", "I erased them. I turned off the machine. I set you free. Of course, that was merely in this instance of the story.", "smug_menace", "Calm, smug sadism with effortless superiority and a chillingly casual correction."),
    ("countdown_extra_time", 2379.60, 2396.08, "Countdown Ending — extending the clock", "The Narrator is enjoying Stanley's panic so much that he adds time to prolong it.", "My goodness, only thirty-four seconds left, but I'm enjoying this so much. To hell with it. I'm going to put some extra time on the clock. Why not? These are precious additional seconds, Stanley. Time doesn't grow on trees.", "smug_menace", "Manic delight and theatrical sadism, savoring Stanley's panic with playful cruelty."),
    ("countdown_mockery", 2397.22, 2413.64, "Countdown Ending — mocking the controls", "Stanley runs between buttons searching for a way to stop the explosion.", "Oh dear me, what's the matter, Stanley? I mean, look at you, running from button to button.", "smug_menace", "Condescending mock concern and amused taunting while observing frantic desperation."),
    ("dream_wake_plea", 2739.78, 2754.42, "Dream Ending — Stanley begs to wake", "Stanley tries to convince himself the world is a dream and pleads to return to his ordinary life.", "Let me wake up, he thought to himself. I'm through with this dream. I wish it to be over. Let me go back to my job. Let me continue pushing the buttons. Please, it's all I want.", "pleading_desperation", "Escalating desperate self-pleading, increasingly frightened and emotionally exposed."),
    ("dream_normal_life", 2754.18, 2768.20, "Dream Ending — Stanley clings to normality", "Stanley lists the ordinary life he needs to believe is real.", "I want my apartment and my wife and my job. All I want is my life exactly the way it's always been. My life is normal. I am normal.", "defensive_reassurance", "Longing and desperate self-reassurance, asserting normality to suppress mounting terror."),
    ("dream_fragile_reassurance", 2768.82, 2774.70, "Dream Ending — reassurance fractures", "Stanley's effort to reassure himself has become short, fragile declarations.", "Everything will be fine. I am okay.", "defensive_reassurance", "Fragile, deliberate reassurance with fear plainly visible beneath the words."),
    ("mariella_clean_recut", 2837.42, 2845.08, "Dream Ending — Mariella reassures herself", "Mariella insists she is sane after seeing Stanley behaving irrationally.", "I am sane. I am in control of my mind. I know what is real, and what isn't.", "defensive_reassurance", "Firm defensive certainty, using measured declarations to cover private unease."),
    ("choice_pleading", 3315.80, 3331.52, "Incorrect Ending — the story begs for a choice", "As the reconstructed story waits for player input, the Narrator becomes increasingly dependent on a response.", "I need you to make a choice. I need you to walk through the door. Are you listening to me? Can you hear me? Is everything all right? Stanley, this is important. The story needs you.", "pleading_desperation", "Escalating urgent pleading, moving from instruction to fear that Stanley is gone."),
    ("choice_desperate", 3346.56, 3361.42, "Incorrect Ending — the story begs for a choice", "No choice arrives, and the Narrator's need becomes overt and personal.", "Do something. This is more important than you can ever know. I need this. The story needs it. So, you hear me? Are you there?", "pleading_desperation", "Raw desperation and dependency, with increasingly isolated calls for any response."),
    ("incorrect_existential_despair", 3531.44, 3538.84, "Incorrect Ending — ruined meeting room", "The Narrator cannot accept continuing with knowledge that his story is permanently incorrect.", "I can't erase that knowledge. I couldn't live that way.", "vulnerable_sad", "Quiet existential despair and devastated finality."),
    ("incorrect_shutdown_breakdown", 3544.98, 3554.70, "Incorrect Ending — ruined meeting room", "Unable to find another answer, the Narrator decides to destroy the game himself.", "What's the answer? What do I do? What do I do? What do I—? No, I have to. I have to shut the game down. I have to. I have to.", "panic_alarm", "Panicked breakdown and compulsive repetition, forcing himself toward a catastrophic decision."),
    ("incorrect_shock_to_rage", 3556.84, 3568.70, "Incorrect Ending — destroyed game", "After the shutdown, the Narrator finds himself still conscious in the wreckage with the player.", "I'm here. I'm still here. Here in this pile of rubbish. With you. You, who thought you were so clever. Now look where we are. My entire game is destroyed.", "wounded_rage", "Stunned shock hardening into personal, wounded rage and accusatory spite."),
    ("incorrect_personal_loss", 3569.04, 3576.38, "Incorrect Ending — destroyed game", "The Narrator names the game as the only thing that belonged to him and accuses the player of ruining it.", "It was the only thing in the world that was mine, and you've run it into the ground. What, did you think that would be funny? You just had to see?", "wounded_rage", "Deep personal hurt exploding into venomous accusation and disbelief."),
    ("incorrect_furious_scolding", 3576.72, 3594.62, "Incorrect Ending — destroyed game", "The Narrator contrasts the player with Stanley and condemns the player's self-centeredness.", "Didn't I impress upon you how important it was to be like Stanley? He understands that if I say to do something, there's a damn good reason for it! That thought hadn't even occurred to you, had it? That there's a world outside of you? You're a child.", "wounded_rage", "Furious, contemptuous scolding with emphatic moral outrage and personal betrayal."),
    ("playtest_rating_one", 4168.32, 4181.24, "Playtest Ending — rating of one", "The Narrator receives the lowest possible rating for a game he proudly presented.", "A one? I mean, I can understand if you had reservations, you saw ways the game could be improved to more fully express itself mechanically and artistically, but a one? That's not even helpful. What am I supposed to do with that?", "wounded_rage", "Wounded professional pride escalating into indignant anger and incredulous protest."),
    ("playtest_rating_three", 4197.20, 4204.28, "Playtest Ending — rating of three", "A middling rating produces contemptuous disappointment rather than useful feedback.", "Oh, of course. A three. Really. Maybe next time we can get you to form an actual opinion, you know?", "contemptuous", "Snide exasperation, clipped contempt, and dismissive disappointment."),
    ("playtest_lost_stanley", 4250.68, 4264.72, "Playtest Ending — Stanley jumps out of reach", "The Narrator realizes Stanley has left the playable space and cannot be followed.", "No, wait. Stanley, where are you? Don't go anywhere. I can't follow you there. I can't help you. No, just stay there. I'll find a way to get you out.", "panic_alarm", "Sudden alarm and urgent pleading, genuinely afraid and scrambling to rescue Stanley."),
    ("minecraft_pride", 4356.36, 4365.04, "Playtest Ending — Minecraft house", "The Narrator presents the house he has just improvised and expects Stanley to admire it.", "I made this, Stanley. Gaze upon my work of art and feel ashamed at your own inadequacy.", "boastful_pride", "Grandiose, theatrical pride with playful arrogance and self-satisfaction."),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:length]


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(text).casefold()))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_segment_rows(prior_root: Path) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for archive_path in sorted((prior_root / "segments").glob("segment_*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            rows[archive_path.stem] = [
                json.loads(line)
                for line in archive.read("metadata.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
    old = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git/lora_datasets/narrator_attempt1/metadata.jsonl")
    if old.is_file():
        rows["pilot_source"] = [json.loads(line) for line in old.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def local_context(source: dict[str, Any], source_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    sequence = source_rows.get(source["source_key"], [])
    index = None
    for candidate_index, row in enumerate(sequence):
        if row.get("text") != source.get("text"):
            continue
        left = row.get("source_start_seconds")
        right = source.get("source_start_seconds")
        if left is None or right is None or abs(float(left) - float(right)) < 0.02:
            index = candidate_index
            break
    if index is None:
        return {"before": [], "after": []}
    return {
        "before": [str(item.get("text") or "") for item in sequence[max(0, index - 2):index]],
        "after": [str(item.get("text") or "") for item in sequence[index + 1:index + 3]],
    }


def run_ffmpeg(source: Path, start: float, end: float, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-to", str(end), "-i", str(source), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or f"ffmpeg failed for {destination}")


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    prior_root = Path(args.prior_root).expanduser().resolve()
    prior_results = Path(args.prior_results).expanduser().resolve()
    source_video = Path(args.source_video).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)

    prior_manifest = load_json(prior_root / "triage-manifest.json")
    source_by_id = {row["sample_id"]: row for row in prior_manifest["rows"]}
    prior_review = load_json(prior_results)
    review_by_id = {row["sample_id"]: row for row in prior_review["rows"]}
    segment_rows = load_segment_rows(prior_root)

    correction_rows = []
    private_corrections = []
    for sample_id, recommendation in CORRECTIONS.items():
        source = source_by_id.get(sample_id)
        original = review_by_id.get(sample_id)
        if source is None or original is None or original.get("status") != "accepted":
            continue
        source_audio = prior_root / "review" / "audio" / f"{sample_id}.wav"
        target = review_root / "audio" / f"correction-{sample_id}.wav"
        shutil.copy2(source_audio, target)
        context = local_context(source, segment_rows)
        recommended_transcript = recommendation.get("transcript") or original["transcript"]
        public = {
            "sample_id": sample_id,
            "kind": "correction",
            "audio_url": f"audio/{target.name}",
            "scene": recommendation["scene"],
            "source_reference": recommendation["source_reference"],
            "context": recommendation["context"],
            "before": context["before"],
            "after": context["after"],
            "original": {
                "transcript": original["transcript"],
                "instruction": original["instruction"],
                "category": original["category"],
                "notes": original.get("notes") or "",
            },
            "recommendation": {
                "transcript": recommended_transcript,
                "instruction": recommendation["instruction"],
                "category": recommendation["category"],
                "reason": recommendation["reason"],
                "default_action": recommendation["action"],
            },
        }
        correction_rows.append(public)
        private_corrections.append({**public, "audio_path": str(target), "audio_sha256": sha256_file(target), "source": source})

    supplement_rows = []
    private_supplement = []
    for key, start, end, scene, context, transcript, category, instruction in SUPPLEMENT:
        sample_id = fingerprint({"key": key, "source_sha256": sha256_file(source_video), "start": start, "end": end, "transcript": transcript})
        target = review_root / "audio" / f"supplement-{sample_id}.wav"
        run_ffmpeg(source_video, start, end, target)
        public = {
            "sample_id": sample_id,
            "kind": "supplement",
            "audio_url": f"audio/{target.name}",
            "scene": scene,
            "context": context,
            "transcript": transcript,
            "instruction": instruction,
            "category": category,
            "source_start_seconds": start,
            "source_end_seconds": end,
            "duration_seconds": round(end - start, 3),
        }
        supplement_rows.append(public)
        private_supplement.append({**public, "audio_path": str(target), "audio_sha256": sha256_file(target)})

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    public_data = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "categories": CATEGORIES,
        "corrections": correction_rows,
        "supplement": supplement_rows,
    }
    (review_root / "data.js").write_text("window.NARRATOR_CONTEXT_DATA = " + json.dumps(public_data, ensure_ascii=False) + ";\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "source_video": str(source_video),
        "source_video_sha256": sha256_file(source_video),
        "prior_round_id": prior_manifest["round_id"],
        "prior_results_sha256": sha256_file(prior_results),
        "corrections": private_corrections,
        "supplement": private_supplement,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        "Alexandria Narrator Context & Emotion Pass\n"
        "===========================================\n\n"
        f"Context corrections: {len(correction_rows)}\n"
        f"Emotion supplement: {len(supplement_rows)}\n\n"
        "Terminal 1:\n"
        f"  cd \"{review_root}\"\n"
        "  python3 -m http.server 8771 --bind 127.0.0.1\n\n"
        "Terminal 2:\n"
        "  open \"http://127.0.0.1:8771/\"\n\n"
        "Review the context corrections first. Apply the recommendation when it "
        "matches the performance, keep your original decision when it does not, "
        "or exclude contaminated audio. Then review the scene-sized emotional "
        "recuts and export the cumulative JSON.\n",
        encoding="utf-8",
    )
    return {"review": str(review_root / "index.html"), "correction_count": len(correction_rows), "supplement_count": len(supplement_rows)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    manifest = load_json(root / "manifest.json")
    missing, bad_hash, external = [], [], []
    review_root = root / "review"
    for row in manifest["corrections"] + manifest["supplement"]:
        path = Path(row["audio_path"]).resolve()
        if not path.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(path) != row["audio_sha256"]:
            bad_hash.append(row["sample_id"])
        try:
            path.relative_to(review_root.resolve())
        except ValueError:
            external.append(row["sample_id"])
    if missing or bad_hash or external:
        raise RuntimeError(f"validation failed missing={missing} bad_hash={bad_hash} external={external}")
    return {"round_id": manifest["round_id"], "corrections": len(manifest["corrections"]), "supplement": len(manifest["supplement"]), "missing": 0, "bad_hash": 0, "external": 0}


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    prior_root = Path(args.prior_root).expanduser().resolve()
    prior_results_path = Path(args.prior_results).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    results_path = Path(args.results).expanduser().resolve()
    output_zip = Path(args.output_zip).expanduser().resolve()
    manifest = load_json(output_root / "manifest.json")
    prior_manifest = load_json(prior_root / "triage-manifest.json")
    prior_source = {row["sample_id"]: row for row in prior_manifest["rows"]}
    prior_results = load_json(prior_results_path)
    result = load_json(results_path)
    if result.get("round_id") != ROUND_ID:
        raise RuntimeError("results belong to a different round")
    context_corrections = {row["sample_id"]: row for row in result.get("corrections", [])}
    supplement_results = {row["sample_id"]: row for row in result.get("supplement", [])}
    correction_manifest = {row["sample_id"]: row for row in manifest["corrections"]}
    supplement_manifest = {row["sample_id"]: row for row in manifest["supplement"]}

    accepted: list[dict[str, Any]] = []
    for prior in prior_results["rows"]:
        if prior.get("status") != "accepted":
            continue
        sample_id = prior["sample_id"]
        source = prior_source[sample_id]
        decision = context_corrections.get(sample_id)
        if decision and decision.get("action") in {"exclude", "replace_audio"}:
            continue
        transcript = prior["transcript"]
        instruction = prior["instruction"]
        category = prior["category"]
        provenance = "prior_review"
        if decision and decision.get("action") in {"apply_recommendation", "edited"}:
            transcript = str(decision.get("transcript") or "").strip()
            instruction = str(decision.get("instruction") or "").strip()
            category = str(decision.get("category") or "").strip()
            provenance = "context_corrected"
        if not transcript or not instruction or not category:
            raise RuntimeError(f"incomplete accepted correction {sample_id}")
        audio = prior_root / "review" / "audio" / f"{sample_id}.wav"
        accepted.append({"sample_id": sample_id, "audio": audio, "transcript": transcript, "instruction": instruction, "category": category, "provenance": provenance, "source_start_seconds": source.get("source_start_seconds")})

    for sample_id, decision in supplement_results.items():
        if decision.get("status") != "accepted":
            continue
        source = supplement_manifest.get(sample_id)
        if source is None:
            raise RuntimeError(f"unknown supplement {sample_id}")
        transcript = str(decision.get("transcript") or "").strip()
        instruction = str(decision.get("instruction") or "").strip()
        category = str(decision.get("category") or "").strip()
        if not decision.get("transcript_confirmed") or not transcript or not instruction or not category:
            raise RuntimeError(f"incomplete accepted supplement {sample_id}")
        accepted.append({"sample_id": sample_id, "audio": Path(source["audio_path"]), "transcript": transcript, "instruction": instruction, "category": category, "provenance": "emotion_supplement", "source_start_seconds": source.get("source_start_seconds")})

    if len(accepted) < int(args.minimum_accepted):
        raise RuntimeError(f"only {len(accepted)} accepted clips; minimum is {args.minimum_accepted}")
    neutral = [row for row in accepted if row["category"] == "neutral"] or accepted
    reference = max(neutral, key=lambda row: Path(row["audio"]).stat().st_size)
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists() and not args.force:
        raise RuntimeError(f"output exists: {output_zip}")
    with tempfile.TemporaryDirectory(prefix="alexandria-context-dataset-") as temporary:
        root = Path(temporary)
        shutil.copy2(reference["audio"], root / "ref.wav")
        (root / "ref_text.txt").write_text(reference["transcript"], encoding="utf-8")
        metadata = []
        for index, row in enumerate(accepted):
            filename = f"sample_{index:04d}.wav"
            shutil.copy2(row["audio"], root / filename)
            metadata.append({"audio_filepath": filename, "text": row["transcript"], "instruction": row["instruction"], "ref_audio": "ref.wav", "review_status": "accepted", "delivery_category": row["category"], "triage_sample_id": row["sample_id"], "provenance": row["provenance"], "source_start_seconds": row["source_start_seconds"]})
        (root / "metadata.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in metadata), encoding="utf-8")
        package = {"schema_version": 1, "dataset_id": args.dataset_id, "created_at": now_iso(), "instruction_mode": "per_record", "accepted_count": len(accepted), "source_round_id": ROUND_ID, "category_counts": {key: sum(row["category"] == key for row in accepted) for key in CATEGORIES}, "review_sha256": sha256_file(results_path)}
        (root / "preparation_manifest.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
        temp_zip = output_zip.with_suffix(output_zip.suffix + ".tmp")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.iterdir()):
                archive.write(path, arcname=path.name)
        os.replace(temp_zip, output_zip)
    return {"output_zip": str(output_zip), "sha256": sha256_file(output_zip), "accepted_count": len(accepted), "supplement_accepted": sum(row["provenance"] == "emotion_supplement" for row in accepted)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Context-correct reviewed Narrator clips and add a high-emotion supplement.")
    sub = parser.add_subparsers(dest="command", required=True)
    assemble_parser = sub.add_parser("assemble")
    assemble_parser.add_argument("--prior-root", required=True)
    assemble_parser.add_argument("--prior-results", required=True)
    assemble_parser.add_argument("--source-video", required=True)
    assemble_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--prior-root", required=True)
    finalize_parser.add_argument("--prior-results", required=True)
    finalize_parser.add_argument("--output-root", required=True)
    finalize_parser.add_argument("--results", required=True)
    finalize_parser.add_argument("--output-zip", required=True)
    finalize_parser.add_argument("--dataset-id", default="narrator_context_instruction_v1")
    finalize_parser.add_argument("--minimum-accepted", type=int, default=24)
    finalize_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "assemble":
            result = assemble(args)
        elif args.command == "validate":
            result = validate(args)
        elif args.command == "finalize":
            result = finalize(args)
        else:
            raise RuntimeError(args.command)
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
