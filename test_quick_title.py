import sys
import json

# Ensure UTF-8 output on Windows console (prevents cp1252 emoji crashes)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from title_tag_engine import generate_smart_title_and_hashtags

def run_demo():
    print("=" * 70)
    print("🚀 AUTONOMOUS CLIPPER - TITLE & JEV DECISION ENGINE TEST")
    print("=" * 70)

    # -------------------------------------------------------------
    # Test Clip 1: Outdoors Survival with Real-Time Visual Context
    # -------------------------------------------------------------
    print("\n--- [CLIP 1: Outdoors Survival with Visual Frame Context] ---")
    transcript_1 = (
        "So we're out here in Alaska and it's negative thirty degrees. "
        "We've got to build a snow cave before dark because the blizzard is "
        "coming in fast and if we don't get shelter we're in serious trouble. "
        "The wind is picking up and I can barely feel my hands."
    )
    visual_context_1 = {
        "scene_type": "Outdoor arctic mountain blizzard",
        "action_detected": "Person digging snow cave with shovel",
        "on_screen_text": "-30°F ALASKA"
    }

    result_1 = generate_smart_title_and_hashtags(
        transcript_text=transcript_1,
        category="outdoors_survival",
        visual_context=visual_context_1
    )

    print(f"🎬 Winning Title:      {result_1['suggested_title']}")
    print(f"📱 Short Title (TikTok): {result_1['short_title']}")
    print(f"🧠 Detected Niche:     {result_1['niche']}")
    print(f"🎯 Winning Framework:  {result_1['winning_framework']}")
    print(f"👁️ Visual Grounding:   {result_1['visual_grounding']}")
    print(f"🏷️ Hashtags:           {' '.join(result_1['suggested_hashtags'])}")
    print("\n📋 All 5 Candidate Titles Generated:")
    for idx, cand in enumerate(result_1['candidates'], 1):
        print(f"   {idx}. [{cand['framework']}] {cand['title']}")

    # -------------------------------------------------------------
    # Test Clip 2: Tech AI Clip (Text Only)
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("--- [CLIP 2: Tech / AI Clip (Transcript Only)] ---")
    transcript_2 = (
        "Most engineers think building an AI agent requires massive LLMs. "
        "The reality is that System One decision models like Jev solve "
        "complex routing and classification in under two hundred milliseconds "
        "with zero hallucinations."
    )

    result_2 = generate_smart_title_and_hashtags(
        transcript_text=transcript_2,
        category="tech_ai"
    )

    print(f"🎬 Winning Title:      {result_2['suggested_title']}")
    print(f"📱 Short Title (TikTok): {result_2['short_title']}")
    print(f"🧠 Detected Niche:     {result_2['niche']}")
    print(f"🎯 Winning Framework:  {result_2['winning_framework']}")
    print(f"👁️ Visual Grounding:   {result_2['visual_grounding']}")
    print(f"🏷️ Hashtags:           {' '.join(result_2['suggested_hashtags'])}")
    print("\n📋 All 5 Candidate Titles Generated:")
    for idx, cand in enumerate(result_2['candidates'], 1):
        print(f"   {idx}. [{cand['framework']}] {cand['title']}")

    print("\n" + "=" * 70)
    print("✅ All title pipelines executed cleanly without errors!")
    print("=" * 70)

if __name__ == "__main__":
    run_demo()
