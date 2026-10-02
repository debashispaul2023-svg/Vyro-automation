# Adaptive editor is a separate layer.
# Production Daily does not import it.
# Enable only for a manual test:
#   ADAPTIVE_EDITOR_ENABLED=1 python test_adaptive_editor.py --no-render
# ChatGPT review is optional, same session idea as Whop:
#   CHATGPT_REVIEW=1 and CHATGPT_COOKIE_HEADER
# If ChatGPT or Gemini fails, the editor logs it and continues.
# Default ADAPTIVE_EDITOR_ENABLED=0. Daily workflow is unchanged.
#
# Experience bank (not a trained model):
#   adaptive_editor/memory/
#   adaptive_editor/data/pattern_memory.json
# Import old footage without upload:
#   python -m adaptive_editor.memory.importer --directory ./old_campaigns
# Memory is reference. Footage is the source of truth.

